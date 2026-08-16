"""
回測 job 佇列（asyncio + subprocess、優先序、狀態機）

設計對應 PLAN_web_ui_v2.md §3.3：
  - 佇列排序：(priority DESC, created_at ASC)；高優先插隊，不中斷已執行中的 job
  - 並行限制：3.7GB RAM 上同時最多 2 個回測（MAX_CONCURRENT）
  - 等候佇列上限 5（QUEUE_LIMIT），超出回 429
  - 狀態機：queued → running → done | failed | cancelled
  - 每次狀態轉移 call sync.push_backtest_status()（非阻塞，失敗由 heartbeat 補推）
  - 子程序：python3 ai_report.py <flags>，stdout/stderr 寫入 jobs/<job_id>.log
  - jobs/<job_id>.json 記錄任務中繼資料（重啟時可復原非終態任務為 failed）
"""
from __future__ import annotations

import asyncio
import heapq
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from . import config, strategies, sync

logger = logging.getLogger("vps_api.backtest")

router = APIRouter(prefix="/backtest", tags=["backtest"])

# 合法狀態
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
TERMINAL_STATES = {STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED}

HEARTBEAT_INTERVAL = 30.0   # 補推失敗狀態的間隔（秒）


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    """單一回測任務（狀態 + 執行中資訊）。"""
    job_id: str
    strategy_key: str
    name: str
    priority: int                       # 0=low | 1=normal | 2=high
    params: dict[str, Any]              # 策展參數
    advanced_json: Optional[dict[str, Any]] = None
    requested_by: str = "web"
    notify: Optional[dict[str, Any]] = None
    # 狀態機
    status: str = STATUS_QUEUED
    progress: float = 0.0               # 0–1（選用）
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    created_at: str = field(default_factory=_now_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    # 執行細節
    log_path: Optional[str] = None
    artifact_path: Optional[str] = None
    # 內部旗標（不寫入 meta）
    cancel_requested: bool = field(default=False, repr=False)
    push_dirty: bool = field(default=True, repr=False)   # 狀態尚未成功推送
    _proc: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    _metadata_floor_mtime: float = field(default=0.0, repr=False)  # P1-4：job 開始前的 metadata mtime 下限

    def to_dict(self) -> dict[str, Any]:
        """序列化（供 meta 檔 / sync push / API 回傳）。"""
        return {
            "job_id": self.job_id,
            "strategy_key": self.strategy_key,
            "name": self.name,
            "priority": self.priority,
            "params": self.params,
            "advanced_json": self.advanced_json,
            "requested_by": self.requested_by,
            "notify": self.notify,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "result": self.result,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log_path": self.log_path,
            "artifact_path": self.artifact_path,
        }

    def push_payload(self) -> dict[str, Any]:
        """sync.push_backtest_status 的 body（對應 Worker /api/vps/sync/backtest）。"""
        return {
            "job_id": self.job_id,
            "strategy_key": self.strategy_key,
            "name": self.name,
            "status": self.status,
            "priority": self.priority,
            "progress": self.progress,
            "result_json": self.result,
            "error": self.error,
            "params_json": {"params": self.params, "advanced_json": self.advanced_json},
            "requested_by": self.requested_by,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "artifact_path": self.artifact_path,
        }


class BacktestQueue:
    """優先序 job 佇列（單一 worker 迴圈 + 並行上限）。"""

    def __init__(self, max_concurrent: int = config.MAX_CONCURRENT,
                 queue_limit: int = config.QUEUE_LIMIT) -> None:
        self.max_concurrent = max_concurrent
        self.queue_limit = queue_limit
        self._pending: list[tuple[int, int, Job]] = []   # heap: (-priority, seq, job)
        self._seq = 0
        self._running: dict[str, Job] = {}
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._worker_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._job_tasks: dict[str, asyncio.Task] = {}   # P1-5：追蹤 _run_job 任務，供 stop() 收尾

    # ---- 啟動 / 關閉 --------------------------------------------------

    async def start(self) -> None:
        """lifespan 啟動：恢復上次殘留任務 + 啟動 worker 與 heartbeat。"""
        self._recover_from_disk()
        self._worker_task = asyncio.create_task(self._worker_loop(), name="backtest-worker")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="backtest-heartbeat")

    async def stop(self) -> None:
        """
        lifespan 關閉：取消 worker 與 heartbeat，
        先 terminate 所有執行中的子程序，再等候 _run_job 任務收尾，
        最後把殘留的非終態任務標記 failed 並推送。

        P1-5：舊版只把 running 標 failed 就返回，uvicorn 退出後
        ai_report.py 子程序會成孤兒繼續跑（佔 RAM/CPU），且子程序
        正常結束後 _run_job 還會以 returncode==0 把 failed 覆寫回 done。
        """
        if self._worker_task:
            self._worker_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        # 先對執行中的子程序送 SIGTERM（讓 ai_report 有機會清理）
        for job in list(self._running.values()):
            if job._proc:
                try:
                    job._proc.terminate()
                except ProcessLookupError:
                    pass

        # 等候所有 _run_job 任務收尾（terminate 後 subprocess 應很快結束）
        tasks = list(self._job_tasks.values())
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=15.0
                )
            except asyncio.TimeoutError:
                # 仍有子程序賴著不退 → 強制 SIGKILL 後再等一次
                logger.warning("stop(): 子程序未在 15s 內退出，強制 SIGKILL")
                for job in list(self._running.values()):
                    if job._proc:
                        try:
                            job._proc.kill()
                        except ProcessLookupError:
                            pass
                await asyncio.gather(*tasks, return_exceptions=True)

        # 收尾後把仍非終態的任務標記 failed 並推送
        for job in list(self._running.values()):
            if job.status not in TERMINAL_STATES:
                job.status = STATUS_FAILED
                job.error = "服務關閉（process 結束）"
                job.finished_at = _now_iso()
            self._save_meta(job)
            await self._push_status(job)
        self._running.clear()
        self._job_tasks.clear()
        # 等候佇列中的任務也標記 failed（下次啟動由 recovery 決定）
        for _, _, job in self._pending:
            if job.status == STATUS_QUEUED:
                job.status = STATUS_FAILED
                job.error = "服務關閉（任務未執行）"
                self._save_meta(job)
                await self._push_status(job)

    # ---- 對外操作 ----------------------------------------------------

    async def submit(self, strategy_key: str, name: str, priority: int,
                     params: dict[str, Any],
                     advanced_json: Optional[dict[str, Any]],
                     requested_by: str,
                     notify: Optional[dict[str, Any]]) -> Job:
        """
        提交任務。佇列已滿（等候中 >= QUEUE_LIMIT）時拋 HTTPException 429。
        提交成功即 push queued 狀態（非阻塞）。
        """
        async with self._lock:
            waiting = self._pending_count_locked()
            if waiting >= self.queue_limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"佇列已滿（等候中 {waiting}/{self.queue_limit}），請稍後再試或改用較低優先序",
                )
            job = Job(
                job_id=uuid.uuid4().hex[:12],
                strategy_key=strategy_key,
                name=name or f"{strategy_key} {_now_iso()[:16]}",
                priority=priority,
                params=params,
                advanced_json=advanced_json,
                requested_by=requested_by,
                notify=notify,
            )
            self._jobs[job.job_id] = job
            heapq.heappush(self._pending, (-job.priority, self._seq, job))
            self._seq += 1
            self._save_meta(job)

        # 非阻塞推送 queued 狀態（失敗由 heartbeat 補推）
        asyncio.create_task(self._push_status(job))
        self._wake.set()
        return job

    async def cancel(self, job_id: str) -> Job:
        """
        取消任務：
          - queued：直接標記 cancelled（worker 取出時會跳過）
          - running：送出 SIGTERM，子程序結束後狀態轉為 cancelled
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"找不到任務 {job_id}")
        if job.status in TERMINAL_STATES:
            raise HTTPException(status_code=409, detail=f"任務已終止（{job.status}），無法取消")

        job.cancel_requested = True
        if job.status == STATUS_QUEUED:
            job.status = STATUS_CANCELLED
            job.finished_at = _now_iso()
            self._save_meta(job)
            asyncio.create_task(self._push_status(job))
            self._wake.set()
        elif job.status == STATUS_RUNNING and job._proc:
            # 先 SIGTERM（讓 ai_report 有機會清理），worker 收尾時判別為 cancelled
            try:
                job._proc.terminate()
            except ProcessLookupError:
                pass
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        """依建立時間倒序列出任務（最新在前）。"""
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]

    def stats(self) -> dict[str, Any]:
        """供 /health 顯示佇列狀態。"""
        return {
            "queued": self._pending_count(),
            "running": len(self._running),
            "max_concurrent": self.max_concurrent,
            "queue_limit": self.queue_limit,
            "total": len(self._jobs),
        }

    def pending_queued(self) -> int:
        """等候佇列中尚未執行的任務數。"""
        return self._pending_count()

    # ---- worker 迴圈 --------------------------------------------------

    async def _worker_loop(self) -> None:
        """持續取出最高優先 job；執行中數量 < 上限時才取。"""
        while True:
            async with self._lock:
                while len(self._running) < self.max_concurrent:
                    job = self._pop_pending_locked()
                    if job is None:
                        break
                    self._running[job.job_id] = job
                    task = asyncio.create_task(self._run_job(job))
                    self._job_tasks[job.job_id] = task   # P1-5：追蹤任務供 stop() 收尾
            await self._wake.wait()
            self._wake.clear()

    async def _run_job(self, job: Job) -> None:
        """執行單一 job：subprocess 跑 ai_report.py，log 寫入 jobs/<id>.log。"""
        job.status = STATUS_RUNNING
        job.started_at = _now_iso()
        job.log_path = str(config.JOBS_DIR / f"{job.job_id}.log")
        # P1-4：記錄 job 開始前的 metadata mtime 下限，
        # 稍後 _extract_result 只接受開始後產生的檔，避免並行 job 交叉污染
        job._metadata_floor_mtime = self._current_metadata_mtime()
        self._save_meta(job)
        await self._push_status(job)

        # 組 CLI：策展參數 + advanced_json 逃生門
        cli_args = strategies.build_cli_args(job.strategy_key, job.params, job.advanced_json)
        cmd = [sys.executable, config.AI_REPORT_SCRIPT, *cli_args]
        logger.info("[%s] 開始執行: %s", job.job_id, " ".join(cmd))

        try:
            with open(job.log_path, "w", encoding="utf-8") as log_f:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(config.TW_STOCKER_ROOT),
                    stdout=log_f,
                    stderr=asyncio.subprocess.STDOUT,
                )
                job._proc = proc
                returncode = await proc.wait()

            if job.cancel_requested:
                job.status = STATUS_CANCELLED
            elif returncode == 0:
                job.status = STATUS_DONE
                job.result = self._extract_result(job)
            else:
                job.status = STATUS_FAILED
                job.error = f"ai_report.py 結束碼 {returncode}（詳見 log）"
            job.progress = 1.0
        except asyncio.CancelledError:
            # 服務關閉中
            job.status = STATUS_FAILED
            job.error = "服務關閉（job 被取消）"
            raise
        except Exception as exc:  # noqa: BLE001 — 任何錯誤都轉為 failed
            logger.exception("[%s] 執行例外", job.job_id)
            job.status = STATUS_FAILED
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = _now_iso()
            self._save_meta(job)
            self._running.pop(job.job_id, None)
            self._job_tasks.pop(job.job_id, None)   # P1-5：任務結束即移出追蹤
            await self._push_status(job)
            self._wake.set()

    def _current_metadata_mtime(self) -> float:
        """
        P1-4：回傳 artifacts/metadata_*.json 目前的最高 mtime（秒）。

        在 job 開始時快照此值作為下限，_extract_result 只接受
        mtime 高於此下限的 metadata 檔，確保並行 job 不會誤取
        其他 job（或先前殘留）產生的結果。
        """
        try:
            artifacts_dir = config.TW_STOCKER_ROOT / "artifacts"
            return max((p.stat().st_mtime for p in artifacts_dir.glob("metadata_*.json")),
                       default=0.0)
        except OSError:
            return 0.0

    def _extract_result(self, job: Job) -> Optional[dict[str, Any]]:
        """
        從 ai_report.py 的 artifacts/metadata_<date>.json 提取摘要。

        註：ai_report.py 目前把 artifacts 寫在固定目錄（artifacts/），
        並行 job 可能互相覆寫 — Phase 2 需為 ai_report 增加輸出目錄參數
        （artifacts/runs/<job_id>/）以達成完整隔離。此處先複製最新 metadata 到 jobs/。

        P1-4：只接受 mtime 高於 job 開始時快照（_metadata_floor_mtime）
        的 metadata 檔；並行 job 同時完成時，不會把 A 的結果掛到 B 名下。
        """
        try:
            artifacts_dir = config.TW_STOCKER_ROOT / "artifacts"
            metas = sorted(
                (p for p in artifacts_dir.glob("metadata_*.json")
                 if p.stat().st_mtime > job._metadata_floor_mtime),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if not metas:
                logger.warning("[%s] 找不到開始後產生的 artifacts/metadata_*.json（floor=%.3f）",
                               job.job_id, job._metadata_floor_mtime)
                return None
            with open(metas[0], encoding="utf-8") as f:
                meta = json.load(f)
            result = {
                "metrics": meta.get("metrics", {}),
                "report_date": meta.get("report_date"),
                "strategy_version": meta.get("strategy_version"),
                "git_sha": meta.get("git_sha"),
                "artifacts": meta.get("artifacts", {}),
            }
            # 快照一份到 jobs/<job_id>.result.json（供前端經隧道下載）
            out = config.JOBS_DIR / f"{job.job_id}.result.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
            job.artifact_path = f"jobs/{job.job_id}.result.json"
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] 提取結果失敗: %s", job.job_id, exc)
            return None

    # ---- 狀態推送（非阻塞 + heartbeat 補推） --------------------------

    async def _push_status(self, job: Job) -> None:
        """
        推送目前狀態到 Worker。失敗時 job.push_dirty=True，
        由 heartbeat 每 30s 補推（Worker 端 upsert 冪等，重推安全）。
        """
        job.push_dirty = True
        ok = await sync.push_backtest_status(job.push_payload())
        if ok:
            job.push_dirty = False
        else:
            logger.warning("[%s] 狀態推送失敗（status=%s），等待 heartbeat 補推",
                           job.job_id, job.status)

    async def _heartbeat_loop(self) -> None:
        """定期補推所有 push_dirty 的任務（隧道中斷復原機制）。"""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            dirty = [j for j in self._jobs.values() if j.push_dirty]
            for job in dirty:
                asyncio.create_task(self._push_status(job))

    # ---- 內部輔助 ----------------------------------------------------

    def _pending_count_locked(self) -> int:
        return sum(1 for _, _, j in self._pending if j.status == STATUS_QUEUED)

    def _pending_count(self) -> int:
        return sum(1 for _, _, j in self._pending if j.status == STATUS_QUEUED)

    def _pop_pending_locked(self) -> Optional[Job]:
        """取出最高優先的 queued job（跳過已取消/終態的殘留項目）。"""
        while self._pending:
            _, _, job = heapq.heappop(self._pending)
            if job.status == STATUS_QUEUED:
                return job
            # 已取消/終態 → 丟棄
        return None

    def _save_meta(self, job: Job) -> None:
        """把任務中繼資料寫入 jobs/<job_id>.json（重啟復原用，best-effort）。"""
        try:
            meta_path = config.JOBS_DIR / f"{job.job_id}.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(job.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        except OSError as exc:
            logger.warning("[%s] 寫入 meta 失敗: %s", job.job_id, exc)

    def _recover_from_disk(self) -> None:
        """
        啟動時掃描 jobs/*.json：載入任務紀錄。
        非終態（queued/running，代表上次 process 被中斷）→ 標記 failed 並推送。
        """
        recovered = 0
        for meta_path in config.JOBS_DIR.glob("*.json"):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("job_id") is None:
                    continue
                job = Job(**{k: v for k, v in data.items() if k in Job.__dataclass_fields__})
                self._jobs[job.job_id] = job
                if job.status not in TERMINAL_STATES:
                    job.status = STATUS_FAILED
                    job.error = "服務重啟，未完成任務已中止"
                    job.finished_at = _now_iso()
                    self._save_meta(job)
                    asyncio.create_task(self._push_status(job))
                recovered += 1
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                logger.warning("復原 %s 失敗: %s", meta_path, exc)
        if recovered:
            logger.info("從 jobs/ 復原 %d 筆任務紀錄", recovered)


# ---------------------------------------------------------------------------
# singleton
# ---------------------------------------------------------------------------

queue = BacktestQueue()


async def start() -> None:
    await queue.start()


async def stop() -> None:
    await queue.stop()


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class BacktestRequest(BaseModel):
    """POST /backtest/jobs body（對應 PLAN §3.2 策展子集）。"""
    strategy: str = Field(..., description="策略 key（strategies registry）")
    name: Optional[str] = None
    priority: int = Field(1, ge=0, le=2, description="0=low | 1=normal | 2=high")
    params: dict[str, Any] = Field(default_factory=dict)
    advanced_json: Optional[dict[str, Any]] = None
    notify: Optional[dict[str, Any]] = None
    requested_by: str = "web"


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(req: BacktestRequest) -> dict[str, Any]:
    """提交回測任務。佇列滿回 429。"""
    meta = strategies.get_strategy(req.strategy)
    if meta is None:
        raise HTTPException(status_code=400, detail=f"未知策略: {req.strategy}（可用: {list(strategies.STRATEGIES)}）")
    # P1-2：schema 未齊的策略（sector_rotation_v2 / meal_money_v2）尚未接線，
    # 若放行會用 ai_report.py 執行並 mislabel 結果 → Phase 3 補齊 schema 前一律 400 拒絕
    if not meta.get("schema"):
        raise HTTPException(
            status_code=400,
            detail=f"策略 {req.strategy} 尚未開放（Phase 3 未開放，schema 補齊後才可提交）",
        )
    errors = strategies.validate_params(req.strategy, req.params)
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})

    job = await queue.submit(
        strategy_key=req.strategy,
        name=req.name,
        priority=req.priority,
        params=req.params,
        advanced_json=req.advanced_json,
        requested_by=req.requested_by,
        notify=req.notify,
    )
    return {"job_id": job.job_id, "status": job.status, "queued": queue.pending_queued()}


@router.get("/jobs")
async def list_jobs(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """任務清單（最新在前）。"""
    return {"jobs": queue.list_jobs(limit), "stats": queue.stats()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    """單一任務詳情（前端輪詢 Worker 讀 D1 為主，此端點為備援）。"""
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"找不到任務 {job_id}")
    return job.to_dict()


@router.get("/jobs/{job_id}/log")
async def get_job_log(job_id: str,
                      tail: int = Query(200, ge=1, le=5000)) -> dict[str, Any]:
    """即時 log tail（選用；狀態一律讀 D1）。"""
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"找不到任務 {job_id}")
    if not job.log_path or not Path(job.log_path).exists():
        return {"job_id": job_id, "lines": [], "note": "log 尚未產生"}
    lines = Path(job.log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    return {"job_id": job_id, "lines": lines[-tail:], "total": len(lines)}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    """取消任務（queued 直接取消；running 送 SIGTERM）。"""
    job = await queue.cancel(job_id)
    return {"job_id": job.job_id, "status": job.status}
