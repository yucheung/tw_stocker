"""TDD for as_of alignment enforcement — the "TOP7 8/24 規劃 8/14 訊號" bug.

Ref: docs/REVIEW-opus-20260907.md §1.3 (independent_sim.py:430/:700/:701)
and §2.3-1 / §3.2 步驟4: `load_orders` only checked internal consistency
(signal_date <-> next_session <-> execution_date) but never compared the
orders file's signal_date against the as_of date actually being planned —
so an explicit `--orders` path pointing at a stale, internally-consistent
file was accepted and planned as if it were today's signal. The acceptance
criteria explicitly call out that this must be caught via the --orders
explicit path, not just the auto-discovery path (which §3.1 already
content-matches by construction).
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
    }), encoding="utf-8")


class TestLoadOrdersAsOfAlignment:
    def test_as_of_none_skips_alignment_check(self, temp_dir, calendar):
        # Backward compatible: no as_of given -> no alignment enforced.
        f = temp_dir / "orders_20260814.json"
        _write_orders(f, "2026-08-14", "2026-08-17")
        orders = sim.load_orders(f, calendar=calendar)
        assert len(orders) == 1

    def test_as_of_matching_signal_date_passes(self, temp_dir, calendar):
        f = temp_dir / "orders_20260814.json"
        _write_orders(f, "2026-08-14", "2026-08-17")
        orders = sim.load_orders(f, calendar=calendar, as_of="2026-08-14")
        assert len(orders) == 1

    def test_stale_orders_file_rejected_when_as_of_mismatches(self, temp_dir, calendar):
        # Internally-consistent file (signal_date -> next_session ==
        # execution_date checks out) but it's for 8/14, not the 8/24 being
        # planned — must be rejected rather than silently accepted.
        f = temp_dir / "orders_20260814.json"
        _write_orders(f, "2026-08-14", "2026-08-17")
        with pytest.raises(ValueError, match="signal_date"):
            sim.load_orders(f, calendar=calendar, as_of="2026-08-24")


class TestRunCloseAndPlanRejectsStaleExplicitOrders:
    def test_stale_explicit_orders_path_raises_not_silently_planned(self, temp_dir):
        # Reproduces the real TOP7 incident: close-and-plan run for 8/24
        # explicitly pointed (e.g. by a manual catch-up) at the 8/14 orders
        # file. Must raise instead of planning "8/14 signal, but the code
        # thinks it's fresh" pending orders (docs/REVIEW-opus-20260907.md
        # §2.3-1).
        sim.init_simulation(data_dir=temp_dir, capital=1_000_000.0, strategy="top2_score_v1")
        stale_orders = temp_dir / "orders_20260814.json"
        _write_orders(stale_orders, "2026-08-14", "2026-08-17")

        with patch.object(sim, "fetch_market_bars", return_value={}), \
             patch.object(sim, "fetch_benchmark_close", return_value=150.0):
            with pytest.raises(ValueError, match="signal_date"):
                sim.run_close_and_plan(
                    data_dir=temp_dir,
                    orders_path=stale_orders,
                    as_of="2026-08-24",
                )

        state = sim.load_state(temp_dir)
        assert state["pending_orders"] == []
        assert state["processed_runs"] == []


class TestPlanOrdersUsesRunAsOf:
    def test_plan_orders_ignores_candidate_stale_signal_date(self):
        # Defense in depth: even if a candidate dict somehow still carries a
        # signal_date that differs from the run's as_of (independent_sim.py
        # original :700/:701 trusted the candidate's own field), plan_orders
        # must stamp orders with the actual run date, not the candidate's.
        state = sim.get_default_state(capital=200000.0)
        candidate = {
            "ticker": "2059",
            "signal_date": "2026-08-14",
            "execution_date": "2026-08-17",
            "rank": 1,
            "score": 4.0,
            "reference_close": 122.0,
            "limit_price": 122.0,
            "atr": 4.5,
        }
        pending = sim.plan_orders(state, [candidate], as_of="2026-08-24")
        assert len(pending) == 1
        assert pending[0]["signal_date"] == "2026-08-24"
        assert pending[0]["order_id"] == "top2_score_v1:2026-08-24:2059:buy"
