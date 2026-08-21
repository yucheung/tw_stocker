#!/usr/bin/env python3
"""Independent Simulation Script for tw_stocker (Top-2 Score Concentration Strategy).

Strategy ID: top2_score_v1
Design & Architecture: PLAN_independent_sim.md

This script operates in complete isolation from paper_tracker.py and paper_equity.json.
It consumes immutable upstream Top-7 orders (artifacts/orders_YYYYMMDD.json), selects the
top 1-2 candidates, simulates 09:00-09:30 buy limit orders at the opening auction of the
next trading day, tracks positions with +4 ATR / -3 ATR TP/SL and 20-day TIME rule, and
maintains authoritative state in JSON and CSV format alongside 0050 benchmark comparisons.
"""

import argparse
import fcntl
import json
import math
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import exchange_calendars as xcals
from strategy.order_execution import DEFAULT_TP_SL, evaluate_buy_limit_at_open

# =====================================================================
# Constants & Defaults
# =====================================================================

DEFAULT_STRATEGY_ID = "top2_score_v1"
SCHEMA_VERSION = 1
DEFAULT_DATA_DIR = Path("independent_sim_data")

DEFAULT_CAPITAL = 200000.0
DEFAULT_MAX_POSITIONS = 2
DEFAULT_POSITION_SIZE = 0.45
DEFAULT_RESERVE_RATIO = 0.10
DEFAULT_TP_ATR_MULT = 4.0
DEFAULT_SL_ATR_MULT = 3.0
DEFAULT_MAX_HOLD_DAYS = 20
DEFAULT_ENTRY_MODEL = "signal_close_limit_next_open_v1"

BUY_COST_RATE = 0.001425     # 0.1425% 手續費
SELL_COST_RATE = 0.004425    # 0.1425% 手續費 + 0.3% 證交稅
SLIPPAGE = 0.003             # 0.3% 賣出滑價

DEFAULT_BENCHMARK_TICKER = "0050.TW"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

STRATEGY_CONFIGS: dict[str, dict[str, Any]] = {
    "top2_score_v1": {
        "strategy_id": "top2_score_v1",
        "name": "Top-2 Score Concentration",
        "default_data_dir": "independent_sim_data",
        "orders_dir": "artifacts",
        "orders_pattern": "orders_{date}.json",
        "max_positions": 2,
        "position_size": 0.45,
        "reserve_ratio": 0.10,
        "max_hold_days": 20,
        "tp_atr_mult": 4.0,
        "sl_atr_mult": 3.0,
        "initial_capital": 200000.0,
    },
    "mr20": {
        "strategy_id": "mr20",
        "name": "MR20 Pullback Mean Reversion",
        "default_data_dir": "independent_sim_data_mr20",
        "orders_dir": "artifacts/mr20",
        "orders_pattern": "orders_mr20_{date}.json",
        "max_positions": 2,
        "position_size": 0.45,
        "reserve_ratio": 0.10,
        "max_hold_days": 20,
        "tp_atr_mult": 4.0,
        "sl_atr_mult": 3.0,
        "initial_capital": 1000000.0,
    },
    "rsi_reversal": {
        "strategy_id": "rsi_reversal",
        "name": "RSI Reversal Strategy",
        "default_data_dir": "independent_sim_data_rsi_reversal",
        "orders_dir": "artifacts/rsi_reversal",
        "orders_pattern": "orders_rsi_reversal_{date}.json",
        "max_positions": 5,
        "position_size": 0.15,
        "reserve_ratio": 0.20,
        "max_hold_days": 5,
        "tp_pct": 0.06,
        "sl_pct": 0.03,
        "tp_atr_mult": 2.0,
        "sl_atr_mult": 2.0,
        "initial_capital": 1000000.0,
    },
}


def resolve_strategy_id(strategy: Optional[str]) -> str:
    """Normalize user-supplied strategy name to canonical strategy_id."""
    if not strategy:
        return DEFAULT_STRATEGY_ID
    s = str(strategy).lower().strip()
    if s in ("top2", "top2_score_v1", "top2_v1"):
        return "top2_score_v1"
    if s in ("mr20", "mr20_pullback_v1", "mr20_v1"):
        return "mr20"
    if s in ("rsi_reversal", "rsi_reversal_v1", "rsi"):
        return "rsi_reversal"
    return s


def get_strategy_config(strategy: Optional[str] = None) -> dict[str, Any]:
    """Retrieve default parameters for strategy_id."""
    sid = resolve_strategy_id(strategy)
    if sid in STRATEGY_CONFIGS:
        return dict(STRATEGY_CONFIGS[sid])
    return {
        "strategy_id": sid,
        "name": sid,
        "default_data_dir": f"independent_sim_data_{sid}",
        "orders_dir": "artifacts",
        "orders_pattern": "orders_{date}.json",
        "max_positions": DEFAULT_MAX_POSITIONS,
        "position_size": DEFAULT_POSITION_SIZE,
        "reserve_ratio": DEFAULT_RESERVE_RATIO,
        "max_hold_days": DEFAULT_MAX_HOLD_DAYS,
        "tp_atr_mult": DEFAULT_TP_ATR_MULT,
        "sl_atr_mult": DEFAULT_SL_ATR_MULT,
        "initial_capital": DEFAULT_CAPITAL,
    }


# =====================================================================
# Calendar & Date Helpers
# =====================================================================

def get_calendar(name: str = "XTAI") -> xcals.ExchangeCalendar:
    """Return exchange calendar for Taiwan Stock Exchange."""
    return xcals.get_calendar(name)


def get_taipei_today() -> str:
    """Return current date string in Asia/Taipei timezone."""
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def get_taipei_now_iso() -> str:
    """Return current ISO timestamp in Asia/Taipei timezone."""
    return datetime.now(TAIPEI_TZ).isoformat()


def is_trading_day(dt_str: str, calendar: Optional[xcals.ExchangeCalendar] = None) -> bool:
    """Check whether dt_str (YYYY-MM-DD) is an XTAI trading session."""
    cal = calendar or get_calendar("XTAI")
    try:
        return bool(cal.is_session(dt_str))
    except Exception:
        return False


def get_next_trading_day(dt_str: str, calendar: Optional[xcals.ExchangeCalendar] = None) -> str:
    """Get the next XTAI trading session date after dt_str."""
    cal = calendar or get_calendar("XTAI")
    nxt = cal.next_session(dt_str)
    return nxt.strftime("%Y-%m-%d")


def get_previous_trading_day(dt_str: str, calendar: Optional[xcals.ExchangeCalendar] = None) -> str:
    """Get the previous XTAI trading session date before dt_str."""
    cal = calendar or get_calendar("XTAI")
    prev = cal.previous_session(dt_str)
    return prev.strftime("%Y-%m-%d")


