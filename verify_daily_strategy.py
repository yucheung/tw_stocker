#!/usr/bin/env python3
"""
tw_stocker 每日策略驗證腳本 (verify_daily_strategy.py)

在台股交易日收盤後，以 paper_equity.json 與 yfinance 市場資料驗證 v8.5 的六項策略規則，
將 Telegram 可直接推播的文字寫到 stdout，並把結構化結果原子化追加到 verify_log.json。
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import sys
import tempfile
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

try:
    from strategy.universe import get_twse_common_stocks
except ImportError:
    def get_twse_common_stocks(verbose: bool = True, **kwargs: Any) -> list[dict[str, Any] | str]:
        # Fallback to local data/twse_listed_common_stocks.json if exists
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "twse_listed_common_stocks.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d.get("stocks", [])
        return []

DEFAULT_PAPER_URL = "https://raw.githubusercontent.com/yucheung/tw_stocker/main/paper_equity.json"
DEFAULT_LOCAL_PAPER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "paper_equity.json"
)
DEFAULT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "verify_log.json"
)
TAIPEI_TZ = timezone(timedelta(hours=8))

SEVERITY_EMOJI: dict[str, str] = {
    "PASS": "✅ PASS",
    "WARNING": "⚠️ WARNING",
    "CRITICAL": "🚨 CRITICAL",
}


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


@dataclass
class VerifyConfig:
    paper_url: str = DEFAULT_PAPER_URL
    paper_local_path: str = DEFAULT_LOCAL_PAPER_PATH
    log_path: str = DEFAULT_LOG_PATH
    auto_adjust: bool = False


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
    t_col = ticker if ticker in market_data.close.columns else (f"{ticker}.TW" if f"{ticker}.TW" in market_data.close.columns else None)
    if t_col is None or t_col not in market_data.high.columns or t_col not in market_data.low.columns:
        return pd.Series(dtype=float)

    close = market_data.close[t_col].dropna()
    high = market_data.high[t_col].dropna()
    low = market_data.low[t_col].dropna()

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

        if effective_days >= 20:
            violations += 1
            details.append(f"{ticker} 持倉 {effective_days} 天（達/超過上限 20 天應已出場）")

    if schema_errors > 0 or violations > 0:
        severity: Literal["PASS", "WARNING", "CRITICAL"] = "CRITICAL"
        if violations > 0:
            summary = f"最長持倉 {max_holding_days} 天（達到或超出上限 20 天）"
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


# =====================================================================
# Top-7 信號一致性驗證
# =====================================================================

def compute_v85_scores(
    market_data: MarketData,
    top_n: int = 60,
    lookback: int = 20,
    ma_period: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    精確重算 v8.5 baseline AI 綜合評分：
    turnover20 = SMA20(close * volume)
    universe = snapshot_date turnover20 Top-60
    momentum20 = close[t] / close[t-20]
    trend_bias = close[t] / SMA60(close)[t]
    score = 3 * percentile_rank(momentum20) + 1 * percentile_rank(trend_bias)
    """
    close_df = market_data.close
    vol_df = market_data.volume

    if close_df.empty or vol_df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    turnover = (close_df * vol_df).rolling(lookback).mean()
    universe_mask = (turnover.rank(axis=1, ascending=False) <= top_n) & close_df.notna() & (close_df > 0)

    mom_20 = close_df / close_df.shift(20)
    ma60 = close_df.rolling(ma_period).mean()
    trend_bias = close_df / ma60

    rank_mom = mom_20.where(universe_mask).rank(axis=1, pct=True)
    rank_trend = trend_bias.where(universe_mask).rank(axis=1, pct=True)

    scores = rank_mom * 3 + rank_trend * 1
    return scores, ma60, universe_mask


