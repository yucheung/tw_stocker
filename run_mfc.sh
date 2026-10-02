#!/bin/bash
# MFC Scheduler Contract — TWSE Strategy C Multifactor v3 (independent_sim)
#
# Mirrors run_mr20.sh. Two independent entry points, invoked separately by
# cron (each does ONE job — no chaining across the 18:10/09:40 boundary in
# a single run):
#   run_mfc.sh close   — run at D 18:10: generate MFC orders for D +
#                        close-and-plan D (settle positions, plan next day)
#   run_mfc.sh open    — run at D+1 09:40: execute open (fill limit orders
#                        at today's open)
#
# MFC rebalances monthly (first session on/after the 11th); on other days
# daily_signal_mfc.py writes an empty-orders artifact and close-and-plan
# only settles/marks. Each phase only proceeds if the invocation date is
# itself a valid XTAI trading session. Non-session dates exit 0 (skip).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR"
PYTHON="${PYTHON:-.venv/bin/python3}"
DATA_DIR=independent_sim_data_mfc
STRATEGY=multifactor_c_v1

MODE="${1:-}"
if [ "$MODE" != "close" ] && [ "$MODE" != "open" ]; then
    echo "Usage: $0 {close|open}" >&2
    echo "  close — run at D 18:10: generate MFC orders for D + close-and-plan D" >&2
    echo "  open  — run at D+1 09:40: execute open fills for today" >&2
    exit 2
fi

# TODAY in Asia/Taipei calendar terms (same helper as MR20).
TODAY=$("$PYTHON" -c "from independent_sim import get_taipei_today; print(get_taipei_today())")

# Trading-day check via independent_sim.py is-session (exit 0=session).
SESSION_OUT=$("$PYTHON" independent_sim.py is-session --date "$TODAY" 2>&1) || true
SESSION_RC=$?
if [ "$SESSION_RC" -ne 0 ]; then
    echo "=== MFC Scheduler [$MODE] [$TODAY] ==="
    echo "Not an XTAI trading session (rc=$SESSION_RC) — skip. $SESSION_OUT"
    exit 0
fi

if [ "$MODE" = "open" ]; then
    echo "=== MFC Scheduler [open] [$TODAY] ==="
    echo ""
    echo "=== Open Execution ($TODAY) ==="
    "$PYTHON" independent_sim.py open -s "$STRATEGY" \
        --data-dir "$DATA_DIR" \
        --as-of "$TODAY"

    echo ""
    echo "=== Status ==="
    "$PYTHON" independent_sim.py status -s "$STRATEGY" --data-dir "$DATA_DIR"
    exit 0
fi

# ── MODE = close ──
D="$TODAY"
D_COMPACT="${D//-/}"
ORDERS="artifacts/multifactor_c/orders_${D_COMPACT}.json"

echo "=== MFC Scheduler [close] [$D] ==="
echo "  Signal date (D): $D"

echo ""
echo "=== Step 1: MFC Signal Generation ($D) ==="
if "$PYTHON" daily_signal_mfc.py --as-of "$D"; then
    EXIT1=0
else
    EXIT1=$?
fi

# Same contract as MR20: untrusted new orders must not block settlement of
# existing positions. FAILED=1 drops --orders (auto-discovery records a
# planning gap instead of using stale data); settlement still runs.
FAILED=0
CLOSE_PLAN_ORDERS_ARGS=(--orders "$ORDERS")

if [ "$EXIT1" -ne 0 ] || [ ! -f "$ORDERS" ] || [ ! -s "$ORDERS" ]; then
    FAILED=1
    CLOSE_PLAN_ORDERS_ARGS=()
    echo ""
    echo "❌ (No orders generated or signal failed — settlement will still run, planning gap recorded)" >&2
else
    EXPECTED_EXEC=$("$PYTHON" -c "
from independent_sim import get_next_trading_day
print(get_next_trading_day('$D'))
")

    # Freshness gate: MFC payload carries top-level signal_date/execution_date.
    if FRESHNESS_OK=$("$PYTHON" -c "
import json, sys
try:
    data = json.load(open('$ORDERS'))
    sig = data.get('signal_date', '')
    exec_d = data.get('execution_date', '')
    if sig != '$D':
        print(f'STALE: signal_date={sig} expected=$D', file=sys.stderr)
        sys.exit(1)
    if exec_d != '$EXPECTED_EXEC':
        print(f'STALE: execution_date={exec_d} expected=$EXPECTED_EXEC', file=sys.stderr)
        sys.exit(1)
    print(f'OK: signal={sig} exec={exec_d}')
except Exception as e:
    print(f'PARSE_ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1); then
        echo "✅ Freshness gate passed: $FRESHNESS_OK"
    else
        FAILED=1
        CLOSE_PLAN_ORDERS_ARGS=()
        echo "❌ Freshness gate FAILED: $FRESHNESS_OK" >&2
        echo "(Refusing to trust these orders — settlement will still run, planning gap recorded)" >&2
    fi
fi

echo ""
echo "=== Step 2: Close-and-Plan ($D) ==="
"$PYTHON" independent_sim.py close-and-plan -s "$STRATEGY" \
    --data-dir "$DATA_DIR" \
    "${CLOSE_PLAN_ORDERS_ARGS[@]}" \
    --as-of "$D"

echo ""
if [ "$FAILED" -ne 0 ]; then
    echo "❌ MFC close finished WITH planning gap (signal/freshness failed, settlement done)" >&2
    exit 1
fi
echo "✅ MFC close done: signal + settlement OK"
