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
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Literal

import exchange_calendars as xcals
import pandas as pd
import yfinance as yf

try:
    from strategy.sector_flow import classify_sector, SECTOR_MAP
except ImportError:
    SECTOR_MAP = {}
    def classify_sector(ticker: str) -> str:
        return "traditional"

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


# =====================================================================
# 市場資料與指標計算
# =====================================================================

def compute_atr20(ticker: str, market_data: MarketData) -> pd.Series:
    """
    計算指定股票的 20 日 ATR (True Range 20 日 Simple Moving Average)。
    TR[t] = max(High[t] - Low[t], abs(High[t] - Close[t-1]), abs(Low[t] - Close[t-1]))
    ATR20[t] = SMA(TR[t-19:t])
    """
    if (
        ticker not in market_data.close.columns
        or ticker not in market_data.high.columns
        or ticker not in market_data.low.columns
    ):
        return pd.Series(dtype=float)

    close = market_data.close[ticker].dropna()
    high = market_data.high[ticker].dropna()
    low = market_data.low[ticker].dropna()

    common_idx = close.index.intersection(high.index).intersection(low.index).sort_values()
    if len(common_idx) < 21:
        return pd.Series(dtype=float)

    c = close.loc[common_idx]
    h = high.loc[common_idx]
    l = low.loc[common_idx]

    prev_c = c.shift(1)
    tr1 = h - l
    tr2 = (h - prev_c).abs()
    tr3 = (l - prev_c).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr20 = tr.rolling(20).mean()
    return atr20


def validate_tp_sl(
    positions: dict[str, dict[str, Any]],
    market_data: MarketData,
    calendar: Any = None,
) -> CheckResult:
    """
    驗證持倉 TP/SL 是否符合進場前一 session ATR20 的 4x / 3x 計算。
    """
    if calendar is None:
        calendar = xcals.get_calendar("XTAI")

    total = len(positions)
    if total == 0:
        return CheckResult(
            rule_id="tp_sl",
            title="TP/SL 價格",
            severity="PASS",
            summary="0 部位",
            details=[],
            metrics={"checked": 0, "violations": 0, "unknown": 0},
        )

    passed = 0
    violations = 0
    unknown = 0
    details: list[str] = []

    for ticker, pos in positions.items():
        entry = float(pos.get("entry", 0))
        stored_tp = float(pos.get("tp", 0))
        stored_sl = float(pos.get("sl", 0))
        entry_date_str = str(pos.get("entry_date", "")).strip()

        if not (0 < stored_sl < entry < stored_tp):
            violations += 1
            details.append(
                f"{ticker} sanity check failed: stored values sl={stored_sl:.2f}, entry={entry:.2f}, tp={stored_tp:.2f} 不符合 0 < SL < Entry < TP"
            )
            continue

        try:
            entry_ts = pd.Timestamp(entry_date_str)
            if calendar.is_session(entry_ts):
                entry_session = entry_ts
            else:
                entry_session = calendar.session_offset(entry_ts, 0)
            atr_asof = calendar.previous_session(entry_session)
        except Exception as e:
            unknown += 1
            details.append(f"{ticker} 交易日曆解析失敗 ({entry_date_str}): {e}")
            continue

        atr_series = compute_atr20(ticker, market_data)
        if atr_series.empty:
            unknown += 1
            details.append(f"{ticker} 缺市場 OHLC 資料，無法驗證 TP/SL")
            continue

        atr_asof_date = atr_asof.strftime("%Y-%m-%d")
        atr_val = None

        for idx_val, val in atr_series.items():
            dt_str = idx_val.strftime("%Y-%m-%d") if hasattr(idx_val, "strftime") else str(idx_val)[:10]
            if dt_str == atr_asof_date and pd.notna(val):
                atr_val = float(val)
                break

        if atr_val is None:
            unknown += 1
            details.append(f"{ticker} 截至 {atr_asof_date} 的 20 根日 K 資料不足")
            continue

        expected_tp = entry + 4.0 * atr_val
        expected_sl = entry - 3.0 * atr_val
        tol_tp = max(0.01, expected_tp * 0.001)
        tol_sl = max(0.01, expected_sl * 0.001)

        diff_tp = abs(stored_tp - expected_tp)
        diff_sl = abs(stored_sl - expected_sl)

        if diff_tp > tol_tp or diff_sl > tol_sl:
            violations += 1
            if diff_tp > tol_tp:
                details.append(
                    f"{ticker} TP={stored_tp:.2f}，預期 {expected_tp:.2f}（entry={entry:.2f}, ATR20={atr_val:.4f}, Δ={diff_tp:.2f}）"
                )
            if diff_sl > tol_sl:
                details.append(
                    f"{ticker} SL={stored_sl:.2f}，預期 {expected_sl:.2f}（entry={entry:.2f}, ATR20={atr_val:.4f}, Δ={diff_sl:.2f}）"
                )
        else:
            passed += 1

    if violations > 0:
        severity: Literal["PASS", "WARNING", "CRITICAL"] = "CRITICAL"
        summary = f"{violations}/{total} 部位不符"
    elif unknown > 0:
        severity = "WARNING"
        summary = f"{unknown}/{total} 部位資料不足"
    else:
        severity = "PASS"
        summary = f"{passed}/{total} 符合 ATR 4×/3×"

    return CheckResult(
        rule_id="tp_sl",
        title="TP/SL 價格",
        severity=severity,
        summary=summary,
        details=details,
        metrics={"checked": total, "passed": passed, "violations": violations, "unknown": unknown},
    )


