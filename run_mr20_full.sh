#!/bin/bash
cd /root/work/tw_stocker
export PYTHONPATH=/root/work/tw_stocker
PYTHON=.venv/bin/python3
AS_OF=$(date -d "yesterday" +%Y-%m-%d)
ORDER_DATE=$(date -d "yesterday" +%Y%m%d)

echo "=== Step 1: MR20 Strategy for $AS_OF ==="
$PYTHON -m strategy.mr20_strategy --as-of "$AS_OF" --data-dir artifacts 2>&1
EXIT1=$?

ORDERS="artifacts/mr20/orders_mr20_${ORDER_DATE}.json"
if [ "$EXIT1" -eq 0 ] && [ -f "$ORDERS" ]; then
    CONTENT=$(cat "$ORDERS")
    if [ "$CONTENT" != "[]" ] && [ -n "$CONTENT" ]; then
        echo ""
        echo "=== Step 2: Close-and-Plan ==="
        $PYTHON independent_sim.py close-and-plan \
            --data-dir independent_sim_data_mr20 \
            --orders "$ORDERS" --as-of "$AS_OF" 2>&1
    else
        echo ""
        echo "(Orders file is empty/empty-array, skipping close-and-plan)"
    fi
else
    echo ""
    echo "(No orders file found or strategy failed, skipping close-and-plan)"
fi

echo ""
echo "=== Step 3: Status ==="
$PYTHON independent_sim.py status --data-dir independent_sim_data_mr20 2>&1
