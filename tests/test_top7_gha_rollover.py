"""Tests for Top-7 GHA late delivery orphan order rollover.

Validates that:
1. When GHA delivers artifacts/orders_YYYYMMDD.json after 18:05,
   close-and-plan on the next trading day automatically discovers
   the latest valid orders file where:
     signal_date <= today and execution_date >= today
2. Outdated orders where execution_date < today are strictly rejected as stale.
3. MR20 strategy is untouched and does not use rollover.
4. End-to-end simulation from GHA late delivery -> next day rollover planning -> open fill.
"""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import exchange_calendars as xcals

import independent_sim as sim


@pytest.fixture
def calendar():
    return xcals.get_calendar("XTAI")


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _write_orders_file(
    path: Path,
    signal_date: str,
    execution_date: str,
    ticker: str = "3605",
    limit_price: float = 183.5,
    atr: float = 12.1,
    rank: int = 1,
    score: float = 4.0,
):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "orders": [
            {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "ticker": ticker,
                "side": "buy",
                "order_type": "limit",
                "limit_price": limit_price,
                "reference_close": limit_price,
                "time_in_force": "DAY_UNTIL_0930",
                "cancel_time": "09:30:00",
                "rank": rank,
                "score": score,
                "atr": atr,
            }
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class TestTop7GHARollover:
    def test_top7_resolve_orders_file_picks_latest_valid_rollover(self, temp_dir):
        """When today's orders file does not exist, resolve_orders_file finds
        the latest file where signal_date <= today and execution_date >= today.
        """
        # Day D (2026-09-24, Thursday) delivered after 18:05, exec is 2026-09-29 (Tuesday)
        f_sep24 = temp_dir / "orders_20260924.json"
        _write_orders_file(f_sep24, signal_date="2026-09-24", execution_date="2026-09-29", ticker="3605")

        # Today is 2026-09-29. orders_20260929.json has not arrived.
        resolved = sim.resolve_orders_file(
            strat_id=sim.DEFAULT_STRATEGY_ID,
            today_str="2026-09-29",
            orders_dir=temp_dir,
        )
        assert resolved == f_sep24

    def test_top7_resolve_orders_file_ignores_expired_orders(self, temp_dir):
        """Files where execution_date < today must NOT be picked."""
        f_old = temp_dir / "orders_20260814.json"
        _write_orders_file(f_old, signal_date="2026-08-14", execution_date="2026-08-17", ticker="2059")

        resolved = sim.resolve_orders_file(
            strat_id=sim.DEFAULT_STRATEGY_ID,
            today_str="2026-09-29",
            orders_dir=temp_dir,
        )
        assert resolved is None

    def test_mr20_does_not_use_rollover(self, temp_dir):
        """MR20 must NOT pick up rollover files from prior sessions."""
        f_sep24 = temp_dir / "orders_mr20_20260924.json"
        _write_orders_file(f_sep24, signal_date="2026-09-24", execution_date="2026-09-29", ticker="6446")

        resolved = sim.resolve_orders_file(
            strat_id="mr20",
            today_str="2026-09-29",
            orders_dir=temp_dir,
        )
        assert resolved is None

    def test_top7_load_orders_allows_rollover(self, temp_dir, calendar):
        """load_orders with allow_rollover=True accepts valid rollover orders."""
        f_sep24 = temp_dir / "orders_20260924.json"
        _write_orders_file(f_sep24, signal_date="2026-09-24", execution_date="2026-09-29", ticker="3605")

        orders = sim.load_orders(
            f_sep24,
            calendar=calendar,
            as_of="2026-09-29",
            allow_rollover=True,
        )
        assert len(orders) == 1
        assert orders[0]["ticker"] == "3605"

        # But without allow_rollover=True, it still raises ValueError (backward-compatible)
        with pytest.raises(ValueError, match="signal_date"):
            sim.load_orders(
                f_sep24,
                calendar=calendar,
                as_of="2026-09-29",
                allow_rollover=False,
            )

    def test_top7_close_and_plan_e2e_rollover(self, temp_dir):
        """Full end-to-end:
        1. 2026-09-24 18:05: close-and-plan runs without orders -> records gap.
        2. GHA delivers orders_20260924.json.
        3. 2026-09-29 18:05: close-and-plan runs, auto-discovers orders_20260924.json,
           plans 3605 for 2026-09-30 execution, clears gaps.
        4. 2026-09-30 09:35: open runs, fills 3605.
        """
        data_dir = temp_dir / "sim_data"
        orders_dir = temp_dir / "artifacts"
        sim.init_simulation(data_dir=data_dir, capital=500_000.0, strategy=sim.DEFAULT_STRATEGY_ID)

        # 1. 2026-09-24 18:05: GHA has not delivered yet (orders_dir is empty)
        with patch.object(sim, "fetch_market_bars", return_value={}), \
             patch.object(sim, "fetch_benchmark_close", return_value=150.0), \
             patch.dict(sim.STRATEGY_CONFIGS[sim.DEFAULT_STRATEGY_ID], {"orders_dir": str(orders_dir)}):
            sim.run_close_and_plan(
                data_dir=data_dir,
                as_of="2026-09-24",
            )

        state = sim.load_state(data_dir)
        assert "2026-09-24" in state.get("planning_gaps", [])
        assert state["pending_orders"] == []

        # 2. GHA delivers orders_20260924.json late (e.g. 22:16)
        orders_file = orders_dir / "orders_20260924.json"
        _write_orders_file(
            orders_file,
            signal_date="2026-09-24",
            execution_date="2026-09-29",
            ticker="3605",
            limit_price=183.5,
            atr=12.1,
        )

        # 3. 2026-09-29 18:05: close-and-plan runs (today's orders_20260929.json not delivered yet)
        mock_bars_sep29 = {
            "3605": {"date": "2026-09-29", "open": 182.0, "high": 185.0, "low": 181.0, "close": 183.5}
        }
        with patch.object(sim, "fetch_market_bars", return_value=mock_bars_sep29), \
             patch.object(sim, "fetch_benchmark_close", return_value=152.0), \
             patch.dict(sim.STRATEGY_CONFIGS[sim.DEFAULT_STRATEGY_ID], {"orders_dir": str(orders_dir)}):
            sim.run_close_and_plan(
                data_dir=data_dir,
                as_of="2026-09-29",
            )

        state = sim.load_state(data_dir)
        assert len(state["pending_orders"]) == 1
        pending = state["pending_orders"][0]
        assert pending["ticker"] == "3605"
        assert pending["execution_date"] == "2026-09-30"
        assert "2026-09-29" not in state.get("planning_gaps", [])
        assert "2026-09-24" not in state.get("planning_gaps", [])

        # 4. 2026-09-30 09:35: open runs and fills 3605
        mock_bars_sep30 = {
            "3605": {"date": "2026-09-30", "open": 183.0, "high": 186.0, "low": 182.0, "close": 185.0}
        }
        with patch.object(sim, "fetch_market_bars", return_value=mock_bars_sep30):
            sim.run_open(
                data_dir=data_dir,
                as_of="2026-09-30",
            )

        state = sim.load_state(data_dir)
        assert len(state["positions"]) == 1
        assert "3605" in state["positions"]
        assert len(state["pending_orders"]) == 0