def validate_time_rule(
    positions: dict[str, dict[str, Any]],
    snapshot_date: str,
    calendar: Any = None,
) -> CheckResult:
    """
    驗證持倉時間規則（最長持倉 20 天）。
    """
    if calendar is None:
        calendar = xcals.get_calendar("XTAI")

    total = len(positions)
    if total == 0:
        return CheckResult(
            rule_id="time_rule",
            title="20 天 TIME rule",
            severity="PASS",
            summary="最長持倉 0 天",
            details=[],
            metrics={"max_holding_days": 0, "positions_checked": 0, "violations": 0},
        )

    max_holding_days = 0
    violations = 0
    mismatches = 0
    schema_errors = 0
    details: list[str] = []

    for ticker, pos in positions.items():
        entry_date_str = str(pos.get("entry_date", "")).strip()
        day_count = pos.get("day_count")

        if not isinstance(day_count, int) or day_count < 0 or entry_date_str > snapshot_date:
            schema_errors += 1
            details.append(
                f"{ticker} entry_date ({entry_date_str}) > snapshot_date ({snapshot_date}) 或無效 day_count ({day_count})"
            )
            continue

        if entry_date_str == snapshot_date:
            calendar_days = 0
        else:
            try:
                all_sessions = calendar.sessions_in_range(entry_date_str, snapshot_date)
                cal_sessions = [s for s in all_sessions if s.strftime("%Y-%m-%d") != entry_date_str]
                calendar_days = len(cal_sessions)
            except Exception as e:
                details.append(f"{ticker} 計算日曆天數失敗: {e}")
                calendar_days = day_count

        effective_days = max(day_count, calendar_days)
        if effective_days > max_holding_days:
            max_holding_days = effective_days

        if day_count != calendar_days:
            mismatches += 1
            details.append(
                f"{ticker} day_count={day_count} 與日曆天數 {calendar_days} 不一致"
            )

        if effective_days > 20:
            violations += 1
            details.append(f"{ticker} 持倉 {effective_days} 天（超過上限 20 天）")

    if schema_errors > 0 or violations > 0:
        severity: Literal["PASS", "WARNING", "CRITICAL"] = "CRITICAL"
        if violations > 0:
            summary = f"最長持倉 {max_holding_days} 天（超出上限 20 天）"
        else:
            summary = f"發現 {schema_errors} 個部位日期異常"
    elif mismatches > 0:
        severity = "WARNING"
        summary = f"最長持倉 {max_holding_days} 天（存在 day_count 與日曆不一致）"
    else:
        severity = "PASS"
        summary = f"最長持倉 {max_holding_days} 天"

    return CheckResult(
        rule_id="time_rule",
        title="20 天 TIME rule",
        severity=severity,
        summary=summary,
        details=details,
        metrics={
            "max_holding_days": max_holding_days,
            "positions_checked": total,
            "violations": violations,
            "mismatches": mismatches,
            "schema_errors": schema_errors,
        },
    )


def regime_cap(above_60: bool, above_20: bool) -> float:
    """
    計算四段式市場 regime 曝險上限：
    - close > MA60 且 close > MA20 -> 100% (1.0)
    - close > MA60 且 close <= MA20 -> 70% (0.7)
    - close <= MA60 且 close > MA20 -> 40% (0.4)
    - close <= MA60 且 close <= MA20 -> 10% (0.1)
    """
    if above_60:
        return 1.0 if above_20 else 0.7
    else:
        return 0.4 if above_20 else 0.1


