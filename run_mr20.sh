#!/bin/bash
# MR20 Scheduler Contract — Phase 1
#
# Two independent entry points, invoked separately by cron (each does ONE
# job — no chaining across the 18:05/09:35 boundary in a single run):
#   run_mr20.sh close   — run at D 18:05: generate MR20 orders for D +
#                         close-and-plan D (settle positions, plan next day)
#   run_mr20.sh open    — run at D+1 09:35: execute open (fill limit orders
#                         at today's open)
#
# Each phase only proceeds if the invocation date is itself a valid XTAI
# trading session (exchange-calendar aware — NOT `date -d yesterday`, which
# breaks across weekends/holidays: e.g. run on Sunday would resolve
# "yesterday" to Saturday instead of the last real trading day). If the
# invocation date is not a session, the phase is skipped (exit 0).
#
# Freshness gate (close phase): both signal_date AND execution_date in the
# generated orders JSON are validated against trading-calendar-derived
# expectations before close-and-plan runs; stale/mismatched orders are
# refused rather than silently processed.
set -euo pipefail
cd /root/work/tw_stocker
export PYTHONPATH=/root/work/tw_stocker
PYTHON=.venv/bin/python3
DATA_DIR=independent_sim_data_mr20

MODE="${1:-}"
if [ "$MODE" != "close" ] && [ "$MODE" != "open" ]; then
    echo "Usage: $0 {close|open}" >&2
    echo "  close — run at D 18:05: generate MR20 orders for D + close-and-plan D" >&2
    echo "  open  — run at D+1 09:35: execute open fills for today" >&2
    exit 2
fi

TODAY=$(date +%Y-%m-%d)

is_session() {
    "$PYTHON" -c "
from independent_sim import is_trading_day
import sys
sys.exit(0 if is_trading_day('$1') else 1)
"
}

if ! is_session "$TODAY"; then
    echo "=== MR20 Scheduler [$MODE] [$TODAY] ==="
    echo "$TODAY 非 XTAI 交易日，略過本次執行"
    exit 0
fi

if [ "$MODE" = "open" ]; then
    echo "=== MR20 Scheduler [open] [$TODAY] ==="
    echo ""
    echo "=== Open Execution ($TODAY) ==="
    "$PYTHON" independent_sim.py open -s mr20 \
        --data-dir "$DATA_DIR" \
        --as-of "$TODAY"

    echo ""
    echo "=== Status ==="
    "$PYTHON" independent_sim.py status -s mr20 --data-dir "$DATA_DIR"
    exit 0
fi

# ── MODE = close ──
D="$TODAY"
D_COMPACT=$(date +%Y%m%d)
ORDERS="artifacts/mr20/orders_mr20_${D_COMPACT}.json"

echo "=== MR20 Scheduler [close] [$D] ==="
echo "  Signal date (D): $D"

echo ""
echo "=== Step 1: MR20 Signal Generation ($D) ==="
if "$PYTHON" -m strategy.mr20_strategy --as-of "$D"; then
    EXIT1=0
else
    EXIT1=$?
fi

if [ "$EXIT1" -ne 0 ] || [ ! -f "$ORDERS" ] || [ ! -s "$ORDERS" ]; then
    echo ""
    echo "(No orders generated or strategy failed — skipping close-and-plan)"
    echo "=== Status ==="
    "$PYTHON" independent_sim.py status -s mr20 --data-dir "$DATA_DIR"
    exit 0
fi

EXPECTED_EXEC=$("$PYTHON" -c "
from independent_sim import get_next_trading_day
print(get_next_trading_day('$D'))
")

# Freshness gate: verify the orders file's signal_date matches $D AND
# execution_date matches the calendar-derived next trading day.
if FRESHNESS_OK=$("$PYTHON" -c "
import json, sys
try:
    data = json.load(open('$ORDERS'))
    diag = data.get('diagnostic', {})
    sig = diag.get('signal_date', '')
    exec_d = diag.get('execution_date', '')
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
    echo "⚠️  Freshness gate FAILED: $FRESHNESS_OK"
    echo "(Refusing to process stale orders — skipping close-and-plan)"
    echo "=== Status ==="
    "$PYTHON" independent_sim.py status -s mr20 --data-dir "$DATA_DIR"
    exit 0
fi

echo ""
echo "=== Step 2: Close-and-Plan ($D) ==="
"$PYTHON" independent_sim.py close-and-plan -s mr20 \
    --data-dir "$DATA_DIR" \
    --orders "$ORDERS" \
    --as-of "$D"

echo ""
echo "=== Status ==="
"$PYTHON" independent_sim.py status -s mr20 --data-dir "$DATA_DIR"
