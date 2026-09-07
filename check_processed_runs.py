#!/usr/bin/env python3
"""Daily alert: has today's close-and-plan + open both actually run?

`independent_sim.py`'s `processed_runs` is repo-internal evidence of whether
the planning and execution hops were invoked at all — orchestration/cron can
silently stop calling one or both without any error surfacing anywhere else
(see docs/REVIEW-opus-20260907.md §2.2). This script checks a single trading
day's `processed_runs` for both `close-and-plan:{date}` and `open:{date}`,
printing/exiting non-zero when either is missing so it can be wired into a
cron alert.

Usage:
    python3 check_processed_runs.py -s mr20 --data-dir independent_sim_data_mr20
    python3 check_processed_runs.py -s mr20 --date 2026-09-03
"""
import argparse
import sys
from pathlib import Path
from typing import Union

from independent_sim import (
    get_strategy_config,
    get_taipei_today,
    is_run_processed,
    is_trading_day,
    load_state,
    resolve_strategy_id,
)


def check_processed_runs(data_dir: Union[Path, str], date: str) -> list[str]:
    """Return run_ids expected but missing from processed_runs for `date`.

    Non-trading days expect nothing, so they always return an empty list.
    """
    if not is_trading_day(date):
        return []
    state = load_state(data_dir)
    expected = [f"close-and-plan:{date}", f"open:{date}"]
    return [run_id for run_id in expected if not is_run_processed(state, run_id)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Alert if a trading day's close-and-plan/open hops were not both run"
    )
    parser.add_argument("--strategy", "-s", type=str, default=None)
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--date", type=str, default=None, help="Trading day to check (default: today, Taipei time)")
    args = parser.parse_args()

    sid = resolve_strategy_id(args.strategy)
    strat_cfg = get_strategy_config(sid)
    data_dir = args.data_dir or strat_cfg["default_data_dir"]
    date = args.date or get_taipei_today()

    missing = check_processed_runs(data_dir, date)
    if missing:
        print(f"🚨 [{sid}] {date}: missing processed_runs: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    print(f"✅ [{sid}] {date}: processed_runs OK (or non-trading day)")
    sys.exit(0)


if __name__ == "__main__":
    main()
