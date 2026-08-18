#!/usr/bin/env python3
"""
tw_stocker 每日策略驗證腳本 (verify_daily_strategy.py)

在台股交易日收盤後，以 paper_equity.json 與 yfinance 市場資料驗證 v8.5 的六項策略規則，
將 Telegram 可直接推播的文字寫到 stdout，並把結構化結果原子化追加到 verify_log.json。
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Literal

import exchange_calendars as xcals
import pandas as pd

DEFAULT_PAPER_URL = "https://raw.githubusercontent.com/yucheung/tw_stocker/main/paper_equity.json"
DEFAULT_LOCAL_PAPER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "paper_equity.json"
)
DEFAULT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "verify_log.json"
)
TAIPEI_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class CheckResult:
    rule_id: str
    title: str
    severity: Literal["PASS", "WARNING", "CRITICAL"]
    summary: str
    details: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketData:
    close: pd.DataFrame
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame


def is_trading_day(date_or_dt: str | datetime | None = None, calendar: Any = None) -> bool:
    """
    判斷指定日期是否為台股 (XTAI) 交易日。
    若未指定日期，預設使用當前 Asia/Taipei 時間。
    """
    if calendar is None:
        calendar = xcals.get_calendar("XTAI")

    if date_or_dt is None:
        dt = datetime.now(TAIPEI_TZ)
        date_str = dt.strftime("%Y-%m-%d")
    elif isinstance(date_or_dt, datetime):
        date_str = date_or_dt.strftime("%Y-%m-%d")
    else:
        date_str = str(date_or_dt).strip()

    try:
        return bool(calendar.is_session(date_str))
    except Exception as e:
        raise RuntimeError(f"Calendar check failed for date {date_str}: {e}") from e


def load_paper_snapshot(
    url: str = DEFAULT_PAPER_URL,
    local_path: str = DEFAULT_LOCAL_PAPER_PATH,
    fetcher: Callable[[str], str | bytes | dict] | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """
    載入 paper snapshot 資料。優先自遠端 GitHub raw 取得，
    若失敗則 fallback 至本機唯讀檔案，並記錄 warning。
    """
    warnings: list[str] = []
    data = None
    source = "github_raw"

    # 1. 嘗試遠端讀取
    try:
        if fetcher is not None:
            raw_res = fetcher(url)
            if isinstance(raw_res, (str, bytes)):
                data = json.loads(raw_res)
            elif isinstance(raw_res, dict):
                data = raw_res
            else:
                raise ValueError(f"Unexpected fetcher return type: {type(raw_res)}")
        else:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "tw_stocker-verifier/1.0"},
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                content = response.read().decode("utf-8")
                data = json.loads(content)
    except Exception as remote_err:
        warnings.append(
            f"Remote paper fetch failed ({remote_err}); fell back to local file {local_path}"
        )
        source = "local_file"
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as local_err:
            raise RuntimeError(
                f"Both remote ({url}) and local ({local_path}) paper snapshot sources failed. "
                f"Remote error: {remote_err}; Local error: {local_err}"
            ) from local_err

    if not isinstance(data, dict):
        raise ValueError("Paper snapshot data must be a JSON object (dict)")

    return data, source, warnings


def validate_snapshot(snapshot: dict[str, Any]) -> tuple[bool, str | None, list[str], list[str]]:
    """
    檢查 paper snapshot 的 schema 完整性與 snapshot_date。
    回傳 (is_valid, snapshot_date, errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []

    required_top_keys = ["capital", "positions", "closed_trades", "equity_curve", "daily_signals"]
    for k in required_top_keys:
        if k not in snapshot:
            errors.append(f"Missing required top-level key: '{k}'")

    if "capital" in snapshot:
        cap = snapshot["capital"]
        if not isinstance(cap, (int, float)) or not math.isfinite(cap):
            errors.append(f"Invalid 'capital' field: {cap}")

    if "positions" in snapshot:
        if not isinstance(snapshot["positions"], dict):
            errors.append("'positions' must be a dictionary")
        else:
            for ticker, pos in snapshot["positions"].items():
                if not isinstance(pos, dict):
                    errors.append(f"Position '{ticker}' must be a dictionary")
                    continue
                req_fields = ["entry", "tp", "sl", "entry_date", "shares", "day_count"]
                for rf in req_fields:
                    if rf not in pos:
                        errors.append(f"Position '{ticker}' missing field '{rf}'")
                for num_field in ["entry", "tp", "sl", "shares"]:
                    val = pos.get(num_field)
                    if not isinstance(val, (int, float)) or not math.isfinite(val) or val <= 0:
                        errors.append(f"Position '{ticker}' invalid {num_field}: {val}")
                dc = pos.get("day_count")
                if not isinstance(dc, int) or dc < 0:
                    errors.append(f"Position '{ticker}' invalid day_count: {dc}")
                ed = pos.get("entry_date")
                if not isinstance(ed, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", ed):
                    errors.append(f"Position '{ticker}' invalid entry_date format: {ed}")
                else:
                    try:
                        datetime.strptime(ed, "%Y-%m-%d")
                    except ValueError:
                        errors.append(f"Position '{ticker}' invalid entry_date: {ed}")

    snapshot_date = None
    equity_curve = snapshot.get("equity_curve")
    daily_signals = snapshot.get("daily_signals")

    if isinstance(equity_curve, list) and len(equity_curve) > 0:
        last_eq = equity_curve[-1]
        if isinstance(last_eq, dict) and "date" in last_eq and last_eq["date"]:
            snapshot_date = str(last_eq["date"])

    if snapshot_date is None:
        if isinstance(daily_signals, list) and len(daily_signals) > 0:
            last_sig = daily_signals[-1]
            if isinstance(last_sig, dict) and "date" in last_sig and last_sig["date"]:
                snapshot_date = str(last_sig["date"])
                warnings.append("equity_curve is empty or missing date; fallback snapshot_date to latest daily_signal date")

    if snapshot_date is None:
        errors.append("Could not determine snapshot_date from equity_curve or daily_signals")

    is_valid = len(errors) == 0
    return is_valid, snapshot_date, errors, warnings
