"""Unit and integration tests for independent_sim.py (Top-2 Score Concentration Strategy).

Tests follow TDD principles and run fully offline with deterministic fixtures.
"""

import json
import math
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import exchange_calendars as xcals
from strategy.order_execution import DEFAULT_TP_SL, evaluate_buy_limit_at_open

import independent_sim as sim


@pytest.fixture
def calendar():
    return xcals.get_calendar("XTAI")


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_orders_data():
    return {
        "orders": [
            {
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "2059",
                "side": "buy",
                "rank": 1,
                "score": 4.0,
                "model_entry_ref": "next_open",
                "reference_close": 122.0,
                "limit_price": 122.0,
                "atr": 4.5,
                "tp_atr_mult": 4.0,
                "sl_atr_mult": 3.0,
                "max_hold_days": 20,
                "model_version": "v8.5",
            },
            {
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "6213",
                "side": "buy",
                "rank": 2,
                "score": 3.9,
                "model_entry_ref": "next_open",
                "reference_close": 450.0,
                "limit_price": 450.0,
                "atr": 15.0,
                "tp_atr_mult": 4.0,
                "sl_atr_mult": 3.0,
                "max_hold_days": 20,
                "model_version": "v8.5",
            },
            {
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "3017",
                "side": "buy",
                "rank": 3,
                "score": 3.8,
                "model_entry_ref": "next_open",
                "reference_close": 320.0,
                "limit_price": 320.0,
                "atr": 10.0,
                "tp_atr_mult": 4.0,
                "sl_atr_mult": 3.0,
                "max_hold_days": 20,
                "model_version": "v8.5",
            },
            {
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "3231",
                "side": "buy",
                "rank": 4,
                "score": 3.5,
                "model_entry_ref": "next_open",
                "reference_close": 150.0,
                "limit_price": 150.0,
                "atr": 5.0,
                "tp_atr_mult": 4.0,
                "sl_atr_mult": 3.0,
                "max_hold_days": 20,
                "model_version": "v8.5",
            },
        ]
    }


# =====================================================================
# Task 1: Top-7 Loading and Candidate Selection Tests
# =====================================================================

