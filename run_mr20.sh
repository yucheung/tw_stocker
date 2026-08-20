#!/bin/bash
cd /root/work/tw_stocker
export PYTHONPATH=/root/work/tw_stocker
PYTHON=.venv/bin/python3
AS_OF=$(date -d "yesterday" +%Y-%m-%d)

echo "=== MR20 Strategy for $AS_OF ==="
$PYTHON -m strategy.mr20_strategy --as-of "$AS_OF" --data-dir artifacts 2>&1
EXIT1=$?

ORDERS="artifacts/mr20/orders_mr20_$(date -d yesterday +%Y%m%d).json"
if [ "$EXIT1" -eq 0 ] && [ -f "$ORDERS" ] && [ -s "$ORDERS" ]; then
    echo ""
    echo "=== Close-and-Plan ==="
    $PYTHON independent_sim.py close-and-plan \
        --data-dir independent_sim_data_mr20 \
        --orders "$ORDERS" --as-of "$AS_OF" 2>&1
else
    echo ""
    echo "(No orders or empty orders file, skipping close-and-plan)"
fi

echo ""
echo "=== Status ==="
$PYTHON independent_sim.py status --data-dir independent_sim_data_mr20 2>&1
