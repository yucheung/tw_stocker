"""
paper 狀態模組

  - GET /paper/state   → 讀取 repo 內 paper_equity.json（source of truth）
  - GET /paper/live    → 用 yfinance 即時報價重算持倉浮動損益（盤中/盤後皆可）
  - build_snapshot()   → 正規化快照（供 sync.push_paper_snapshot 推送 D1 mirror）

對應 PLAN_web_ui_v2.md：paper 狀態 source of truth 維持在 VPS/Repo，
D1 只當顯示用 mirror；寫入 D1 的唯一路徑是 Worker /api/vps/sync/paper。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from . import config, sync

logger = logging.getLogger("vps_api.paper")

router = APIRouter(prefix="/paper", tags=["paper"])


# ---------------------------------------------------------------------------
# 讀取 / 重算
# ---------------------------------------------------------------------------

def _load_paper_file() -> tuple[dict[str, Any], Optional[str]]:
    """讀取 paper_equity.json；回傳 (data, mtime_iso)。檔案不存在回傳 None data。"""
    path: Path = config.PAPER_FILE
    if not path.exists():
        logger.warning("paper_equity.json 不存在: %s", path)
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        return data, mtime
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("讀取 paper_equity.json 失敗: %s", exc)
        raise HTTPException(status_code=500, detail=f"paper_equity.json 讀取失敗: {exc}") from exc


def _latest_equity(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """取權益曲線最後一筆（{date, equity, capital, n_positions}）。"""
    curve = data.get("equity_curve") or []
    return curve[-1] if curve else None


async def _fetch_latest_prices(tickers: list[str]) -> dict[str, float]:
    """
    用 yfinance 抓最新收盤價（執行緒池執行，避免阻塞 event loop）。

    回傳 {ticker: 最新 close}。yfinance 對台股代碼需加 .TW 後綴。
    註：yfinance 是 blocking I/O，故用 run_in_executor 包住。
    """
    if not tickers:
        return {}
    symbols = [f"{t}.TW" for t in tickers]

    def _download() -> dict[str, float]:
        import yfinance as yf  # 延遲 import（僅此端點需要）
        try:
            df = yf.download(symbols, period="5d", progress=False, auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance 下載失敗: %s", exc)
            return {}
        if df is None or df.empty:
            return {}
        # df 可能是 MultiIndex（多檔）或單一 Series（一檔）
        close = df["Close"] if "Close" in df else df
        if hasattr(close, "columns") and len(close.columns) > 0 and close.columns.nlevels > 1:
            # MultiIndex columns: (Close, Ticker) — 取最後一列
            last = close.iloc[-1]
            out = {}
            for col in last.index:
                ticker = col[1]
                if ticker.endswith(".TW"):
                    ticker = ticker[:-3]
                val = last[col]
                if val is not None and val == val:  # 非 NaN
                    out[ticker] = float(val)
            return out
        # 單檔情形
        last = close.iloc[-1]
        val = float(last) if last == last else 0.0
        return {tickers[0]: val} if val else {}

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _download)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.get("/state")
async def get_paper_state() -> dict[str, Any]:
    """讀取 paper_equity.json 即時狀態（不做重算）。"""
    data, mtime = _load_paper_file()
    if data is None:
        raise HTTPException(status_code=404, detail="paper_equity.json 尚不存在（等待首次 GH Actions 更新）")
    return {
        "mtime": mtime,
        "source": str(config.PAPER_FILE),
        "start_date": data.get("start_date"),
        "capital": data.get("capital"),
        "initial_capital": data.get("initial_capital"),
        "latest_equity": _latest_equity(data),
        "n_positions": len(data.get("positions") or {}),
        "n_closed_trades": len(data.get("closed_trades") or []),
        "n_pending_orders": len(data.get("pending_orders") or []),
    }


@router.get("/live")
async def get_paper_live() -> dict[str, Any]:
    """
    yfinance 即時報價重算：每個持倉以最新 close mark-to-market，
    計算浮動損益與估算總權益。此為估算（未含手續費），正式值以 paper_tracker 為準。
    """
    data, mtime = _load_paper_file()
    if data is None:
        raise HTTPException(status_code=404, detail="paper_equity.json 尚不存在")

    positions = data.get("positions") or {}
    if not positions:
        return {"mtime": mtime, "positions": [], "total_equity_est": data.get("capital"),
                "note": "目前無持倉"}

    prices = await _fetch_latest_prices(list(positions.keys()))

    rows = []
    total_mv = 0.0
    realized = 0.0  # 已實現損益（簡化：由 equity_curve 起點反推）
    for ticker, pos in positions.items():
        entry = float(pos.get("entry", 0))
        shares = float(pos.get("shares", 0))
        price = prices.get(ticker)
        mv = price * shares if price else entry * shares
        total_mv += mv
        rows.append({
            "ticker": ticker,
            "entry": entry,
            "tp": pos.get("tp"),
            "sl": pos.get("sl"),
            "shares": shares,
            "entry_date": pos.get("entry_date"),
            "day_count": pos.get("day_count"),
            "latest_price": price,
            "market_value": round(mv, 2),
            "pnl": round(mv - entry * shares, 2) if price else None,
            "pnl_pct": round((price - entry) / entry, 4) if price and entry else None,
        })

    capital = float(data.get("capital", 0))
    # P1-3：capital 已是純現金（開倉時扣成本、平倉時加回），
    # 總權益 = 現金 + 持倉市值；舊式 `capital - invested_cost + total_mv`
    # 會把投入成本重複扣減一次，導致有持倉時權益低估達 2×成本。
    total_equity_est = round(capital + total_mv, 2)

    return {
        "mtime": mtime,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "positions": rows,
        "total_equity_est": total_equity_est,
        "n_positions": len(rows),
        "note": "即時估算值，正式權益以 paper_tracker 產出為準",
    }


@router.post("/refresh")
async def refresh_paper() -> dict[str, Any]:
    """觸發即時重算並推送 snapshot 到 Worker（Phase 3 完整版會呼叫 paper_tracker）。"""
    live = await get_paper_live()
    snapshot = build_snapshot()
    ok = await sync.push_paper_snapshot(snapshot)
    return {"ok": ok, "live": live, "snapshot_pushed": ok}


# ---------------------------------------------------------------------------
# snapshot 產出（供 sync 推送 / cron 使用）
# ---------------------------------------------------------------------------

def build_snapshot() -> dict[str, Any]:
    """
    正規化 paper snapshot（對應 D1 paper_snapshots / paper_positions / paper_trades 三表）。

    Worker 收到後拆表寫入；原始大 JSON 由 repo 端保留為 source of truth。
    """
    data, _ = _load_paper_file()
    if data is None:
        return {"error": "paper_equity.json 不存在"}

    latest = _latest_equity(data)
    positions = data.get("positions") or {}
    trades = data.get("closed_trades") or []

    return {
        "snapshot_date": latest.get("date") if latest else date.today().isoformat(),
        "equity": latest.get("equity") if latest else None,
        "capital": data.get("capital"),
        "n_positions": len(positions),
        "n_closed": len(trades),
        "positions": [
            {"ticker": t, "entry": p.get("entry"), "tp": p.get("tp"), "sl": p.get("sl"),
             "shares": p.get("shares"), "entry_date": p.get("entry_date"),
             "day_count": p.get("day_count")}
            for t, p in positions.items()
        ],
        "trades": [
            {"ticker": tr.get("ticker"), "entry": tr.get("entry"), "exit": tr.get("exit"),
             "shares": tr.get("shares"), "pnl": tr.get("pnl"), "pnl_pct": tr.get("pnl_pct"),
             "reason": tr.get("reason"), "entry_date": tr.get("entry_date"),
             "exit_date": tr.get("exit_date")}
            for tr in trades
        ],
    }