class TestCandidateSelection:
    def test_load_orders_valid(self, temp_dir, sample_orders_data, calendar):
        orders_file = temp_dir / "orders_20260814.json"
        orders_file.write_text(json.dumps(sample_orders_data), encoding="utf-8")

        orders = sim.load_orders(orders_file, calendar=calendar)
        assert len(orders) == 4
        assert orders[0]["ticker"] == "2059"
        assert orders[0]["signal_date"] == "2026-08-14"
        assert orders[0]["execution_date"] == "2026-08-17"

    def test_load_orders_mismatched_signal_date_raises(self, temp_dir, calendar):
        bad_data = {
            "orders": [
                {"ticker": "2330", "signal_date": "2026-08-14", "execution_date": "2026-08-17", "limit_price": 1000},
                {"ticker": "2454", "signal_date": "2026-08-15", "execution_date": "2026-08-17", "limit_price": 1000},
            ]
        }
        f = temp_dir / "bad_orders.json"
        f.write_text(json.dumps(bad_data), encoding="utf-8")
        with pytest.raises(ValueError, match="Inconsistent signal_date"):
            sim.load_orders(f, calendar=calendar)

    def test_load_orders_invalid_execution_date_raises(self, temp_dir, calendar):
        # 2026-08-14 is Friday, next session is 2026-08-17 (Monday)
        bad_data = {
            "orders": [
                {"ticker": "2330", "signal_date": "2026-08-14", "execution_date": "2026-08-15", "limit_price": 1000},
            ]
        }
        f = temp_dir / "bad_orders.json"
        f.write_text(json.dumps(bad_data), encoding="utf-8")
        with pytest.raises(ValueError, match="execution_date .* is not next XTAI trading session"):
            sim.load_orders(f, calendar=calendar)

    def test_select_candidates_auto_top2(self, sample_orders_data):
        orders = sample_orders_data["orders"]
        selected = sim.select_candidates(orders, held=set(), pending=set(), max_picks=2)
        assert len(selected) == 2
        assert selected[0]["ticker"] == "2059"
        assert selected[1]["ticker"] == "6213"
        assert selected[0]["selection_mode"] == "auto"

    def test_select_candidates_excludes_held_and_pending(self, sample_orders_data):
        orders = sample_orders_data["orders"]
        # If 2059 is held and 6213 is pending, next 2 should be 3017 and 3231
        selected = sim.select_candidates(orders, held={"2059"}, pending={"6213"}, max_picks=2)
        assert len(selected) == 2
        assert selected[0]["ticker"] == "3017"
        assert selected[1]["ticker"] == "3231"

    def test_select_candidates_low_price_filter(self, sample_orders_data):
        orders = sample_orders_data["orders"]
        # max_price = 200 -> eligible: 2059 (122.0), 3231 (150.0). 6213 (450) and 3017 (320) filtered out.
        selected = sim.select_candidates(orders, held=set(), pending=set(), max_picks=2, max_price=200.0)
        assert len(selected) == 2
        assert selected[0]["ticker"] == "2059"
        assert selected[1]["ticker"] == "3231"
        assert selected[0]["selection_mode"] == "low_price"

    def test_select_candidates_low_price_no_backfill(self, sample_orders_data):
        orders = sample_orders_data["orders"]
        # max_price = 130 -> only 2059 (122.0) qualifies. Should NOT backfill with 3231 (150) or others.
        selected = sim.select_candidates(orders, held=set(), pending=set(), max_picks=2, max_price=130.0)
        assert len(selected) == 1
        assert selected[0]["ticker"] == "2059"

    def test_select_candidates_manual_mode(self, sample_orders_data):
        orders = sample_orders_data["orders"]
        selected = sim.select_candidates(
            orders, held=set(), pending=set(), max_picks=2, manual_tickers=["3231", "3017"]
        )
        assert len(selected) == 2
        assert [s["ticker"] for s in selected] == ["3231", "3017"]
        assert selected[0]["selection_mode"] == "manual"

    def test_select_candidates_manual_invalid_ticker_raises(self, sample_orders_data):
        orders = sample_orders_data["orders"]
        with pytest.raises(ValueError, match="not in upstream Top-7 signals"):
            sim.select_candidates(orders, held=set(), pending=set(), manual_tickers=["9999"])

    def test_select_candidates_manual_held_ticker_raises(self, sample_orders_data):
        orders = sample_orders_data["orders"]
        with pytest.raises(ValueError, match="already held or pending"):
            sim.select_candidates(orders, held={"2059"}, pending=set(), manual_tickers=["2059"])

    def test_select_candidates_tie_breaker_by_ticker(self):
        orders = [
            {"ticker": "3008", "rank": None, "score": 3.5, "reference_close": 100.0, "signal_date": "2026-08-14", "execution_date": "2026-08-17"},
            {"ticker": "2330", "rank": None, "score": 3.5, "reference_close": 100.0, "signal_date": "2026-08-14", "execution_date": "2026-08-17"},
        ]
        selected = sim.select_candidates(orders, held=set(), pending=set(), max_picks=2)
        assert [s["ticker"] for s in selected] == ["2330", "3008"]


# =====================================================================
# Task 2: State, Atomic Save, and CLI Initialization Tests
# =====================================================================

