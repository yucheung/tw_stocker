#!/bin/bash
cd /root/work/tw_stocker
export PYTHONPATH=/root/work/tw_stocker
PYTHON=.venv/bin/python3

echo "=== Installing missing deps ==="
.venv/bin/pip install exchange_calendars 2>&1 | tail -3

echo ""
echo "=== Status ==="
$PYTHON independent_sim.py status --data-dir independent_sim_data_mr20 2>&1