def validate_signal_consistency(
    snapshot: dict[str, Any],
    market_data: MarketData,
    snapshot_date: str,
    calendar: Any = None,
) -> CheckResult:
    """
    驗證 snapshot_date 的 Top-7 訊號一致性：
    1. Score >= 2.0 (基於 Top-60 流動性母體)
    2. 股價 > 60MA
    3. 進場執行模型（signal_close_limit_next_open_v1）：
       limit = 訊號日 (snapshot_date) 收盤價；
       next_open <= limit -> 預期 FILLED；next_open > limit -> 預期於 09:30 CANCELLED_OPEN_ABOVE_LIMIT；
       缺 next_open -> WARNING / indeterminate。
       daily_signals 本身不等於成交；若 order_events 或 positions 能證明實際結果與預期矛盾
       （尤其 entry > limit 的不可能成交），才判定為 CRITICAL。
    4. 數量 <= 7、無 duplicate、排名與合格候選一致
    """
    if calendar is None:
        calendar = xcals.get_calendar("XTAI")

    daily_signals = snapshot.get("daily_signals", [])
    matching_signals = [s for s in daily_signals if isinstance(s, dict) and s.get("date") == snapshot_date]

    if not matching_signals:
        return CheckResult(
            rule_id="signal_consistency",
            title="Top-7 信號一致性",
            severity="WARNING",
            summary="當日無信號資料（snapshot 中 daily_signals 無符合日期）",
            details=["snapshot_date 有持倉但無對應 daily_signals，可能是資料缺失"],
            metrics={"signal_count": 0, "indeterminate": True},
        )

    warnings_list: list[str] = []
    if len(matching_signals) > 1:
        warnings_list.append(f"存在重複日期的 daily_signals (count={len(matching_signals)})，採用最後一筆")

    signal_entry = matching_signals[-1]
    raw_tickers = signal_entry.get("tickers", [])
    if not isinstance(raw_tickers, list):
        return CheckResult(
            rule_id="signal_consistency",
            title="Top-7 信號一致性",
            severity="CRITICAL",
            summary="當日信號 tickers 型別錯誤（schema 異常）",
            details=warnings_list + [f"tickers 欄位型別為 {type(raw_tickers).__name__}，預期 list"],
            metrics={"signal_count": 0, "schema_error": True},
        )
    tickers = [str(t).replace(".TW", "").replace(".TWO", "") for t in raw_tickers]

    order_events = snapshot.get("order_events", [])
    if not isinstance(order_events, list):
        order_events = []
    all_positions = snapshot.get("positions", {})
    if not isinstance(all_positions, dict):
        all_positions = {}

    violations: list[str] = []
    unknown_reasons: list[str] = []
    missing_open_tickers: list[str] = []
    expected_fills: list[str] = []
    expected_cancellations: list[str] = []
    passed_tickers: list[str] = []

    # 檢查數量與重複
    count_violation = len(tickers) > 7
    dup_violation = len(tickers) != len(set(tickers))

    if count_violation:
        violations.append(f"信號數量 {len(tickers)} 超過上限 7: {tickers}")
    if dup_violation:
        violations.append(f"信號包含重複代號: {tickers}")

    if not tickers:
        return CheckResult(
            rule_id="signal_consistency",
            title="Top-7 信號一致性",
            severity="CRITICAL",
            summary="當日信號存在但 tickers 為空（schema 異常）",
            details=warnings_list + ["signal entry 存在但 tickers 陣列為空或缺失"],
            metrics={"signal_count": 0, "schema_error": True},
        )

    # 計算全市場分數
    scores_df, ma60_df, universe_mask = compute_v85_scores(market_data, top_n=60)

    snap_idx = None
    if not scores_df.empty:
        for idx in scores_df.index:
            dt_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            if dt_str == snapshot_date:
                snap_idx = idx
                break

    universe_coverage_insufficient = False
    active_universe_count = 0
    download_coverage = 0.0

    if snap_idx is None or len(market_data.close.columns) < 54:
        universe_coverage_insufficient = True
    else:
        active_universe_count = int(universe_mask.loc[snap_idx].sum()) if not universe_mask.empty else 0
        valid_close_count = int(market_data.close.loc[snap_idx].notna().sum())
        total_cols = len(market_data.close.columns)
        download_coverage = valid_close_count / total_cols if total_cols > 0 else 0.0
        if active_universe_count < 48 or download_coverage < 0.70:
            universe_coverage_insufficient = True

    if universe_coverage_insufficient:
        unknown_reasons.append(
            f"全市場流動性母體不足 (有效 {active_universe_count} 檔 < 48 或 coverage {download_coverage*100:.1f}% < 70%)，無法完整重算 Top-60 score 排名"
        )

    try:
        snap_ts = pd.Timestamp(snapshot_date)
        if calendar.is_session(snap_ts):
            next_session = calendar.next_session(snap_ts)
        else:
            next_session = calendar.session_offset(snap_ts, 1)
        next_session_str = next_session.strftime("%Y-%m-%d")
    except Exception:
        next_session = None
        next_session_str = "下一交易日"

    # Step 1: 若母體充足，自完整 Top-60 建立合格候選池並排序
    eligible_candidates: list[tuple[str, float, float]] = []
    expected_top_tickers: list[str] = []

    if not universe_coverage_insufficient and snap_idx is not None:
        for col in universe_mask.columns:
            if universe_mask.loc[snap_idx, col]:
                sc = scores_df.loc[snap_idx, col] if col in scores_df.columns else None
                if sc is not None and pd.notna(sc) and float(sc) >= 2.0:
                    c_series = market_data.close[col].dropna()
                    c_filtered = c_series[
                        c_series.index.map(lambda x: (x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) <= snapshot_date)
                    ]
                    if len(c_filtered) >= 60:
                        c_val = float(c_filtered.iloc[-1])
                        ma60_val = float(c_filtered.rolling(60).mean().iloc[-1])
                        if c_val > ma60_val:
                            ticker_clean = str(col).replace(".TW", "").replace(".TWO", "")
                            eligible_candidates.append((ticker_clean, float(sc), c_val))

        # 排序：score 降冪，score 相同時依代號排序以保證確定性
        eligible_candidates.sort(key=lambda x: (-x[1], x[0]))
        expected_top_tickers = [c[0] for c in eligible_candidates[:7]]

        # 比對排名與選股清單
        expected_set = set(expected_top_tickers)
        actual_set = set(tickers)

        omitted = [t for t in expected_top_tickers if t not in actual_set]
        unwarranted = [t for t in tickers if t not in expected_set]

        if omitted or unwarranted:
            violations.append(
                f"選股名單不符 Top-7 排名：選入未入選/低分股 {unwarranted}，遺漏高分合格股 {omitted}（預期 Top-{len(expected_top_tickers)}: {expected_top_tickers}，實際: {tickers}）"
            )
        elif tickers != expected_top_tickers:
            violations.append(
                f"選股順序不符排名：預期順序 {expected_top_tickers}，實際順序 {tickers}"
            )

    # Step 2: 驗證 actual tickers 各項指標
    for ticker in tickers:
        ticker_passed = True
        t_col = ticker if ticker in market_data.close.columns else (f"{ticker}.TW" if f"{ticker}.TW" in market_data.close.columns else (f"{ticker}.TWO" if f"{ticker}.TWO" in market_data.close.columns else None))

        # 1. 驗證 Score
        if universe_coverage_insufficient:
            ticker_passed = False
        else:
            in_universe = False
            sc = None
            for probe_col in [ticker, f"{ticker}.TW", f"{ticker}.TWO"]:
                if probe_col in universe_mask.columns and bool(universe_mask.loc[snap_idx, probe_col]):
                    in_universe = True
                if probe_col in scores_df.columns:
                    sc = scores_df.loc[snap_idx, probe_col]
                    if pd.notna(sc):
                        break

            if not in_universe:
                violations.append(f"{ticker} 不在當日 Top-60 流動性池")
                ticker_passed = False
            elif sc is None or pd.isna(sc) or float(sc) < 2.0:
                sc_str = f"{sc:.4f}" if sc is not None and pd.notna(sc) else "None"
                violations.append(f"{ticker} score={sc_str} 未達門檻 2.0")
                ticker_passed = False

        # 2. 驗證 MA60
        if t_col is None:
            unknown_reasons.append(f"{ticker} 缺收盤價，無法判定 MA60")
            ticker_passed = False
        else:
            c_series = market_data.close[t_col].dropna()
            c_filtered = c_series[
                c_series.index.map(lambda x: (x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) <= snapshot_date)
            ]
            if len(c_filtered) < 60:
                unknown_reasons.append(f"{ticker} 收盤價資料少於 60 根，無法計算 MA60")
                ticker_passed = False
            else:
                c_val = float(c_filtered.iloc[-1])
                ma60_val = float(c_filtered.rolling(60).mean().iloc[-1])
                if c_val <= ma60_val:
                    violations.append(f"{ticker} 收盤價 {c_val:.2f} <= 60MA {ma60_val:.2f}")
                    ticker_passed = False

        # 3. 驗證進場執行模型（signal_close_limit_next_open_v1）
        # limit = 訊號日 (snapshot_date) 收盤價；next_open <= limit -> 預期 FILLED，
        # next_open > limit -> 預期於 09:30 CANCELLED_OPEN_ABOVE_LIMIT。
        next_open = None
        t_open_col = ticker if ticker in market_data.open.columns else (f"{ticker}.TW" if f"{ticker}.TW" in market_data.open.columns else (f"{ticker}.TWO" if f"{ticker}.TWO" in market_data.open.columns else None))
        if t_open_col is not None and next_session is not None:
            o_series = market_data.open[t_open_col].dropna()
            for idx_val, val in o_series.items():
                dt_str = idx_val.strftime("%Y-%m-%d") if hasattr(idx_val, "strftime") else str(idx_val)[:10]
                if dt_str == next_session_str and pd.notna(val):
                    next_open = float(val)
                    break

        limit_price = None
        if t_col is not None:
            c_series = market_data.close[t_col].dropna()
            for idx_val, val in c_series.items():
                dt_str = idx_val.strftime("%Y-%m-%d") if hasattr(idx_val, "strftime") else str(idx_val)[:10]
                if dt_str == snapshot_date and pd.notna(val):
                    limit_price = float(val)
                    break

        if next_open is None:
            missing_open_tickers.append(ticker)
        elif limit_price is None:
            unknown_reasons.append(f"{ticker} 缺 {snapshot_date} 收盤價，無法判定限價")
        else:
            tolerance = max(0.01, limit_price * 0.001)
            if next_open <= limit_price:
                expected_status = "FILLED"
                expected_fills.append(ticker)
            else:
                expected_status = "CANCELLED_OPEN_ABOVE_LIMIT"
                expected_cancellations.append(ticker)

            # daily_signals 本身不等於成交；只有 order_events/positions 能證明實際結果，
            # 與預期矛盾（尤其 entry > limit 的不可能成交）才判定為 CRITICAL。
            matched_events = [
                e for e in order_events
                if isinstance(e, dict) and e.get("ticker") == ticker and e.get("signal_date") == snapshot_date
            ]
            event = matched_events[-1] if matched_events else None
            pos = all_positions.get(ticker)
            pos_matches_execution = (
                isinstance(pos, dict) and str(pos.get("entry_date", "")).strip() == next_session_str
            )

            if event is not None:
                ev_status = event.get("status")
                if ev_status == "FILLED" and expected_status == "CANCELLED_OPEN_ABOVE_LIMIT":
                    violations.append(
                        f"{ticker} 預期應於 {next_session_str} 09:30 撤單（open {next_open:.2f} > limit {limit_price:.2f}），"
                        f"但 order_events 記為 FILLED（entry > limit，不可能成交）"
                    )
                    ticker_passed = False
                ev_fill = event.get("fill_price")
                if ev_fill is not None:
                    try:
                        ev_fill_f = float(ev_fill)
                    except (TypeError, ValueError):
                        ev_fill_f = None
                    if ev_fill_f is not None and ev_fill_f > limit_price + tolerance:
                        violations.append(
                            f"{ticker} order_events fill_price={ev_fill_f:.2f} > limit={limit_price:.2f}（entry > limit，不可能成交）"
                        )
                        ticker_passed = False
            elif pos_matches_execution:
                try:
                    entry_f = float(pos.get("entry"))
                except (TypeError, ValueError):
                    entry_f = None
                if entry_f is not None:
                    if entry_f > limit_price + tolerance:
                        violations.append(
                            f"{ticker} 部位 entry={entry_f:.2f} > limit={limit_price:.2f}（entry > limit，不可能成交）"
                        )
                        ticker_passed = False
                    elif expected_status == "CANCELLED_OPEN_ABOVE_LIMIT":
                        violations.append(
                            f"{ticker} 預期應於 {next_session_str} 09:30 撤單（open {next_open:.2f} > limit {limit_price:.2f}），"
                            f"但已建倉 entry={entry_f:.2f}"
                        )
                        ticker_passed = False
                    elif abs(entry_f - next_open) > tolerance:
                        violations.append(
                            f"{ticker} 部位 entry={entry_f:.2f} 與次日開盤 {next_open:.2f} 不符"
                        )
                        ticker_passed = False

        if ticker_passed:
            passed_tickers.append(ticker)

    all_details = warnings_list + violations + unknown_reasons
    if missing_open_tickers:
        all_details.append(
            f"開盤成交結果 PENDING：{', '.join(missing_open_tickers)} 待 {next_session_str} 開盤驗證"
        )

    if count_violation:
        severity: Literal["PASS", "WARNING", "CRITICAL"] = "CRITICAL"
        summary = f"信號數量 {len(tickers)} 超過上限 7"
    elif dup_violation:
        severity = "CRITICAL"
        summary = "信號包含重複代號"
    elif violations:
        severity = "CRITICAL"
        summary = f"訊號不符策略規則（{len(violations)} 項違規）"
    elif universe_coverage_insufficient:
        severity = "WARNING"
        if missing_open_tickers:
            summary = f"母體資料不足 (< 48 檔或 coverage < 70%)；開盤成交結果待 {next_session_str} 驗證"
        else:
            summary = "母體資料不足 (< 48 檔或 coverage < 70%)，無法完整判定 score/MA"
    elif unknown_reasons or missing_open_tickers:
        severity = "WARNING"
        if missing_open_tickers:
            summary = f"{len(passed_tickers)}/{len(tickers)} score/MA 通過；開盤成交結果待 {next_session_str} 驗證"
        else:
            summary = "部分資料不足，無法完整判定"
    else:
        severity = "PASS"
        summary = f"{len(passed_tickers)}/{len(tickers)} score 與 MA 合格；排名一致；限價成交結果已驗證"

    return CheckResult(
        rule_id="signal_consistency",
        title="Top-7 信號一致性",
        severity=severity,
        summary=summary,
        details=all_details,
        metrics={
            "total_signals": len(tickers),
            "passed_count": len(passed_tickers),
            "violations_count": len(violations),
            "expected_fills": expected_fills,
            "expected_cancellations": expected_cancellations,
            "missing_next_open_count": len(missing_open_tickers),
            "indeterminate": bool(missing_open_tickers),
            "universe_coverage_insufficient": universe_coverage_insufficient,
            "active_universe_count": active_universe_count,
            "download_coverage": download_coverage,
        },
    )


