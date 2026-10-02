#!/usr/bin/env python3
"""Replay MFC at next-session opens and compare with TWSE Research v3 closes.

This tool reads cached pickle files only. It does not fetch data or write to the
TWSE_Research repository.
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import independent_sim as sim
from daily_signal_mfc import create_daily_signal, is_monthly_rebalance_date
from strategy.multifactor_c import (
    BENCHMARK_SYMBOL,
    FactorBuilder,
    StrategyC,
    build_market_membership,
)


def _naive_index(index: pd.Index) -> pd.Index:
    return index.tz_localize(None) if getattr(index, "tz", None) is not None else index


def _row_on_or_before(frame: pd.DataFrame, session: pd.Timestamp) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    dates = _naive_index(frame.index)
    history = frame[dates <= session]
    return None if history.empty else history.iloc[-1]


def _exact_bar(frame: pd.DataFrame | None, session: pd.Timestamp) -> dict[str, Any] | None:
    if frame is None or frame.empty:
        return None
    dates = _naive_index(frame.index)
    exact = frame[dates == session]
    if exact.empty:
        return None
    row = exact.iloc[-1]
    return {
        "date": session.strftime("%Y-%m-%d"),
        "open": float(row["Open"]) if pd.notna(row.get("Open")) else None,
        "high": float(row["High"]) if pd.notna(row.get("High")) else None,
        "low": float(row["Low"]) if pd.notna(row.get("Low")) else None,
        "close": float(row["Close"]) if pd.notna(row.get("Close")) else None,
    }


def simulate_open_price_replay(
    start_date: str,
    end_date: str,
    prices_dict: dict[str, pd.DataFrame],
    revenue_dict: dict[str, pd.DataFrame],
    institutional_dict: dict[str, pd.DataFrame],
    initial_capital: float = 1_000_000.0,
    orders_dir: Path | str = "artifacts/multifactor_c/replay",
) -> dict[str, Any]:
    """Run signal → JSON → pending orders → next-open execution → close marking."""
    for frame in prices_dict.values():
        if getattr(frame.index, "tz", None) is not None:
            frame.index = frame.index.tz_localize(None)
    if BENCHMARK_SYMBOL not in prices_dict:
        raise ValueError(f"Missing benchmark price series {BENCHMARK_SYMBOL}")

    membership = build_market_membership(prices_dict)
    factor_builder = FactorBuilder(
        prices_dict,
        revenue_dict,
        institutional_dict,
        universe_by_year=membership,
    )
    rebalance_dates = factor_builder.get_rebalance_dates(start_date, end_date)
    if not rebalance_dates:
        raise ValueError(f"No rebalance dates between {start_date} and {end_date}")

    benchmark_dates = _naive_index(prices_dict[BENCHMARK_SYMBOL].index)
    sessions = [
        pd.Timestamp(session)
        for session in benchmark_dates
        if rebalance_dates[0] <= session <= pd.Timestamp(end_date)
    ]
    state = sim.get_default_state(capital=initial_capital, strategy_id="multifactor_c_v1")
    order_dir = Path(orders_dir)
    signals: list[dict[str, Any]] = []
    signals_by_execution_date: dict[str, dict[str, Any]] = {}
    executions: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    filled_orders = 0
    cancelled_orders = 0

    for session in sessions:
        date_text = session.strftime("%Y-%m-%d")
        due_tickers = [
            order["ticker"]
            for order in state["pending_orders"]
            if order.get("execution_date") == date_text
        ]
        open_bars = {
            ticker: _exact_bar(prices_dict.get(ticker), session)
            for ticker in set(due_tickers)
        }
        open_bars = {ticker: bar for ticker, bar in open_bars.items() if bar is not None}
        open_events = sim.execute_open_orders(state, open_bars, as_of=date_text)
        all_events.extend(open_events)
        day_fills = sum(event["status"] == "FILLED" for event in open_events)
        day_cancels = len(open_events) - day_fills
        filled_orders += day_fills
        cancelled_orders += day_cancels

        for event in open_events:
            prior_signal = signals_by_execution_date.get(date_text)
            if prior_signal is not None:
                prior_signal["fills"] = prior_signal.get("fills", 0) + int(event["status"] == "FILLED")
                prior_signal["cancellations"] = prior_signal.get("cancellations", 0) + int(event["status"] != "FILLED")
                prior_signal["held_after_open"] = sorted(state["positions"])
        if open_events:
            executions.append(
                {
                    "execution_date": date_text,
                    "filled": day_fills,
                    "cancelled": day_cancels,
                    "held_symbols": sorted(state["positions"]),
                }
            )

        closes = {}
        for ticker in state["positions"]:
            row = _row_on_or_before(prices_dict.get(ticker), session)
            if row is not None and pd.notna(row.get("Close")):
                closes[ticker] = float(row["Close"])
        benchmark_row = _row_on_or_before(prices_dict[BENCHMARK_SYMBOL], session)
        benchmark_close = float(benchmark_row["Close"]) if benchmark_row is not None else 0.0
        sim.mark_equity(state, closes, benchmark_close, date_text)

        if is_monthly_rebalance_date(date_text, prices_dict[BENCHMARK_SYMBOL]):
            payload = create_daily_signal(
                date_text,
                prices_dict,
                revenue_dict,
                institutional_dict,
                state,
                order_dir,
            )
            orders_path = order_dir / f"orders_{date_text.replace('-', '')}.json"
            orders = sim.load_orders(orders_path, as_of=date_text)
            pending = sim.plan_mfc_orders(state, orders, as_of=date_text)
            signal = {
                "signal_date": date_text,
                "execution_date": payload["execution_date"],
                "target_symbols": payload["metadata"]["target_symbols"],
                "market_bullish": payload["metadata"]["market_bullish"],
                "equity_exposure": payload["metadata"]["equity_exposure"],
                "orders_planned": len(pending),
                "fills": 0,
                "cancellations": 0,
            }
            signals.append(signal)
            signals_by_execution_date[signal["execution_date"]] = signal

    return {
        "state": state,
        "signals": signals,
        "executions": executions,
        "order_events": all_events,
        "filled_orders": filled_orders,
        "cancelled_orders": cancelled_orders,
        "membership": membership,
    }


def _load_pickle(path: Path) -> dict[str, pd.DataFrame]:
    if not path.exists():
        raise FileNotFoundError(f"Required cached data is missing: {path}")
    with path.open("rb") as handle:
        value = pickle.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a symbol-to-DataFrame pickle cache: {path}")
    return value


def load_research_cache(cache_dir: Path | str) -> tuple[dict, dict, dict]:
    directory = Path(cache_dir)
    return (
        _load_pickle(directory / "market_prices.pkl"),
        _load_pickle(directory / "market_revenue.pkl"),
        _load_pickle(directory / "market_institutional.pkl"),
    )


def run_reference_close_engine(
    research_root: Path | str,
    prices: dict[str, pd.DataFrame],
    revenue: dict[str, pd.DataFrame],
    institutional: dict[str, pd.DataFrame],
    start_date: str,
    end_date: str,
) -> tuple[dict[str, Any], dict[str, dict[str, float]]]:
    """Run the checked-in Research v3 engine and collect its monthly targets."""
    root = Path(research_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.backtest.engine import BacktestEngine
    from src.data.market_pool import build_market_membership as reference_market_membership
    from src.factors.factor_builder import FactorBuilder as ReferenceFactorBuilder
    from src.strategies.strategy_c_multifactor import EnsembleMultiFactorStrategy

    membership = reference_market_membership(prices, top_n=200)
    factor_builder = ReferenceFactorBuilder(
        prices,
        revenue_dict=revenue,
        institutional_dict=institutional,
        liquidity_min_turnover=30_000_000.0,
        universe_by_year=membership,
    )
    strategy = EnsembleMultiFactorStrategy(top_n=20, bear_equity_exposure=0.2)
    result = BacktestEngine(prices, factor_builder, initial_capital=1_000_000.0).run_strategy(
        strategy,
        start_date=start_date,
        end_date=end_date,
    )
    target_by_date: dict[str, dict[str, float]] = {}
    for date in factor_builder.get_rebalance_dates(start_date, end_date):
        key = pd.Timestamp(date).strftime("%Y-%m-%d")
        bullish = factor_builder.is_market_bullish(date)
        snapshot = factor_builder.compute_factors_snapshot(date)
        target_by_date[key] = strategy.select_portfolio(snapshot, is_market_bullish=bullish)
    return result, target_by_date


def _max_drawdown(values: pd.Series) -> float:
    if values.empty:
        return 0.0
    return float((values / values.cummax() - 1.0).min())


def render_comparison_report(
    start_date: str,
    end_date: str,
    replay: dict[str, Any],
    reference: dict[str, Any],
    reference_targets: dict[str, dict[str, float]],
    benchmark_close: pd.Series,
    source_cache: Path | str,
) -> str:
    """Format measured portfolio alignment and close-vs-open performance."""
    state = replay["state"]
    ref_nav = reference["daily_nav"]["nav"]
    sim_nav = pd.Series(
        [row["equity"] for row in state["equity_curve"]],
        index=pd.to_datetime([row["date"] for row in state["equity_curve"]]),
    )
    initial_capital = float(state["config"]["initial_capital"])
    ref_return = float(ref_nav.iloc[-1] / initial_capital - 1.0)
    sim_return = float(sim_nav.iloc[-1] / initial_capital - 1.0) if not sim_nav.empty else 0.0
    benchmark_window = benchmark_close[
        (benchmark_close.index >= pd.Timestamp(start_date)) & (benchmark_close.index <= pd.Timestamp(end_date))
    ]
    benchmark_return = float(benchmark_window.iloc[-1] / benchmark_window.iloc[0] - 1.0)
    signal_by_date = {signal["signal_date"]: signal for signal in replay["signals"]}

    rows = []
    exact_matches = 0
    total_overlap = 0
    total_union = 0
    for signal_date, target_weights in reference_targets.items():
        signal = signal_by_date.get(signal_date)
        open_targets = set(signal["target_symbols"] if signal else [])
        close_targets = set(target_weights)
        intersection = close_targets & open_targets
        union = close_targets | open_targets
        jaccard = len(intersection) / len(union) if union else 1.0
        exact = close_targets == open_targets
        exact_matches += int(exact)
        total_overlap += len(intersection)
        total_union += len(union)
        if signal is None:
            signal = {}
        held_after_open = ", ".join(signal.get("held_after_open", [])) or "尚未成交"
        rows.append(
            "| {date} | {n_ref} | {n_open} | {jaccard:.0%} | {held} | {fills} | {cancels} |".format(
                date=signal_date,
                n_ref=len(close_targets),
                n_open=len(open_targets),
                jaccard=jaccard,
                held=held_after_open,
                fills=signal.get("fills", 0),
                cancels=signal.get("cancellations", 0),
            )
        )

    ref_mdd = _max_drawdown(ref_nav)
    sim_mdd = _max_drawdown(sim_nav)
    target_jaccard = total_overlap / total_union if total_union else 1.0
    exact_rate = exact_matches / len(reference_targets) if reference_targets else 1.0
    buy_fills = [event for event in replay["order_events"] if event.get("status") == "FILLED" and event.get("side") == "buy"]
    gap_bps = [
        (float(event["fill_price"]) / float(event["reference_close"]) - 1.0) * 10_000.0
        for event in buy_fills
        if event.get("reference_close") and event.get("fill_price")
    ]
    avg_buy_gap_bps = float(np.mean(gap_bps)) if gap_bps else 0.0

    return "\n".join(
        [
            "# TWSE Strategy C Multifactor Replay (2024–2026)",
            "",
            f"- Replay window: {start_date} to {end_date}",
            f"- Read-only TWSE Research cache: `{Path(source_cache)}`",
            "- Reference: checked-in TWSE Research engine v3, close-price rebalances, top_n=20, bear exposure=0.2.",
            "- Paper replay: next-session opening auction; buy limits use prior close, odd-lot shares round down, sells execute at open; commission, transaction tax, and existing simulator sell slippage apply. No TP/SL or cash yield.",
            "",
            "## Performance",
            "",
            "| Variant | Ending equity (TWD) | Cumulative return | Maximum drawdown |",
            "|---|---:|---:|---:|",
            f"| Research close-fill | {float(ref_nav.iloc[-1]):,.0f} | {ref_return:+.2%} | {ref_mdd:.2%} |",
            f"| Independent sim open-fill | {float(sim_nav.iloc[-1]) if not sim_nav.empty else initial_capital:,.0f} | {sim_return:+.2%} | {sim_mdd:.2%} |",
            f"| 0050 close benchmark | — | {benchmark_return:+.2%} | — |",
            f"| Open-fill minus close-fill | — | {(sim_return - ref_return) * 100:+.2f} percentage points | — |",
            "",
            "## Monthly target and execution comparison",
            "",
            f"Target set exact matches: {exact_matches}/{len(reference_targets)} ({exact_rate:.1%}); aggregate target Jaccard: {target_jaccard:.1%}.",
            f"Buy orders filled: {len(buy_fills)}/{sum(event.get('side') == 'buy' for event in replay['order_events'])}; average filled buy open-vs-reference-close gap: {avg_buy_gap_bps:+.1f} bps.",
            f"All MFC orders: {replay['filled_orders']} filled; {replay['cancelled_orders']} canceled/skipped.",
            "",
            "| Signal date | Research targets | Port targets | Target Jaccard | Held after next open | Fills | Cancels |",
            "|---|---:|---:|---:|---|---:|---:|",
            *rows,
            "",
            "## Interpretation",
            "",
            "Selection parity is evaluated before execution so an open-vs-close price difference cannot hide a factor or point-in-time bug. Filled holdings can diverge from the target set when the next open gaps above the close-based buy limit, when a slot cannot afford one share, or when sell/buy friction consumes cash. The simulator does not credit the Research engine's annual cash yield; both differences are reported model assumptions alongside the open-close price effect.",
            "",
        ]
    )


def run_full_replay(
    research_root: Path | str,
    cache_dir: Path | str,
    start_date: str = "2024-01-01",
    end_date: str = "2026-09-30",
    orders_dir: Path | str = "/tmp/mfc-replay-orders",
) -> tuple[str, dict[str, Any]]:
    prices, revenue, institutional = load_research_cache(cache_dir)
    if BENCHMARK_SYMBOL not in prices:
        raise ValueError(f"{BENCHMARK_SYMBOL} is missing from the cached prices")
    replay = simulate_open_price_replay(
        start_date,
        end_date,
        prices,
        revenue,
        institutional,
        initial_capital=1_000_000.0,
        orders_dir=orders_dir,
    )
    reference, reference_targets = run_reference_close_engine(
        research_root,
        prices,
        revenue,
        institutional,
        start_date,
        end_date,
    )
    port_targets = {signal["signal_date"]: set(signal["target_symbols"]) for signal in replay["signals"]}
    differences = {
        date: (set(weights), port_targets.get(date, set()))
        for date, weights in reference_targets.items()
        if set(weights) != port_targets.get(date, set())
    }
    if differences:
        samples = "; ".join(
            f"{date}: Research={sorted(ref)}, port={sorted(port)}"
            for date, (ref, port) in list(differences.items())[:3]
        )
        raise RuntimeError(f"Strategy C target selections differ from Research v3: {samples}")

    benchmark = prices[BENCHMARK_SYMBOL]
    benchmark_series = benchmark["Close"].copy()
    benchmark_series.index = _naive_index(benchmark_series.index)
    report = render_comparison_report(
        start_date,
        end_date,
        replay,
        reference,
        reference_targets,
        benchmark_series,
        cache_dir,
    )
    return report, {"replay": replay, "reference": reference, "targets": reference_targets}


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare Strategy C open-fill replay with TWSE Research v3")
    parser.add_argument("--research-root", default="/root/work/TWSE_Research")
    parser.add_argument("--cache-dir", default="/root/work/TWSE_Research/data/cache")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--orders-dir", default="/tmp/mfc-replay-orders")
    parser.add_argument("--report", default="reports/strategy_c_multifactor_replay_2024_2026.md")
    return parser


def main() -> None:
    args = build_cli_parser().parse_args()
    report, _ = run_full_replay(
        args.research_root,
        args.cache_dir,
        args.start,
        args.end,
        args.orders_dir,
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"Replay report written to {report_path}")


if __name__ == "__main__":
    main()
