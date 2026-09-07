"""TDD for run_mr20.sh scheduler contract hardening.

Ref: docs/REVIEW-codex-r3-20260907.md F4 — the close-phase cron script:
  - resolved TODAY/D_COMPACT via bare `date +%Y-%m-%d`/`date +%Y%m%d`
    (host-local TZ), not pinned to Asia/Taipei like the rest of the system
    (independent_sim.get_taipei_today());
  - its session-day check swallowed every exception from the trading
    calendar lookup as "not a session" (holiday), indistinguishable from a
    genuine failure;
  - a failed signal-generation step or a failed freshness gate both logged
    a warning and exited 0, hiding the failure from cron/monitoring;
  - check_processed_runs.py (the daily processed_runs/gap monitor) was
    never actually invoked from the scheduler.

These tests drive run_mr20.sh via a fake `$PYTHON` stub (the script honors
a `PYTHON` env var override) so the control flow can be exercised without
network access, real market data, or the real trading calendar.
"""
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import independent_sim as sim

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "run_mr20.sh"


class TestIsSessionCliExitCodeContract:
    """The `is-session` CLI subcommand backing run_mr20.sh's session check
    must expose a 3-way exit-code contract (0=session/1=holiday/2=lookup
    error) instead of collapsing lookup errors into "holiday"."""

    def test_trading_day_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "independent_sim.py"), "is-session", "--date", "2026-09-07"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_weekend_exits_one(self):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "independent_sim.py"), "is-session", "--date", "2026-09-06"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        assert result.returncode == 1

    def test_malformed_date_exits_two_not_one(self):
        # A broken calendar lookup must not look like a plain holiday.
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "independent_sim.py"), "is-session", "--date", "not-a-date"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        assert result.returncode == 2


class TestIsTradingDayStrictPropagatesErrors:
    def test_valid_trading_day_true(self):
        assert sim.is_trading_day_strict("2026-09-07") is True

    def test_weekend_false(self):
        assert sim.is_trading_day_strict("2026-09-06") is False

    def test_malformed_date_raises(self):
        with pytest.raises(Exception):
            sim.is_trading_day_strict("not-a-date")

FAKE_PYTHON_SRC = textwrap.dedent(r"""
    #!/usr/bin/env python3
    import os
    import sys

    argv = sys.argv[1:]

    if argv[:1] == ['-c']:
        code = argv[1]
        if 'get_taipei_today' in code:
            print(os.environ['FAKE_TODAY'])
            sys.exit(0)
        if 'get_next_trading_day' in code:
            print(os.environ['FAKE_NEXT_DAY'])
            sys.exit(0)
        # Self-contained freshness-check snippet (json/sys only) — run for real.
        ns = {}
        exec(compile(code, '<fake-python-c>', 'exec'), ns)
        sys.exit(0)

    if argv[:2] == ['-m', 'strategy.mr20_strategy']:
        rc = int(os.environ.get('FAKE_STRATEGY_RC', '0'))
        if rc == 0:
            orders_path = os.environ.get('FAKE_ORDERS_PATH')
            content = os.environ.get('FAKE_ORDERS_CONTENT')
            if orders_path and content is not None:
                with open(orders_path, 'w', encoding='utf-8') as f:
                    f.write(content)
        sys.exit(rc)

    if argv and argv[0] == 'independent_sim.py':
        sub = argv[1] if len(argv) > 1 else None
        if sub == 'is-session':
            sys.exit(int(os.environ.get('FAKE_SESSION_RC', '0')))
        if sub == 'close-and-plan':
            sys.exit(int(os.environ.get('FAKE_CLOSE_PLAN_RC', '0')))
        if sub == 'open':
            sys.exit(int(os.environ.get('FAKE_OPEN_RC', '0')))
        if sub == 'status':
            print('FAKE STATUS')
            sys.exit(0)
        sys.exit(0)

    if argv and argv[0] == 'check_processed_runs.py':
        sys.exit(int(os.environ.get('FAKE_CHECK_RC', '0')))

    sys.exit(0)
    """)

FAKE_DATE_SRC = textwrap.dedent(r"""
    #!/usr/bin/env python3
    import sys
    print('POISON-DATE-DO-NOT-USE', file=sys.stderr)
    sys.exit(1)
    """)


@pytest.fixture
def fake_bin(tmp_path):
    """A fake `$PYTHON` + a fake `date` (in PATH) that fails loudly if
    called, to prove run_mr20.sh no longer resolves TODAY via bare `date`.
    """
    python_path = tmp_path / "fake_python3"
    python_path.write_text(FAKE_PYTHON_SRC.lstrip("\n"))
    python_path.chmod(python_path.stat().st_mode | stat.S_IEXEC)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    date_path = bin_dir / "date"
    date_path.write_text(FAKE_DATE_SRC.lstrip("\n"))
    date_path.chmod(date_path.stat().st_mode | stat.S_IEXEC)

    return python_path, bin_dir


