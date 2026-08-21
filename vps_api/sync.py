"""
D1 push client — VPS 主動推送狀態到 Worker（架構方向鐵則，見 PLAN_web_ui_v2.md §1）

VPS 永不直連 D1。D1 的唯一寫入入口是 Worker 的 /api/vps/sync/*（Service Token 保護）。
本模組負責：
  - push_backtest_status()：回測狀態機每次轉移時推送（冪等 upsert）
  - push_paper_snapshot()：paper 快照推送（每日 cron / 手動觸發）
  - 指數退避重試（1s → 2s → 4s → 8s，含 jitter，上限 5 次）
  - 失敗不拋例外：記錄 last_sync 供 /health 顯示，交由 heartbeat 補推

注意：所有 push 皆為「盡力而為」— 失敗絕不阻塞回測主流程。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from . import config

logger = logging.getLogger("vps_api.sync")

# 指數退避參數（秒）：第 1 次失敗後等 1s、2s、4s、8s…共 max_attempts 次
BACKOFF_BASE = 1.0
BACKOFF_MAX = 8.0
DEFAULT_MAX_ATTEMPTS = 5

# 上次推送狀態記錄（供 /health 聚合顯示）
_last_sync: dict[str, dict[str, Any]] = {}


class SyncClient:
    """Worker sync API 的 async client（httpx）。"""

    def __init__(self) -> None:
        self.base_url = config.WORKER_URL
        self.client_id = config.SYNC_CLIENT_ID
        self.client_secret = config.SYNC_CLIENT_SECRET
        self._http: Optional[httpx.AsyncClient] = None
        self.enabled = bool(self.base_url and self.client_id and self.client_secret)

    async def init(self) -> None:
        """在 lifespan 內建立 httpx client（需在 event loop 內）。"""
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                headers={
                    "CF-Access-Client-Id": self.client_id,
                    "CF-Access-Client-Secret": self.client_secret,
                    "Content-Type": "application/json",
                },
            )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ---- 對外 API -------------------------------------------------

    async def push_backtest_status(self, payload: dict[str, Any]) -> bool:
        """推送回測任務狀態轉移 → POST /api/vps/sync/backtest（Worker 端冪等 upsert）。"""
        return await self._push("/api/vps/sync/backtest", payload, sync_key="backtest")

    async def push_paper_snapshot(self, payload: dict[str, Any]) -> bool:
        """推送 paper 快照 → POST /api/vps/sync/paper（正規化三表由 Worker 處理）。"""
        return await self._push("/api/vps/sync/paper", payload, sync_key="paper")

    async def push_report_summary(self, payload: dict[str, Any]) -> bool:
        """推送日報摘要 → POST /api/vps/sync/report（訊號 mirror）。"""
        return await self._push("/api/vps/sync/report", payload, sync_key="report")

    # ---- 內部實作 -------------------------------------------------

    async def _push(self, path: str, payload: dict[str, Any], sync_key: str,
                    max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> bool:
        """
        帶指數退避的推送。回傳 True=成功；False=失敗（已記錄，不拋例外）。

        Worker 端 upsert 為冪等操作，因此重試安全；同步失敗由 heartbeat 補推。
        """
        if not self.enabled:
            logger.warning("sync 未啟用（缺 WORKER_URL 或 Service Token），略過 %s", path)
            self._record(sync_key, ok=False, error="sync 未設定（缺 WORKER_URL / Token）")
            return False

        url = f"{self.base_url}{path}"
        attempts = 0
        delay = BACKOFF_BASE
        while attempts < max_attempts:
            attempts += 1
            try:
                http = self._http
                if http is None:
                    await self.init()
                    http = self._http
                assert http is not None  # init() 後必存在
                resp = await http.post(url, json=payload)
                if resp.status_code < 300:
                    self._record(sync_key, ok=True, attempts=attempts)
                    return True
                # 4xx 代表請求本身有問題（如 body 超限），重試無意義 → 直接失敗
                if 400 <= resp.status_code < 500:
                    logger.error("push %s 回 4xx（不重試）: %s %s", path, resp.status_code, resp.text[:300])
                    self._record(sync_key, ok=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}")
                    return False
                logger.warning("push %s 回 %s（第 %d/%d 次）", path, resp.status_code, attempts, max_attempts)
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                logger.warning("push %s 失敗（第 %d/%d 次）: %s", path, attempts, max_attempts, exc)

            if attempts < max_attempts:
                # 指數退避 + jitter，避免 thundering herd
                jitter = random.uniform(0, delay * 0.5)
                await asyncio.sleep(delay + jitter)
                delay = min(delay * 2, BACKOFF_MAX)

        self._record(sync_key, ok=False, error=f"重試 {max_attempts} 次仍失敗")
        return False

    def _record(self, sync_key: str, ok: bool, **extra: Any) -> None:
        """記錄最後一次推送結果（供 /health 顯示）。"""
        _last_sync[sync_key] = {
            "ok": ok,
            "at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }

    def last_sync(self) -> dict[str, dict[str, Any]]:
        return dict(_last_sync)


# ---- 模組層級 singleton（lifespan 內 init） ------------------------------

_client: Optional[SyncClient] = None


def get_client() -> SyncClient:
    """取得 sync client singleton（未 init 時惰性建立）。"""
    global _client
    if _client is None:
        _client = SyncClient()
    return _client


async def init() -> SyncClient:
    """lifespan 啟動時呼叫。"""
    client = get_client()
    await client.init()
    return client


async def close() -> None:
    """lifespan 關閉時呼叫。"""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def push_backtest_status(payload: dict[str, Any]) -> bool:
    """模組層級便捷函式：推送回測狀態（供 backtest.py 呼叫）。"""
    return await get_client().push_backtest_status(payload)


async def push_paper_snapshot(payload: dict[str, Any]) -> bool:
    """模組層級便捷函式：推送 paper 快照。"""
    return await get_client().push_paper_snapshot(payload)


def last_sync() -> dict[str, dict[str, Any]]:
    """供 /health 讀取各類 sync 的最後狀態。"""
    return get_client().last_sync()


def _json_dumps(payload: dict[str, Any]) -> str:
    """除錯用：payload 序列化（避免 import 未使用）。"""
    return json.dumps(payload, ensure_ascii=False, default=str)


# 供測試/除錯：直接呼叫
if __name__ == "__main__":
    async def _main() -> None:
        logging.basicConfig(level=logging.DEBUG)
        await init()
        print("sync 設定:", {
            "base_url": get_client().base_url,
            "enabled": get_client().enabled,
            "last_sync": last_sync(),
        })
        await close()

    asyncio.run(_main())
