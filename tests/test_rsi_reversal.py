"""Unit and integration tests for RSI Reversal Strategy (strategy/rsi_reversal.py).

Tests follow TDD principles and run fully offline with deterministic fixtures.
Specifications: docs/INVESTMENT_STRATEGY.md §2.3 (RSI 14, MA 200, 3 triggers, ATR exit).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import exchange_calendars as xcals

from strategy.rsi_reversal import (
    RSIReversalConfig,
    build_rsi_reversal_order_record,
    compute_rsi_reversal_indicators,
    filter_rsi_reversal_candidates,
    generate_rsi_reversal_orders,
    get_next_trading_day,
    save_orders_to_json,
)
import independent_sim as sim


@pytest.fixture
def calendar():
    return xcals.get_calendar("XTAI")


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


# =====================================================================
# Indicator & Selection Condition Tests
# =====================================================================

def test_rsi_reversal_config_defaults():
    """Verify default RSI Reversal strategy configuration (INVESTMENT_STRATEGY.md §2.3)."""
    cfg = RSIReversalConfig()
    assert cfg.rsi_period == 14
    assert cfg.rsi_max == 28.0
    assert cfg.min_volume_ratio == 1.5
    assert cfg.ma_long == 200
    assert cfg.ma_trend == 200
    assert cfg.bb_period == 20
    assert cfg.bb_std == 2.0
    assert cfg.drop_5d_threshold == -0.12
    assert cfg.atr_period == 14
    assert cfg.tp_atr_mult == 2.0
    assert cfg.sl_atr_mult == 2.0
    assert cfg.max_hold_days == 5
    assert cfg.position_size == 0.15
    assert cfg.max_candidates == 5
    assert cfg.tp_sl_mode == "atr"
    assert cfg.model_version == "rsi_reversal_v1"


def test_rsi_reversal_selection_3_oversold_triggers():
    """Test the 3 oversold triggers: (a) RSI(14)<=28+Vol>1.5x, (b) Close<BB_Lower, (c) 5d drop<=-12%."""
    dates = pd.date_range("2025-01-01", periods=220, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")

    base = [50.0] * 180 + np.linspace(50.0, 150.0, 18).tolist()

    # 1. PASS_RSI: drops to 75 (still > MA200 ~ 61), RSI14 drops to ~22 <= 28, Volume 2.5x MA20
    p_pass_rsi = base + np.linspace(150.0, 75.0, 22).tolist()
    v_pass_rsi = [10_000_000.0] * 219 + [25_000_000.0]

    # 2. PASS_BB: drops suddenly to pierce Bollinger lower band (still > MA200)
    p_pass_bb = [100.0] * 210 + [150.0] * 5 + [149.0, 148.0, 147.0, 140.0, 110.0]
    v_pass_bb = [10_000_000.0] * 220

    # 3. PASS_DROP: 5-day drop from 150 to 125 (-16.6% <= -12%), still > MA200 ~ 100
    p_pass_drop = [100.0] * 210 + [150.0] * 5 + [145.0, 140.0, 135.0, 130.0, 125.0]
    v_pass_drop = [10_000_000.0] * 220

    # 4. FAIL_MA200: Downtrend stock, Close < MA200
    p_fail_ma200 = np.linspace(150.0, 50.0, 210).tolist() + [45.0, 40.0, 35.0, 30.0, 28.0, 26.0, 25.0, 24.0, 23.0, 22.0]
    v_fail_ma200 = [10_000_000.0] * 219 + [25_000_000.0]

    # 5. FAIL_NO_TRIGGER: Mild pullback (RSI ~ 45, within BB, drop -2%)
    p_fail_no_trig = [100.0] * 210 + [150.0] * 5 + [149.0, 149.0, 148.0, 148.0, 147.0]
    v_fail_no_trig = [10_000_000.0] * 220

    # 6. FAIL_LIQ: Low turnover (outside top 5)
    p_fail_liq = p_pass_rsi
    v_fail_liq = [10.0] * 220

    close_df = pd.DataFrame({
        "PASS_RSI": p_pass_rsi,
        "PASS_BB": p_pass_bb,
        "PASS_DROP": p_pass_drop,
        "FAIL_MA200": p_fail_ma200,
        "FAIL_NO_TRIG": p_fail_no_trig,
        "FAIL_LIQ": p_fail_liq,
    }, index=dates)

    vol_df = pd.DataFrame({
        "PASS_RSI": v_pass_rsi,
        "PASS_BB": v_pass_bb,
        "PASS_DROP": v_pass_drop,
        "FAIL_MA200": v_fail_ma200,
        "FAIL_NO_TRIG": v_fail_no_trig,
        "FAIL_LIQ": v_fail_liq,
    }, index=dates)

    cfg = RSIReversalConfig(liquidity_top_n=5, min_history_days=200)
    open_df = close_df * 0.98  # All candles bullish (close > open)
    candidates = filter_rsi_reversal_candidates(close_df, vol_df, open_df=open_df, as_of_date=sig_date, config=cfg)

    selected_tickers = [c["ticker"] for c in candidates]
    assert "PASS_RSI" in selected_tickers
    assert "PASS_BB" in selected_tickers
    assert "PASS_DROP" in selected_tickers
    assert "FAIL_MA200" not in selected_tickers
    assert "FAIL_NO_TRIG" not in selected_tickers
    assert "FAIL_LIQ" not in selected_tickers
    assert len(selected_tickers) == 3


def test_rsi_reversal_ranking_and_tiebreaking():
    """Verify score weighting and tiebreak by vol_ratio, then turnover, then ticker."""
    dates = pd.date_range("2025-01-01", periods=220, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")

    base = [30.0] * 180 + np.linspace(30.0, 150.0, 18).tolist()

    # Stock A: deep oversold (RSI14 ~ 24, drop to 70)
    p_a = base + np.linspace(150.0, 70.0, 22).tolist()
    # Stock B: RSI14 ~ 27, drop to 80, Vol ratio = 3.0x
    p_b = base + np.linspace(150.0, 80.0, 22).tolist()
    # Stock C: Same price as B, Vol ratio = 2.0x
    p_c = list(p_b)
    # Stock D: Same price as C, higher turnover
    p_d = list(p_b)

    v_a = [50_000_000.0] * 219 + [50_000_000.0]
    v_b = [20_000_000.0] * 219 + [60_000_000.0]  # vol ratio 3.0x
    v_c = [20_000_000.0] * 219 + [20_000_000.0]  # vol ratio 1.0x
    v_d = [20_000_000.0] * 219 + [40_000_000.0]  # vol ratio 2.0x, higher turnover than C

    close_df = pd.DataFrame({
        "2330": p_a,
        "2454": p_b,
        "2317": p_c,
        "3008": p_d,
    }, index=dates)

    vol_df = pd.DataFrame({
        "2330": v_a,
        "2454": v_b,
        "2317": v_c,
        "3008": v_d,
    }, index=dates)

    open_df = close_df * 0.98
    cfg = RSIReversalConfig(liquidity_top_n=10, max_candidates=5, min_history_days=200)
    candidates = filter_rsi_reversal_candidates(close_df, vol_df, open_df=open_df, as_of_date=sig_date, config=cfg)

    assert len(candidates) == 4
    # 2330 lowest RSI & biggest drop -> highest score -> Rank 1
    assert candidates[0]["ticker"] == "2330"
    assert candidates[0]["rank"] == 1

    # 2454 higher vol ratio (3.0x vs 2.0x) -> Rank 2
    assert candidates[1]["ticker"] == "2454"
    assert candidates[1]["rank"] == 2

    # 3008 higher turnover than 2317 -> Rank 3
    assert candidates[2]["ticker"] == "3008"
    assert candidates[2]["rank"] == 3

    # 2317 -> Rank 4
    assert candidates[3]["ticker"] == "2317"
    assert candidates[3]["rank"] == 4


# =====================================================================
# P1-1, P1-2, New-3 Validation Tests
# =====================================================================

def test_rsi_reversal_open_df_missing_rejected():
    """P1-1: Candidates are rejected when open_df is None, lacks ticker, or contains NaN."""
    dates = pd.date_range("2025-01-01", periods=220, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")

    base = [50.0] * 180 + np.linspace(50.0, 150.0, 18).tolist()
    p_pass = base + np.linspace(150.0, 75.0, 22).tolist()
    v_pass = [10_000_000.0] * 219 + [25_000_000.0]

    close_df = pd.DataFrame({"2330": p_pass, "2454": p_pass}, index=dates)
    vol_df = pd.DataFrame({"2330": v_pass, "2454": v_pass}, index=dates)
    cfg = RSIReversalConfig(liquidity_top_n=5, min_history_days=200)

    # 1. open_df is None -> rejected
    cands_none = filter_rsi_reversal_candidates(close_df, vol_df, open_df=None, as_of_date=sig_date, config=cfg)
    assert len(cands_none) == 0

    # 2. open_df missing ticker "2454" -> only "2330" considered (if 2330 has bullish candle)
    open_df_partial = pd.DataFrame({"2330": close_df["2330"] * 0.98}, index=dates)
    cands_partial = filter_rsi_reversal_candidates(close_df, vol_df, open_df=open_df_partial, as_of_date=sig_date, config=cfg)
    assert len(cands_partial) == 1
    assert cands_partial[0]["ticker"] == "2330"

    # 3. open_df has NaN on signal date -> rejected
    open_df_nan = close_df * 0.98
    open_df_nan.loc[sig_date, "2330"] = np.nan
    cands_nan = filter_rsi_reversal_candidates(close_df, vol_df, open_df=open_df_nan, as_of_date=sig_date, config=cfg)
    assert "2330" not in [c["ticker"] for c in cands_nan]

    # 4. Bearish candle (Close <= Open) -> rejected
    open_df_bearish = close_df * 1.02
    cands_bearish = filter_rsi_reversal_candidates(close_df, vol_df, open_df=open_df_bearish, as_of_date=sig_date, config=cfg)
    assert len(cands_bearish) == 0


def test_rsi_reversal_strict_volume_ratio():
    """P1-2: Volume trigger requires strictly Volume > 1.5 * VolMA20 (not >=)."""
    dates = pd.date_range("2025-01-01", periods=220, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")

    base = [50.0] * 180 + np.linspace(50.0, 150.0, 18).tolist()
    p_pass = base + np.linspace(150.0, 75.0, 22).tolist()

    close_df = pd.DataFrame({"2330": p_pass}, index=dates)
    open_df = close_df * 0.98

    # 219 days of 10M volume.
    # Today 15.4M -> vol_ratio = 20 * 15.4 / (190 + 15.4) = 1.4995 <= 1.50
    vol_df_lte = pd.DataFrame({"2330": [10_000_000.0] * 219 + [15_400_000.0]}, index=dates)
    # Today 15.5M -> vol_ratio = 20 * 15.5 / (190 + 15.5) = 1.5085 > 1.50
    vol_df_gt = pd.DataFrame({"2330": [10_000_000.0] * 219 + [15_500_000.0]}, index=dates)

    # Disable BB and 5d triggers to isolate trig_a
    cfg = RSIReversalConfig(
        liquidity_top_n=5,
        min_history_days=200,
        drop_5d_threshold=-0.50,  # disable 5d drop trigger
        bb_std=10.0,  # disable BB trigger
        min_volume_ratio=1.5,
    )

    cands_lte = filter_rsi_reversal_candidates(close_df, vol_df_lte, open_df=open_df, as_of_date=sig_date, config=cfg)
    assert len(cands_lte) == 0, "Volume ratio <= 1.5 should NOT pass strict > 1.5"

    cands_gt = filter_rsi_reversal_candidates(close_df, vol_df_gt, open_df=open_df, as_of_date=sig_date, config=cfg)
    assert len(cands_gt) == 1, "Strictly > 1.5x should pass"


def test_rsi_reversal_rank_based_turnover_scoring():
    """New-3: Turnover scoring uses normalized rank within liquid universe, not raw turnover."""
    dates = pd.date_range("2025-01-01", periods=220, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")

    base = [50.0] * 180 + np.linspace(50.0, 150.0, 18).tolist()
    p = base + np.linspace(150.0, 75.0, 22).tolist()

    # Two identical stocks in terms of RSI and 5d drop, differing only in turnover
    close_df = pd.DataFrame({"STOCK_HI": p, "STOCK_LO": p}, index=dates)
    vol_df = pd.DataFrame({
        "STOCK_HI": [10_000_000.0] * 219 + [25_000_000.0],
        "STOCK_LO": [1_000_000.0] * 219 + [2_500_000.0],
    }, index=dates)
    open_df = close_df * 0.98

    cfg = RSIReversalConfig(liquidity_top_n=2, min_history_days=200)
    candidates = filter_rsi_reversal_candidates(close_df, vol_df, open_df=open_df, as_of_date=sig_date, config=cfg)

    assert len(candidates) == 2
    # In 2-item universe:
    # STOCK_HI is rank 0 -> turnover_score = (1 - 0/2)*100 = 100.0 (30.0 pts)
    # STOCK_LO is rank 1 -> turnover_score = (1 - 1/2)*100 = 50.0 (15.0 pts)
    hi_cand = [c for c in candidates if c["ticker"] == "STOCK_HI"][0]
    lo_cand = [c for c in candidates if c["ticker"] == "STOCK_LO"][0]
    assert hi_cand["score"] > lo_cand["score"]
    assert pytest.approx(hi_cand["score"] - lo_cand["score"], 0.01) == 15.0


# =====================================================================
# Order Generation & Schema Tests
# =====================================================================

def test_build_rsi_reversal_order_record():
    """Verify order record schema and ATR TP/SL (Entry ± 2.0 * ATR14)."""
    cfg = RSIReversalConfig(tp_atr_mult=2.0, sl_atr_mult=2.0, max_hold_days=5, position_size=0.15)
    order = build_rsi_reversal_order_record(
        signal_date="2026-08-20",
        execution_date="2026-08-21",
        ticker="2330",
        rank=1,
        score=85.0,
        reference_close=100.0,
        atr=3.5,
        config=cfg,
    )

    assert order["signal_date"] == "2026-08-20"
    assert order["execution_date"] == "2026-08-21"
    assert order["ticker"] == "2330"
    assert order["side"] == "buy"
    assert order["order_type"] == "limit"
    assert order["limit_price"] == 100.0
    assert order["reference_close"] == 100.0
    assert order["rank"] == 1
    assert order["score"] == 85.0
    assert order["tp_atr_mult"] == 2.0
    assert order["sl_atr_mult"] == 2.0
    # TP = 100 + 2.0 * 3.5 = 107.0, SL = 100 - 2.0 * 3.5 = 93.0
    assert order["tp_price"] == 107.0
    assert order["sl_price"] == 93.0
    assert order["max_hold_days"] == 5
    assert order["position_size"] == 0.15
    assert order["tp_sl_mode"] == "atr"
    assert order["model_version"] == "rsi_reversal_v1"


def test_generate_rsi_reversal_orders_non_trading_day_raises(calendar):
    """Verify non-trading day raises ValueError."""
    dates = pd.date_range("2025-01-01", periods=220, freq="B")
    close_df = pd.DataFrame({"2330": np.linspace(100, 150, 220)}, index=dates)
    vol_df = pd.DataFrame({"2330": np.full(220, 10_000_000.0)}, index=dates)

    # 2026-08-15 is Saturday
    with pytest.raises(ValueError, match="not a valid XTAI trading session"):
        generate_rsi_reversal_orders(close_df, vol_df, signal_date="2026-08-15", calendar=calendar)


def test_rsi_reversal_cli_help():
    """Verify python3 strategy/rsi_reversal.py --help runs successfully."""
    cmd = [sys.executable, "strategy/rsi_reversal.py", "--help"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    assert res.returncode == 0
    assert "RSI" in res.stdout or "reversal" in res.stdout.lower()


# =====================================================================
# Compatibility with independent_sim.py
# =====================================================================

def test_rsi_reversal_orders_compatible_with_independent_sim(temp_dir, calendar):
    """Verify that orders generated by rsi_reversal.py work with independent_sim.py."""
    dates = pd.date_range("2025-01-01", periods=220, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")
    expected_exec = calendar.next_session(sig_date).strftime("%Y-%m-%d")

    base = [50.0] * 180 + np.linspace(50.0, 150.0, 18).tolist()
    p_pass = base + np.linspace(150.0, 75.0, 22).tolist()
    v_pass = [10_000_000.0] * 219 + [25_000_000.0]

    close_df = pd.DataFrame({"2330": p_pass}, index=dates)
    vol_df = pd.DataFrame({"2330": v_pass}, index=dates)
    open_df = close_df * 0.98

    orders_dict = generate_rsi_reversal_orders(close_df, vol_df, open_df=open_df, signal_date=sig_date, calendar=calendar)
    orders_file = temp_dir / f"orders_rsi_reversal_{sig_date.replace('-', '')}.json"
    save_orders_to_json(orders_dict, orders_file)

    # 1. Load orders
    loaded = sim.load_orders(orders_file, calendar=calendar)
    assert len(loaded) == 1
    assert loaded[0]["ticker"] == "2330"
    assert loaded[0]["tp_atr_mult"] == 2.0
    assert loaded[0]["sl_atr_mult"] == 2.0
    assert loaded[0]["max_hold_days"] == 5

    # 2. Select candidates
    candidates = sim.select_candidates(loaded, held=set(), pending=set(), max_picks=5)
    assert len(candidates) == 1

    # 3. Simulate in independent_sim
    rsi_data_dir = temp_dir / "independent_sim_data_rsi_reversal"
    sim.init_simulation(data_dir=rsi_data_dir, strategy="rsi_reversal")

    mock_bars_sig = {
        "2330": {"date": sig_date, "open": 74.0, "high": 78.0, "low": 72.0, "close": 75.0},
    }
    with patch("independent_sim.fetch_market_bars", return_value=mock_bars_sig), \
         patch("independent_sim.fetch_benchmark_close", return_value=150.0):
        sim.run_close_and_plan(
            data_dir=rsi_data_dir,
            orders_path=orders_file,
            as_of=sig_date,
        )

    state = sim.load_state(rsi_data_dir)
    assert len(state["pending_orders"]) == 1
    assert state["pending_orders"][0]["ticker"] == "2330"

    # Open execution
    mock_bars_open = {
        "2330": {"date": expected_exec, "open": 74.0, "high": 80.0, "low": 73.0, "close": 78.0},
    }
    with patch("independent_sim.fetch_market_bars", return_value=mock_bars_open):
        sim.run_open(data_dir=rsi_data_dir, as_of=expected_exec)

    state_after = sim.load_state(rsi_data_dir)
    assert "2330" in state_after["positions"]
    pos = state_after["positions"]["2330"]
    assert pos["entry"] == 74.0
    # TP/SL recomputed at open based on ATR (tp_atr_mult=2.0, sl_atr_mult=2.0)
    atr_val = state["pending_orders"][0]["atr"]
    assert pytest.approx(pos["tp"], 0.01) == 74.0 + 2.0 * atr_val
    assert pytest.approx(pos["sl"], 0.01) == 74.0 - 2.0 * atr_val
    assert pos["max_hold_days"] == 5
