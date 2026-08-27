#!/bin/bash
# run_mr20_status.sh — Quick status check for MR20 simulation
cd /root/work/tw_stocker
export PYTHONPATH=/root/work/tw_stocker
PYTHON=.venv/bin/python3

echo "=== MR20 Status ==="
$PYTHON independent_sim.py status -s mr20 --data-dir independent_sim_data_mr20 2>&1
