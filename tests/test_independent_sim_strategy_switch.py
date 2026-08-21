"""Unit tests for strategy switching in independent_sim.py."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import pytest
import exchange_calendars as xcals
import independent_sim as sim


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def test_resolve_strategy_id():
    assert sim.resolve_strategy_id(None) == "top2_score_v1"
    assert sim.resolve_strategy_id("top2") == "top2_score_v1"
    assert sim.resolve_strategy_id("top2_score_v1") == "top2_score_v1"
    assert sim.resolve_strategy_id("mr20") == "mr20"
    assert sim.resolve_strategy_id("mr20_pullback_v1") == "mr20"
    assert sim.resolve_strategy_id("rsi_reversal") == "rsi_reversal"
    assert sim.resolve_strategy_id("rsi_reversal_v1") == "rsi_reversal"


def test_init_simulation_with_mr20(temp_dir):
    mr20_dir = temp_dir / "mr20_data"
    state = sim.init_simulation(data_dir=mr20_dir, strategy="mr20")
    assert state["strategy_id"] == "mr20"
    assert state["config"]["initial_capital"] == 1_000_000.0
    assert state["config"]["max_positions"] == 2
    assert state["config"]["position_size"] == 0.45
    assert state["config"]["max_hold_days"] == 20


def test_init_simulation_with_rsi_reversal(temp_dir):
    rsi_dir = temp_dir / "rsi_data"
    state = sim.init_simulation(data_dir=rsi_dir, strategy="rsi_reversal")
    assert state["strategy_id"] == "rsi_reversal"
    assert state["config"]["initial_capital"] == 1_000_000.0
    assert state["config"]["max_positions"] == 5
    assert state["config"]["position_size"] == 0.15
    assert state["config"]["max_hold_days"] == 5


def test_cli_strategy_flag_defaults(temp_dir):
    parser = sim.build_cli_parser()
    # Test --strategy mr20 init
    args = parser.parse_args(["--strategy", "mr20", "init"])
    assert args.strategy == "mr20"
    assert args.command == "init"

    # Test subcommand level --strategy
    args_sub = parser.parse_args(["init", "--strategy", "rsi_reversal"])
    assert args_sub.strategy == "rsi_reversal"
    assert args_sub.command == "init"


def test_cli_execution_with_strategy_mr20(temp_dir):
    cmd = [
        sys.executable, "independent_sim.py",
        "--strategy", "mr20",
        "init",
        "--data-dir", str(temp_dir / "mr20_test"),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    assert res.returncode == 0
    assert "Initialized simulation state for [mr20]" in res.stdout
    state = sim.load_state(temp_dir / "mr20_test")
    assert state["strategy_id"] == "mr20"


def test_cli_execution_with_strategy_rsi_reversal(temp_dir):
    cmd = [
        sys.executable, "independent_sim.py",
        "--strategy", "rsi_reversal",
        "init",
        "--data-dir", str(temp_dir / "rsi_test"),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    assert res.returncode == 0
    assert "Initialized simulation state for [rsi_reversal]" in res.stdout
    state = sim.load_state(temp_dir / "rsi_test")
    assert state["strategy_id"] == "rsi_reversal"
    assert state["config"]["max_positions"] == 5


def test_no_cross_strategy_fallback_when_orders_missing(temp_dir):
    """Verify MR20 and RSI do not fall back to top2 artifacts/orders_*.json when their own orders are missing."""
    mr20_dir = temp_dir / "mr20_data"
    sim.init_simulation(data_dir=mr20_dir, strategy="mr20")

    # Create dummy top2 orders file in temp artifacts dir
    artifacts_dir = temp_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    top2_file = artifacts_dir / "orders_20260820.json"
    top2_orders = {
        "orders": [
            {
                "ticker": "2330",
                "rank": 1,
                "score": 90.0,
                "limit_price": 100.0,
                "reference_close": 100.0,
                "atr": 5.0,
                "execution_date": "2026-08-21",
                "signal_date": "2026-08-20",
            }
        ]
    }
    top2_file.write_text(json.dumps(top2_orders))

    mock_bars = {
        "2330": {"date": "2026-08-20", "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0}
    }
    with patch("independent_sim.fetch_market_bars", return_value=mock_bars), \
         patch("independent_sim.fetch_benchmark_close", return_value=150.0):
        # run_close_and_plan without specifying orders_path
        sim.run_close_and_plan(
            data_dir=mr20_dir,
            as_of="2026-08-20",
        )

    state = sim.load_state(mr20_dir)
    # Should NOT load the top2 orders from artifacts/orders_20260820.json
    assert len(state["pending_orders"]) == 0


def test_generate_markdown_report_dynamic_headers_mr20(temp_dir):
    """P2-3: Verify MR20 report dynamically displays MR20 strategy title and ATR exit headers."""
    state = sim.init_simulation(data_dir=temp_dir / "mr20", strategy="mr20")
    state["positions"]["2330"] = {
        "ticker": "2330",
        "entry": 100.0,
        "shares": 1000,
        "tp": 120.0,
        "sl": 85.0,
        "entry_date": "2026-08-20",
        "day_count": 3,
        "max_hold_days": 20,
    }
    perf = sim.compute_performance(sim.pd.DataFrame(), [], [])
    report_md = sim.generate_markdown_report(state, perf)

    assert "# MR20 Pullback Mean Reversion Simulation Report (mr20)" in report_md
    assert "| 指標 | MR20 Pullback Mean Reversion | 0050 (基準) | 差異 (Alpha) |" in report_md
    assert "| 標的 | 進場日 | 進場價 | 股數 | TP (+4 ATR) | SL (-3 ATR) | 已持有天數 |" in report_md
    assert "Top-2" not in report_md


def test_generate_markdown_report_dynamic_headers_rsi_reversal(temp_dir):
    """P2-3: Verify RSI Reversal report dynamically displays RSI strategy title and exit headers."""
    state = sim.init_simulation(data_dir=temp_dir / "rsi", strategy="rsi_reversal")
    state["positions"]["2330"] = {
        "ticker": "2330",
        "entry": 100.0,
        "shares": 1000,
        "tp": 106.0,
        "sl": 97.0,
        "entry_date": "2026-08-20",
        "day_count": 1,
        "max_hold_days": 5,
    }
    perf = sim.compute_performance(sim.pd.DataFrame(), [], [])
    report_md = sim.generate_markdown_report(state, perf)

    assert "# RSI Reversal Strategy Simulation Report (rsi_reversal)" in report_md
    assert "| 指標 | RSI Reversal Strategy | 0050 (基準) | 差異 (Alpha) |" in report_md
    assert "TP (+6%)" in report_md or "TP (+2 ATR)" in report_md
    assert "Top-2" not in report_md


def test_generate_markdown_report_dynamic_headers_top2(temp_dir):
    """P2-3: Verify Top-2 report dynamically displays Top-2 strategy title and ATR exit headers."""
    state = sim.init_simulation(data_dir=temp_dir / "top2", strategy="top2_score_v1")
    state["positions"]["2330"] = {
        "ticker": "2330",
        "entry": 100.0,
        "shares": 1000,
        "tp": 120.0,
        "sl": 85.0,
        "entry_date": "2026-08-20",
        "day_count": 3,
        "max_hold_days": 20,
    }
    perf = sim.compute_performance(sim.pd.DataFrame(), [], [])
    report_md = sim.generate_markdown_report(state, perf)

    assert "# Top-2 Score Concentration Simulation Report (top2_score_v1)" in report_md
    assert "| 指標 | Top-2 Score Concentration | 0050 (基準) | 差異 (Alpha) |" in report_md
    assert "| 標的 | 進場日 | 進場價 | 股數 | TP (+4 ATR) | SL (-3 ATR) | 已持有天數 |" in report_md


def test_order_level_tp_sl_override_in_position_and_report(temp_dir):
    """P2-4: Verify order-level TP/SL overrides are stored on position and reflected in report header."""
    state = sim.init_simulation(data_dir=temp_dir / "override_test", strategy="top2_score_v1")
    state["pending_orders"] = [
        {
            "order_id": "test_1",
            "signal_date": "2026-08-20",
            "execution_date": "2026-08-21",
            "ticker": "2330",
            "limit_price": 100.0,
            "reference_close": 100.0,
            "atr": 5.0,
            "tp_atr_mult": 2.5,
            "sl_atr_mult": 1.5,
            "max_hold_days": 10,
        }
    ]
    bars = {
        "2330": {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0, "date": "2026-08-21"}
    }
    terminal_events = sim.execute_open_orders(state, bars, as_of="2026-08-21")
    assert len(terminal_events) == 1
    assert terminal_events[0]["status"] == "FILLED"

    pos = state["positions"]["2330"]
    assert pos["tp_atr_mult"] == 2.5
    assert pos["sl_atr_mult"] == 1.5

    perf = sim.compute_performance(sim.pd.DataFrame(), [], [])
    report_md = sim.generate_markdown_report(state, perf)
    assert "TP (+2.5 ATR)" in report_md
    assert "SL (-1.5 ATR)" in report_md


def test_independent_sim_shares_sizing_deducts_buy_commission(temp_dir):
    """New-2: Simulator share calculation accounts for BUY_COST_RATE so orders do not cancel on exact cash."""
    state = sim.init_simulation(data_dir=temp_dir / "commission_test", strategy="rsi_reversal")
    # Set capital so available_cash = exactly 10,000 (reserve_cash = 200,000, cash = 210,000)
    state["cash"] = 210_000.0
    state["config"]["initial_capital"] = 1_000_000.0
    state["config"]["reserve_cash_ratio"] = 0.20  # reserve = 200,000 -> available_cash = 10,000
    state["config"]["position_size"] = 0.50  # target_amount will be capped at available_cash (10,000)

    state["pending_orders"] = [
        {
            "order_id": "test_comm",
            "signal_date": "2026-08-20",
            "execution_date": "2026-08-21",
            "ticker": "2330",
            "limit_price": 100.0,
            "reference_close": 100.0,
            "atr": 5.0,
        }
    ]
    bars = {
        "2330": {"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "date": "2026-08-21"}
    }
    terminal_events = sim.execute_open_orders(state, bars, as_of="2026-08-21")
    assert len(terminal_events) == 1
    assert terminal_events[0]["status"] == "FILLED"
    assert state["positions"]["2330"]["shares"] == 99
    assert state["cash"] >= 200_000.0


