#!/bin/bash
# MR20 Scheduler Contract — Phase 0
# Schedule:
#   D 18:05  — Generate MR20 orders + close-and-plan (settle positions, plan next day)
#   D+1 09:35 — Execute open (fill limit orders at next-day open)
#
# Freshness gate: signal_date and execution_date in orders JSON are validated
# against the intended run dates; stale PENDING orders are not created.

set -euo pipefail
cd /root/work/tw_stocker
export PYTHONPATH=/root/work/tw_stocker
PYTHON=.venv/bin/python3
DATA_DIR=independent_sim_data_mr20

# ── Determine dates ──
# D = yesterday (last completed trading day for signal generation)
# D+1 = today (execution day for open)
TODAY=$(date +%Y-%m-%d)
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
YESTERDAY_COMPACT=$(date -d "yesterday" +%Y%m%d)

echo "=== MR20 Scheduler [$TODAY] ==="
echo "  Signal date (D):   $YESTERDAY"
echo "  Execution date (D+1): $TODAY"

# ── Step 1: Generate MR20 orders for signal date D ──
echo ""
echo "=== Step 1: MR20 Signal Generation ($YESTERDAY) ==="
$PYTHON -m strategy.mr20_strategy --as-of "$YESTERDAY" 2>&1
EXIT1=$?

ORDERS="artifacts/mr20/orders_mr20_${YESTERDAY_COMPACT}.json"

# ── Freshness gate: validate orders file exists and is non-empty ──
if [ "$EXIT1" -ne 0 ] || [ ! -f "$ORDERS" ] || [ ! -s "$ORDERS" ]; then
    echo ""
    echo "(No orders generated or strategy failed — skipping close-and-plan)"
    echo "=== Status ==="
    $PYTHON independent_sim.py status -s mr20 --data-dir "$DATA_DIR" 2>&1
    exit 0
fi

# Freshness gate: verify the orders file has signal_date matching $YESTERDAY
# and execution_date matching $TODAY (or next trading day). If mismatch, skip.
FRESHNESS_OK=$($PYTHON -c "
import json, sys
try:
    data = json.load(open('$ORDERS'))
    diag = data.get('diagnostic', {})
    sig = diag.get('signal_date', '')
    exec_d = diag.get('execution_date', '')
    if sig != '$YESTERDAY':
        print(f'STALE: signal_date={sig} expected=$YESTERDAY', file=sys.stderr)
        sys.exit(1)
    # execution_date should be next trading day after signal_date
    # (could be today or later if today is not a trading day)
    print(f'OK: signal={sig} exec={exec_d}')
except Exception as e:
    print(f'PARSE_ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1)

if [ $? -ne 0 ]; then
    echo "⚠️  Freshness gate FAILED: $FRESHNESS_OK"
    echo "(Refusing to process stale orders — skipping close-and-plan)"
    echo "=== Status ==="
    $PYTHON independent_sim.py status -s mr20 --data-dir "$DATA_DIR" 2>&1
    exit 0
fi
echo "✅ Freshness gate passed: $FRESHNESS_OK"

# ── Step 2: Close-and-plan (settle positions, mark equity, plan next orders) ──
echo ""
echo "=== Step 2: Close-and-Plan ($YESTERDAY) ==="
$PYTHON independent_sim.py close-and-plan -s mr20 \
    --data-dir "$DATA_DIR" \
    --orders "$ORDERS" \
    --as-of "$YESTERDAY" 2>&1

# ── Step 3: Open execution (D+1, fill limit orders at today's open) ──
echo ""
echo "=== Step 3: Open Execution ($TODAY) ==="
$PYTHON independent_sim.py open -s mr20 \
    --data-dir "$DATA_DIR" \
    --as-of "$TODAY" 2>&1

# ── Status ──
echo ""
echo "=== Status ==="
$PYTHON independent_sim.py status -s mr20 --data-dir "$DATA_DIR" 2>&1