def validate_regime_exposure(
    snapshot: dict[str, Any],
    market_data: MarketData,
    snapshot_date: str,
) -> CheckResult:
    """
    驗證市場 regime 曝險比例是否符合 0050 MA20/MA60 四段式上限。
    """
    capital = float(snapshot.get("capital", 0))
    positions = snapshot.get("positions", {})
    details: list[str] = []

    # 1. 取得 0050 系列
    col_0050 = "0050" if "0050" in market_data.close.columns else ("0050.TW" if "0050.TW" in market_data.close.columns else None)
    if col_0050 is None:
        details.append("0050 不在市場資料中，無法判定市場 regime")
        return CheckResult(
            rule_id="regime_exposure",
            title="Regime 曝險",
            severity="WARNING",
            summary="0050 或部位缺收盤價，無法完整判定",
            details=details,
            metrics={"indeterminate": True},
        )

    close_0050_series = market_data.close[col_0050].dropna()
    # 截取到 snapshot_date
    close_0050_filtered = close_0050_series[
        close_0050_series.index.map(lambda x: (x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) <= snapshot_date)
    ]

    if len(close_0050_filtered) < 60:
        details.append(f"0050 截至 {snapshot_date} 僅有 {len(close_0050_filtered)} 根資料（需 >= 60 根）")
        return CheckResult(
            rule_id="regime_exposure",
            title="Regime 曝險",
            severity="WARNING",
            summary="0050 或部位缺收盤價，無法完整判定",
            details=details,
            metrics={"indeterminate": True, "bars_0050": len(close_0050_filtered)},
        )

    ma20_0050 = close_0050_filtered.rolling(20).mean().iloc[-1]
    ma60_0050 = close_0050_filtered.rolling(60).mean().iloc[-1]
    last_0050_close = close_0050_filtered.iloc[-1]

    above_60 = bool(last_0050_close > ma60_0050)
    above_20 = bool(last_0050_close > ma20_0050)
    allowed_cap = regime_cap(above_60, above_20)

    # 2. 計算各部位市值
    missing_prices = []
    total_position_val = 0.0

    for ticker, pos in positions.items():
        shares = float(pos.get("shares", 0))
        t_col = ticker if ticker in market_data.close.columns else (f"{ticker}.TW" if f"{ticker}.TW" in market_data.close.columns else None)
        if t_col is None:
            missing_prices.append(ticker)
            continue
        c_series = market_data.close[t_col].dropna()
        c_filtered = c_series[
            c_series.index.map(lambda x: (x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) == snapshot_date)
        ]
        if c_filtered.empty or pd.isna(c_filtered.iloc[-1]):
            missing_prices.append(ticker)
            continue
        price = float(c_filtered.iloc[-1])
        total_position_val += price * shares

    if missing_prices:
        details.append(f"部位 {', '.join(missing_prices)} 缺 {snapshot_date} 收盤價")
        details.append(f"0050 close={last_0050_close:.2f}, MA20={ma20_0050:.2f}, MA60={ma60_0050:.2f}, cap={allowed_cap*100:.1f}%")
        return CheckResult(
            rule_id="regime_exposure",
            title="Regime 曝險",
            severity="WARNING",
            summary="0050 或部位缺收盤價，無法完整判定",
            details=details,
            metrics={"indeterminate": True, "missing_prices": missing_prices, "regime_cap": allowed_cap},
        )

    total_equity = capital + total_position_val
    if total_equity <= 0:
        details.append(f"總權益異常: equity={total_equity:.2f} <= 0 (capital={capital:.2f}, pos_val={total_position_val:.2f})")
        return CheckResult(
            rule_id="regime_exposure",
            title="Regime 曝險",
            severity="CRITICAL",
            summary=f"總權益異常 ({total_equity:.2f} <= 0)",
            details=details,
            metrics={"total_equity": total_equity},
        )

    exposure = total_position_val / total_equity
    tolerance = 0.005  # 0.5% tolerance

    details.append(f"0050 close={last_0050_close:.2f}, MA20={ma20_0050:.2f}, MA60={ma60_0050:.2f}")
    details.append(
        f"position_val={total_position_val:.2f}, capital={capital:.2f}, total_equity={total_equity:.2f}, exposure={exposure*100:.1f}%, cap={allowed_cap*100:.1f}%"
    )

    if exposure > allowed_cap + tolerance:
        severity: Literal["PASS", "WARNING", "CRITICAL"] = "CRITICAL"
        summary = f"實際 {exposure*100:.1f}% > 上限 {allowed_cap*100:.1f}%"
    else:
        severity = "PASS"
        summary = f"實際 {exposure*100:.1f}% <= 上限 {allowed_cap*100:.1f}%"

    return CheckResult(
        rule_id="regime_exposure",
        title="Regime 曝險",
        severity=severity,
        summary=summary,
        details=details,
        metrics={
            "exposure": exposure,
            "regime_cap": allowed_cap,
            "total_position_value": total_position_val,
            "total_equity": total_equity,
            "capital": capital,
            "0050_close": last_0050_close,
            "0050_ma20": ma20_0050,
            "0050_ma60": ma60_0050,
        },
    )


