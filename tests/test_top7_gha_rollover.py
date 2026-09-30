"""Tests for Top-7 GHA late delivery orphan order rollover.

Validates that:
1. Strategy configs define allow_orders_fallback flag:
   - top7: True
   - top2_score_v1 (DEFAULT): True
   - mr20: False
2. When GHA delivers artifacts/orders_YYYYMMDD.json after 18:05,
   close-and-plan on the next trading day automatically discovers
   the latest valid orders file where:
     signal_date <= today and execution_date >= today
   for strategies with allow_orders_fallback=True (top7 and DEFAULT).
3. Outdated orders where execution_date < today are strictly rejected as stale.
4. MR20 strategy is untouched and does not use rollover (allow_orders_fallback=False).
5. DEFAULT strategy behavior is preserved.
6. End-to-end simulation from GHA late delivery -> next day rollover planning -> open fill.
"""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
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
    def test_strategy_configs_allow_orders_fallback_flags(self):
        """Top-7 and DEFAULT strategy have allow_orders_fallback=True; MR20 has False."""
        cfg_top7 = sim.get_strategy_config("top7")
        cfg_default = sim.get_strategy_config(sim.DEFAULT_STRATEGY_ID)
        cfg_mr20 = sim.get_strategy_config("mr20")

        assert cfg_top7.get("allow_orders_fallback") is True
        assert cfg_default.get("allow_orders_fallback") is True
        assert cfg_mr20.get("allow_orders_fallback", False) is False

    def test_top7_resolve_orders_file_picks_latest_valid_rollover(self, temp_dir):
        """When today's orders file does not exist, resolve_orders_file for top7 finds
        the latest file where signal_date <= today and execution_date >= today.
        """
        f_sep24 = temp_dir / "orders_20260924.json"
        _write_orders_file(f_sep24, signal_date="2026-09-24", execution_date="2026-09-29", ticker="3605")

        resolved = sim.resolve_orders_file(
            strat_id="top7",
            today_str="2026-09-29",
            orders_dir=temp_dir,
        )
        assert resolved == f_sep24

    def test_default_strategy_resolve_orders_file_picks_latest_valid_rollover(self, temp_dir):
        """DEFAULT strategy still picks valid rollover (behavior unchanged)."""
        f_sep24 = temp_dir / "orders_20260924.json"
        _write_orders_file(f_sep24, signal_date="2026-09-24", execution_date="2026-09-29", ticker="3605")

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
            strat_id="top7",
            today_str="2026-09-29",
            orders_dir=temp_dir,
        )
        assert resolved is None

    def test_resolve_orders_file_fast_candidates_fallback(self, temp_dir):
        """When orders_dir is None, base_dir != Path('artifacts'), and allow_orders_fallback=True,
        fast_candidates includes Path('artifacts') / f'orders_{compact_date}.json'.
        For strategies with allow_orders_fallback=False, it does not.
        """
        custom_dir = temp_dir / "custom_orders"
        custom_dir.mkdir(parents=True, exist_ok=True)
        art_file = Path("artifacts") / "orders_99991231.json"
        _write_orders_file(art_file, signal_date="9999-12-31", execution_date="9999-12-31", ticker="3605")

        try:
            # Top-7 has allow_orders_fallback=True -> fallback to artifacts
            with patch.dict(sim.STRATEGY_CONFIGS["top7"], {"orders_dir": str(custom_dir)}):
                resolved = sim.resolve_orders_file("top7", "9999-12-31", orders_dir=None)
                assert resolved == art_file

            # MR20 has allow_orders_fallback=False -> does NOT fallback to artifacts
            with patch.dict(sim.STRATEGY_CONFIGS["mr20"], {"orders_dir": str(custom_dir)}):
                resolved = sim.resolve_orders_file("mr20", "9999-12-31", orders_dir=None)
                assert resolved is None
        finally:
            if art_file.exists():
                art_file.unlink()

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

    def test_plan_orders_for_day_allow_rollover_by_strategy(self, temp_dir):
        """_resolve_and_plan_orders respects allow_orders_fallback flag:
        - top7 allows rollover: loads rollover file without ValueError and plans orders.
        - DEFAULT allows rollover: loads rollover file without ValueError and plans orders.
        - mr20 does NOT allow rollover: auto-discovery skips rollover and flags gap.
        """
        orders_dir = temp_dir / "artifacts"
        f_sep24 = orders_dir / "orders_20260924.json"
        _write_orders_file(f_sep24, signal_date="2026-09-24", execution_date="2026-09-29", ticker="3605")

        mock_closes = pd.Series([180.0, 182.0, 183.5] * 10, index=pd.date_range("2026-08-01", periods=30))

        # 1. Top-7 state
        state_top7 = sim.get_default_state(strategy_id="top7")
        with patch.dict(sim.STRATEGY_CONFIGS["top7"], {"orders_dir": str(orders_dir)}), \
             patch("independent_sim.fetch_ticker_closes", return_value=mock_closes):
            count_top7 = sim._resolve_and_plan_orders(
                state_top7, today_str="2026-09-29", orders_path=None, max_price=None, tickers=None
            )
            assert count_top7 == 1
            assert len(state_top7["pending_orders"]) == 1
            assert state_top7["pending_orders"][0]["ticker"] == "3605"

        # 2. DEFAULT strategy state
        state_default = sim.get_default_state(strategy_id=sim.DEFAULT_STRATEGY_ID)
        with patch.dict(sim.STRATEGY_CONFIGS[sim.DEFAULT_STRATEGY_ID], {"orders_dir": str(orders_dir)}), \
             patch("independent_sim.fetch_ticker_closes", return_value=mock_closes):
            count_default = sim._resolve_and_plan_orders(
                state_default, today_str="2026-09-29", orders_path=None, max_price=None, tickers=None
            )
            assert count_default == 1
            assert len(state_default["pending_orders"]) == 1

        # 3. MR20 state with MR20 orders file (rollover dates)
        f_mr20 = orders_dir / "orders_mr20_20260924.json"
        _write_orders_file(f_mr20, signal_date="2026-09-24", execution_date="2026-09-29", ticker="6446")
        state_mr20 = sim.get_default_state(strategy_id="mr20")
        with patch.dict(sim.STRATEGY_CONFIGS["mr20"], {"orders_dir": str(orders_dir)}):
            count_mr20 = sim._resolve_and_plan_orders(
                state_mr20, today_str="2026-09-29", orders_path=None, max_price=None, tickers=None
            )
            assert count_mr20 == 0
            assert "2026-09-29" in state_mr20["planning_gaps"]
            assert len(state_mr20["pending_orders"]) == 0

    def test_top7_close_and_plan_e2e_rollover(self, temp_dir):
        """Full end-to-end for Top-7 strategy:
        1. 2026-09-24 18:05: close-and-plan runs without orders -> records gap.
        2. GHA delivers orders_20260924.json late.
        3. 2026-09-29 18:05: close-and-plan runs for top7, auto-discovers orders_20260924.json,
           plans 3605 for 2026-09-30 execution, clears gaps.
        4. 2026-09-30 09:35: open runs, fills 3605.
        """
        data_dir = temp_dir / "sim_data_top7"
        orders_dir = temp_dir / "artifacts"
        sim.init_simulation(data_dir=data_dir, capital=20_000.0, strategy="top7")

        # 1. 2026-09-24 18:05: GHA has not delivered yet (orders_dir is empty)
        with patch.object(sim, "fetch_market_bars", return_value={}), \
             patch.object(sim, "fetch_benchmark_close", return_value=150.0), \
             patch.dict(sim.STRATEGY_CONFIGS["top7"], {"orders_dir": str(orders_dir)}):
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
             patch.dict(sim.STRATEGY_CONFIGS["top7"], {"orders_dir": str(orders_dir)}):
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

    def test_default_strategy_close_and_plan_e2e_rollover(self, temp_dir):
        """DEFAULT strategy still works end-to-end with rollover (behavior unchanged)."""
        data_dir = temp_dir / "sim_data_default"
        orders_dir = temp_dir / "artifacts"
        sim.init_simulation(data_dir=data_dir, capital=500_000.0, strategy=sim.DEFAULT_STRATEGY_ID)

        # 1. 2026-09-24 18:05: GHA has not delivered yet
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

        # 2. GHA delivers orders_20260924.json late
        orders_file = orders_dir / "orders_20260924.json"
        _write_orders_file(
            orders_file,
            signal_date="2026-09-24",
            execution_date="2026-09-29",
            ticker="3605",
            limit_price=183.5,
            atr=12.1,
        )

        # 3. 2026-09-29 18:05: close-and-plan runs
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
