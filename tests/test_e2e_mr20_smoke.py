"""End-to-end smoke test for the MR20 pipeline: order generation -> plan ->
next-day open -> terminal order_events landed.

Ref: docs/REVIEW-opus-20260907.md §3.1-3 / §3.2 步驟2, and
docs/REVIEW-codex-r2-20260907.md F2 — the smoke test previously started from
a hand-written orders JSON, skipping the order-generation hop entirely (no
call to strategy.mr20_strategy.generate_mr20_orders / the CLI). That let a
break in hop 1 go undetected. This drives the real generate_mr20_orders()
against a fixed, deterministic synthetic price panel (no network) to
produce the orders artifact, then feeds that real artifact through the rest
of the pipeline independent_sim.py owns — three hops end to end: generate
-> close-and-plan -> open.
"""
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import exchange_calendars as xcals

import independent_sim as sim
from check_processed_runs import check_processed_runs
from strategy.mr20_strategy import generate_mr20_orders, save_orders_to_json

SIGNAL_DATE = "2026-09-02"
EXEC_DATE = "2026-09-03"
TICKER = "2330"


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _generate_real_orders_artifact(orders_file: Path) -> dict:
    """Hop 1: real order generation via a fixed synthetic price panel — no
    network fetch, but the actual MR20 filter/scoring/order-building code
    path, not a hand-authored JSON fixture."""
    calendar = xcals.get_calendar("XTAI")
    dates = pd.date_range(end=SIGNAL_DATE, periods=70, freq="B")
    assert dates[-1].strftime("%Y-%m-%d") == SIGNAL_DATE

    # Uptrend into an oversold RSI(5) pullback near MA20 — passes the MR20
    # filter (same shape as tests/test_mr20_strategy.py's known-passing
    # fixture).
    prices = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 132.0, 136.0]
    close_df = pd.DataFrame({TICKER: prices}, index=dates)
    vol_df = pd.DataFrame({TICKER: np.full(70, 10_000_000.0)}, index=dates)

    orders_dict = generate_mr20_orders(close_df, vol_df, signal_date=SIGNAL_DATE, calendar=calendar)
    assert len(orders_dict["orders"]) == 1
    assert orders_dict["orders"][0]["ticker"] == TICKER
    save_orders_to_json(orders_dict, orders_file)
    return orders_dict


def test_generate_to_plan_to_open_lands_terminal_event(temp_dir):
    sim.init_simulation(data_dir=temp_dir, capital=1_000_000.0, strategy="mr20")

    orders_file = temp_dir / f"orders_mr20_{SIGNAL_DATE.replace('-', '')}.json"
    orders_dict = _generate_real_orders_artifact(orders_file)
    limit_price = orders_dict["orders"][0]["limit_price"]

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
    assert state["pending_orders"][0]["ticker"] == TICKER

    # Hop 3: open execution on the next trading day (D+1 09:35). Open below
    # the limit price so the order actually fills.
    assert limit_price > 130.0
    open_bars = {
        TICKER: {"date": EXEC_DATE, "open": 130.0, "high": 138.0, "low": 128.0, "close": 133.0},
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
    assert filled["ticker"] == TICKER
    assert state["pending_orders"] == []
    assert TICKER in state["positions"]

    # SIGNAL_DATE only ever gets a close-and-plan (this is D-1, the very
    # first day of the sim — nothing was pending before it to open). The
    # first calendar day that can show a *complete* pair is EXEC_DATE: its
    # open already ran above; run its close-and-plan (settle + plan next,
    # no new orders) to complete the pair and confirm the daily check goes
    # green for a genuinely fully-processed trading day.
    empty_orders_file = temp_dir / "orders_mr20_no_new_signal.json"
    empty_orders_file.write_text(json.dumps({"orders": []}), encoding="utf-8")
    with patch_market_bars(sim, {TICKER: {"date": EXEC_DATE, "open": 130.0, "high": 138.0, "low": 128.0, "close": 133.0}}), \
         patch_benchmark_close(sim, 150.0):
        sim.run_close_and_plan(
            data_dir=temp_dir,
            # Explicit empty-orders file (rather than orders_path=None) avoids
            # the auto-discovery fallback, which searches the real repo's
            # artifacts/mr20/ dir and would otherwise pick up production
            # orders_mr20_20260903.json.
            orders_path=empty_orders_file,
            as_of=EXEC_DATE,
        )

    assert check_processed_runs(temp_dir, EXEC_DATE) == []
    # An explicit, fresh, but empty orders file is a legitimate zero-signal
    # day — not a planning gap.
    state = sim.load_state(temp_dir)
    assert state.get("planning_gaps", []) == []


def test_auto_discovery_miss_is_a_real_gap_surfaced_by_the_daily_check(temp_dir):
    # If hop 1 (order generation) never produced a file for the signal date
    # and auto-discovery can't find one either, that is genuinely
    # indistinguishable from an upstream failure — close-and-plan still
    # records its own hop as processed (settlement can't be undone), but
    # the miss must show up as an unresolved planning_gap in the daily
    # check, not as a silently healthy day (docs/REVIEW-codex-r2-20260907.md
    # F1). Uses a date with no real orders_mr20_*.json fixture anywhere in
    # the repo, so the auto-discovery fallback in run_close_and_plan (which
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
    assert state.get("planning_gaps") == [no_orders_date]
    assert check_processed_runs(temp_dir, no_orders_date) == [
        f"open:{no_orders_date}",
        f"planning_gap:{no_orders_date}",
    ]

    # The gap is retryable same-day once a late orders file shows up —
    # backfilling it clears the alert without re-running settlement.
    late_orders_file = temp_dir / "orders_mr20_late_catchup.json"
    calendar = xcals.get_calendar("XTAI")
    dates = pd.date_range(end=no_orders_date, periods=70, freq="B")
    prices = np.linspace(60.0, 180.0, 65).tolist() + [160.0, 150.0, 140.0, 132.0, 136.0]
    close_df = pd.DataFrame({TICKER: prices}, index=dates)
    vol_df = pd.DataFrame({TICKER: np.full(70, 10_000_000.0)}, index=dates)
    orders_dict = generate_mr20_orders(close_df, vol_df, signal_date=no_orders_date, calendar=calendar)
    save_orders_to_json(orders_dict, late_orders_file)

    sim.run_close_and_plan(
        data_dir=temp_dir,
        orders_path=late_orders_file,
        as_of=no_orders_date,
    )

    state = sim.load_state(temp_dir)
    assert state.get("planning_gaps", []) == []
    assert len(state["pending_orders"]) == 1
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
