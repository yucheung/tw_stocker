#!/bin/bash
# run_mr20_full.sh — Alias for run_mr20.sh (unified scheduler)
exec "$(dirname "$0")/run_mr20.sh" "$@"