# =====================================================================
# 下載與資料整合
# =====================================================================

def fetch_market_data(
    tickers: list[str],
    start_date: str,
    end_date: str,
    fetcher: Callable[..., MarketData] | None = None,
) -> MarketData:
    """
    使用 yfinance 批次取得指定 ticker 集合的 OHLCV 日 K 資料。
    強制 auto_adjust=False, progress=False。
    """
    if fetcher is not None:
        return fetcher(tickers, start_date=start_date, end_date=end_date)

    if not tickers:
        empty = pd.DataFrame()
        return MarketData(close=empty, open=empty, high=empty, low=empty, volume=empty)

    tw_symbols = [f"{t}.TW" if not t.endswith((".TW", ".TWO")) else t for t in tickers]

    # 批次下載
    batch_size = 100
    all_dfs = []
    for i in range(0, len(tw_symbols), batch_size):
        batch = tw_symbols[i : i + batch_size]
        for retry in range(3):
            try:
                df = yf.download(
                    batch,
                    start=start_date,
                    end=end_date,
                    auto_adjust=False,
                    progress=False,
                )
                if not df.empty:
                    all_dfs.append(df)
                break
            except Exception:
                if retry < 2:
                    time.sleep(1.0 * (retry + 1))

    if not all_dfs:
        empty = pd.DataFrame()
        return MarketData(close=empty, open=empty, high=empty, low=empty, volume=empty)

    merged = all_dfs[0] if len(all_dfs) == 1 else pd.concat(all_dfs, axis=1)

    def _extract(field_name: str) -> pd.DataFrame:
        if merged.empty:
            return pd.DataFrame()
        if isinstance(merged.columns, pd.MultiIndex):
            try:
                ext = merged.xs(field_name, level=0, axis=1)
            except KeyError:
                return pd.DataFrame(index=merged.index)
        elif field_name in merged.columns:
            ext = merged[[field_name]]
        else:
            return pd.DataFrame(index=merged.index)
        ext = ext.copy()
        ext.columns = [str(c).replace(".TW", "").replace(".TWO", "") for c in ext.columns]
        if ext.columns.duplicated().any():
            ext = ext.T.groupby(level=0).first().T
        return ext

    close_df = _extract("Close")
    open_df = _extract("Open")
    high_df = _extract("High")
    low_df = _extract("Low")
    vol_df = _extract("Volume")

    # 檢查缺資料之 ticker，重試 .TWO
    missing = [t for t in tickers if t not in close_df.columns or close_df[t].dropna().empty]
    if missing:
        two_symbols = [f"{t}.TWO" for t in missing]
        for retry in range(3):
            try:
                two_df = yf.download(
                    two_symbols,
                    start=start_date,
                    end=end_date,
                    auto_adjust=False,
                    progress=False,
                )
                if not two_df.empty:
                    for f_name, target_df in [
                        ("Close", close_df),
                        ("Open", open_df),
                        ("High", high_df),
                        ("Low", low_df),
                        ("Volume", vol_df),
                    ]:
                        if isinstance(two_df.columns, pd.MultiIndex) and f_name in two_df.columns.levels[0]:
                            t_ext = two_df.xs(f_name, level=0, axis=1).copy()
                            t_ext.columns = [str(c).replace(".TWO", "") for c in t_ext.columns]
                            for col in t_ext.columns:
                                target_df[col] = t_ext[col]
                break
            except Exception:
                if retry < 2:
                    time.sleep(1.0 * (retry + 1))

    # Close 做 1 天 forward-fill
    close_df = close_df.ffill(limit=1)

    return MarketData(close=close_df, open=open_df, high=high_df, low=low_df, volume=vol_df)


