"""Unit and integration tests for RSI Reversal Strategy (strategy/rsi_reversal.py).

Tests follow TDD principles and run fully offline with deterministic fixtures.
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
from strategy.mr20_strategy import compute_wilder_rsi, compute_v85_atr20
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
    """Verify default RSI Reversal strategy configuration."""
    cfg = RSIReversalConfig()
    assert cfg.rsi_period == 5
    assert cfg.rsi_max == 20.0
    assert cfg.min_volume_ratio == 1.5
    assert cfg.ma_trend == 60
    assert cfg.max_hold_days == 5
    assert cfg.tp_pct == 0.06
    assert cfg.sl_pct == 0.03
    assert cfg.position_size == 0.15
    assert cfg.max_candidates == 5
    assert cfg.tp_sl_mode == "fixed_pct"
    assert cfg.model_version == "rsi_reversal_v1"


def test_rsi_reversal_selection_3_core_conditions():
    """Test the 3 core conditions: RSI(5) <= 20, Volume > 1.5*MA20, Close > MA60."""
    dates = pd.date_range("2026-01-01", periods=70, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")

    # 1. PASS: Long uptrend (Close > MA60), sudden deep oversold (RSI(5) <= 20), massive volume (> 1.5x MA20)
    # 65 days rising 50->150, then drops 150->110 (still > MA60 ~ 95), RSI5 drops to ~15
    p_pass = np.linspace(50.0, 150.0, 65).tolist() + [135.0, 125.0, 118.0, 112.0, 110.0]
    v_pass = [10_000_000.0] * 69 + [25_000_000.0]  # 2.5x MA20 on last day

    # 2. FAIL_RSI: Shallow dip, RSI(5) is ~40 > 20
    p_fail_rsi = np.linspace(50.0, 150.0, 65).tolist() + [149.0, 148.0, 147.0, 146.0, 148.0]
    v_fail_rsi = [10_000_000.0] * 69 + [25_000_000.0]

    # 3. FAIL_VOL: Low volume on signal day (0.8x MA20 <= 1.5x)
    p_fail_vol = p_pass
    v_fail_vol = [10_000_000.0] * 69 + [8_000_000.0]

    # 4. FAIL_MA60: Downtrend stock, Close < MA60
    p_fail_ma60 = np.linspace(150.0, 50.0, 65).tolist() + [45.0, 40.0, 35.0, 30.0, 28.0]
    v_fail_ma60 = [10_000_000.0] * 69 + [25_000_000.0]

    # 5. FAIL_LIQ: Low overall turnover (outside top 5)
    p_fail_liq = p_pass
    v_fail_liq = [10.0] * 70

    close_df = pd.DataFrame({
        "PASS": p_pass,
        "FAIL_RSI": p_fail_rsi,
        "FAIL_VOL": p_fail_vol,
        "FAIL_MA60": p_fail_ma60,
        "FAIL_LIQ": p_fail_liq,
    }, index=dates)

    vol_df = pd.DataFrame({
        "PASS": v_pass,
        "FAIL_RSI": v_fail_rsi,
        "FAIL_VOL": v_fail_vol,
        "FAIL_MA60": v_fail_ma60,
        "FAIL_LIQ": v_fail_liq,
    }, index=dates)

    cfg = RSIReversalConfig(liquidity_top_n=4, rsi_max=20.0, min_volume_ratio=1.5, min_history_days=60)
    candidates = filter_rsi_reversal_candidates(close_df, vol_df, as_of_date=sig_date, config=cfg)

    selected_tickers = [c["ticker"] for c in candidates]
    assert "PASS" in selected_tickers
    assert "FAIL_RSI" not in selected_tickers
    assert "FAIL_VOL" not in selected_tickers
    assert "FAIL_MA60" not in selected_tickers
    assert "FAIL_LIQ" not in selected_tickers
    assert len(selected_tickers) == 1


def test_rsi_reversal_ranking_and_tiebreaking():
    """Verify score = 100 - RSI(5), sorted descending by score, tiebreak by vol_ratio, then turnover, then ticker."""
    dates = pd.date_range("2026-01-01", periods=70, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")

    # Stock A: RSI5 ~ 11 (Score ~ 89), Vol ratio = 2.0x
    p_a = np.linspace(10.0, 150.0, 65).tolist() + [130.0, 120.0, 112.0, 105.0, 100.0]
    # Stock B: RSI5 ~ 16 (Score ~ 84), Vol ratio = 3.0x
    p_b = np.linspace(10.0, 150.0, 65).tolist() + [140.0, 134.0, 128.0, 124.0, 120.0]
    # Stock C: Same as B (RSI5 ~ 16), but Vol ratio = 2.0x
    p_c = list(p_b)
    # Stock D: Same as C (RSI5 ~ 16, Vol ratio = 2.0x), but higher turnover
    p_d = list(p_b)

    v_a = [10_000_000.0] * 69 + [20_000_000.0]  # 2.0x
    v_b = [10_000_000.0] * 69 + [30_000_000.0]  # 3.0x
    v_c = [10_000_000.0] * 69 + [20_000_000.0]  # 2.0x
    v_d = [20_000_000.0] * 69 + [40_000_000.0]  # 2.0x, higher turnover

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

    cfg = RSIReversalConfig(liquidity_top_n=10, max_candidates=5)
    candidates = filter_rsi_reversal_candidates(close_df, vol_df, as_of_date=sig_date, config=cfg)

    assert len(candidates) == 4
    # 2330 lowest RSI -> highest score -> Rank 1
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
# Order Generation & Schema Tests
# =====================================================================

def test_build_rsi_reversal_order_record():
    """Verify order record schema and fixed pct TP/SL."""
    cfg = RSIReversalConfig(tp_pct=0.06, sl_pct=0.03, max_hold_days=5, position_size=0.15)
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
    assert order["tp_pct"] == 0.06
    assert order["sl_pct"] == 0.03
    assert order["tp_price"] == 106.0
    assert order["sl_price"] == 97.0
    assert order["max_hold_days"] == 5
    assert order["position_size"] == 0.15
    assert order["tp_sl_mode"] == "fixed_pct"
    assert order["model_version"] == "rsi_reversal_v1"


def test_generate_rsi_reversal_orders_non_trading_day_raises(calendar):
    """Verify non-trading day raises ValueError."""
    dates = pd.date_range("2026-05-01", periods=70, freq="B")
    close_df = pd.DataFrame({"2330": np.linspace(100, 150, 70)}, index=dates)
    vol_df = pd.DataFrame({"2330": np.full(70, 10_000_000.0)}, index=dates)

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
    dates = pd.date_range("2026-05-01", periods=70, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")
    expected_exec = calendar.next_session(sig_date).strftime("%Y-%m-%d")

    p_pass = np.linspace(50.0, 150.0, 65).tolist() + [135.0, 125.0, 118.0, 112.0, 110.0]
    v_pass = [10_000_000.0] * 69 + [25_000_000.0]

    close_df = pd.DataFrame({"2330": p_pass}, index=dates)
    vol_df = pd.DataFrame({"2330": v_pass}, index=dates)

    orders_dict = generate_rsi_reversal_orders(close_df, vol_df, signal_date=sig_date, calendar=calendar)
    orders_file = temp_dir / f"orders_rsi_reversal_{sig_date.replace('-', '')}.json"
    save_orders_to_json(orders_dict, orders_file)

    # 1. Load orders
    loaded = sim.load_orders(orders_file, calendar=calendar)
    assert len(loaded) == 1
    assert loaded[0]["ticker"] == "2330"
    assert loaded[0]["tp_pct"] == 0.06
    assert loaded[0]["sl_pct"] == 0.03
    assert loaded[0]["max_hold_days"] == 5

    # 2. Select candidates
    candidates = sim.select_candidates(loaded, held=set(), pending=set(), max_picks=5)
    assert len(candidates) == 1

    # 3. Simulate in independent_sim
    rsi_data_dir = temp_dir / "independent_sim_data_rsi_reversal"
    sim.init_simulation(data_dir=rsi_data_dir, capital=1_000_000.0, position_size=0.15)

    mock_bars_sig = {
        "2330": {"date": sig_date, "open": 108.0, "high": 112.0, "low": 107.0, "close": 110.0},
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
        "2330": {"date": expected_exec, "open": 109.0, "high": 114.0, "low": 108.0, "close": 113.0},
    }
    with patch("independent_sim.fetch_market_bars", return_value=mock_bars_open):
        sim.run_open(data_dir=rsi_data_dir, as_of=expected_exec)

    state_after = sim.load_state(rsi_data_dir)
    assert "2330" in state_after["positions"]
    pos = state_after["positions"]["2330"]
    assert pos["entry"] == 109.0
    # TP = 109 * 1.06 = 115.54, SL = 109 * 0.97 = 105.73
    assert pytest.approx(pos["tp"], 0.01) == 109.0 * 1.06
    assert pytest.approx(pos["sl"], 0.01) == 109.0 * 0.97
    assert pos["max_hold_days"] == 5
