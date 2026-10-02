#!/usr/bin/env python3
"""Generate isolated monthly Strategy C orders after the TWSE close."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import independent_sim as sim
from strategy.multifactor_c import (
    BENCHMARK_SYMBOL,
    DEFAULT_BEAR_EQUITY_EXPOSURE,
    DEFAULT_TOP_N,
    FactorBuilder,
    StrategyC,
    build_market_membership,
    build_rebalance_orders,
)
from strategy.multifactor_c_data import MfcDataStore


TAIPEI_TZ = ZoneInfo("Asia/Taipei")
STRATEGY_ID = "multifactor_c_v1"


def _naive_dates(index: pd.Index) -> pd.Index:
    return index.tz_localize(None) if getattr(index, "tz", None) is not None else index


def is_monthly_rebalance_date(as_of_date: str, benchmark_prices: pd.DataFrame) -> bool:
    """True on the first benchmark session dated 11 or later in the month."""
    as_of = pd.Timestamp(as_of_date)
    if as_of.day < 11:
        return False
    dates = _naive_dates(benchmark_prices.index)
    month_sessions = [
        pd.Timestamp(day)
        for day in dates
        if day.year == as_of.year and day.month == as_of.month and day.day >= 11 and day <= as_of
    ]
    return bool(month_sessions and min(month_sessions) == as_of)


def _close_as_of(prices: dict[str, pd.DataFrame], ticker: str, as_of: pd.Timestamp) -> float | None:
    frame = prices.get(ticker)
    if frame is None or frame.empty or "Close" not in frame:
        return None
    dates = _naive_dates(frame.index)
    history = frame[dates <= as_of]
    if history.empty:
        return None
    close = float(history.iloc[-1]["Close"])
    return close if close > 0 else None


def _write_orders(output_dir: Path | str, payload: dict) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    signal_date = payload["signal_date"].replace("-", "")
    target = directory / f"orders_{signal_date}.json"
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def create_daily_signal(
    as_of_date: str,
    prices_dict: dict[str, pd.DataFrame],
    revenue_dict: dict[str, pd.DataFrame],
    institutional_dict: dict[str, pd.DataFrame],
    state: dict,
    orders_dir: Path | str,
) -> dict:
    """Build a point-in-time signal and write the standard orders JSON artifact."""
    as_of = pd.Timestamp(as_of_date)
    if state.get("strategy_id") != STRATEGY_ID:
        raise ValueError(f"daily_signal_mfc requires a {STRATEGY_ID} simulation state")
    benchmark = prices_dict.get(BENCHMARK_SYMBOL)
    if benchmark is None or as_of not in _naive_dates(benchmark.index):
        raise ValueError(f"Missing {BENCHMARK_SYMBOL} closing bar for {as_of_date}")

    membership = build_market_membership(prices_dict)
    builder = FactorBuilder(
        prices_dict,
        revenue_dict=revenue_dict,
        institutional_dict=institutional_dict,
        universe_by_year=membership,
    )
    rebalance = is_monthly_rebalance_date(as_of_date, benchmark)
    previous_holdings = sorted(state.get("positions", {}).keys())
    target_weights = {}
    market_bullish = None
    orders = []
    execution_date = sim.get_next_trading_day(as_of_date)

    if rebalance:
        market_bullish = builder.is_market_bullish(as_of)
        snapshot = builder.compute_factors_snapshot(as_of)
        target_weights = StrategyC(
            top_n=DEFAULT_TOP_N,
            bear_equity_exposure=DEFAULT_BEAR_EQUITY_EXPOSURE,
        ).select_portfolio(snapshot, is_market_bullish=market_bullish)
        reference_closes = {
            ticker: float(row["close"])
            for _, row in snapshot[snapshot["symbol"].isin(target_weights)].iterrows()
            for ticker in [row["symbol"]]
        }
        for ticker, position in state.get("positions", {}).items():
            close = _close_as_of(prices_dict, ticker, as_of)
            if close is not None:
                reference_closes[ticker] = close
            else:
                fallback = position.get("last_valid_close", position.get("entry"))
                if fallback is not None and float(fallback) > 0:
                    reference_closes[ticker] = float(fallback)

        equity = float(state.get("cash", 0.0))
        for ticker, position in state.get("positions", {}).items():
            close = reference_closes.get(ticker)
            if close is not None:
                equity += int(position["shares"]) * close
        orders = build_rebalance_orders(
            signal_date=as_of_date,
            execution_date=execution_date,
            target_weights=target_weights,
            reference_closes=reference_closes,
            current_positions=state.get("positions", {}),
            equity=equity,
        )

    payload = {
        "strategy_id": STRATEGY_ID,
        "signal_date": as_of_date,
        "execution_date": execution_date,
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(),
        "is_rebalance_date": rebalance,
        "orders": orders,
        "metadata": {
            "market_bullish": market_bullish,
            "equity_exposure": (
                1.0 if market_bullish else DEFAULT_BEAR_EQUITY_EXPOSURE
            ) if rebalance else 0.0,
            "previous_holdings": previous_holdings,
            "target_symbols": list(target_weights),
            "target_weights": target_weights,
            "top_n": DEFAULT_TOP_N,
            "bear_equity_exposure": DEFAULT_BEAR_EQUITY_EXPOSURE,
        },
    }
    _write_orders(orders_dir, payload)
    return payload


def run_daily_signal(
    as_of_date: str | None = None,
    data_dir: Path | str = "independent_sim_data_mfc",
    cache_dir: Path | str | None = None,
    orders_dir: Path | str = "artifacts/multifactor_c",
    throttle_seconds: float = 0.5,
    refresh_universe: bool = False,
) -> dict:
    """Update the MFC-only cache and write today's order artifact."""
    signal_date = as_of_date or sim.get_taipei_today()
    if not sim.is_trading_day(signal_date):
        raise ValueError(f"{signal_date} is not an XTAI trading session")
    state = sim.load_state(data_dir)
    if state.get("strategy_id") != STRATEGY_ID:
        raise ValueError(f"Expected a {STRATEGY_ID} state in {data_dir}")

    mfc_cache = Path(cache_dir) if cache_dir else Path(data_dir) / "cache"
    store = MfcDataStore(mfc_cache, throttle_seconds=throttle_seconds)
    symbols = store.get_market_symbols(refresh=refresh_universe)
    prices = store.update_prices(
        symbols,
        signal_date,
        initial_start_date=f"{pd.Timestamp(signal_date).year - 1}-01-01",
    )
    membership = build_market_membership(prices)
    pool = membership.get(pd.Timestamp(signal_date).year, [])
    if not pool:
        raise ValueError(f"No v3 top-{200} market pool is available for {signal_date[:4]}")

    if is_monthly_rebalance_date(signal_date, prices[BENCHMARK_SYMBOL]):
        revenue = store.update_revenue(pool, signal_date, initial_start_date="2020-01-01")
        institutional = store.update_institutional(pool, signal_date, initial_start_date="2020-01-01")
    else:
        _, revenue, institutional = store.load_all()
    return create_daily_signal(
        signal_date,
        prices,
        revenue,
        institutional,
        state,
        orders_dir,
    )


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate isolated Strategy C daily orders")
    parser.add_argument("--as-of", default=None, help="Signal date YYYY-MM-DD; defaults to Taipei today")
    parser.add_argument("--data-dir", default="independent_sim_data_mfc")
    parser.add_argument("--cache-dir", default=None, help="MFC cache directory; defaults under data-dir")
    parser.add_argument("--orders-dir", default="artifacts/multifactor_c")
    parser.add_argument("--throttle-seconds", type=float, default=0.5)
    parser.add_argument("--refresh-universe", action="store_true")
    return parser


def main() -> None:
    args = build_cli_parser().parse_args()
    payload = run_daily_signal(
        as_of_date=args.as_of,
        data_dir=args.data_dir,
        cache_dir=args.cache_dir,
        orders_dir=args.orders_dir,
        throttle_seconds=args.throttle_seconds,
        refresh_universe=args.refresh_universe,
    )
    print(
        f"MFC signal {payload['signal_date']}: "
        f"{'調倉日' if payload['is_rebalance_date'] else '非調倉日'}，"
        f"{len(payload['orders'])} orders"
    )


if __name__ == "__main__":
    main()