class TestStateAndInit:
    def test_init_creates_valid_state(self, temp_dir):
        state = sim.init_simulation(data_dir=temp_dir, capital=200000.0)
        assert state["schema_version"] == 1
        assert state["strategy_id"] == "top2_score_v1"
        assert state["cash"] == 200000.0
        assert state["config"]["initial_capital"] == 200000.0
        assert state["config"]["max_positions"] == 2
        assert state["config"]["position_size"] == 0.45
        assert state["config"]["reserve_ratio"] == 0.10

        loaded = sim.load_state(temp_dir)
        assert loaded == state

    def test_init_does_not_overwrite_existing_state(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=200000.0)
        # Re-initializing without trades should still fail without --force
        with pytest.raises(FileExistsError, match="already exists. Use --force to overwrite"):
            sim.init_simulation(data_dir=temp_dir, capital=300000.0)

    def test_init_does_not_overwrite_existing_state_with_trades(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=200000.0)
        state = sim.load_state(temp_dir)
        state["closed_trades"].append({"trade_id": "test_trade"})
        sim.save_state_atomic(state, data_dir=temp_dir)

        with pytest.raises(FileExistsError, match="already exists. Use --force to overwrite"):
            sim.init_simulation(data_dir=temp_dir, capital=300000.0)

    def test_init_force_overwrites_existing_history(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=200000.0)
        state = sim.load_state(temp_dir)
        state["closed_trades"].append({"trade_id": "test_trade"})
        sim.save_state_atomic(state, data_dir=temp_dir)

        forced_state = sim.init_simulation(data_dir=temp_dir, capital=300000.0, force=True)
        assert forced_state["cash"] == 300000.0
        assert len(forced_state["closed_trades"]) == 0

    def test_atomic_save_leaves_no_temp_files(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        sim.save_state_atomic(state, data_dir=temp_dir)
        files = list(temp_dir.iterdir())
        file_names = {f.name for f in files if not f.name.endswith(".lock")}
        assert file_names == {"state.json"}

    def test_atomic_save_cleans_up_on_failure(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        with patch("json.dump", side_effect=IOError("Simulated disk error")):
            with pytest.raises(IOError, match="Simulated disk error"):
                sim.save_state_atomic(state, data_dir=temp_dir)
        temp_files = [f.name for f in temp_dir.iterdir() if f.name.startswith("state.json.tmp.")]
        assert len(temp_files) == 0

    def test_corrupt_state_fails_closed(self, temp_dir):
        state_file = temp_dir / "state.json"
        state_file.write_text("{corrupt json", encoding="utf-8")
        with pytest.raises(ValueError, match="Corrupt or invalid state file"):
            sim.load_state(temp_dir)

    def test_idempotent_command_execution(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        sim.save_state_atomic(state, data_dir=temp_dir)
        run_id = "close-and-plan:2026-08-14"
        assert not sim.is_run_processed(state, run_id)
        sim.mark_run_processed(state, run_id)
        assert sim.is_run_processed(state, run_id)


# =====================================================================
# Task 3: ATR and Pending Orders Planning Tests
# =====================================================================

class TestATRAndPlanning:
    def test_compute_v85_atr20(self):
        # 25 days of closes
        closes = pd.Series([100.0 + i * 2.0 for i in range(25)])
        expected = (closes.pct_change().abs().rolling(20).mean() * closes).iloc[-1]
        atr = sim.compute_v85_atr20(closes)
        assert atr is not None
        assert math.isclose(atr, expected, rel_tol=1e-5)

    def test_compute_v85_atr20_insufficient_bars_returns_none(self):
        closes = pd.Series([100.0 + i for i in range(15)])
        assert sim.compute_v85_atr20(closes) is None

    def test_plan_orders_creates_pending_orders_with_atr(self, temp_dir, sample_orders_data):
        state = sim.get_default_state(capital=200000.0)
        selected = sample_orders_data["orders"][:2]
        pending = sim.plan_orders(state, selected, as_of="2026-08-14")
        assert len(pending) == 2
        assert pending[0]["order_id"] == "top2_score_v1:2026-08-14:2059:buy"
        assert pending[0]["limit_price"] == 122.0
        assert pending[0]["atr"] == 4.5
        assert len(state["pending_orders"]) == 2
        assert len(state["order_events"]) == 2
        assert state["order_events"][0]["status"] == "PENDING"

    def test_plan_orders_fallback_atr_missing_records_skipped(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        candidate_no_atr = {
            "signal_date": "2026-08-14",
            "execution_date": "2026-08-17",
            "ticker": "2059",
            "rank": 1,
            "score": 4.0,
            "reference_close": 122.0,
            "limit_price": 122.0,
            "atr": None,
        }
        # mock fetch_closes returning insufficient data
        with patch.object(sim, "fetch_ticker_closes", return_value=pd.Series([100.0] * 10)):
            pending = sim.plan_orders(state, [candidate_no_atr], as_of="2026-08-14")
            assert len(pending) == 0
            assert len(state["pending_orders"]) == 0
            assert len(state["order_events"]) == 1
            assert state["order_events"][0]["status"] == "SKIPPED_NO_ATR"

    def test_expire_pending_orders(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["pending_orders"] = [
            {
                "order_id": "top2_score_v1:2026-08-14:2059:buy",
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "2059",
                "limit_price": 100.0,
                "atr": 5.0,
            },
            {
                "order_id": "top2_score_v1:2026-08-17:6213:buy",
                "signal_date": "2026-08-17",
                "execution_date": "2026-08-18",
                "ticker": "6213",
                "limit_price": 400.0,
                "atr": 10.0,
            },
        ]
        # Today is 2026-08-18, order with execution_date 2026-08-17 is expired
        expired = sim.expire_pending_orders(state, as_of="2026-08-18")
        assert len(expired) == 1
        assert expired[0]["ticker"] == "2059"
        assert expired[0]["status"] == "CANCELLED_EXPIRED"
        assert len(state["pending_orders"]) == 1
        assert state["pending_orders"][0]["ticker"] == "6213"
        assert len(state["order_events"]) == 1
        assert state["order_events"][0]["status"] == "CANCELLED_EXPIRED"


# =====================================================================
# Task 4: 09:30 Open Limit Order Execution Tests
# =====================================================================

class TestOpenLimitExecution:
    def test_execute_open_fill_below_or_equal_limit(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["pending_orders"] = [
            {
                "order_id": "top2_score_v1:2026-08-14:2059:buy",
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "2059",
                "limit_price": 100.0,
                "atr": 5.0,
                "tp_atr_mult": 4.0,
                "sl_atr_mult": 3.0,
                "max_hold_days": 20,
                "selection_mode": "auto",
            }
        ]
        # Open price is 98.0 (<= 100.0 limit) -> FILLED
        bars = {
            "2059": {"date": "2026-08-17", "open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0}
        }
        events = sim.execute_open_orders(state, bars, as_of="2026-08-17")
        assert len(events) == 1
        assert events[0]["status"] == "FILLED"
        assert events[0]["fill_price"] == 98.0
        assert "2059" in state["positions"]
        pos = state["positions"]["2059"]
        assert pos["entry"] == 98.0
        # TP = 98.0 + 4.0 * 5.0 = 118.0, SL = 98.0 - 3.0 * 5.0 = 83.0
        assert pos["tp"] == 118.0
        assert pos["sl"] == 83.0
        assert len(state["pending_orders"]) == 0
        # Shares and cash deduction
        # Target amount = min(200000 * 0.45, 200000 - 20000) = min(90000, 180000) = 90000
        # shares = floor(90000 / 98) = 918
        assert pos["shares"] == 918
        trade_amount = 918 * 98.0
        buy_cost = trade_amount * 0.001425
        assert math.isclose(state["cash"], 200000.0 - trade_amount - buy_cost, abs_tol=0.01)

    def test_execute_open_cancel_above_limit(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["pending_orders"] = [
            {
                "order_id": "top2_score_v1:2026-08-14:2059:buy",
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "2059",
                "limit_price": 100.0,
                "atr": 5.0,
                "tp_atr_mult": 4.0,
                "sl_atr_mult": 3.0,
                "max_hold_days": 20,
            }
        ]
        # Open price is 101.0 (> 100.0 limit) -> CANCELLED_OPEN_ABOVE_LIMIT
        bars = {
            "2059": {"date": "2026-08-17", "open": 101.0, "high": 105.0, "low": 99.0, "close": 104.0}
        }
        events = sim.execute_open_orders(state, bars, as_of="2026-08-17")
        assert len(events) == 1
        assert events[0]["status"] == "CANCELLED_OPEN_ABOVE_LIMIT"
        assert len(state["positions"]) == 0
        assert len(state["pending_orders"]) == 0
        assert state["cash"] == 200000.0

    def test_execute_open_missing_or_stale_bar(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["pending_orders"] = [
            {
                "order_id": "top2_score_v1:2026-08-14:2059:buy",
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "2059",
                "limit_price": 100.0,
                "atr": 5.0,
            }
        ]
        # Stale date
        bars = {"2059": {"date": "2026-08-14", "open": 95.0}}
        events = sim.execute_open_orders(state, bars, as_of="2026-08-17")
        assert len(events) == 1
        assert events[0]["status"] == "CANCELLED_NO_OPEN_PRICE"

    def test_execute_open_insufficient_cash(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["cash"] = 15000.0  # Less than reserve cash (20,000)
        state["pending_orders"] = [
            {
                "order_id": "top2_score_v1:2026-08-14:2059:buy",
                "signal_date": "2026-08-14",
                "execution_date": "2026-08-17",
                "ticker": "2059",
                "limit_price": 100.0,
                "atr": 5.0,
            }
        ]
        bars = {"2059": {"date": "2026-08-17", "open": 95.0}}
        events = sim.execute_open_orders(state, bars, as_of="2026-08-17")
        assert len(events) == 1
        assert events[0]["status"] == "CANCELLED_INSUFFICIENT_CASH"


# =====================================================================
# Task 5: Exits (SL/TP/TIME), Costs, and Equity Curve Tests
# =====================================================================

class TestSettlementAndEquity:
    def test_settle_skips_entry_date_position(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["positions"]["2059"] = {
            "ticker": "2059",
            "entry": 100.0,
            "shares": 1000,
            "tp": 120.0,
            "sl": 85.0,
            "atr": 5.0,
            "entry_date": "2026-08-17",
            "day_count": 0,
            "max_hold_days": 20,
            "signal_date": "2026-08-14",
        }
        # On entry date (2026-08-17), even if price touches SL or TP, position is not settled and day_count remains 0
        bars = {
            "2059": {"date": "2026-08-17", "open": 100.0, "high": 125.0, "low": 80.0, "close": 110.0}
        }
        trades = sim.settle_positions(state, bars, as_of="2026-08-17")
        assert len(trades) == 0
        assert "2059" in state["positions"]
        assert state["positions"]["2059"]["day_count"] == 0

    def test_settle_sl_takes_precedence_over_tp_when_both_touched(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["positions"]["2059"] = {
            "ticker": "2059",
            "entry": 100.0,
            "shares": 1000,
            "tp": 120.0,
            "sl": 85.0,
            "atr": 5.0,
            "entry_date": "2026-08-17",
            "day_count": 1,
            "max_hold_days": 20,
            "signal_date": "2026-08-14",
        }
        # Bar hits both SL (low 80 <= 85) and TP (high 125 >= 120)
        bars = {
            "2059": {"date": "2026-08-18", "open": 90.0, "high": 125.0, "low": 80.0, "close": 110.0}
        }
        trades = sim.settle_positions(state, bars, as_of="2026-08-18")
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "SL"
        assert trades[0]["exit_price"] == 85.0
        assert "2059" not in state["positions"]

    def test_settle_sl_gap_down_exits_at_open(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["positions"]["2059"] = {
            "ticker": "2059",
            "entry": 100.0,
            "shares": 1000,
            "tp": 120.0,
            "sl": 85.0,
            "entry_date": "2026-08-17",
            "day_count": 1,
            "max_hold_days": 20,
        }
        # Open is 80.0, below SL 85.0 -> exit at open 80.0
        bars = {
            "2059": {"date": "2026-08-18", "open": 80.0, "high": 84.0, "low": 75.0, "close": 78.0}
        }
        trades = sim.settle_positions(state, bars, as_of="2026-08-18")
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "SL"
        assert trades[0]["exit_price"] == 80.0

    def test_settle_tp_gap_up_exits_at_open(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["positions"]["2059"] = {
            "ticker": "2059",
            "entry": 100.0,
            "shares": 1000,
            "tp": 120.0,
            "sl": 85.0,
            "entry_date": "2026-08-17",
            "day_count": 1,
            "max_hold_days": 20,
        }
        # Open is 125.0, above TP 120.0 -> exit at open 125.0
        bars = {
            "2059": {"date": "2026-08-18", "open": 125.0, "high": 130.0, "low": 122.0, "close": 128.0}
        }
        trades = sim.settle_positions(state, bars, as_of="2026-08-18")
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "TP"
        assert trades[0]["exit_price"] == 125.0

    def test_settle_time_exit_on_day_20(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["positions"]["2059"] = {
            "ticker": "2059",
            "entry": 100.0,
            "shares": 1000,
            "tp": 120.0,
            "sl": 85.0,
            "entry_date": "2026-08-17",
            "day_count": 19,
            "max_hold_days": 20,
        }
        # 20th day: neither SL nor TP touched -> TIME exit at close
        bars = {
            "2059": {"date": "2026-09-14", "open": 105.0, "high": 108.0, "low": 103.0, "close": 106.0}
        }
        trades = sim.settle_positions(state, bars, as_of="2026-09-14")
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "TIME"
        assert trades[0]["exit_price"] == 106.0
        assert trades[0]["days_held"] == 20

    def test_cost_and_slippage_accounting(self, temp_dir):
        state = sim.get_default_state(capital=100000.0)
        entry_price = 100.0
        shares = 1000
        buy_cost = entry_price * shares * 0.001425  # 142.5
        state["cash"] = 100000.0 - (entry_price * shares + buy_cost)
        state["positions"]["2059"] = {
            "ticker": "2059",
            "entry": entry_price,
            "shares": shares,
            "tp": 120.0,
            "sl": 85.0,
            "entry_date": "2026-08-17",
            "day_count": 0,
            "max_hold_days": 20,
        }

        # Exit at TP 120.0
        bars = {
            "2059": {"date": "2026-08-18", "open": 110.0, "high": 122.0, "low": 108.0, "close": 119.0}
        }
        trades = sim.settle_positions(state, bars, as_of="2026-08-18")
        trade = trades[0]

        sell_price = 120.0
        sell_cost = sell_price * shares * 0.004425  # 531.0
        slippage_cost = sell_price * shares * 0.003  # 360.0
        proceeds = sell_price * shares - sell_cost - slippage_cost  # 119109.0
        cost_basis = entry_price * shares + buy_cost  # 100142.5
        net_pnl = proceeds - cost_basis  # 18966.5

        assert math.isclose(trade["buy_cost"], buy_cost, abs_tol=0.1)
        assert math.isclose(trade["sell_cost"], sell_cost, abs_tol=0.1)
        assert math.isclose(trade["slippage_cost"], slippage_cost, abs_tol=0.1)
        assert math.isclose(trade["net_pnl"], net_pnl, abs_tol=0.1)
        assert math.isclose(state["cash"], 100000.0 - cost_basis + proceeds, abs_tol=0.1)

    def test_mark_equity_and_benchmark(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["cash"] = 100000.0
        state["positions"]["2059"] = {"ticker": "2059", "entry": 100.0, "shares": 1000}

        closes = {"2059": 110.0}
        # Day 1: 0050 at 150.0
        eq1 = sim.mark_equity(state, closes=closes, benchmark_close=150.0, as_of="2026-08-17")
        assert eq1["equity"] == 210000.0
        assert eq1["benchmark_equity"] == 200000.0
        assert eq1["cumulative_return"] == 0.05
        assert eq1["benchmark_return"] == 0.0
        assert eq1["excess_return"] == 0.05
        assert eq1["drawdown"] == 0.0

        # Day 2: 2059 at 105.0, 0050 at 153.0
        closes = {"2059": 105.0}
        eq2 = sim.mark_equity(state, closes=closes, benchmark_close=153.0, as_of="2026-08-18")
        assert eq2["equity"] == 205000.0
        # Benchmark went up 2%: 153 / 150 * 200,000 = 204,000
        assert eq2["benchmark_equity"] == 204000.0
        assert math.isclose(eq2["benchmark_return"], 0.02, abs_tol=1e-5)
        # Peak was 210,000; current is 205,000 -> drawdown = (205 - 210) / 210 = -0.0238095
        assert math.isclose(eq2["drawdown"], (205000 - 210000) / 210000, abs_tol=1e-5)


# =====================================================================
# Task 6: CSV Export, Performance Report, and Charting Tests
# =====================================================================

class TestReporting:
    def test_export_ledgers_creates_valid_csvs(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        state["order_events"].append({
            "order_id": "top2_score_v1:2026-08-14:2059:buy",
            "signal_date": "2026-08-14",
            "execution_date": "2026-08-17",
            "event_time": "2026-08-17T09:30:00+08:00",
            "ticker": "2059",
            "upstream_rank": 1,
            "score": 4.0,
            "selection_mode": "auto",
            "reference_close": 100.0,
            "limit_price": 100.0,
            "open_price": 98.0,
            "status": "FILLED",
            "fill_price": 98.0,
            "atr": 5.0,
            "tp_price": 118.0,
            "sl_price": 83.0,
            "shares": 918,
            "message": "Filled at open",
        })
        state["closed_trades"].append({
            "trade_id": "top2_score_v1:2026-08-14:2059:2026-08-17:2026-08-18",
            "ticker": "2059",
            "signal_date": "2026-08-14",
            "entry_date": "2026-08-17",
            "exit_date": "2026-08-18",
            "entry_price": 98.0,
            "exit_price": 118.0,
            "shares": 918,
            "atr": 5.0,
            "tp_price": 118.0,
            "sl_price": 83.0,
            "days_held": 1,
            "exit_reason": "TP",
            "buy_cost": 128.2,
            "sell_cost": 479.4,
            "slippage_cost": 325.0,
            "gross_pnl": 18360.0,
            "net_pnl": 17427.4,
            "net_return_pct": 19.34,
        })
        state["equity_curve"].append({
            "date": "2026-08-17",
            "cash": 110000.0,
            "market_value": 90000.0,
            "equity": 200000.0,
            "daily_return": 0.0,
            "cumulative_return": 0.0,
            "benchmark_close": 150.0,
            "benchmark_equity": 200000.0,
            "benchmark_return": 0.0,
            "excess_return": 0.0,
            "drawdown": 0.0,
        })

        sim.export_ledgers(state, data_dir=temp_dir)
        assert (temp_dir / "orders.csv").exists()
        assert (temp_dir / "trades.csv").exists()
        assert (temp_dir / "equity.csv").exists()

        orders_df = pd.read_csv(temp_dir / "orders.csv")
        assert list(orders_df.columns) == [
            "order_id", "signal_date", "execution_date", "event_time", "ticker",
            "upstream_rank", "score", "selection_mode", "reference_close",
            "limit_price", "open_price", "status", "fill_price", "atr",
            "tp_price", "sl_price", "shares", "message"
        ]

    def test_compute_performance_empty_curve(self):
        perf = sim.compute_performance(pd.DataFrame(), [], [])
        assert perf["total_return"] == 0.0
        assert perf["benchmark_return"] == 0.0
        assert perf["total_trades"] == 0
        assert perf["sharpe"] is None

    def test_generate_report_and_chart(self, temp_dir):
        state = sim.get_default_state(capital=200000.0)
        # Add 3 days of equity curve
        for i, dt in enumerate(["2026-08-17", "2026-08-18", "2026-08-19"]):
            state["equity_curve"].append({
                "date": dt,
                "cash": 200000.0,
                "market_value": 0.0,
                "equity": 200000.0 + i * 2000.0,
                "daily_return": 0.01 if i > 0 else 0.0,
                "cumulative_return": i * 0.01,
                "benchmark_close": 150.0 + i,
                "benchmark_equity": 200000.0 * (150.0 + i) / 150.0,
                "benchmark_return": i / 150.0,
                "excess_return": i * 0.01 - i / 150.0,
                "drawdown": 0.0,
            })
        sim.save_state_atomic(state, data_dir=temp_dir)
        report_md, chart_path = sim.generate_report(data_dir=temp_dir)
        assert (temp_dir / "performance.md").exists()
        assert (temp_dir / "performance.png").exists()
        assert "Top-2" in report_md
        assert "0050" in report_md


# =====================================================================
# Task 7 & Integration: Smoke Test (init -> close-and-plan -> open -> report)
# =====================================================================

class TestSmokeWorkflow:
    def test_full_offline_simulation_smoke_test(self, temp_dir, sample_orders_data):
        # 1. Initialize
        state = sim.init_simulation(data_dir=temp_dir, capital=200000.0)
        assert state["cash"] == 200000.0

        # Create sample orders artifact
        orders_file = temp_dir / "orders_20260814.json"
        orders_file.write_text(json.dumps(sample_orders_data), encoding="utf-8")

        # 2. D-Day 18:05: close-and-plan
        with patch.object(sim, "fetch_market_bars", return_value={}), \
             patch.object(sim, "fetch_benchmark_close", return_value=150.0):
            sim.run_close_and_plan(
                data_dir=temp_dir,
                orders_path=orders_file,
                as_of="2026-08-14",
            )

        state = sim.load_state(temp_dir)
        # Should have 2 pending orders (Top 2: 2059 and 6213)
        assert len(state["pending_orders"]) == 2
        assert [p["ticker"] for p in state["pending_orders"]] == ["2059", "6213"]
        assert len(state["equity_curve"]) == 1
        assert state["equity_curve"][0]["date"] == "2026-08-14"

        # 3. D+1 09:35: open (2059 opens at 120 <= 122 -> fills; 6213 opens at 460 > 450 -> cancels)
        d_plus_1_bars = {
            "2059": {"date": "2026-08-17", "open": 120.0, "high": 124.0, "low": 118.0, "close": 123.0},
            "6213": {"date": "2026-08-17", "open": 460.0, "high": 465.0, "low": 455.0, "close": 462.0},
        }
        with patch.object(sim, "fetch_market_bars", return_value=d_plus_1_bars):
            sim.run_open(data_dir=temp_dir, as_of="2026-08-17")

        state = sim.load_state(temp_dir)
        assert len(state["positions"]) == 1
        assert "2059" in state["positions"]
        assert "6213" not in state["positions"]
        assert len(state["pending_orders"]) == 0

        # Verify no 3rd rank backfill
        assert "3017" not in state["positions"]

        # 4. D+1 18:05: close-and-plan for next session (2059 still holding)
        with patch.object(sim, "fetch_market_bars", return_value=d_plus_1_bars), \
             patch.object(sim, "fetch_benchmark_close", return_value=152.0):
            sim.run_close_and_plan(
                data_dir=temp_dir,
                orders_path=None,  # No new orders
                as_of="2026-08-17",
            )

        state = sim.load_state(temp_dir)
        assert len(state["positions"]) == 1
        assert state["positions"]["2059"]["day_count"] == 0
        assert len(state["equity_curve"]) == 2

        # 5. Report command
        report_md, chart_path = sim.run_report(data_dir=temp_dir)
        assert (temp_dir / "performance.md").exists()
        assert (temp_dir / "performance.png").exists()
        assert (temp_dir / "orders.csv").exists()
        assert (temp_dir / "trades.csv").exists()
        assert (temp_dir / "equity.csv").exists()


# =====================================================================
# Telegram Notification Tests
# =====================================================================

class TestTelegramNotification:
    def test_notify_telegram_no_env_noop(self):
        with patch.dict(os.environ, {}, clear=True):
            res = sim.notify_telegram("Test message")
            assert res is False

    def test_notify_telegram_success(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "mock_tok", "TELEGRAM_CHAT_ID": "mock_cid"}):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                res = sim.notify_telegram("Hello Test")
                assert res is True

    def test_notify_telegram_handles_error_gracefully(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "mock_tok", "TELEGRAM_CHAT_ID": "mock_cid"}):
            with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
                res = sim.notify_telegram("Hello Test")
                assert res is False


# =====================================================================
# Market Data Query Tests (with as_of)
# =====================================================================

class TestMarketDataProviders:
    def test_fetch_market_bars_with_as_of(self):
        mock_df = pd.DataFrame(
            {
                ("Close", "2330.TW"): [1000.0, 1010.0],
                ("Open", "2330.TW"): [990.0, 1005.0],
                ("High", "2330.TW"): [1005.0, 1015.0],
                ("Low", "2330.TW"): [985.0, 1000.0],
            },
            index=pd.to_datetime(["2026-08-14", "2026-08-17"]),
        )
        with patch("yfinance.download", return_value=mock_df) as mock_yf:
            bars = sim.fetch_market_bars(["2330"], as_of="2026-08-14")
            assert "2330" in bars
            assert bars["2330"]["date"] == "2026-08-14"
            assert bars["2330"]["close"] == 1000.0
            mock_yf.assert_called_once()
            _, kwargs = mock_yf.call_args
            assert "start" in kwargs and "end" in kwargs

    def test_fetch_benchmark_close_with_as_of(self):
        mock_df = pd.DataFrame(
            {"Close": [150.0, 155.0]},
            index=pd.to_datetime(["2026-08-14", "2026-08-17"]),
        )
        with patch("yfinance.download", return_value=mock_df) as mock_yf:
            bm = sim.fetch_benchmark_close(as_of="2026-08-14")
            assert bm == 150.0
            mock_yf.assert_called_once()
            _, kwargs = mock_yf.call_args
            assert "start" in kwargs and "end" in kwargs
