#!/bin/bash
# run_backtest_worker.sh — Backtest Worker execution wrapper
# Usage:
#   ./run_backtest_worker.sh               # run single cycle for queued jobs
#   ./run_backtest_worker.sh --job-id ID   # run specific job
#   ./run_backtest_worker.sh --dry-run     # preview queued jobs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR"
PYTHON="${PYTHON:-.venv/bin/python3}"

# Load credentials from /root/.tw-stocker-web-sync.env if present
if [ -f /root/.tw-stocker-web-sync.env ]; then
    set -a
    . /root/.tw-stocker-web-sync.env
    set +a
fi

exec "$PYTHON" backtest_worker.py "$@"