# =====================================================================
# 報告渲染、Log 記錄與流程串接
# =====================================================================

def render_report(context: dict[str, Any], results: list[CheckResult]) -> str:
    """
    格式化產出 Telegram 可直接推播的純文字報告。
    """
    run_date = context.get("run_date", "")
    snapshot_date = context.get("snapshot_date", "")
    run_id = context.get("run_id", "")
    overall_status = context.get("overall_status", "PASS")
    paper_source = context.get("paper_source", "github_raw")

    crit_cnt = context.get("critical_count", 0)
    warn_cnt = context.get("warning_count", 0)
    pass_cnt = context.get("pass_count", 0)

    if overall_status == "PASS":
        summary_line = f"總結：✅ PASS｜{pass_cnt} pass"
    else:
        parts = []
        if crit_cnt:
            parts.append(f"{crit_cnt} critical")
        if warn_cnt:
            parts.append(f"{warn_cnt} warning")
        if pass_cnt:
            parts.append(f"{pass_cnt} pass")
        emoji_status = SEVERITY_EMOJI.get(overall_status, overall_status)
        summary_line = f"總結：{emoji_status}｜{' / '.join(parts)}"

    lines = [
        "📋 tw_stocker v8.5 每日策略驗證",
        f"日期：{run_date}（snapshot: {snapshot_date}）",
        summary_line,
        "",
    ]

    for r in results:
        emoji = SEVERITY_EMOJI.get(r.severity, r.severity)
        lines.append(f"{emoji} {r.title}｜{r.summary}")
        for d in r.details:
            lines.append(f"  {d}")

    lines.append("")
    source_desc = "GitHub raw" if paper_source == "github_raw" else "本地 paper_equity.json"
    lines.append(f"資料來源：{source_desc} + yfinance（未調整日 K）")
    lines.append(f"稽核 ID：{run_id}")

    return "\n".join(lines)


