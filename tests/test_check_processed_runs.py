"""TDD for check_processed_runs.py — daily alert on missing close-and-plan/open hops.

Ref: docs/REVIEW-opus-20260907.md §3.1-3 / §3.2 步驟2 —「一條每日核對
processed_runs 是否同時含當日 close-and-plan 與 open、缺任一即告警的檢查」。
"""
import shutil
import tempfile
from pathlib import Path

import pytest

import independent_sim as sim
from check_processed_runs import check_processed_runs


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


TRADING_DAY = "2026-09-07"  # Monday, XTAI session
NON_TRADING_DAY = "2026-09-06"  # Sunday


def test_non_trading_day_has_no_missing_runs(temp_dir):
    sim.init_simulation(data_dir=temp_dir, capital=100000.0)
    assert check_processed_runs(temp_dir, NON_TRADING_DAY) == []


def test_trading_day_missing_both_hops(temp_dir):
    sim.init_simulation(data_dir=temp_dir, capital=100000.0)
    missing = check_processed_runs(temp_dir, TRADING_DAY)
    assert missing == [f"close-and-plan:{TRADING_DAY}", f"open:{TRADING_DAY}"]


def test_trading_day_missing_open_only(temp_dir):
    sim.init_simulation(data_dir=temp_dir, capital=100000.0)
    state = sim.load_state(temp_dir)
    sim.mark_run_processed(state, f"close-and-plan:{TRADING_DAY}")
    sim.save_state_atomic(state, data_dir=temp_dir)

    missing = check_processed_runs(temp_dir, TRADING_DAY)
    assert missing == [f"open:{TRADING_DAY}"]


def test_trading_day_both_hops_present(temp_dir):
    sim.init_simulation(data_dir=temp_dir, capital=100000.0)
    state = sim.load_state(temp_dir)
    sim.mark_run_processed(state, f"close-and-plan:{TRADING_DAY}")
    sim.mark_run_processed(state, f"open:{TRADING_DAY}")
    sim.save_state_atomic(state, data_dir=temp_dir)

    assert check_processed_runs(temp_dir, TRADING_DAY) == []