def validate_sector_concentration(
    positions: dict[str, dict[str, Any]],
    prices: dict[str, float] | MarketData | None = None,
    snapshot_date: str | None = None,
) -> CheckResult:
    """
    驗證各板塊持倉市值佔比是否不超過 75% (+0.5% 容差)。
    """
    total_positions = len(positions)
    if total_positions == 0:
        return CheckResult(
            rule_id="sector_concentration",
            title="Sector concentration",
            severity="PASS",
            summary="最高 0.0% <= 75.0%",
            details=[],
            metrics={"max_sector_pct": 0.0, "sectors": {}},
        )

    # 提取價格
    resolved_prices: dict[str, float] = {}
    missing_prices: list[str] = []

    for ticker in positions.keys():
        p = None
        if isinstance(prices, dict):
            p = prices.get(ticker)
        elif isinstance(prices, MarketData) and snapshot_date is not None:
            t_col = ticker if ticker in prices.close.columns else (f"{ticker}.TW" if f"{ticker}.TW" in prices.close.columns else None)
            if t_col is not None:
                c_series = prices.close[t_col].dropna()
                c_matched = c_series[
                    c_series.index.map(lambda x: (x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) == snapshot_date)
                ]
                if not c_matched.empty and pd.notna(c_matched.iloc[-1]):
                    p = float(c_matched.iloc[-1])

        if p is None or pd.isna(p) or p <= 0:
            missing_prices.append(ticker)
        else:
            resolved_prices[ticker] = float(p)

    if missing_prices:
        return CheckResult(
            rule_id="sector_concentration",
            title="Sector concentration",
            severity="WARNING",
            summary=f"{', '.join(missing_prices)} 缺收盤價，無法完整判定",
            details=[f"{t} 缺收盤價，無法計算板塊市值" for t in missing_prices],
            metrics={"missing_prices": missing_prices},
        )

    # 依板塊計算市值
    sector_values: dict[str, float] = {}
    total_value = 0.0

    for ticker, pos in positions.items():
        shares = float(pos.get("shares", 0))
        price = resolved_prices[ticker]
        val = price * shares
        sec = classify_sector(ticker)
        sector_values[sec] = sector_values.get(sec, 0.0) + val
        total_value += val

    if total_value <= 0:
        return CheckResult(
            rule_id="sector_concentration",
            title="Sector concentration",
            severity="PASS",
            summary="最高 0.0% <= 75.0%",
            details=[],
            metrics={"max_sector_pct": 0.0, "sectors": {}},
        )

    max_sec, max_val = max(sector_values.items(), key=lambda item: item[1])
    max_pct = max_val / total_value
    sec_label = SECTOR_MAP.get(max_sec, {}).get("label", max_sec)

    tolerance = 0.005  # 0.5% tolerance
    if max_pct > 0.75 + tolerance:
        severity: Literal["PASS", "WARNING", "CRITICAL"] = "CRITICAL"
        summary = f"最高{sec_label} {max_pct*100:.1f}% > 75.0%"
    else:
        severity = "PASS"
        summary = f"最高{sec_label} {max_pct*100:.1f}% <= 75.0%"

    details = [
        f"{SECTOR_MAP.get(s, {}).get('label', s)}: {v:.2f} ({(v/total_value)*100:.1f}%)"
        for s, v in sorted(sector_values.items(), key=lambda x: x[1], reverse=True)
    ]

    return CheckResult(
        rule_id="sector_concentration",
        title="Sector concentration",
        severity=severity,
        summary=summary,
        details=details,
        metrics={
            "max_sector": max_sec,
            "max_sector_label": sec_label,
            "max_sector_pct": max_pct,
            "sectors": {s: v / total_value for s, v in sector_values.items()},
        },
    )


def validate_position_count(positions: dict[str, dict[str, Any]], limit: int = 7) -> CheckResult:
    """
    驗證目前持倉總數量是否在上限（預設 7 檔）之內。
    """
    count = len(positions)
    if count <= limit:
        severity: Literal["PASS", "WARNING", "CRITICAL"] = "PASS"
        summary = f"{count} / {limit}"
        details: list[str] = []
    else:
        severity = "CRITICAL"
        summary = f"{count} / {limit}（超出上限 {limit}）"
        details = [f"持倉數量 {count} 超過上限 {limit}，目前持倉: {', '.join(positions.keys())}"]

    return CheckResult(
        rule_id="position_count",
        title="持倉數量",
        severity=severity,
        summary=summary,
        details=details,
        metrics={"position_count": count, "limit": limit},
    )
