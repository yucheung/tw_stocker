"""End-to-end smoke test for the MR20 pipeline: orders artifact -> plan ->
next-day open -> terminal order_events landed.

Ref: docs/REVIEW-opus-20260907.md §3.1-3 / §3.2 步驟2 — this must go green
before any strategy parameter is touched. The "order generation" hop itself
(strategy/mr20_strategy.py, which needs network data) is out of scope here;
this test starts from a realistic orders_mr20_*.json artifact (schema
matches artifacts/mr20/orders_mr20_20260902.json) and drives the rest of the
pipeline that independent_sim.py owns.
"""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

import independent_sim as sim
from check_processed_runs import check_processed_runs

SIGNAL_DATE = "2026-09-02"
EXEC_DATE = "2026-09-03"


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _mr20_orders_artifact():
    return {
        "orders": [
            {
                "signal_date": SIGNAL_DATE,
                "execution_date": EXEC_DATE,
                "ticker": "3037",
                "side": "buy",
                "order_type": "limit",
                "limit_price": 973.0,
                "reference_close": 973.0,
                "rank": 1,
                "score": 73.4577,
                "atr": 27.9166,
                "tp_atr_mult": 4.0,
                "sl_atr_mult": 3.0,
                "max_hold_days": 20,
                "position_size": 0.45,
                "tp_sl_mode": "atr",
            }
        ],
        "diagnostic": {
            "strategy": "mr20",
            "signal_date": SIGNAL_DATE,
            "execution_date": EXEC_DATE,
        },
    }


def test_orders_to_plan_to_open_lands_terminal_event(temp_dir):
    sim.init_simulation(data_dir=temp_dir, capital=1_000_000.0, strategy="mr20")

    orders_file = temp_dir / f"orders_mr20_{SIGNAL_DATE.replace('-', '')}.json"
    orders_file.write_text(json.dumps(_mr20_orders_artifact()), encoding="utf-8")

    # Hop 2: close-and-plan on the signal date (D 18:05) — reads the orders
    # artifact produced by hop 1 and plans a pending order for EXEC_DATE.
    with patch_market_bars(sim, {}), patch_benchmark_close(sim, 150.0):
        sim.run_close_and_plan(
            data_dir=temp_dir,
            orders_path=orders_file,
            as_of=SIGNAL_DATE,
        )

    state = sim.load_state(temp_dir)
    assert len(state["pending_orders"]) == 1
    assert state["pending_orders"][0]["ticker"] == "3037"

    # Hop 3: open execution on the next trading day (D+1 09:35).
    open_bars = {
        "3037": {"date": EXEC_DATE, "open": 960.0, "high": 980.0, "low": 955.0, "close": 970.0},
    }
    with patch_market_bars(sim, open_bars):
        sim.run_open(data_dir=temp_dir, as_of=EXEC_DATE)

    state = sim.load_state(temp_dir)

    # A terminal FILLED event must have landed (open < limit) alongside the
    # PENDING event recorded at planning time — not silently dropped as a
    # phantom pending order.
    statuses = [e["status"] for e in state["order_events"]]
    assert statuses.count("PENDING") == 1
    assert statuses.count("FILLED") == 1
    filled = next(e for e in state["order_events"] if e["status"] == "FILLED")
    assert filled["ticker"] == "3037"
    assert state["pending_orders"] == []
    assert "3037" in state["positions"]

    # SIGNAL_DATE only ever gets a close-and-plan (this is D-1, the very
    # first day of the sim — nothing was pending before it to open). The
    # first calendar day that can show a *complete* pair is EXEC_DATE: its
    # open already ran above; run its close-and-plan (settle + plan next,
    # no new orders) to complete the pair and confirm the daily check goes
    # green for a genuinely fully-processed trading day.
    with patch_market_bars(sim, {"3037": {"date": EXEC_DATE, "open": 960.0, "high": 980.0, "low": 955.0, "close": 970.0}}), \
         patch_benchmark_close(sim, 150.0):
        sim.run_close_and_plan(
            data_dir=temp_dir,
            # Explicit nonexistent path avoids the auto-discovery fallback
            # (which searches the real repo's artifacts/mr20/ dir and would
            # otherwise pick up production orders_mr20_20260903.json).
            orders_path=temp_dir / "no_such_orders_file.json",
            as_of=EXEC_DATE,
        )

    assert check_processed_runs(temp_dir, EXEC_DATE) == []


def test_missing_orders_file_still_marks_close_and_plan_hop_processed(temp_dir):
    # If hop 1 (order generation) never produced a file for the signal date,
    # close-and-plan must still record its own hop as processed (a zero-order
    # day is a legitimate outcome, not an error) while planning zero orders.
    # The check correctly still flags open:no_orders_date as missing here
    # since this test never calls run_open — it only exercises close-and-plan.
    # Uses a date with no real orders_mr20_*.json fixture anywhere in the
    # repo, so the auto-discovery fallback in run_close_and_plan (which
    # searches the real artifacts/mr20/ dir, not temp_dir) can't accidentally
    # pick up production data.
    no_orders_date = "2026-09-10"
    sim.init_simulation(data_dir=temp_dir, capital=1_000_000.0, strategy="mr20")

    with patch_market_bars(sim, {}), patch_benchmark_close(sim, 150.0):
        sim.run_close_and_plan(
            data_dir=temp_dir,
            orders_path=None,
            as_of=no_orders_date,
        )

    state = sim.load_state(temp_dir)
    assert state["pending_orders"] == []
    assert check_processed_runs(temp_dir, no_orders_date) == [f"open:{no_orders_date}"]


# ── Minimal local mocking helpers (avoid pulling unittest.mock boilerplate
# into every test body above) ──
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def patch_market_bars(module, bars):
    with patch.object(module, "fetch_market_bars", return_value=bars):
        yield


@contextmanager
def patch_benchmark_close(module, value):
    with patch.object(module, "fetch_benchmark_close", return_value=value):
        yield