def _run(mode, env_overrides, fake_bin):
    python_path, poison_date_dir = fake_bin
    env = dict(os.environ)
    env["PYTHON"] = str(python_path)
    env["PATH"] = f"{poison_date_dir}:{env['PATH']}"
    # Deliberately implausible date: run_mr20.sh writes/reads
    # artifacts/mr20/orders_mr20_<D_COMPACT>.json in the REAL repo checkout
    # (it hardcodes `cd /root/work/tw_stocker`), so this test touches a real
    # path on disk. A real-looking date could collide with and destroy a
    # genuine production artifact; 2099 never will.
    env.setdefault("FAKE_TODAY", "2099-01-05")
    env.setdefault("FAKE_NEXT_DAY", "2099-01-06")
    env.setdefault("FAKE_SESSION_RC", "0")
    env.setdefault("FAKE_STRATEGY_RC", "0")
    env.setdefault("FAKE_CLOSE_PLAN_RC", "0")
    env.setdefault("FAKE_CHECK_RC", "0")
    env.setdefault(
        "FAKE_ORDERS_CONTENT",
        '{"diagnostic": {"signal_date": "%s", "execution_date": "%s"}}'
        % (env["FAKE_TODAY"], env["FAKE_NEXT_DAY"]),
    )
    env.update(env_overrides)
    # ORDERS filename is derived from D_COMPACT ("%s" % D with '-' stripped);
    # point the strategy stub at the exact path run_mr20.sh will look for.
    d_compact = env["FAKE_TODAY"].replace("-", "")
    real_orders_path = REPO_ROOT / f"artifacts/mr20/orders_mr20_{d_compact}.json"
    env["FAKE_ORDERS_PATH"] = str(real_orders_path)
    # Refuse to run against a path that already exists on disk — never
    # delete a file this test didn't create itself.
    if real_orders_path.exists():
        pytest.fail(
            f"{real_orders_path} already exists; refusing to run (would risk "
            "deleting a real file). Pick a different FAKE_TODAY."
        )
    try:
        result = subprocess.run(
            ["bash", str(SCRIPT), mode],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        if real_orders_path.exists():
            real_orders_path.unlink()
    return result


class TestTaipeiTimezonePinning:
    def test_close_mode_never_invokes_host_date_command(self, fake_bin):
        # A poisoned `date` on PATH exits 1 and prints a sentinel to stderr.
        # If run_mr20.sh still resolves TODAY/D via `date`, this run either
        # fails outright or the sentinel leaks into stderr.
        result = _run("close", {}, fake_bin)
        assert "POISON-DATE-DO-NOT-USE" not in result.stderr
        assert "POISON-DATE-DO-NOT-USE" not in result.stdout

    def test_open_mode_never_invokes_host_date_command(self, fake_bin):
        result = _run("open", {}, fake_bin)
        assert "POISON-DATE-DO-NOT-USE" not in result.stderr
        assert "POISON-DATE-DO-NOT-USE" not in result.stdout


class TestSessionCheckErrorVsHoliday:
    def test_holiday_skips_with_exit_zero(self, fake_bin):
        result = _run("close", {"FAKE_SESSION_RC": "1"}, fake_bin)
        assert result.returncode == 0
        assert "非 XTAI 交易日" in result.stdout

    def test_session_lookup_error_is_not_treated_as_holiday(self, fake_bin):
        result = _run("close", {"FAKE_SESSION_RC": "2"}, fake_bin)
        assert result.returncode != 0
        assert "非 XTAI 交易日" not in result.stdout


class TestFailuresExitNonZero:
    def test_strategy_generation_failure_exits_nonzero(self, fake_bin):
        result = _run("close", {"FAKE_STRATEGY_RC": "1"}, fake_bin)
        assert result.returncode != 0

    def test_freshness_gate_failure_exits_nonzero(self, fake_bin):
        # Strategy "succeeds" but writes an orders file whose diagnostic
        # signal_date doesn't match D — freshness gate must reject it.
        env = {
            "FAKE_ORDERS_CONTENT": '{"diagnostic": {"signal_date": "2020-01-01", "execution_date": "2020-01-02"}}',
        }
        result = _run("close", env, fake_bin)
        assert result.returncode != 0

    def test_healthy_close_run_still_exits_zero(self, fake_bin):
        result = _run("close", {}, fake_bin)
        assert result.returncode == 0


class TestCheckProcessedRunsWiredIntoScheduler:
    def test_daily_monitor_failure_fails_the_close_run(self, fake_bin):
        result = _run("close", {"FAKE_CHECK_RC": "1"}, fake_bin)
        assert result.returncode != 0

    def test_daily_monitor_invoked_on_healthy_close_run(self, fake_bin):
        result = _run("close", {"FAKE_CHECK_RC": "0"}, fake_bin)
        assert result.returncode == 0
        assert "check_processed_runs" in (result.stdout + result.stderr) or "Daily Monitor" in result.stdout
