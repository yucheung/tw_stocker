"""TDD for content-based orders-file resolution and the close-and-plan
missing-orders state machine.

Ref: docs/REVIEW-opus-20260907.md §3.1-1 / §3.2 步驟3 —
independent_sim.py:1516/:1519 資解候選檔僅用 as_of 組檔名而非比對訂單內容
signal_date，且 :1546/:1550 缺檔一律靜默略過並標記完成，讓自動搜尋找不到
檔案與「合法零訊號日」無法區分、也讓明確指定的 --orders 路徑打錯字時被
靜默吞掉。
"""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import independent_sim as sim


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _write_orders(path: Path, signal_date: str, execution_date: str, ticker: str = "2059"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "orders": [{
            "signal_date": signal_date,
            "execution_date": execution_date,
            "ticker": ticker,
            "rank": 1,
            "score": 1.0,
            "reference_close": 100.0,
            "limit_price": 100.0,
            "atr": 5.0,
        }],
        "diagnostic": {"signal_date": signal_date, "execution_date": execution_date},
    }), encoding="utf-8")


class TestResolveOrdersFile:
    def test_fast_path_matches_conventional_filename(self, temp_dir):
        f = temp_dir / "orders_mr20_20260902.json"
        _write_orders(f, "2026-09-02", "2026-09-03")
        found = sim.resolve_orders_file("mr20", "2026-09-02", orders_dir=temp_dir)
        assert found == f

    def test_falls_back_to_content_scan_when_filename_mismatched(self, temp_dir):
        # Saved under an unexpected name (e.g. a delayed/manual catch-up
        # run) — content signal_date is still 2026-09-02, must be found.
        f = temp_dir / "orders_mr20_late_catchup.json"
        _write_orders(f, "2026-09-02", "2026-09-03")
        found = sim.resolve_orders_file("mr20", "2026-09-02", orders_dir=temp_dir)
        assert found == f

    def test_conventional_filename_with_wrong_content_is_rejected(self, temp_dir):
        # File exists at the expected path but its content is for a
        # different signal_date (e.g. left over from a previous run) —
        # must not be blindly trusted just because the filename matches.
        f = temp_dir / "orders_mr20_20260902.json"
        _write_orders(f, "2026-08-31", "2026-09-01")
        found = sim.resolve_orders_file("mr20", "2026-09-02", orders_dir=temp_dir)
        assert found is None

    def test_no_matching_file_returns_none(self, temp_dir):
        assert sim.resolve_orders_file("mr20", "2026-09-02", orders_dir=temp_dir) is None


class TestCloseAndPlanMissingOrdersStateMachine:
    def test_explicit_orders_path_missing_raises_and_persists_nothing(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=1_000_000.0, strategy="mr20")
        missing_path = temp_dir / "does_not_exist.json"

        with patch.object(sim, "fetch_market_bars", return_value={}), \
             patch.object(sim, "fetch_benchmark_close", return_value=150.0):
            with pytest.raises(FileNotFoundError):
                sim.run_close_and_plan(
                    data_dir=temp_dir,
                    orders_path=missing_path,
                    as_of="2026-09-02",
                )

        state = sim.load_state(temp_dir)
        assert state["processed_runs"] == []
        assert state["equity_curve"] == []

    def test_auto_discovery_miss_still_completes_settlement_and_flags_gap(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=1_000_000.0, strategy="mr20")

        with patch.object(sim, "fetch_market_bars", return_value={}), \
             patch.object(sim, "fetch_benchmark_close", return_value=150.0), \
             patch.object(sim, "resolve_orders_file", return_value=None):
            sim.run_close_and_plan(
                data_dir=temp_dir,
                orders_path=None,
                as_of="2026-09-02",
            )

        state = sim.load_state(temp_dir)
        assert "close-and-plan:2026-09-02" in state["processed_runs"]
        assert len(state["equity_curve"]) == 1
        assert state["pending_orders"] == []
        assert state.get("planning_gaps") == ["2026-09-02"]

    def test_auto_discovery_hit_under_unconventional_name_still_plans(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=1_000_000.0, strategy="mr20")
        f = temp_dir / "weirdly_named_orders.json"
        _write_orders(f, "2026-09-02", "2026-09-03")

        with patch.object(sim, "fetch_market_bars", return_value={}), \
             patch.object(sim, "fetch_benchmark_close", return_value=150.0), \
             patch.object(sim, "resolve_orders_file", return_value=f):
            sim.run_close_and_plan(
                data_dir=temp_dir,
                orders_path=None,
                as_of="2026-09-02",
            )

        state = sim.load_state(temp_dir)
        assert len(state["pending_orders"]) == 1
        assert state["pending_orders"][0]["ticker"] == "2059"
        assert state.get("planning_gaps", []) == []
