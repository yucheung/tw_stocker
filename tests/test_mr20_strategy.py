"""Unit and integration tests for MR20 Pullback Strategy (strategy/mr20_strategy.py).

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

from strategy.mr20_strategy import (
    MR20Config,
    build_mr20_order_record,
    compute_mr20_indicators,
    compute_v85_atr20,
    compute_wilder_rsi,
    filter_mr20_candidates,
    generate_mr20_orders,
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
# Indicator Tests: Wilder RSI & ATR20
# =====================================================================

def test_compute_wilder_rsi_basic():
    """Verify Wilder RSI calculation matches standard exponential smoothing."""
    # 10 days of prices: 5 up moves then 4 down moves
    prices = pd.Series([100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 105.0, 100.0, 95.0, 90.0])
    rsi = compute_wilder_rsi(prices, period=5)
    
    # First 5 diffs (indices 0..5) -> index 5 is first valid RSI
    assert pd.isna(rsi.iloc[4])
    assert not pd.isna(rsi.iloc[5])
    # At index 5: all gains -> RSI should be 100.0
    assert pytest.approx(rsi.iloc[5], 0.01) == 100.0
    # At index 9: strong down moves -> RSI should be low (< 35)
    assert rsi.iloc[9] < 35.0


def test_compute_wilder_rsi_all_losses_and_flat():
    """Test edge cases: all losses -> 0, flat price -> 50."""
    # All losses
    down_prices = pd.Series([100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0])
    rsi_down = compute_wilder_rsi(down_prices, period=5)
    assert pytest.approx(rsi_down.iloc[5], 0.01) == 0.0
    
    # Flat prices
    flat_prices = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    rsi_flat = compute_wilder_rsi(flat_prices, period=5)
    assert pytest.approx(rsi_flat.iloc[5], 0.01) == 50.0


def test_compute_wilder_rsi_dataframe():
    """Verify compute_wilder_rsi works across a 2D DataFrame."""
    dates = pd.date_range("2026-01-01", periods=10, freq="B")
    df = pd.DataFrame({
        "A": [10, 11, 12, 13, 14, 15, 14, 13, 12, 11],
        "B": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    }, index=dates)
    rsi_df = compute_wilder_rsi(df, period=5)
    assert rsi_df.shape == df.shape
    assert pd.isna(rsi_df.iloc[4, 0])
    assert not pd.isna(rsi_df.iloc[5, 0])
    assert rsi_df.iloc[9]["B"] < 5.0


def test_compute_v85_atr20():
    """Verify close-based ATR20 definition: mean(abs(pct_change), 20) * Close."""
    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    # Constant 1% daily move
    prices = [100.0 * (1.01 ** i) for i in range(25)]
    df = pd.DataFrame({"2330": prices}, index=dates)
    
    atr_df = compute_v85_atr20(df, period=20)
    assert pd.isna(atr_df.iloc[19]["2330"])
    # On day 20, pct_change is exactly 0.01 each day, so ATR20 is 0.01 * Close[20]
    expected_atr = 0.01 * df.iloc[20]["2330"]
    assert pytest.approx(atr_df.iloc[20]["2330"], rel=1e-3) == expected_atr


# =====================================================================
# MR20 5 Core Selection Conditions Tests
# =====================================================================

def test_mr20_selection_5_conditions():
    """Test that all 5 conditions must be met for a stock to qualify."""
    # 70 trading days to satisfy min_history_days >= 60
    dates = pd.date_range("2026-01-01", periods=70, freq="B")
    
    # We will construct 6 test stocks:
    # 1. 'PASS': Meets all 5 conditions
    #    - High turnover (top 50)
    #    - MA20 > MA60, Close > MA60
    #    - Close < MA20
    #    - RSI(5) <= 35
    #    - Close[t] > Close[t-1]
    # 2. 'FAIL_LIQ': Low volume (outside top 5)
    # 3. 'FAIL_BULL': MA20 < MA60 (downtrend)
    # 4. 'FAIL_PB': Close >= MA20 (strong uptrend / momentum)
    # 5. 'FAIL_OS': RSI(5) > 35 (e.g. 50+)
    # 6. 'FAIL_REB': Close[t] <= Close[t-1] (falling knife, no bounce)
    
    tickers = ['PASS', 'FAIL_LIQ', 'FAIL_BULL', 'FAIL_PB', 'FAIL_OS', 'FAIL_REB']
    
    close_dict = {}
    vol_dict = {}
    
    # PASS: Uptrend from 60 to 180 over 65 days, then pullback to 132 and bounce to 136
    p_pass = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 132.0, 136.0]
    close_dict['PASS'] = np.array(p_pass)
    vol_dict['PASS'] = np.full(70, 100_000_000.0)
    
    # FAIL_LIQ: exact same prices as PASS, but tiny volume so it ranks low in turnover
    close_dict['FAIL_LIQ'] = np.array(p_pass)
    vol_dict['FAIL_LIQ'] = np.full(70, 1.0)
    
    # FAIL_BULL: downtrend from 200 to 100 (MA20 < MA60)
    p_bear = np.linspace(200.0, 100.0, 65).tolist() + [90.0, 80.0, 70.0, 65.0, 68.0]
    close_dict['FAIL_BULL'] = np.array(p_bear)
    vol_dict['FAIL_BULL'] = np.full(70, 100_000_000.0)
    
    # FAIL_PB: continues straight up, Close[t] > MA20 (not pulled back)
    p_up = np.linspace(80.0, 200.0, 70)
    close_dict['FAIL_PB'] = p_up
    vol_dict['FAIL_PB'] = np.full(70, 100_000_000.0)
    
    # FAIL_OS: shallow pullback, RSI(5) stays above 35
    p_shallow = np.linspace(60.0, 180.0, 65).tolist() + [179.0, 178.0, 177.0, 176.0, 178.0]
    close_dict['FAIL_OS'] = np.array(p_shallow)
    vol_dict['FAIL_OS'] = np.full(70, 100_000_000.0)
    
    # FAIL_REB: still dropping on day t (Close[t] <= Close[t-1])
    p_noreb = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 132.0, 128.0]
    close_dict['FAIL_REB'] = np.array(p_noreb)
    vol_dict['FAIL_REB'] = np.full(70, 100_000_000.0)
    
    close_df = pd.DataFrame(close_dict, index=dates)
    vol_df = pd.DataFrame(vol_dict, index=dates)
    
    # Top 5 by turnover keeps PASS, FAIL_BULL, FAIL_PB, FAIL_OS, FAIL_REB; excludes FAIL_LIQ (#6)
    config = MR20Config(liquidity_top_n=5, rsi_max=35.0, min_history_days=60)
    
    signal_date = dates[-1].strftime("%Y-%m-%d")
    candidates = filter_mr20_candidates(close_df, vol_df, as_of_date=signal_date, config=config)
    
    selected_tickers = [c["ticker"] for c in candidates]
    assert "PASS" in selected_tickers
    assert "FAIL_LIQ" not in selected_tickers
    assert "FAIL_BULL" not in selected_tickers
    assert "FAIL_PB" not in selected_tickers
    assert "FAIL_OS" not in selected_tickers
    assert "FAIL_REB" not in selected_tickers
    assert len(selected_tickers) == 1


def test_mr20_min_history_filter():
    """Stocks with fewer than 60 days of history are excluded."""
    dates = pd.date_range("2026-01-01", periods=50, freq="B")
    close_df = pd.DataFrame({"2330": np.linspace(100, 150, 50)}, index=dates)
    vol_df = pd.DataFrame({"2330": np.full(50, 1_000_000.0)}, index=dates)
    
    config = MR20Config(min_history_days=60)
    candidates = filter_mr20_candidates(close_df, vol_df, as_of_date=dates[-1].strftime("%Y-%m-%d"), config=config)
    assert len(candidates) == 0


def test_mr20_optional_distance_and_price_filters():
    """Test optional ma20_dist_min, ma20_dist_max, min_price, max_price."""
    dates = pd.date_range("2026-01-01", periods=70, freq="B")
    
    # PASS price series: MA20 is around 161, Close is 136 -> dist is (136-161)/161 = -15.5%
    p_pass = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 132.0, 136.0]
    close_df = pd.DataFrame({"2330": p_pass}, index=dates)
    vol_df = pd.DataFrame({"2330": np.full(70, 10_000_000.0)}, index=dates)
    
    # 1. Price filter: min_price=200 should reject
    cfg_price = MR20Config(min_price=200.0)
    res = filter_mr20_candidates(close_df, vol_df, as_of_date=dates[-1].strftime("%Y-%m-%d"), config=cfg_price)
    assert len(res) == 0
    
    # 2. Distance filter: ma20_dist_min=-0.10 (-10%) should reject if pullback is -15.5%
    cfg_dist = MR20Config(ma20_dist_min=-0.10)
    res_dist = filter_mr20_candidates(close_df, vol_df, as_of_date=dates[-1].strftime("%Y-%m-%d"), config=cfg_dist)
    assert len(res_dist) == 0
    
    # 3. Distance filter matching range: ma20_dist_min=-0.25, ma20_dist_max=-0.05 should pass
    cfg_dist_pass = MR20Config(ma20_dist_min=-0.25, ma20_dist_max=-0.05)
    res_pass = filter_mr20_candidates(close_df, vol_df, as_of_date=dates[-1].strftime("%Y-%m-%d"), config=cfg_dist_pass)
    assert len(res_pass) == 1


# =====================================================================
# Ranking and Score Tests
# =====================================================================

def test_mr20_ranking_score_and_tiebreaking():
    """Verify score = 100 - RSI(5), sorted descending by score, tiebreak by turnover then ticker."""
    dates = pd.date_range("2026-01-01", periods=70, freq="B")
    
    # Stock 1 (RSI ~ 19.5 -> Score ~ 80.5)
    p_a = np.linspace(60.0, 180.0, 65).tolist() + [155.0, 142.0, 133.0, 130.0, 133.0]
    # Stock 2 (RSI ~ 23.0 -> Score ~ 77.0)
    p_b = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 135.0, 139.0]
    
    close_df = pd.DataFrame({
        "2330": p_a,
        "2317": p_b,
        "2454": p_b,
        "1101": p_b,
    }, index=dates)
    
    vol_df = pd.DataFrame({
        "2330": np.full(70, 10_000_000.0),
        "2317": np.full(70, 50_000_000.0),
        "2454": np.full(70, 20_000_000.0),
        "1101": np.full(70, 20_000_000.0),
    }, index=dates)
    
    config = MR20Config(liquidity_top_n=10, max_candidates=7)
    candidates = filter_mr20_candidates(close_df, vol_df, as_of_date=dates[-1].strftime("%Y-%m-%d"), config=config)
    
    assert len(candidates) == 4
    # 2330 should be rank 1 (lowest RSI -> highest score)
    assert candidates[0]["ticker"] == "2330"
    assert candidates[0]["rank"] == 1
    assert candidates[0]["score"] > candidates[1]["score"]
    
    # 2317 should be rank 2 (higher turnover among same RSI)
    assert candidates[1]["ticker"] == "2317"
    assert candidates[1]["rank"] == 2
    
    # 1101 should be rank 3 (alphabetical order before 2454)
    assert candidates[2]["ticker"] == "1101"
    assert candidates[2]["rank"] == 3
    
    # 2454 should be rank 4
    assert candidates[3]["ticker"] == "2454"
    assert candidates[3]["rank"] == 4


def test_mr20_max_candidates_cap():
    """Verify strategy outputs at most max_candidates (default 7)."""
    dates = pd.date_range("2026-01-01", periods=70, freq="B")
    p_pass = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 132.0, 136.0]
    
    tickers = [f"T{i:02d}" for i in range(10)]
    close_df = pd.DataFrame({t: p_pass for t in tickers}, index=dates)
    vol_df = pd.DataFrame({t: np.full(70, 10_000_000.0 + i * 1000) for i, t in enumerate(tickers)}, index=dates)
    
    config = MR20Config(liquidity_top_n=50, max_candidates=7)
    candidates = filter_mr20_candidates(close_df, vol_df, as_of_date=dates[-1].strftime("%Y-%m-%d"), config=config)
    assert len(candidates) == 7
    assert [c["rank"] for c in candidates] == list(range(1, 8))


# =====================================================================
# Order Record & JSON Generation Tests
# =====================================================================

def test_build_mr20_order_record():
    """Verify order record schema matches independent_sim expectations."""
    config = MR20Config(tp_atr_mult=4.0, sl_atr_mult=3.0, max_hold_days=20, position_size=0.45)
    order = build_mr20_order_record(
        signal_date="2026-08-19",
        execution_date="2026-08-20",
        ticker="2330",
        rank=1,
        score=72.5,
        reference_close=1180.0,
        atr=25.0,
        config=config,
    )
    
    assert order["signal_date"] == "2026-08-19"
    assert order["execution_date"] == "2026-08-20"
    assert order["ticker"] == "2330"
    assert order["side"] == "buy"
    assert order["rank"] == 1
    assert order["score"] == 72.5
    assert order["reference_close"] == 1180.0
    assert order["limit_price"] == 1180.0
    assert order["atr"] == 25.0
    assert order["tp_price"] == 1180.0 + 4.0 * 25.0
    assert order["sl_price"] == 1180.0 - 3.0 * 25.0
    assert order["tp_atr_mult"] == 4.0
    assert order["sl_atr_mult"] == 3.0
    assert order["max_hold_days"] == 20
    assert order["order_type"] == "limit"
    assert order["entry_model"] == "signal_close_limit_next_open_v1"
    assert order["time_in_force"] == "DAY_UNTIL_0930"
    assert order["cancel_time"] == "09:30:00"
    assert order["timezone"] == "Asia/Taipei"
    assert order["model_version"] == "mr20_pullback_v1"


def test_generate_mr20_orders_with_calendar(calendar):
    """Test generate_mr20_orders returns valid dict with next XTAI trading session."""
    dates = pd.date_range("2026-05-01", periods=70, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")
    p_pass = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 132.0, 136.0]
    
    close_df = pd.DataFrame({"2330": p_pass}, index=dates)
    vol_df = pd.DataFrame({"2330": np.full(70, 10_000_000.0)}, index=dates)
    
    orders_data = generate_mr20_orders(close_df, vol_df, signal_date=sig_date, calendar=calendar)
    assert "orders" in orders_data
    assert len(orders_data["orders"]) == 1
    order = orders_data["orders"][0]
    assert order["signal_date"] == sig_date
    expected_exec = calendar.next_session(sig_date).strftime("%Y-%m-%d")
    assert order["execution_date"] == expected_exec
    assert order["ticker"] == "2330"


# =====================================================================
# Compatibility with independent_sim.py Tests
# =====================================================================

def test_mr20_orders_compatible_with_independent_sim(temp_dir, calendar):
    """Verify that orders generated by mr20_strategy.py can be directly consumed by independent_sim.py."""
    dates = pd.date_range("2026-05-01", periods=70, freq="B")
    sig_date = dates[-1].strftime("%Y-%m-%d")
    expected_exec = calendar.next_session(sig_date).strftime("%Y-%m-%d")
    
    p_pass1 = np.linspace(60.0, 180.0, 65).tolist() + [155.0, 142.0, 133.0, 130.0, 133.0]
    p_pass2 = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 135.0, 139.0]
    
    close_df = pd.DataFrame({"2330": p_pass1, "2454": p_pass2}, index=dates)
    vol_df = pd.DataFrame({"2330": np.full(70, 50_000_000.0), "2454": np.full(70, 40_000_000.0)}, index=dates)
    
    orders_dict = generate_mr20_orders(close_df, vol_df, signal_date=sig_date, calendar=calendar)
    orders_file = temp_dir / f"orders_mr20_{sig_date.replace('-', '')}.json"
    save_orders_to_json(orders_dict, orders_file)
    
    # 1. Test independent_sim.load_orders
    loaded = sim.load_orders(orders_file, calendar=calendar)
    assert len(loaded) == 2
    assert loaded[0]["ticker"] in ["2330", "2454"]
    assert loaded[0]["signal_date"] == sig_date
    assert loaded[0]["execution_date"] == expected_exec
    
    # 2. Test independent_sim.select_candidates
    candidates = sim.select_candidates(loaded, held=set(), pending=set(), max_picks=2)
    assert len(candidates) == 2
    
    # 3. Test independent_sim full simulation lifecycle with independent_sim_data_mr20
    mr20_data_dir = temp_dir / "independent_sim_data_mr20"
    sim.init_simulation(data_dir=mr20_data_dir, capital=1_000_000.0, position_size=0.40)
    
    # Close-and-plan for signal date
    mock_bars_sig = {
        "2330": {"date": sig_date, "open": 130.0, "high": 135.0, "low": 129.0, "close": 133.0},
        "2454": {"date": sig_date, "open": 136.0, "high": 140.0, "low": 134.0, "close": 139.0},
    }
    with patch("independent_sim.fetch_market_bars", return_value=mock_bars_sig), \
         patch("independent_sim.fetch_benchmark_close", return_value=150.0):
        sim.run_close_and_plan(
            data_dir=mr20_data_dir,
            orders_path=orders_file,
            as_of=sig_date,
        )
        
    state = sim.load_state(mr20_data_dir)
    assert len(state["pending_orders"]) == 2
    assert {p["ticker"] for p in state["pending_orders"]} == {"2330", "2454"}
    
    # Open for expected_exec (both open at/below limit price -> both fill)
    mock_bars_open = {
        "2330": {"date": expected_exec, "open": 132.0, "high": 136.0, "low": 131.0, "close": 135.0},
        "2454": {"date": expected_exec, "open": 138.0, "high": 142.0, "low": 137.0, "close": 141.0},
    }
    with patch("independent_sim.fetch_market_bars", return_value=mock_bars_open):
        sim.run_open(data_dir=mr20_data_dir, as_of=expected_exec)
        
    state_after_open = sim.load_state(mr20_data_dir)
    assert len(state_after_open["positions"]) == 2
    assert "2330" in state_after_open["positions"]
    assert "2454" in state_after_open["positions"]
    assert len(state_after_open["pending_orders"]) == 0
    
    # Generate report
    report_md, chart_p = sim.generate_report(data_dir=mr20_data_dir)
    assert "top2_score_v1" in report_md or "Performance Report" in report_md or "Summary" in report_md


# =====================================================================
# CLI Tests
# =====================================================================

def test_mr20_cli_help():
    """Verify python3 strategy/mr20_strategy.py --help runs successfully and displays help message."""
    cmd = [sys.executable, "strategy/mr20_strategy.py", "--help"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    assert res.returncode == 0
    assert "MR20" in res.stdout or "pullback" in res.stdout.lower()