def _opt_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Convert value to float safely; return default on failure, None, NaN, or Inf."""
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


# =====================================================================
# Market Data Providers (yfinance wrappers with test decoupling)
# =====================================================================

def fetch_market_bars(tickers: list[str], as_of: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Fetch OHLC bar for each ticker up to as_of using yfinance.

    Returns dict mapping ticker (e.g. '2330') to:
    {'date': 'YYYY-MM-DD', 'open': float, 'high': float, 'low': float, 'close': float}
    """
    if not tickers:
        return {}
    import yfinance as yf

    bars: dict[str, dict[str, Any]] = {}

    def _download(symbols: list[str]) -> Optional[pd.DataFrame]:
        try:
            if as_of:
                start_dt = (pd.to_datetime(as_of) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
                end_dt = (pd.to_datetime(as_of) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                return yf.download(symbols, start=start_dt, end=end_dt, progress=False)
            return yf.download(symbols, period="5d", progress=False)
        except Exception:
            return None

    def _get_series(df: pd.DataFrame, field: str, symbol: str) -> Optional[pd.Series]:
        if isinstance(df.columns, pd.MultiIndex):
            if (field, symbol) in df.columns:
                return df[(field, symbol)].dropna()
            return None
        elif field in df.columns:
            return df[field].dropna()
        return None

    def _parse_bars(df: Optional[pd.DataFrame], symbol_map: dict[str, str]) -> dict[str, dict[str, Any]]:
        if df is None or df.empty:
            return {}
        res = {}
        for ticker, sym in symbol_map.items():
            c_series = _get_series(df, "Close", sym)
            if c_series is None or len(c_series) == 0:
                continue
            if as_of:
                c_series = c_series[c_series.index <= pd.to_datetime(as_of)]
                if len(c_series) == 0:
                    continue
            last_dt = c_series.index[-1]
            dt_str = last_dt.strftime("%Y-%m-%d") if hasattr(last_dt, "strftime") else str(last_dt)[:10]

            o_series = _get_series(df, "Open", sym)
            h_series = _get_series(df, "High", sym)
            l_series = _get_series(df, "Low", sym)

            o = float(o_series.loc[last_dt]) if (o_series is not None and last_dt in o_series.index and math.isfinite(float(o_series.loc[last_dt]))) else None
            h = float(h_series.loc[last_dt]) if (h_series is not None and last_dt in h_series.index and math.isfinite(float(h_series.loc[last_dt]))) else None
            l = float(l_series.loc[last_dt]) if (l_series is not None and last_dt in l_series.index and math.isfinite(float(l_series.loc[last_dt]))) else None
            c = float(c_series.loc[last_dt])
            res[ticker] = {
                "date": dt_str,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        return res

    # 1. Try .TW
    tw_map = {t: f"{t}.TW" for t in tickers}
    df_tw = _download(list(tw_map.values()))
    bars.update(_parse_bars(df_tw, tw_map))

    # 2. Try .TWO for missing
    missing = [t for t in tickers if t not in bars]
    if missing:
        two_map = {t: f"{t}.TWO" for t in missing}
        df_two = _download(list(two_map.values()))
        bars.update(_parse_bars(df_two, two_map))

    return bars


def fetch_ticker_closes(
    ticker: str, count: int = 60, end_date: Optional[str] = None, as_of: Optional[str] = None
) -> pd.Series:
    """Fetch historical close series for ATR20 computation up to end_date/as_of."""
    import yfinance as yf

    eff_end = end_date or as_of
    for suffix in [".TW", ".TWO"]:
        sym = f"{ticker}{suffix}"
        try:
            if eff_end:
                start_dt = (pd.to_datetime(eff_end) - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
                end_dt = (pd.to_datetime(eff_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                df = yf.download(sym, start=start_dt, end=end_dt, progress=False)
            else:
                df = yf.download(sym, period="6mo", progress=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    if ("Close", sym) in df.columns:
                        s = df[("Close", sym)].dropna()
                    else:
                        s = df["Close"].iloc[:, 0].dropna()
                elif "Close" in df.columns:
                    s = df["Close"].dropna()
                else:
                    continue

                if eff_end:
                    s = s[s.index <= pd.to_datetime(eff_end)]
                if len(s) >= 21:
                    return s
        except Exception:
            continue
    return pd.Series(dtype=float)


def fetch_benchmark_close(as_of: str, benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER) -> Optional[float]:
    """Fetch benchmark close price for date as_of."""
    import yfinance as yf

    try:
        start_dt = (pd.to_datetime(as_of) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        end_dt = (pd.to_datetime(as_of) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        df = yf.download(benchmark_ticker, start=start_dt, end=end_dt, progress=False)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                if ("Close", benchmark_ticker) in df.columns:
                    s = df[("Close", benchmark_ticker)].dropna()
                else:
                    s = df["Close"].iloc[:, 0].dropna()
            elif "Close" in df.columns:
                s = df["Close"].dropna()
            else:
                return None
            s = s[s.index <= pd.to_datetime(as_of)]
            if len(s) > 0:
                val = float(s.iloc[-1])
                return val if math.isfinite(val) and val > 0 else None
    except Exception:
        pass
    return None


# =====================================================================
# Task 1 & ATR: Top-7 Candidate Selection & ATR Calculation
# =====================================================================

def compute_v85_atr20(closes: pd.Series) -> Optional[float]:
    """Compute v8.5 ATR20 from close price series.

    Formula: ATR20 = mean(abs(close.pct_change()), 20) * close
    Requires at least 21 valid bars.
    """
    valid = closes.dropna()
    if len(valid) < 21:
        return None
    atr_series = valid.pct_change().abs().rolling(20).mean() * valid
    val = atr_series.iloc[-1]
    if val is None or not math.isfinite(val) or val <= 0:
        return None
    return float(val)


def load_orders(orders_path: Path | str, calendar: Optional[xcals.ExchangeCalendar] = None) -> list[dict[str, Any]]:
    """Load and strictly validate upstream Top-7 orders from JSON artifact."""
    p = Path(orders_path)
    if not p.exists():
        raise FileNotFoundError(f"Orders file not found: {p}")

    try:
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        raise ValueError(f"Failed to parse orders file {p}: {e}") from e

    if not isinstance(data, dict) or "orders" not in data or not isinstance(data["orders"], list):
        raise ValueError(f"Invalid schema: missing 'orders' list in {p}")

    orders = data["orders"]
    if not orders:
        return []

    cal = calendar or get_calendar("XTAI")
    signal_dates = {o.get("signal_date") for o in orders if o.get("signal_date")}
    if len(signal_dates) != 1:
        raise ValueError(f"Inconsistent signal_date found in orders: {signal_dates}")

    sig_date = list(signal_dates)[0]
    expected_exec_date = cal.next_session(sig_date).strftime("%Y-%m-%d")

    for idx, o in enumerate(orders):
        if not o.get("ticker"):
            raise ValueError(f"Order #{idx} missing required field 'ticker'")
        exec_date = o.get("execution_date")
        if exec_date and exec_date != expected_exec_date:
            raise ValueError(
                f"Order for ticker {o.get('ticker')} execution_date {exec_date} is not next XTAI trading session ({expected_exec_date})"
            )

    return orders


def select_candidates(
    orders: list[dict[str, Any]],
    held: set[str],
    pending: set[str],
    max_picks: int = 2,
    max_price: Optional[float] = None,
    manual_tickers: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Select up to max_picks candidates according to Auto, Low-Price, or Manual mode."""
    if not orders:
        return []

    order_map = {o["ticker"]: o for o in orders if o.get("ticker")}

    # Mode 1: Manual selection
    if manual_tickers is not None:
        if len(manual_tickers) > max_picks:
            raise ValueError(f"Cannot select more than {max_picks} manual tickers (got {len(manual_tickers)})")
        if len(set(manual_tickers)) != len(manual_tickers):
            raise ValueError("Duplicate tickers in manual selection")

        selected = []
        for tkr in manual_tickers:
            if tkr not in order_map:
                raise ValueError(f"Ticker {tkr} not in upstream Top-7 signals")
            if tkr in held or tkr in pending:
                raise ValueError(f"Ticker {tkr} is already held or pending")
            o = dict(order_map[tkr])
            o["selection_mode"] = "manual"
            selected.append(o)
        return selected

    # Filter out held and pending
    eligible = [o for o in orders if o.get("ticker") not in held and o.get("ticker") not in pending]

    # Mode 2: Low-Price filter
    if max_price is not None and max_price > 0:
        eligible = [
            o for o in eligible
            if _opt_float(o.get("reference_close", o.get("limit_price"))) is not None
            and _opt_float(o.get("reference_close", o.get("limit_price"))) <= max_price
        ]
        selection_mode = "low_price"
    else:
        selection_mode = "auto"

    # Sort key: rank ascending (if rank is None, sort by score desc, then ticker asc)
    def _sort_key(o: dict[str, Any]) -> tuple:
        rank = o.get("rank")
        score = _opt_float(o.get("score"), 0.0)
        tkr = str(o.get("ticker", ""))
        if rank is not None and isinstance(rank, int):
            return (0, rank, -score, tkr)
        return (1, -score, tkr)

    eligible.sort(key=_sort_key)
    picked = eligible[:max_picks]

    result = []
    for o in picked:
        item = dict(o)
        item["selection_mode"] = selection_mode
        result.append(item)
    return result


# =====================================================================
# Task 2: State Management & Atomic I/O
# =====================================================================

def get_default_state(
    capital: Optional[float] = None,
    max_positions: Optional[int] = None,
    position_size: Optional[float] = None,
    reserve_ratio: Optional[float] = None,
    max_price: Optional[float] = None,
    tp_atr_mult: Optional[float] = None,
    sl_atr_mult: Optional[float] = None,
    max_hold_days: Optional[int] = None,
    entry_model: str = DEFAULT_ENTRY_MODEL,
    strategy_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    """Return fresh initial state dictionary."""
    sid = resolve_strategy_id(strategy_id)
    strat_cfg = get_strategy_config(sid)

    eff_capital = float(capital) if capital is not None else float(strat_cfg["initial_capital"])
    eff_max_pos = int(max_positions) if max_positions is not None else int(strat_cfg["max_positions"])
    eff_pos_size = float(position_size) if position_size is not None else float(strat_cfg["position_size"])
    eff_reserve = float(reserve_ratio) if reserve_ratio is not None else float(strat_cfg["reserve_ratio"])
    eff_tp_mult = float(tp_atr_mult) if tp_atr_mult is not None else float(strat_cfg.get("tp_atr_mult", DEFAULT_TP_ATR_MULT))
    eff_sl_mult = float(sl_atr_mult) if sl_atr_mult is not None else float(strat_cfg.get("sl_atr_mult", DEFAULT_SL_ATR_MULT))
    eff_max_hold = int(max_hold_days) if max_hold_days is not None else int(strat_cfg.get("max_hold_days", DEFAULT_MAX_HOLD_DAYS))

    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": sid,
        "created_at": created_at or get_taipei_now_iso(),
        "config": {
            "initial_capital": eff_capital,
            "max_positions": eff_max_pos,
            "position_size": eff_pos_size,
            "reserve_ratio": eff_reserve,
            "max_price": _opt_float(max_price),
            "tp_atr_mult": eff_tp_mult,
            "sl_atr_mult": eff_sl_mult,
            "max_hold_days": eff_max_hold,
            "entry_model": entry_model,
        },
        "cash": eff_capital,
        "positions": {},
        "pending_orders": [],
        "order_events": [],
        "closed_trades": [],
        "equity_curve": [],
        "processed_runs": [],
    }


def validate_state(state: dict[str, Any]) -> None:
    """Validate that state dict meets schema requirements."""
    if not isinstance(state, dict):
        raise ValueError("State must be a dictionary")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version: {state.get('schema_version')}")
    required_keys = [
        "strategy_id", "config", "cash", "positions",
        "pending_orders", "order_events", "closed_trades", "equity_curve", "processed_runs"
    ]
    for k in required_keys:
        if k not in state:
            raise ValueError(f"State missing required key: {k}")


def load_state(data_dir: Path | str = DEFAULT_DATA_DIR) -> dict[str, Any]:
    """Load and validate state.json from data_dir."""
    p = Path(data_dir) / "state.json"
    if not p.exists():
        raise FileNotFoundError(f"State file not found at {p}")
    try:
        content = p.read_text(encoding="utf-8")
        state = json.loads(content)
        validate_state(state)
        return state
    except Exception as e:
        raise ValueError(f"Corrupt or invalid state file at {p}: {e}") from e


def save_state_atomic(state: dict[str, Any], data_dir: Path | str = DEFAULT_DATA_DIR) -> None:
    """Save state atomically using temporary file + fsync + replace under file lock."""
    validate_state(state)
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    state_file = d / "state.json"
    lock_file = d / "state.json.lock"

    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=d,
                prefix="state.json.tmp.",
                delete=False,
                encoding="utf-8",
            ) as tf:
                tmp_path = Path(tf.name)
                json.dump(state, tf, indent=2, ensure_ascii=False)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp_path, state_file)
            tmp_path = None
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            fcntl.flock(lf, fcntl.LOCK_UN)


def is_run_processed(state: dict[str, Any], run_id: str) -> bool:
    """Check if command run_id was already processed."""
    return run_id in state.get("processed_runs", [])


def mark_run_processed(state: dict[str, Any], run_id: str) -> None:
    """Record command run_id to processed_runs."""
    if "processed_runs" not in state:
        state["processed_runs"] = []
    if run_id not in state["processed_runs"]:
        state["processed_runs"].append(run_id)


def init_simulation(
    data_dir: Optional[Path | str] = None,
    capital: Optional[float] = None,
    max_price: Optional[float] = None,
    position_size: Optional[float] = None,
    reserve_ratio: Optional[float] = None,
    max_positions: Optional[int] = None,
    max_hold_days: Optional[int] = None,
    strategy: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Initialize a new simulation state."""
    sid = resolve_strategy_id(strategy)
    strat_cfg = get_strategy_config(sid)
    d = Path(data_dir) if data_dir is not None else Path(strat_cfg["default_data_dir"])

    state_path = d / "state.json"
    if state_path.exists() and not force:
        raise FileExistsError(f"{state_path} already exists. Use --force to overwrite.")

    state = get_default_state(
        capital=capital,
        max_positions=max_positions,
        max_price=max_price,
        position_size=position_size,
        reserve_ratio=reserve_ratio,
        max_hold_days=max_hold_days,
        strategy_id=sid,
    )
    save_state_atomic(state, data_dir=d)
    export_ledgers(state, data_dir=d)
    return state


# =====================================================================
# Task 3 & 4: Order Planning & 09:30 Open Execution
# =====================================================================

def plan_orders(
    state: dict[str, Any],
    selected_candidates: list[dict[str, Any]],
    as_of: str,
    fetch_closes_fn: Optional[Callable[[str], pd.Series]] = None,
) -> list[dict[str, Any]]:
    """Plan buy limit orders for next trading session based on selected candidates."""
    cfg = state["config"]
    max_pos = cfg.get("max_positions", 2)
    held_count = len(state["positions"])
    pending_count = len(state["pending_orders"])
    capacity = max(0, max_pos - held_count - pending_count)

    if capacity <= 0 or not selected_candidates:
        return []

    fetcher = fetch_closes_fn or (lambda tkr: fetch_ticker_closes(tkr, end_date=as_of))
    new_pending = []

    for candidate in selected_candidates[:capacity]:
        ticker = candidate["ticker"]
        sig_date = candidate.get("signal_date", as_of)
        exec_date = candidate.get("execution_date") or get_next_trading_day(sig_date)
        order_id = f"{state['strategy_id']}:{sig_date}:{ticker}:buy"

        # Check existing order_id in pending_orders or order_events to ensure idempotency
        if any(p["order_id"] == order_id for p in state["pending_orders"]):
            continue

        ref_close = _opt_float(candidate.get("reference_close", candidate.get("limit_price")))
        limit_price = _opt_float(candidate.get("limit_price", candidate.get("reference_close")))
        if ref_close is None or limit_price is None or ref_close <= 0 or limit_price <= 0:
            continue

        # ATR determination
        atr_val = _opt_float(candidate.get("atr"))
        if atr_val is None or atr_val <= 0:
            hist_closes = fetcher(ticker)
            atr_val = compute_v85_atr20(hist_closes)

        event_base = {
            "order_id": order_id,
            "signal_date": sig_date,
            "execution_date": exec_date,
            "event_time": get_taipei_now_iso(),
            "ticker": ticker,
            "upstream_rank": candidate.get("rank"),
            "score": candidate.get("score"),
            "selection_mode": candidate.get("selection_mode", "auto"),
            "reference_close": ref_close,
            "limit_price": limit_price,
            "open_price": None,
            "fill_price": None,
            "atr": atr_val,
            "tp_price": None,
            "sl_price": None,
            "shares": 0,
            "message": "",
        }

        if atr_val is None or atr_val <= 0:
            event = {**event_base, "status": "SKIPPED_NO_ATR", "message": "ATR20 could not be computed (insufficient bars)"}
            state["order_events"].append(event)
            continue

        pending_order = {
            "order_id": order_id,
            "signal_date": sig_date,
            "execution_date": exec_date,
            "ticker": ticker,
            "upstream_rank": candidate.get("rank"),
            "score": candidate.get("score"),
            "selection_mode": candidate.get("selection_mode", "auto"),
            "reference_close": ref_close,
            "limit_price": limit_price,
            "atr": atr_val,
            "tp_atr_mult": candidate.get("tp_atr_mult", cfg.get("tp_atr_mult", DEFAULT_TP_ATR_MULT)),
            "sl_atr_mult": candidate.get("sl_atr_mult", cfg.get("sl_atr_mult", DEFAULT_SL_ATR_MULT)),
            "tp_pct": candidate.get("tp_pct"),
            "sl_pct": candidate.get("sl_pct"),
            "tp_sl_mode": candidate.get("tp_sl_mode"),
            "max_hold_days": candidate.get("max_hold_days", cfg.get("max_hold_days", DEFAULT_MAX_HOLD_DAYS)),
            "created_at": get_taipei_now_iso(),
        }
        state["pending_orders"].append(pending_order)
        new_pending.append(pending_order)

        event = {**event_base, "status": "PENDING", "message": "Order placed for next session"}
        state["order_events"].append(event)

    return new_pending


def execute_open_orders(
    state: dict[str, Any],
    bars: dict[str, dict[str, Any]],
    as_of: str,
) -> list[dict[str, Any]]:
    """Execute due pending orders at 09:30 on execution_date as_of."""
    cfg = state["config"]
    initial_cap = cfg["initial_capital"]
    reserve_cash = initial_cap * cfg.get("reserve_ratio", DEFAULT_RESERVE_RATIO)
    pos_size = cfg.get("position_size", DEFAULT_POSITION_SIZE)
    max_pos = cfg.get("max_positions", DEFAULT_MAX_POSITIONS)

    due_orders = [o for o in state["pending_orders"] if o.get("execution_date") == as_of]
    remaining_pending = [o for o in state["pending_orders"] if o.get("execution_date") != as_of]
    state["pending_orders"] = remaining_pending

    terminal_events = []

    for order in due_orders:
        ticker = order["ticker"]
        order_id = order["order_id"]
        limit_price = order["limit_price"]
        atr = order["atr"]

        event_base = {
            "order_id": order_id,
            "signal_date": order["signal_date"],
            "execution_date": as_of,
            "event_time": get_taipei_now_iso(),
            "ticker": ticker,
            "upstream_rank": order.get("upstream_rank"),
            "score": order.get("score"),
            "selection_mode": order.get("selection_mode", "auto"),
            "reference_close": order.get("reference_close", limit_price),
            "limit_price": limit_price,
            "open_price": None,
            "status": "",
            "fill_price": None,
            "atr": atr,
            "tp_price": None,
            "sl_price": None,
            "shares": 0,
            "message": "",
        }

        # Check max positions capacity
        if len(state["positions"]) >= max_pos:
            event = {**event_base, "status": "CANCELLED_NO_CAPACITY", "message": "Max positions reached"}
            state["order_events"].append(event)
            terminal_events.append(event)
            continue

        bar = bars.get(ticker)
        # Check bar freshness and valid open price
        if not bar or bar.get("date") != as_of or bar.get("open") is None:
            event = {**event_base, "status": "CANCELLED_NO_OPEN_PRICE", "message": "Missing or stale open price"}
            state["order_events"].append(event)
            terminal_events.append(event)
            continue

        open_price = bar.get("open")
        decision = evaluate_buy_limit_at_open(limit_price=limit_price, open_price=open_price)

        if decision.status != "FILLED":
            event = {
                **event_base,
                "open_price": open_price,
                "status": decision.status,
                "message": "Open price above limit" if decision.status == "CANCELLED_OPEN_ABOVE_LIMIT" else "Invalid open price",
            }
            state["order_events"].append(event)
            terminal_events.append(event)
            continue

        # Sizing and Cash allocation
        fill_price = decision.fill_price
        current_market_val = sum(
            pos["shares"] * bars.get(t, {}).get("close", pos["entry"])
            for t, pos in state["positions"].items()
        )
        current_equity = state["cash"] + current_market_val
        available_cash = max(0.0, state["cash"] - reserve_cash)
        target_amount = min(current_equity * pos_size, available_cash)
        shares = math.floor(target_amount / fill_price) if fill_price > 0 else 0
        trade_amount = shares * fill_price
        buy_cost = trade_amount * BUY_COST_RATE

        if shares < 1 or (state["cash"] - trade_amount - buy_cost < reserve_cash):
            event = {
                **event_base,
                "open_price": open_price,
                "status": "CANCELLED_INSUFFICIENT_CASH",
                "message": f"Insufficient cash to buy 1 share while preserving reserve {reserve_cash:.0f}",
            }
            state["order_events"].append(event)
            terminal_events.append(event)
            continue

        # Deduct cash
        state["cash"] -= (trade_amount + buy_cost)

        # Compute TP/SL anchored on fill_price
        tp_sl_mode = order.get("tp_sl_mode")
        tp_pct = order.get("tp_pct")
        sl_pct = order.get("sl_pct")

        if tp_sl_mode == "fixed_pct" or tp_pct is not None:
            tp_p = _opt_float(tp_pct, 0.06)
            sl_p = _opt_float(sl_pct, 0.03)
            tp_price = fill_price * (1.0 + tp_p)
            sl_price = fill_price * (1.0 - sl_p)
        else:
            tp_mult = order.get("tp_atr_mult", DEFAULT_TP_ATR_MULT)
            sl_mult = order.get("sl_atr_mult", DEFAULT_SL_ATR_MULT)
            tp_price = fill_price + tp_mult * atr
            sl_price = fill_price - sl_mult * atr

        if sl_price <= 0:
            sl_price = fill_price * (1 - DEFAULT_TP_SL["sl_pct"])

        # Create position
        state["positions"][ticker] = {
            "ticker": ticker,
            "entry": fill_price,
            "shares": int(shares),
            "tp": round(tp_price, 2),
            "sl": round(sl_price, 2),
            "atr": atr,
            "entry_date": as_of,
            "day_count": 0,
            "max_hold_days": order.get("max_hold_days", DEFAULT_MAX_HOLD_DAYS),
            "signal_date": order["signal_date"],
            "order_id": order_id,
        }

        event = {
            **event_base,
            "open_price": open_price,
            "status": "FILLED",
            "fill_price": fill_price,
            "tp_price": round(tp_price, 2),
            "sl_price": round(sl_price, 2),
            "shares": int(shares),
            "message": f"Filled at open {fill_price:.2f}",
        }
        state["order_events"].append(event)
        terminal_events.append(event)

    return terminal_events


# =====================================================================
# Task 5: Exits (SL/TP/TIME), Costs & Equity Mark-to-Market
# =====================================================================

def settle_positions(
    state: dict[str, Any],
    bars: dict[str, dict[str, Any]],
    as_of: str,
) -> list[dict[str, Any]]:
    """Settle existing positions on as_of trading day (SL > TP > TIME)."""
    closed_trades = []
    to_delete = []

    for ticker, pos in state["positions"].items():
        if pos.get("entry_date") == as_of:
            continue
        bar = bars.get(ticker)
        if not bar or bar.get("close") is None:
            # No valid bar today; do not increment day count or check exit
            continue
        if bar.get("date") != as_of:
            continue

        pos["day_count"] = pos.get("day_count", 0) + 1
        day_count = pos["day_count"]
        max_hold = pos.get("max_hold_days", DEFAULT_MAX_HOLD_DAYS)

        reason = None
        exit_price = bar["close"]

        # Conservative priority: SL > TP > TIME
        if bar.get("low") is not None and bar["low"] <= pos["sl"]:
            reason = "SL"
            open_px = bar.get("open")
            exit_price = open_px if (open_px is not None and open_px < pos["sl"]) else pos["sl"]
        elif bar.get("high") is not None and bar["high"] >= pos["tp"]:
            reason = "TP"
            open_px = bar.get("open")
            exit_price = open_px if (open_px is not None and open_px > pos["tp"]) else pos["tp"]
        elif day_count >= max_hold:
            reason = "TIME"
            exit_price = bar["close"]

        if reason:
            shares = pos["shares"]
            entry_price = pos["entry"]
            sell_cost = exit_price * shares * SELL_COST_RATE
            slippage_cost = exit_price * shares * SLIPPAGE
            proceeds = exit_price * shares - sell_cost - slippage_cost
            buy_cost = entry_price * shares * BUY_COST_RATE
            cost_basis = entry_price * shares + buy_cost
            gross_pnl = (exit_price - entry_price) * shares
            net_pnl = proceeds - cost_basis
            net_return_pct = (net_pnl / cost_basis) * 100.0 if cost_basis > 0 else 0.0

            state["cash"] += proceeds

            trade = {
                "trade_id": f"{state['strategy_id']}:{pos.get('signal_date')}:{ticker}:{pos['entry_date']}:{as_of}",
                "ticker": ticker,
                "signal_date": pos.get("signal_date", ""),
                "entry_date": pos["entry_date"],
                "exit_date": as_of,
                "entry_price": float(entry_price),
                "exit_price": float(exit_price),
                "shares": int(shares),
                "atr": pos.get("atr"),
                "tp_price": float(pos["tp"]),
                "sl_price": float(pos["sl"]),
                "days_held": int(day_count),
                "exit_reason": reason,
                "buy_cost": round(buy_cost, 2),
                "sell_cost": round(sell_cost, 2),
                "slippage_cost": round(slippage_cost, 2),
                "gross_pnl": round(gross_pnl, 2),
                "net_pnl": round(net_pnl, 2),
                "net_return_pct": round(net_return_pct, 4),
            }
            state["closed_trades"].append(trade)
            closed_trades.append(trade)
            to_delete.append(ticker)

    for t in to_delete:
        del state["positions"][t]

    return closed_trades


def mark_equity(
    state: dict[str, Any],
    closes: dict[str, float],
    benchmark_close: float,
    as_of: str,
) -> dict[str, Any]:
    """Calculate and record daily equity and benchmark equity for as_of."""
    initial_cap = state["config"]["initial_capital"]
    market_value = sum(
        closes.get(tkr, pos["entry"]) * pos["shares"]
        for tkr, pos in state["positions"].items()
    )
    equity = state["cash"] + market_value

    if state["equity_curve"]:
        prev_equity = state["equity_curve"][-1]["equity"]
        first_bm_close = state["equity_curve"][0]["benchmark_close"]
    else:
        prev_equity = initial_cap
        first_bm_close = benchmark_close

    daily_return = (equity / prev_equity - 1.0) if prev_equity > 0 else 0.0
    cum_return = (equity / initial_cap - 1.0)

    if first_bm_close and first_bm_close > 0:
        bm_equity = initial_cap * (benchmark_close / first_bm_close)
        bm_return = (bm_equity / initial_cap - 1.0)
    else:
        bm_equity = initial_cap
        bm_return = 0.0

    excess_return = cum_return - bm_return

    all_equities = [e["equity"] for e in state["equity_curve"]] + [equity]
    peak_equity = max(all_equities)
    drawdown = (equity - peak_equity) / peak_equity if peak_equity > 0 else 0.0

    # Avoid duplicate entry for same date
    state["equity_curve"] = [e for e in state["equity_curve"] if e.get("date") != as_of]

    record = {
        "date": as_of,
        "cash": round(state["cash"], 2),
        "market_value": round(market_value, 2),
        "equity": round(equity, 2),
        "daily_return": round(daily_return, 6),
        "cumulative_return": round(cum_return, 6),
        "benchmark_close": round(benchmark_close, 2),
        "benchmark_equity": round(bm_equity, 2),
        "benchmark_return": round(bm_return, 6),
        "excess_return": round(excess_return, 6),
        "drawdown": round(drawdown, 6),
    }
    state["equity_curve"].append(record)
    return record


# =====================================================================
# Task 6: CSV Export, Performance Report & Charts
# =====================================================================

def export_ledgers(state: dict[str, Any], data_dir: Path | str = DEFAULT_DATA_DIR) -> None:
    """Export orders.csv, trades.csv, and equity.csv deterministically."""
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)

    # 1. orders.csv
    order_cols = [
        "order_id", "signal_date", "execution_date", "event_time", "ticker",
        "upstream_rank", "score", "selection_mode", "reference_close",
        "limit_price", "open_price", "status", "fill_price", "atr",
        "tp_price", "sl_price", "shares", "message"
    ]
    orders_df = pd.DataFrame(state.get("order_events", []), columns=order_cols)
    orders_df.to_csv(d / "orders.csv", index=False)

    # 2. trades.csv
    trade_cols = [
        "trade_id", "ticker", "signal_date", "entry_date", "exit_date",
        "entry_price", "exit_price", "shares", "atr", "tp_price", "sl_price",
        "days_held", "exit_reason", "buy_cost", "sell_cost", "slippage_cost",
        "gross_pnl", "net_pnl", "net_return_pct"
    ]
    trades_df = pd.DataFrame(state.get("closed_trades", []), columns=trade_cols)
    trades_df.to_csv(d / "trades.csv", index=False)

    # 3. equity.csv
    equity_cols = [
        "date", "cash", "market_value", "equity", "daily_return",
        "cumulative_return", "benchmark_close", "benchmark_equity",
        "benchmark_return", "excess_return", "drawdown"
    ]
    equity_df = pd.DataFrame(state.get("equity_curve", []), columns=equity_cols)
    equity_df.to_csv(d / "equity.csv", index=False)


def compute_performance(
    equity_df: pd.DataFrame,
    trades: list[dict[str, Any]],
    order_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute summary performance metrics."""
    res: dict[str, Any] = {
        "total_return": 0.0,
        "benchmark_return": 0.0,
        "excess_return": 0.0,
        "max_drawdown": 0.0,
        "benchmark_max_drawdown": 0.0,
        "total_trades": len(trades),
        "win_rate": 0.0,
        "avg_trade_pnl": 0.0,
        "avg_trade_return_pct": 0.0,
        "profit_factor": None,
        "fill_rate": 0.0,
        "sharpe": None,
        "trading_days": len(equity_df),
    }

    if not equity_df.empty and "cumulative_return" in equity_df.columns:
        last = equity_df.iloc[-1]
        res["total_return"] = float(last.get("cumulative_return", 0.0))
        res["benchmark_return"] = float(last.get("benchmark_return", 0.0))
        res["excess_return"] = float(last.get("excess_return", 0.0))
        res["max_drawdown"] = float(equity_df["drawdown"].min()) if "drawdown" in equity_df.columns else 0.0

        if "benchmark_equity" in equity_df.columns:
            bm_peak = equity_df["benchmark_equity"].cummax()
            bm_dd = (equity_df["benchmark_equity"] - bm_peak) / bm_peak
            res["benchmark_max_drawdown"] = float(bm_dd.min())

        # Sharpe ratio (>= 30 trading days)
        if len(equity_df) >= 30 and "daily_return" in equity_df.columns:
            rets = equity_df["daily_return"].iloc[1:]  # skip day 0
            if len(rets) > 0 and rets.std() > 0:
                res["sharpe"] = float((rets.mean() / rets.std()) * np.sqrt(252))

    if trades:
        wins = [t for t in trades if t.get("net_pnl", 0) > 0]
        losses = [t for t in trades if t.get("net_pnl", 0) <= 0]
        res["win_rate"] = len(wins) / len(trades)
        res["avg_trade_pnl"] = sum(t.get("net_pnl", 0) for t in trades) / len(trades)
        res["avg_trade_return_pct"] = sum(t.get("net_return_pct", 0) for t in trades) / len(trades)

        tot_win = sum(t.get("net_pnl", 0) for t in wins)
        tot_loss = abs(sum(t.get("net_pnl", 0) for t in losses))
        if tot_loss > 0:
            res["profit_factor"] = tot_win / tot_loss
        elif tot_win > 0:
            res["profit_factor"] = float("inf")

    # Fill rate from terminal orders
    terminal_orders = [e for e in order_events if e.get("status") in [
        "FILLED", "CANCELLED_OPEN_ABOVE_LIMIT", "CANCELLED_NO_OPEN_PRICE",
        "CANCELLED_NO_CAPACITY", "CANCELLED_INSUFFICIENT_CASH", "CANCELLED_EXPIRED"
    ]]
    if terminal_orders:
        filled_count = sum(1 for e in terminal_orders if e.get("status") == "FILLED")
        res["fill_rate"] = filled_count / len(terminal_orders)

    return res


def generate_markdown_report(state: dict[str, Any], perf: dict[str, Any]) -> str:
    """Generate Markdown performance comparison report."""
    cfg = state["config"]
    sharpe_str = f"{perf['sharpe']:.2f}" if perf["sharpe"] is not None else "N/A（樣本不足 < 30 日）"
    pf_str = f"{perf['profit_factor']:.2f}" if perf["profit_factor"] is not None else "N/A"

    max_price_val = cfg.get("max_price")
    max_price_desc = f"<= {max_price_val} 元" if max_price_val is not None else "無"

    lines = [
        f"# Top-2 Score Concentration Simulation Report ({state['strategy_id']})",
        "",
        f"- **建立時間 / 狀態更新**: {get_taipei_now_iso()}",
        f"- **初始資金**: {cfg['initial_capital']:,.0f} TWD",
        f"- **目前現金**: {state['cash']:,.0f} TWD",
        f"- **最大持倉**: {cfg['max_positions']} 檔 (每檔投入 ~{cfg['position_size']*100:.0f}% 權益)",
        f"- **低價篩選**: {max_price_desc}",
        "",
        "## 1. 策略 vs 0050 績效比較",
        "",
        "| 指標 | Top-2 策略 | 0050 (基準) | 差異 (Alpha) |",
        "|---|---|---|---|",
        f"| **累積報酬率** | **{perf['total_return']*100:+.2f}%** | {perf['benchmark_return']*100:+.2f}% | **{perf['excess_return']*100:+.2f}%** |",
        f"| **最大回撤 (MDD)** | {perf['max_drawdown']*100:.2f}% | {perf['benchmark_max_drawdown']*100:.2f}% | - |",
        f"| **年化 Sharpe** | {sharpe_str} | - | - |",
        f"| **交易天數** | {perf['trading_days']} 日 | {perf['trading_days']} 日 | - |",
        "",
        "## 2. 交易統計",
        "",
        "| 統計項目 | 數值 |",
        "|---|---|",
        f"| **已平倉總筆數** | {perf['total_trades']} 筆 |",
        f"| **勝率** | {perf['win_rate']*100:.1f}% |",
        f"| **平均每筆損益** | {perf['avg_trade_pnl']:+,.0f} TWD ({perf['avg_trade_return_pct']:+.2f}%) |",
        f"| **獲利因子 (Profit Factor)** | {pf_str} |",
        f"| **委託成交率** | {perf['fill_rate']*100:.1f}% |",
        "",
    ]

    # Active positions
    lines.append("## 3. 目前持倉")
    if state["positions"]:
        lines.append("| 標的 | 進場日 | 進場價 | 股數 | TP (+4 ATR) | SL (-3 ATR) | 已持有天數 |")
        lines.append("|---|---|---|---|---|---|---|")
        for tkr, pos in state["positions"].items():
            lines.append(
                f"| `{tkr}` | {pos['entry_date']} | {pos['entry']:.2f} | {pos['shares']:,} | "
                f"{pos['tp']:.2f} | {pos['sl']:.2f} | {pos['day_count']}/{pos.get('max_hold_days', 20)} |"
            )
    else:
        lines.append("*(目前無持倉，全持有現金)*")
    lines.append("")

    # Recent trades
    lines.append("## 4. 最近已平倉交易 (Latest 5)")
    trades = state.get("closed_trades", [])
    if trades:
        lines.append("| 標的 | 進場日 | 出場日 | 進場價 | 出場價 | 股數 | 淨損益 (TWD) | 報酬率 | 原因 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for t in trades[-5:]:
            lines.append(
                f"| `{t['ticker']}` | {t['entry_date']} | {t['exit_date']} | {t['entry_price']:.2f} | "
                f"{t['exit_price']:.2f} | {t['shares']:,} | {t['net_pnl']:+,.0f} | {t['net_return_pct']:+.2f}% | `{t['exit_reason']}` |"
            )
    else:
        lines.append("*(尚無平倉交易記錄)*")
    lines.append("")

    return "\n".join(lines)


def generate_performance_chart(
    equity_df: pd.DataFrame, output_path: Path | str
) -> bool:
    """Generate normalized equity curve and drawdown chart using matplotlib."""
    if equity_df.empty or "date" not in equity_df.columns:
        return False

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(10, 8), sharex=True, gridspec_kw={"height_ratios": [2.5, 1]}
        )

        dates = pd.to_datetime(equity_df["date"])
        strat_eq = equity_df["equity"]
        bm_eq = equity_df["benchmark_equity"]
        dd = equity_df["drawdown"] * 100.0

        # Subplot 1: Equity Curves
        ax1.plot(dates, strat_eq, label="Top-2 Strategy (Net Equity)", color="#2563eb", linewidth=2.0)
        ax1.plot(dates, bm_eq, label="0050 Benchmark", color="#9ca3af", linewidth=1.5, linestyle="--")
        ax1.set_title("Top-2 Concentration vs 0050 Normalized Equity", fontsize=13, fontweight="bold")
        ax1.set_ylabel("Equity (TWD)", fontsize=10)
        ax1.yaxis.set_major_formatter(mticker.StrMethodFormatter("{x:,.0f}"))
        ax1.legend(loc="upper left")
        ax1.grid(True, linestyle=":", alpha=0.6)

        # Subplot 2: Drawdown
        ax2.fill_between(dates, dd, 0, color="#ef4444", alpha=0.3, label="Top-2 Drawdown")
        ax2.plot(dates, dd, color="#dc2626", linewidth=1.2)
        ax2.set_ylabel("Drawdown (%)", fontsize=10)
        ax2.set_xlabel("Date", fontsize=10)
        ax2.yaxis.set_major_formatter(mticker.StrMethodFormatter("{x:.1f}%"))
        ax2.grid(True, linestyle=":", alpha=0.6)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150)
        plt.close(fig)
        return True
    except Exception as e:
        print(f"Warning: Failed to render performance chart: {e}", file=sys.stderr)
        return False


def generate_report(data_dir: Path | str = DEFAULT_DATA_DIR) -> tuple[str, Optional[Path]]:
    """Generate Markdown report and PNG chart from authoritative state."""
    d = Path(data_dir)
    state = load_state(d)
    export_ledgers(state, data_dir=d)

    equity_df = pd.DataFrame(state.get("equity_curve", []))
    trades = state.get("closed_trades", [])
    order_events = state.get("order_events", [])

    perf = compute_performance(equity_df, trades, order_events)
    report_md = generate_markdown_report(state, perf)

    md_path = d / "performance.md"
    md_path.write_text(report_md, encoding="utf-8")

    chart_path = d / "performance.png"
    has_chart = generate_performance_chart(equity_df, chart_path)

    return report_md, (chart_path if has_chart else None)


# =====================================================================
# Task 7: Telegram Notifications
# =====================================================================

def notify_telegram(
    message: str,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """Send Telegram message safely using standard library HTTP client."""
    bot_token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not cid:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": message,
        "parse_mode": "Markdown",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "tw_stocker_sim/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        # Fail softly without exposing bot token in logs
        print(f"Warning: Telegram notification failed: {e.__class__.__name__}", file=sys.stderr)
        return False


def expire_pending_orders(state: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
    """Expire pending orders whose execution_date is before as_of."""
    active_pending = []
    expired_events = []
    for order in state.get("pending_orders", []):
        exec_date = order.get("execution_date")
        if exec_date and exec_date < as_of:
            event = {
                "order_id": order["order_id"],
                "signal_date": order.get("signal_date", ""),
                "execution_date": exec_date,
                "event_time": get_taipei_now_iso(),
                "ticker": order["ticker"],
                "upstream_rank": order.get("upstream_rank"),
                "score": order.get("score"),
                "selection_mode": order.get("selection_mode", "auto"),
                "reference_close": order.get("reference_close", order.get("limit_price")),
                "limit_price": order.get("limit_price"),
                "open_price": None,
                "status": "CANCELLED_EXPIRED",
                "fill_price": None,
                "atr": order.get("atr"),
                "tp_price": None,
                "sl_price": None,
                "shares": 0,
                "message": f"Order expired before execution ({exec_date} < {as_of})",
            }
            state.setdefault("order_events", []).append(event)
            expired_events.append(event)
        else:
            active_pending.append(order)
    state["pending_orders"] = active_pending
    return expired_events


# =====================================================================
# Orchestrated CLI Subcommand Runners
# =====================================================================

def run_open(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    as_of: Optional[str] = None,
    notify: bool = False,
) -> None:
    """Run 09:35 open execution on trading day as_of."""
    today_str = as_of or get_taipei_today()
    if not is_trading_day(today_str):
        print(f"Notice: {today_str} is not an XTAI trading session. Skipping open.")
        return

    d = Path(data_dir)
    state = load_state(d)
    run_id = f"open:{today_str}"
    if is_run_processed(state, run_id):
        print(f"Notice: Run {run_id} was already processed. Idempotent skip.")
        return

    # Fetch fresh open bars for pending tickers
    due_tickers = list({o["ticker"] for o in state["pending_orders"] if o.get("execution_date") == today_str})
    bars = fetch_market_bars(due_tickers, as_of=today_str) if due_tickers else {}

    events = execute_open_orders(state, bars, as_of=today_str)
    mark_run_processed(state, run_id)
    save_state_atomic(state, data_dir=d)
    export_ledgers(state, data_dir=d)

    filled = [e for e in events if e.get("status") == "FILLED"]
    cancelled = [e for e in events if e.get("status") != "FILLED"]
    print(f"✅ Open execution completed for {today_str}: {len(filled)} filled, {len(cancelled)} cancelled/skipped.")

    if notify:
        msg_lines = [f"📊 *Top-2 Open Execution ({today_str})*"]
        for f_ev in filled:
            msg_lines.append(f"🟢 成交: `{f_ev['ticker']}` @ {f_ev['fill_price']:.2f} × {f_ev['shares']:,} 股")
        for c_ev in cancelled:
            msg_lines.append(f"⚪ 撤單/略過: `{c_ev['ticker']}` [{c_ev['status']}]")
        notify_telegram("\n".join(msg_lines))


def run_close_and_plan(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    orders_path: Optional[Path | str] = None,
    as_of: Optional[str] = None,
    max_price: Optional[float] = None,
    tickers: Optional[list[str]] = None,
    notify: bool = False,
) -> None:
    """Run 18:05 close settlement and order planning on trading day as_of."""
    today_str = as_of or get_taipei_today()
    if not is_trading_day(today_str):
        print(f"Notice: {today_str} is not an XTAI trading session. Skipping close-and-plan.")
        return

    d = Path(data_dir)
    state = load_state(d)
    run_id = f"close-and-plan:{today_str}"
    if is_run_processed(state, run_id):
        print(f"Notice: Run {run_id} was already processed. Idempotent skip.")
        return

    # 0. Expire outdated pending orders
    expired_events = expire_pending_orders(state, as_of=today_str)

    # 1. Settle existing positions (SL / TP / TIME)
    held_tickers = list(state["positions"].keys())
    bars = fetch_market_bars(held_tickers, as_of=today_str) if held_tickers else {}
    closed_trades = settle_positions(state, bars, as_of=today_str)

    # 2. Mark daily equity and 0050 benchmark
    remaining_tickers = list(state["positions"].keys())
    rem_bars = fetch_market_bars(remaining_tickers, as_of=today_str) if remaining_tickers else {}
    closes = {t: rem_bars[t]["close"] for t in remaining_tickers if t in rem_bars and rem_bars[t].get("close")}
    bm_close = fetch_benchmark_close(today_str) or (
        state["equity_curve"][-1]["benchmark_close"] if state["equity_curve"] else 150.0
    )
    mark_equity(state, closes=closes, benchmark_close=bm_close, as_of=today_str)

    # 3. Plan next session orders
    orders_file = None
    if orders_path:
        orders_file = Path(orders_path)
    else:
        compact_date = today_str.replace("-", "")
        strat_id = state.get("strategy_id", DEFAULT_STRATEGY_ID)
        strat_cfg = get_strategy_config(strat_id)
        candidate_files = [
            Path(strat_cfg.get("orders_dir", "artifacts")) / strat_cfg.get("orders_pattern", "orders_{date}.json").format(date=compact_date),
            Path("artifacts") / f"orders_{compact_date}.json",
            Path("artifacts") / strat_id / f"orders_{strat_id}_{compact_date}.json",
        ]
        for cf in candidate_files:
            if cf.exists():
                orders_file = cf
                break

    planned_count = 0
    if orders_file and orders_file.exists():
        orders = load_orders(orders_file)
        held_set = set(state["positions"].keys())
        pending_set = {p["ticker"] for p in state["pending_orders"]}
        eff_max_price = max_price if max_price is not None else state["config"].get("max_price")
        candidates = select_candidates(
            orders,
            held=held_set,
            pending=pending_set,
            max_picks=state["config"].get("max_positions", 2),
            max_price=eff_max_price,
            manual_tickers=tickers,
        )
        new_pending = plan_orders(state, candidates, as_of=today_str)
        planned_count = len(new_pending)

    mark_run_processed(state, run_id)
    save_state_atomic(state, data_dir=d)
    export_ledgers(state, data_dir=d)

    print(
        f"✅ Close-and-plan completed for {today_str}: {len(closed_trades)} positions closed, "
        f"{planned_count} new orders planned, {len(expired_events)} expired orders cancelled. "
        f"Equity: {state['equity_curve'][-1]['equity']:,.0f} TWD"
    )

    if notify:
        msg_lines = [
            f"📈 *Top-2 Daily Summary ({today_str})*",
            f"💰 權益: {state['equity_curve'][-1]['equity']:,.0f} TWD (現金: {state['cash']:,.0f} TWD)",
        ]
        for tr in closed_trades:
            msg_lines.append(f"🏁 平倉: `{tr['ticker']}` [{tr['exit_reason']}] PnL: {tr['net_pnl']:+,.0f} ({tr['net_return_pct']:+.2f}%)")
        if planned_count > 0:
            msg_lines.append(f"📝 明日委託: {planned_count} 檔已排定")
        notify_telegram("\n".join(msg_lines))


def run_report(data_dir: Path | str = DEFAULT_DATA_DIR, notify: bool = False) -> tuple[str, Optional[Path]]:
    """Regenerate performance report and chart."""
    report_md, chart_path = generate_report(data_dir=data_dir)
    print(report_md)
    if chart_path:
        print(f"\n📊 Performance chart saved to: {chart_path}")

    if notify:
        notify_telegram(report_md)

    return report_md, chart_path


def print_status(data_dir: Path | str = DEFAULT_DATA_DIR) -> None:
    """Print current simulation status to console."""
    state = load_state(data_dir)
    cfg = state["config"]
    print(f"=== Simulation Status [{state['strategy_id']}] ===")
    print(f"Initial Capital : {cfg['initial_capital']:,.0f} TWD")
    print(f"Current Cash    : {state['cash']:,.0f} TWD")
    print(f"Positions ({len(state['positions'])}/{cfg['max_positions']}):")
    for tkr, pos in state["positions"].items():
        print(f"  - {tkr}: entry={pos['entry']:.2f}, shares={pos['shares']:,}, TP={pos['tp']:.2f}, SL={pos['sl']:.2f}, days={pos['day_count']}")
    if not state["positions"]:
        print("  (None)")

    print(f"Pending Orders ({len(state['pending_orders'])}):")
    for p in state["pending_orders"]:
        print(f"  - {p['ticker']} (exec {p['execution_date']}): limit={p['limit_price']:.2f}, atr={p['atr']:.2f}")
    if not state["pending_orders"]:
        print("  (None)")

    print(f"Closed Trades   : {len(state.get('closed_trades', []))}")
    if state["equity_curve"]:
        last_eq = state["equity_curve"][-1]
        print(f"Latest Equity   : {last_eq['equity']:,.0f} TWD ({last_eq['cumulative_return']*100:+.2f}%) on {last_eq['date']}")


# =====================================================================
# CLI Parser
# =====================================================================

def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Independent Simulation CLI for tw_stocker strategies"
    )
    parser.add_argument(
        "--strategy", "-s",
        type=str,
        default=None,
        help="Strategy ID (top2_score_v1, mr20, rsi_reversal)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize simulation state")
    p_init.add_argument("--strategy", "-s", type=str, default=argparse.SUPPRESS, help="Strategy ID (top2_score_v1, mr20, rsi_reversal)")
    p_init.add_argument("--capital", type=float, default=None, help="Initial capital in TWD")
    p_init.add_argument("--max-price", type=float, default=None, help="Optional max price filter")
    p_init.add_argument("--position-size", type=float, default=None, help="Target position size fraction")
    p_init.add_argument("--reserve-ratio", type=float, default=None, help="Minimum cash reserve fraction")
    p_init.add_argument("--max-positions", type=int, default=None, help="Max positions count")
    p_init.add_argument("--max-hold-days", type=int, default=None, help="Max holding days")
    p_init.add_argument("--data-dir", type=str, default=None, help="Data directory")
    p_init.add_argument("--force", action="store_true", help="Force overwrite existing state")

    # close-and-plan
    p_cp = subparsers.add_parser("close-and-plan", help="Settle positions, mark equity, and plan next day orders")
    p_cp.add_argument("--strategy", "-s", type=str, default=argparse.SUPPRESS, help="Strategy ID (top2_score_v1, mr20, rsi_reversal)")
    p_cp.add_argument("--orders", type=str, default=None, help="Path to orders_YYYYMMDD.json artifact")
    p_cp.add_argument("--as-of", type=str, default=None, help="Evaluation date (YYYY-MM-DD), default today")
    p_cp.add_argument("--max-price", type=float, default=None, help="Optional max price override")
    p_cp.add_argument("--tickers", nargs="+", default=None, help="Optional manual tickers selection")
    p_cp.add_argument("--data-dir", type=str, default=None, help="Data directory")
    p_cp.add_argument("--notify", action="store_true", help="Send Telegram notification")

    # open
    p_open = subparsers.add_parser("open", help="Simulate 09:00-09:30 open limit order fills")
    p_open.add_argument("--strategy", "-s", type=str, default=argparse.SUPPRESS, help="Strategy ID (top2_score_v1, mr20, rsi_reversal)")
    p_open.add_argument("--as-of", type=str, default=None, help="Execution date (YYYY-MM-DD), default today")
    p_open.add_argument("--data-dir", type=str, default=None, help="Data directory")
    p_open.add_argument("--notify", action="store_true", help="Send Telegram notification")

    # report
    p_rep = subparsers.add_parser("report", help="Generate performance report, charts, and export CSVs")
    p_rep.add_argument("--strategy", "-s", type=str, default=argparse.SUPPRESS, help="Strategy ID (top2_score_v1, mr20, rsi_reversal)")
    p_rep.add_argument("--data-dir", type=str, default=None, help="Data directory")
    p_rep.add_argument("--notify", action="store_true", help="Send Telegram notification")

    # status
    p_stat = subparsers.add_parser("status", help="Print current status summary")
    p_stat.add_argument("--strategy", "-s", type=str, default=argparse.SUPPRESS, help="Strategy ID (top2_score_v1, mr20, rsi_reversal)")
    p_stat.add_argument("--data-dir", type=str, default=None, help="Data directory")

    return parser


def main() -> None:
    parser = build_cli_parser()
    args = parser.parse_args()

    # Resolve strategy & data_dir
    strategy = getattr(args, "strategy", None)
    sid = resolve_strategy_id(strategy)
    strat_cfg = get_strategy_config(sid)

    raw_dir = getattr(args, "data_dir", None)
    if raw_dir is not None:
        data_dir = raw_dir
    elif strategy is not None:
        data_dir = strat_cfg["default_data_dir"]
    else:
        data_dir = str(DEFAULT_DATA_DIR)

    if args.command == "init":
        init_simulation(
            data_dir=data_dir,
            capital=args.capital,
            max_price=args.max_price,
            position_size=args.position_size,
            reserve_ratio=args.reserve_ratio,
            max_positions=args.max_positions,
            max_hold_days=args.max_hold_days,
            strategy=sid,
            force=args.force,
        )
        print(f"Initialized simulation state for [{sid}] in {data_dir}.")
    elif args.command == "close-and-plan":
        run_close_and_plan(
            data_dir=data_dir,
            orders_path=args.orders,
            as_of=args.as_of,
            max_price=args.max_price,
            tickers=args.tickers,
            notify=args.notify,
        )
    elif args.command == "open":
        run_open(
            data_dir=data_dir,
            as_of=args.as_of,
            notify=args.notify,
        )
    elif args.command == "report":
        run_report(
            data_dir=data_dir,
            notify=args.notify,
        )
    elif args.command == "status":
        print_status(data_dir=data_dir)


if __name__ == "__main__":
    main()
