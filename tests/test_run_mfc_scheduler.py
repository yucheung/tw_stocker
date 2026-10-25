"""Tests for run_mfc.sh scheduler contract and 3-state is-session handling.

Mirrors tests/test_run_mr20_scheduler.py using an isolated tmp_path checkout
and a fake `$PYTHON` stub with impossible future dates (2099-01-05) so no
network access, real market data, strategy scripts, or live state/artifacts
are ever touched.
"""
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "run_mfc.sh"

FAKE_PYTHON_SRC = textwrap.dedent(r"""
    #!/usr/bin/env python3
    import os
    import sys

    argv = sys.argv[1:]
    call_log = os.environ.get('FAKE_CALL_LOG')

    def log_call(tag):
        if call_log:
            with open(call_log, 'a', encoding='utf-8') as f:
                f.write(tag + '\n')

    if argv[:1] == ['-c']:
        code = argv[1]
        if 'get_taipei_today' in code:
            log_call('get_taipei_today')
            print(os.environ['FAKE_TODAY'])
            sys.exit(0)
        if 'get_next_trading_day' in code:
            log_call('get_next_trading_day')
            print(os.environ['FAKE_NEXT_DAY'])
            sys.exit(0)
        log_call('freshness_gate')
        ns = {}
        exec(compile(code, '<fake-python-c>', 'exec'), ns)
        sys.exit(0)

    if argv and argv[0] == 'daily_signal_mfc.py':
        log_call('daily_signal_mfc')
        print('SIGNAL_GEN_CALLED')
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
            log_call('is-session')
            rc = int(os.environ.get('FAKE_SESSION_RC', '0'))
            if not os.environ.get('FAKE_SESSION_NO_MARKER'):
                if rc == 0:
                    print(f"SESSION_RESULT:TRADING_DAY:{os.environ.get('FAKE_TODAY', '')}")
                elif rc == 1:
                    print(f"SESSION_RESULT:HOLIDAY:{os.environ.get('FAKE_TODAY', '')}")
            else:
                print(os.environ.get('FAKE_SESSION_RAW_OUT', 'ERROR: unexpected failure'))
            extra_bytes = int(os.environ.get('FAKE_SESSION_EXTRA_BYTES', '0'))
            if extra_bytes > 0:
                chunk = ('x' * 79 + '\n') * ((extra_bytes // 80) + 1)
                sys.stdout.write(chunk[:extra_bytes])
                if not chunk[:extra_bytes].endswith('\n'):
                    sys.stdout.write('\n')
                sys.stdout.flush()
            sys.exit(rc)
        if sub == 'close-and-plan':
            if '--orders' in argv:
                log_call('close-and-plan:with-orders')
                print('CLOSE_AND_PLAN_ORDERS_ARG_PRESENT')
            else:
                log_call('close-and-plan:without-orders')
                print('CLOSE_AND_PLAN_ORDERS_ARG_ABSENT')
            sys.exit(int(os.environ.get('FAKE_CLOSE_PLAN_RC', '0')))
        if sub == 'open':
            log_call('open')
            print('OPEN_CALLED')
            sys.exit(int(os.environ.get('FAKE_OPEN_RC', '0')))
        if sub == 'status':
            log_call('status')
            print('FAKE STATUS')
            sys.exit(0)
        sys.exit(0)

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
    """Isolated copy of run_mfc.sh in tmp_path with fake $PYTHON and poisoned date."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "artifacts" / "multifactor_c").mkdir(parents=True)
    script_path = workdir / "run_mfc.sh"
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

    call_log = tmp_path / "calls.log"
    return python_path, bin_dir, workdir, script_path, call_log


def _run(mode, env_overrides, fake_bin):
    python_path, poison_date_dir, workdir, script_path, call_log = fake_bin
    if call_log.exists():
        call_log.unlink()
    env = dict(os.environ)
    env["PYTHON"] = str(python_path)
    env["PATH"] = f"{poison_date_dir}:{env['PATH']}"
    env["FAKE_CALL_LOG"] = str(call_log)
    env.setdefault("FAKE_TODAY", "2099-01-05")
    env.setdefault("FAKE_NEXT_DAY", "2099-01-06")
    env.setdefault("FAKE_SESSION_RC", "0")
    env.setdefault("FAKE_STRATEGY_RC", "0")
    env.setdefault("FAKE_CLOSE_PLAN_RC", "0")
    env.setdefault(
        "FAKE_ORDERS_CONTENT",
        '{"signal_date": "%s", "execution_date": "%s"}'
        % (env["FAKE_TODAY"], env["FAKE_NEXT_DAY"]),
    )
    env.update(env_overrides)
    d_compact = env["FAKE_TODAY"].replace("-", "")
    orders_path = workdir / f"artifacts/multifactor_c/orders_{d_compact}.json"
    env["FAKE_ORDERS_PATH"] = str(orders_path)
    result = subprocess.run(
        ["bash", str(script_path), mode],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = call_log.read_text().splitlines() if call_log.exists() else []
    return result, calls


class TestTradingDayExecution:
    def test_close_runs_signal_generation_and_settlement_on_trading_day(self, fake_bin):
        result, calls = _run("close", {"FAKE_SESSION_RC": "0"}, fake_bin)
        assert result.returncode == 0
        assert "SIGNAL_GEN_CALLED" in result.stdout
        assert "CLOSE_AND_PLAN_ORDERS_ARG_PRESENT" in result.stdout
        assert "POISON-DATE-DO-NOT-USE" not in result.stderr
        assert calls == [
            "get_taipei_today",
            "is-session",
            "daily_signal_mfc",
            "get_next_trading_day",
            "freshness_gate",
            "close-and-plan:with-orders",
        ]

    def test_open_runs_open_simulation_on_trading_day(self, fake_bin):
        result, calls = _run("open", {"FAKE_SESSION_RC": "0"}, fake_bin)
        assert result.returncode == 0
        assert "OPEN_CALLED" in result.stdout
        assert "FAKE STATUS" in result.stdout
        assert "POISON-DATE-DO-NOT-USE" not in result.stderr
        assert calls == ["get_taipei_today", "is-session", "open", "status"]


class TestSessionCheckHolidayAndFailureModes:
    @pytest.mark.parametrize("mode", ["close", "open"])
    def test_genuine_holiday_skips_with_exit_zero_and_touches_nothing(self, fake_bin, mode):
        result, calls = _run(mode, {"FAKE_SESSION_RC": "1"}, fake_bin)
        assert result.returncode == 0
        assert "非 XTAI 交易日" in result.stdout
        assert calls == ["get_taipei_today", "is-session"]

    @pytest.mark.parametrize("mode", ["close", "open"])
    def test_genuine_holiday_with_large_trailing_output_skips_without_sigpipe(
        self, fake_bin, mode
    ):
        result, calls = _run(
            mode,
            {"FAKE_SESSION_RC": "1", "FAKE_SESSION_EXTRA_BYTES": str(1024 * 1024)},
            fake_bin,
        )
        assert result.returncode == 0
        assert "非 XTAI 交易日" in result.stdout
        assert calls == ["get_taipei_today", "is-session"]

    @pytest.mark.parametrize("mode", ["close", "open"])
    def test_calendar_lookup_error_rc2_fails_closed(self, fake_bin, mode):
        result, calls = _run(mode, {"FAKE_SESSION_RC": "2"}, fake_bin)
        assert result.returncode != 0
        assert "非 XTAI 交易日" not in result.stdout
        assert "交易日判斷失敗" in result.stderr
        assert calls == ["get_taipei_today", "is-session"]

    @pytest.mark.parametrize("mode", ["close", "open"])
    def test_rc1_without_holiday_marker_fails_closed(self, fake_bin, mode):
        result, calls = _run(
            mode,
            {"FAKE_SESSION_RC": "1", "FAKE_SESSION_NO_MARKER": "1"},
            fake_bin,
        )
        assert result.returncode != 0
        assert "非 XTAI 交易日" not in result.stdout
        assert "交易日判斷失敗" in result.stderr
        assert calls == ["get_taipei_today", "is-session"]

    @pytest.mark.parametrize("mode", ["close", "open"])
    def test_signal_killed_is_session_rc137_fails_closed(self, fake_bin, mode):
        result, calls = _run(mode, {"FAKE_SESSION_RC": "137"}, fake_bin)
        assert result.returncode != 0
        assert "非 XTAI 交易日" not in result.stdout
        assert "交易日判斷失敗" in result.stderr
        assert calls == ["get_taipei_today", "is-session"]


class TestSignalGenerationFailureStillSettles:
    def test_signal_generation_failure_runs_settlement_without_orders_and_exits_nonzero(
        self, fake_bin
    ):
        result, calls = _run("close", {"FAKE_STRATEGY_RC": "1"}, fake_bin)
        assert result.returncode != 0
        assert "Step 2: Close-and-Plan" in result.stdout
        assert "CLOSE_AND_PLAN_ORDERS_ARG_ABSENT" in result.stdout
        assert calls == [
            "get_taipei_today",
            "is-session",
            "daily_signal_mfc",
            "close-and-plan:without-orders",
        ]

    def test_freshness_gate_failure_runs_settlement_without_orders_and_exits_nonzero(
        self, fake_bin
    ):
        env = {
            "FAKE_ORDERS_CONTENT": '{"signal_date": "2020-01-01", "execution_date": "2020-01-02"}',
        }
        result, calls = _run("close", env, fake_bin)
        assert result.returncode != 0
        assert "Step 2: Close-and-Plan" in result.stdout
        assert "CLOSE_AND_PLAN_ORDERS_ARG_ABSENT" in result.stdout
        assert calls == [
            "get_taipei_today",
            "is-session",
            "daily_signal_mfc",
            "get_next_trading_day",
            "freshness_gate",
            "close-and-plan:without-orders",
        ]