def append_verify_log(path: str, context: dict[str, Any], results: list[CheckResult]) -> None:
    """
    使用暫存檔 + os.replace 原子性追加驗證紀錄至 verify_log.json。
    包含跨程序 fcntl.flock 排他鎖、tempfile 暫存檔、以及 directory fsync。
    """
    log_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(log_dir, exist_ok=True)
    lock_path = f"{os.path.abspath(path)}.lock"

    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            data: dict[str, Any] = {"schema_version": 1, "runs": []}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            loaded = json.loads(content)
                            if isinstance(loaded, dict) and "runs" in loaded:
                                data = loaded
                            else:
                                raise ValueError("Invalid schema: missing 'runs' field")
                except Exception as e:
                    raise RuntimeError(f"Failed to read existing verify_log.json at {path}: {e}") from e

            run_entry = {
                "run_id": context.get("run_id"),
                "run_at": context.get("run_at"),
                "run_date": context.get("run_date"),
                "snapshot_date": context.get("snapshot_date"),
                "overall_status": context.get("overall_status"),
                "source": {
                    "paper": context.get("paper_source", "github_raw"),
                    "paper_url": context.get("paper_url", DEFAULT_PAPER_URL),
                    "market": "yfinance",
                    "auto_adjust": False,
                },
                "summary": {
                    "critical": context.get("critical_count", 0),
                    "warning": context.get("warning_count", 0),
                    "pass": context.get("pass_count", 0),
                },
                "checks": [
                    {
                        "rule_id": r.rule_id,
                        "title": r.title,
                        "severity": r.severity,
                        "summary": r.summary,
                        "details": r.details,
                        "metrics": r.metrics,
                    }
                    for r in results
                ],
            }

            data["runs"].append(run_entry)

            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    dir=log_dir,
                    delete=False,
                    encoding="utf-8",
                    prefix=".verify_log_",
                    suffix=".tmp",
                ) as tf:
                    temp_path = tf.name
                    json.dump(data, tf, indent=2, ensure_ascii=False)
                    tf.flush()
                    os.fsync(tf.fileno())

                os.replace(temp_path, path)
                temp_path = None

                # Directory fsync to persist directory entry metadata
                try:
                    dir_fd = os.open(log_dir, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
            except Exception as e:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                raise RuntimeError(f"Failed to write verify log atomically to {path}: {e}") from e
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def run_verification(
    config: VerifyConfig | None = None,
    now: datetime | None = None,
    fetcher: Callable[[str], str | bytes | dict] | None = None,
    market_fetcher: Callable[..., MarketData] | None = None,
    calendar: Any = None,
) -> str:
    """
    執行完整的每日策略驗證流程：
    1. 交易日 gate
    2. 載入 paper snapshot
    3. 下載市場資料
    4. 執行驗證器與 freshness/schema check
    5. 產出報告與寫入 log
    """
    if config is None:
        config = VerifyConfig()

    if now is None:
        now = datetime.now(TAIPEI_TZ)

    if calendar is None:
        calendar = xcals.get_calendar("XTAI")

    run_date = now.strftime("%Y-%m-%d")

    # 1. 交易日檢查（非交易日完全靜默）
    if not is_trading_day(now, calendar=calendar):
        return ""

    run_id = now.strftime("%Y%m%dT%H%M%S%z")
    run_at = now.isoformat()

    # 2. 載入 paper snapshot
    try:
        snapshot, paper_source, fetch_warnings = load_paper_snapshot(
            url=config.paper_url,
            local_path=config.paper_local_path,
            fetcher=fetcher,
        )
    except Exception as e:
        crit_check = CheckResult(
            rule_id="paper_snapshot",
            title="Paper Snapshot 載入",
            severity="CRITICAL",
            summary=f"無法載入或解析 Paper Snapshot: {e}",
            details=[str(e)],
        )
        context = {
            "run_id": run_id,
            "run_at": run_at,
            "run_date": run_date,
            "snapshot_date": run_date,
            "overall_status": "CRITICAL",
            "paper_source": "none",
            "paper_url": config.paper_url,
            "critical_count": 1,
            "warning_count": 0,
            "pass_count": 0,
        }
        report = render_report(context, [crit_check])
        append_verify_log(config.log_path, context, [crit_check])
        return report

    is_valid, snapshot_date, schema_errors, schema_warnings = validate_snapshot(snapshot)
    if snapshot_date is None:
        snapshot_date = run_date

    positions = snapshot.get("positions", {}) if isinstance(snapshot.get("positions"), dict) else {}
    daily_signals = snapshot.get("daily_signals", []) if isinstance(snapshot.get("daily_signals"), list) else []

    # 3. 確定需抓取的 tickers
    needed_tickers: set[str] = {"0050"}
    for t in positions.keys():
        needed_tickers.add(str(t))

    for s in daily_signals:
        if isinstance(s, dict) and s.get("date") == snapshot_date:
            for t in s.get("tickers", []):
                needed_tickers.add(str(t))

    # 加入 TWSE 全市場股票以供 Top-60 universe 重算 (傳 verbose=False 避免 stdout 污染)
    twse_stocks = get_twse_common_stocks(verbose=False)
    for stock in twse_stocks:
        code = stock.get("code") if isinstance(stock, dict) else str(stock)
        if code:
            needed_tickers.add(str(code))

    # 確定日期範圍：至少 90 個交易日
    try:
        sessions_back = calendar.sessions_in_range(
            (pd.Timestamp(snapshot_date) - pd.Timedelta(days=160)).strftime("%Y-%m-%d"),
            (pd.Timestamp(snapshot_date) + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
        )
        start_date = sessions_back[0].strftime("%Y-%m-%d")
        end_date = (pd.Timestamp(snapshot_date) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    except Exception:
        start_date = (pd.Timestamp(snapshot_date) - pd.Timedelta(days=160)).strftime("%Y-%m-%d")
        end_date = (pd.Timestamp(snapshot_date) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")

    # 4. 下載市場資料
    market_data = fetch_market_data(
        list(needed_tickers),
        start_date=start_date,
        end_date=end_date,
        fetcher=market_fetcher,
    )

    # 5. 執行 6 項驗證器 + snapshot health / freshness checks
    results: list[CheckResult] = []

    if schema_errors:
        results.append(
            CheckResult(
                rule_id="snapshot_schema",
                title="Paper Snapshot 格式",
                severity="CRITICAL",
                summary=f"Snapshot 包含 {len(schema_errors)} 項 Schema 錯誤",
                details=schema_errors + schema_warnings,
                metrics={"schema_errors": schema_errors},
            )
        )

    if snapshot_date != run_date:
        results.append(
            CheckResult(
                rule_id="snapshot_freshness",
                title="Snapshot 時效性",
                severity="CRITICAL",
                summary=f"Stale snapshot ({snapshot_date} != {run_date})",
                details=[f"snapshot_date ({snapshot_date}) != run_date ({run_date}) (stale snapshot)"],
                metrics={"snapshot_date": snapshot_date, "run_date": run_date},
            )
        )

    # Rule 1: TP/SL 價格
    try:
        r_tp_sl = validate_tp_sl(positions, market_data, calendar=calendar)
    except Exception as e:
        r_tp_sl = CheckResult("tp_sl", "TP/SL 價格", "WARNING", f"驗證器執行異常: {e}", [str(e)])
    results.append(r_tp_sl)

    # Rule 2: 20 天 TIME rule
    try:
        r_time = validate_time_rule(positions, snapshot_date, calendar=calendar)
    except Exception as e:
        r_time = CheckResult("time_rule", "20 天 TIME rule", "WARNING", f"驗證器執行異常: {e}", [str(e)])
    results.append(r_time)

    # Rule 3: Regime 曝險
    try:
        r_regime = validate_regime_exposure(snapshot, market_data, snapshot_date)
    except Exception as e:
        r_regime = CheckResult("regime_exposure", "Regime 曝險", "WARNING", f"驗證器執行異常: {e}", [str(e)])
    results.append(r_regime)

    # Rule 4: Sector concentration
    try:
        r_sector = validate_sector_concentration(positions, market_data, snapshot_date)
    except Exception as e:
        r_sector = CheckResult("sector_concentration", "Sector concentration", "WARNING", f"驗證器執行異常: {e}", [str(e)])
    results.append(r_sector)

    # Rule 5: Top-7 信號一致性
    try:
        r_signal = validate_signal_consistency(snapshot, market_data, snapshot_date, calendar=calendar)
    except Exception as e:
        r_signal = CheckResult("signal_consistency", "Top-7 信號一致性", "WARNING", f"驗證器執行異常: {e}", [str(e)])
    results.append(r_signal)

    # Rule 6: 持倉數量
    try:
        r_count = validate_position_count(positions, limit=7)
    except Exception as e:
        r_count = CheckResult("position_count", "持倉數量", "WARNING", f"驗證器執行異常: {e}", [str(e)])
    results.append(r_count)

    # 6. 計算總體狀態
    crit_cnt = sum(1 for r in results if r.severity == "CRITICAL")
    warn_cnt = sum(1 for r in results if r.severity == "WARNING")
    pass_cnt = sum(1 for r in results if r.severity == "PASS")

    if crit_cnt > 0:
        overall_status: Literal["PASS", "WARNING", "CRITICAL"] = "CRITICAL"
    elif warn_cnt > 0:
        overall_status = "WARNING"
    else:
        overall_status = "PASS"

    context = {
        "run_id": run_id,
        "run_at": run_at,
        "run_date": run_date,
        "snapshot_date": snapshot_date,
        "overall_status": overall_status,
        "paper_source": paper_source,
        "paper_url": config.paper_url,
        "critical_count": crit_cnt,
        "warning_count": warn_cnt,
        "pass_count": pass_cnt,
    }

    # 7. 渲染報告與寫入 Log
    report = render_report(context, results)
    append_verify_log(config.log_path, context, results)

    return report


def main() -> int:
    """
    CLI 進入點。
    """
    try:
        report = run_verification()
        if report:
            print(report)
        return 0
    except Exception as e:
        sys.stderr.write(f"Fatal operational error in verify_daily_strategy: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
