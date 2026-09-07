"""TDD for check_processed_runs.py — daily alert on missing close-and-plan/open hops.

Ref: docs/REVIEW-opus-20260907.md §3.1-3 / §3.2 步驟2 —「一條每日核對
processed_runs 是否同時含當日 close-and-plan 與 open、缺任一即告警的檢查」。

Ref: docs/REVIEW-codex-r2-20260907.md F1 — the check previously only read
two run_ids and never looked at state["planning_gaps"], so a day where
close-and-plan ran but couldn't find an orders file (a real gap, see
independent_sim.py's run_close_and_plan/_resolve_and_plan_orders) showed up
as fully healthy. Also, the previous test suite only ever called the
check_processed_runs() function directly, never the CLI's actual alert path
(exit code / stderr) — a bug in main()'s exit/print logic could hide behind
green tests. TestCheckProcessedRunsCLIAlertPath below drives the real
`python3 check_processed_runs.py` subprocess end-to-end.
"""
import shutil
import subprocess
import sys
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
REPO_ROOT = Path(__file__).resolve().parent.parent


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


def test_trading_day_both_hops_present_but_planning_gap_open(temp_dir):
    """Both run_ids are marked processed (close-and-plan settled and marked
    the day done) but the orders file was never found — a real gap that a
    check reading only run_ids would miss entirely."""
    sim.init_simulation(data_dir=temp_dir, capital=100000.0)
    state = sim.load_state(temp_dir)
    sim.mark_run_processed(state, f"close-and-plan:{TRADING_DAY}")
    sim.mark_run_processed(state, f"open:{TRADING_DAY}")
    state.setdefault("planning_gaps", []).append(TRADING_DAY)
    sim.save_state_atomic(state, data_dir=temp_dir)

    assert check_processed_runs(temp_dir, TRADING_DAY) == [f"planning_gap:{TRADING_DAY}"]


def test_planning_gap_for_a_different_date_does_not_leak_into_this_check(temp_dir):
    sim.init_simulation(data_dir=temp_dir, capital=100000.0)
    state = sim.load_state(temp_dir)
    sim.mark_run_processed(state, f"close-and-plan:{TRADING_DAY}")
    sim.mark_run_processed(state, f"open:{TRADING_DAY}")
    state.setdefault("planning_gaps", []).append("2026-09-01")
    sim.save_state_atomic(state, data_dir=temp_dir)

    assert check_processed_runs(temp_dir, TRADING_DAY) == []


class TestCheckProcessedRunsCLIAlertPath:
    """Exercises the real `python3 check_processed_runs.py` subprocess so the
    alert path itself (stderr message + non-zero exit code) is verified,
    not just the internal check_processed_runs() function being invoked."""

    def _run_cli(self, data_dir, date):
        return subprocess.run(
            [sys.executable, "check_processed_runs.py", "--data-dir", str(data_dir), "--date", date],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_cli_exits_nonzero_and_reports_missing_hops_on_stderr(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=100000.0)
        result = self._run_cli(temp_dir, TRADING_DAY)

        assert result.returncode == 1
        assert f"close-and-plan:{TRADING_DAY}" in result.stderr
        assert f"open:{TRADING_DAY}" in result.stderr

    def test_cli_exits_zero_and_reports_ok_on_stdout_when_healthy(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=100000.0)
        state = sim.load_state(temp_dir)
        sim.mark_run_processed(state, f"close-and-plan:{TRADING_DAY}")
        sim.mark_run_processed(state, f"open:{TRADING_DAY}")
        sim.save_state_atomic(state, data_dir=temp_dir)

        result = self._run_cli(temp_dir, TRADING_DAY)

        assert result.returncode == 0
        assert "OK" in result.stdout
        assert result.stderr == ""

    def test_cli_exits_nonzero_on_unresolved_planning_gap_even_with_both_run_ids_present(self, temp_dir):
        sim.init_simulation(data_dir=temp_dir, capital=100000.0)
        state = sim.load_state(temp_dir)
        sim.mark_run_processed(state, f"close-and-plan:{TRADING_DAY}")
        sim.mark_run_processed(state, f"open:{TRADING_DAY}")
        state.setdefault("planning_gaps", []).append(TRADING_DAY)
        sim.save_state_atomic(state, data_dir=temp_dir)

        result = self._run_cli(temp_dir, TRADING_DAY)

        assert result.returncode == 1
        assert f"planning_gap:{TRADING_DAY}" in result.stderr
