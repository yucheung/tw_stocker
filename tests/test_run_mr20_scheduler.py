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
import shutil
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
        assert "SESSION_RESULT:TRADING_DAY:" in result.stdout

    def test_weekend_exits_one(self):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "independent_sim.py"), "is-session", "--date", "2026-09-06"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        assert result.returncode == 1
        # run_mr20.sh must be able to tell a genuine holiday apart from any
        # other exit-1 (e.g. an uncaught crash) via a recognizable marker,
        # not exit code alone (docs/REVIEW-codex-r4-20260907.md R4-1).
        assert "SESSION_RESULT:HOLIDAY:" in result.stdout

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
            rc = int(os.environ.get('FAKE_SESSION_RC', '0'))
            # Real CLI only ever prints SESSION_RESULT:* for rc 0/1. Any
            # other rc (2, or a signal kill like 137) — and, to simulate an
            # uncaught crash that happens to also exit 1 — leave FAKE_SESSION_NO_MARKER
            # unset to omit the marker even for rc 1.
            if not os.environ.get('FAKE_SESSION_NO_MARKER'):
                if rc == 0:
                    print(f"SESSION_RESULT:TRADING_DAY:{os.environ.get('FAKE_TODAY', '')}")
                elif rc == 1:
                    print(f"SESSION_RESULT:HOLIDAY:{os.environ.get('FAKE_TODAY', '')}")
            sys.exit(rc)
        if sub == 'close-and-plan':
            if '--orders' in argv:
                print('CLOSE_AND_PLAN_ORDERS_ARG_PRESENT')
            else:
                print('CLOSE_AND_PLAN_ORDERS_ARG_ABSENT')
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
    """An isolated run_mr20.sh checkout: the real script copied into its own
    tmp workdir (with an `artifacts/mr20` directory precreated), plus a fake
    `$PYTHON` + a fake `date` (in PATH) that fails loudly if called, to prove
    run_mr20.sh no longer resolves TODAY via bare `date`.

    run_mr20.sh now `cd`s to its own script directory rather than a
    hardcoded checkout path (docs/REVIEW-codex-r4-20260907.md R4-4), so this
    fixture runs a copy of it from a disposable tmp dir instead of touching
    the real repo checkout's artifacts/mr20/.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "artifacts" / "mr20").mkdir(parents=True)
    script_path = workdir / "run_mr20.sh"
    script_path.write_text(SCRIPT.read_text())
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

    python_path = tmp_path / "fake_python3"
    python_path.write_text(FAKE_PYTHON_SRC.lstrip("\n"))
    python_path.chmod(python_path.stat().st_mode | stat.S_IEXEC)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    date_path = bin_dir / "date"
    date_path.write_text(FAKE_DATE_SRC.lstrip("\n"))
    date_path.chmod(date_path.stat().st_mode | stat.S_IEXEC)

    return python_path, bin_dir, workdir, script_path


def _run(mode, env_overrides, fake_bin):
    python_path, poison_date_dir, workdir, script_path = fake_bin
    env = dict(os.environ)
    env["PYTHON"] = str(python_path)
    env["PATH"] = f"{poison_date_dir}:{env['PATH']}"
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
    # point the strategy stub at the exact path run_mr20.sh will look for,
    # relative to the isolated workdir (run_mr20.sh cd's there first).
    d_compact = env["FAKE_TODAY"].replace("-", "")
    orders_path = workdir / f"artifacts/mr20/orders_mr20_{d_compact}.json"
    env["FAKE_ORDERS_PATH"] = str(orders_path)
    result = subprocess.run(
        ["bash", str(script_path), mode],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
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

    def test_session_helper_killed_by_signal_is_not_treated_as_holiday(self, fake_bin):
        # exit 137 == killed by SIGKILL (128+9). Previously any non-2, non-0
        # exit code silently collapsed into "holiday, skip" — hiding the
        # helper being killed (docs/REVIEW-codex-r4-20260907.md R4-1).
        result = _run("close", {"FAKE_SESSION_RC": "137"}, fake_bin)
        assert result.returncode != 0
        assert "非 XTAI 交易日" not in result.stdout

    def test_session_exit_one_without_holiday_marker_is_not_treated_as_holiday(self, fake_bin):
        # A generic exit(1) (e.g. an uncaught exception/import failure
        # before the CLI's own holiday branch ever runs) is indistinguishable
        # from a real holiday by exit code alone — it must not be skipped
        # silently just because rc == 1 (docs/REVIEW-codex-r4-20260907.md R4-1).
        result = _run("close", {"FAKE_SESSION_RC": "1", "FAKE_SESSION_NO_MARKER": "1"}, fake_bin)
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


class TestSettlementStillRunsOnOrderGenerationFailure:
    """R4-2 (docs/REVIEW-codex-r4-20260907.md): rejecting untrustworthy new
    orders must not also block settling today's existing positions — the
    close-and-plan step (SL/TP/TIME settlement, equity mark, planning-gap
    bookkeeping) has to run either way, with failure reported afterward."""

    def test_strategy_failure_still_runs_close_and_plan(self, fake_bin):
        result = _run("close", {"FAKE_STRATEGY_RC": "1"}, fake_bin)
        assert result.returncode != 0
        assert "Step 2: Close-and-Plan" in result.stdout

    def test_strategy_failure_close_and_plan_does_not_receive_untrusted_orders_path(self, fake_bin):
        # No orders file was produced at all — must not pass a bogus/missing
        # --orders path through; let close-and-plan auto-discover (finds
        # nothing, records a planning gap) instead.
        result = _run("close", {"FAKE_STRATEGY_RC": "1"}, fake_bin)
        assert result.returncode != 0
        assert "CLOSE_AND_PLAN_ORDERS_ARG_ABSENT" in result.stdout

    def test_freshness_failure_still_runs_close_and_plan(self, fake_bin):
        env = {
            "FAKE_ORDERS_CONTENT": '{"diagnostic": {"signal_date": "2020-01-01", "execution_date": "2020-01-02"}}',
        }
        result = _run("close", env, fake_bin)
        assert result.returncode != 0
        assert "Step 2: Close-and-Plan" in result.stdout

    def test_freshness_failure_close_and_plan_does_not_receive_stale_orders_path(self, fake_bin):
        env = {
            "FAKE_ORDERS_CONTENT": '{"diagnostic": {"signal_date": "2020-01-01", "execution_date": "2020-01-02"}}',
        }
        result = _run("close", env, fake_bin)
        assert result.returncode != 0
        assert "CLOSE_AND_PLAN_ORDERS_ARG_ABSENT" in result.stdout

    def test_healthy_run_still_passes_fresh_orders_path_to_close_and_plan(self, fake_bin):
        result = _run("close", {}, fake_bin)
        assert result.returncode == 0
        assert "CLOSE_AND_PLAN_ORDERS_ARG_PRESENT" in result.stdout


class TestCheckProcessedRunsWiredIntoScheduler:
    def test_daily_monitor_failure_fails_the_close_run(self, fake_bin):
        result = _run("close", {"FAKE_CHECK_RC": "1"}, fake_bin)
        assert result.returncode != 0

    def test_daily_monitor_invoked_on_healthy_close_run(self, fake_bin):
        result = _run("close", {"FAKE_CHECK_RC": "0"}, fake_bin)
        assert result.returncode == 0
        assert "check_processed_runs" in (result.stdout + result.stderr) or "Daily Monitor" in result.stdout


def _git(args, cwd, **kwargs):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, **kwargs
    )


@pytest.fixture
def fake_bin_git(fake_bin, tmp_path):
    """`fake_bin`'s workdir turned into a real git checkout with a bare
    "origin" remote, so the delivery step (run_mr20.sh Step 4) has an actual
    git repo to commit/push to — this is what a real cron host checkout
    looks like, unlike the plain `fake_bin` tmp dir used by every other test
    in this file (docs/REVIEW-opus-r5-20260907.md §2.2: the real producer of
    artifacts/mr20/*.json is this script, on a git checkout, not any CI job).
    """
    python_path, poison_date_dir, workdir, script_path = fake_bin

    remote = tmp_path / "origin.git"
    remote.mkdir()
    _git(["init", "--bare", "-b", "main", str(remote)], cwd=tmp_path)

    _git(["init", "-b", "main", str(workdir)], cwd=tmp_path)
    _git(["config", "user.email", "test@example.com"], cwd=workdir)
    _git(["config", "user.name", "Test"], cwd=workdir)
    _git(["remote", "add", "origin", str(remote)], cwd=workdir)
    (workdir / "README.md").write_text("seed\n")
    _git(["add", "README.md"], cwd=workdir)
    _git(["commit", "-m", "seed"], cwd=workdir)
    _git(["push", "origin", "HEAD:main"], cwd=workdir)

    return python_path, poison_date_dir, workdir, script_path, remote


class TestOrderArtifactDelivery:
    """R4-3 交付端 (docs/REVIEW-opus-r5-20260907.md §2.2): the real producer
    of artifacts/mr20/*.json is this scheduler script running on a cron
    host git checkout — delivery (commit + push) must happen here, not in
    an unrelated CI workflow that never runs the strategy."""

    def test_delivers_new_orders_file_to_git_remote_on_success(self, fake_bin_git):
        python_path, poison_date_dir, workdir, script_path, remote = fake_bin_git
        result = _run("close", {}, (python_path, poison_date_dir, workdir, script_path))
        assert result.returncode == 0
        assert "Delivered" in result.stdout

        orders_path = workdir / "artifacts/mr20/orders_mr20_20990105.json"
        assert orders_path.exists()

        remote_head = _git(["rev-parse", "main"], cwd=remote).stdout.strip()
        local_head = _git(["rev-parse", "HEAD"], cwd=workdir).stdout.strip()
        assert remote_head == local_head

        shown = _git(
            ["show", f"{remote_head}:artifacts/mr20/orders_mr20_20990105.json"], cwd=remote
        ).stdout
        assert shown == orders_path.read_text()

    def test_no_delivery_when_strategy_generation_failed(self, fake_bin_git):
        python_path, poison_date_dir, workdir, script_path, remote = fake_bin_git
        result = _run(
            "close", {"FAKE_STRATEGY_RC": "1"}, (python_path, poison_date_dir, workdir, script_path)
        )
        assert result.returncode != 0
        assert "Skipping delivery" in result.stdout

        remote_head = _git(["rev-parse", "main"], cwd=remote).stdout.strip()
        seed_head = _git(["rev-parse", "HEAD"], cwd=workdir).stdout.strip()
        # No orders file was produced — nothing new to commit, remote must
        # still be at the seed commit pushed by the fixture.
        assert remote_head == seed_head

    def test_gracefully_skips_when_not_a_git_checkout(self, fake_bin):
        # `fake_bin` (no git init at all) — the delivery step must not
        # crash the script when the checkout isn't a git repo.
        result = _run("close", {}, fake_bin)
        assert result.returncode == 0
        assert "Skipping delivery" in result.stdout

    def test_push_failure_still_fails_run_but_settlement_already_ran(self, fake_bin_git):
        python_path, poison_date_dir, workdir, script_path, remote = fake_bin_git
        # Break the remote after the fixture's seed push so the delivery
        # step's push (and its fetch/rebase retry) both fail.
        shutil.rmtree(remote)

        result = _run("close", {}, (python_path, poison_date_dir, workdir, script_path))
        assert result.returncode != 0
        assert "Step 2: Close-and-Plan" in result.stdout
        assert "Failed to push" in result.stderr
