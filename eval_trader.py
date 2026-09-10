#!/usr/bin/env python3
"""Eval Trader V2: headless back-test driver.

Chains universe.py → strategy signals → order_execution D+1 fill/cancel →
daily MTM → risk_metrics output.  New code only; reuses all existing modules.

Usage:
    python eval_trader.py --days 180 --strategy baseline --out artifacts/eval/latest
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Project imports
from strategy.ai_strategy import fetch_panel_data
from strategy.order_execution import evaluate_buy_limit_at_open
from strategy.risk_metrics import compute_risk_metrics
from strategy.sizing import size_position

# ── Cost constants (TWSE, configurable) ──────────────────────────────────────
COMMISSION_RATE = 0.001425    # 0.1425% brokerage (ceiling; brokers vary)
SELL_TAX_RATE = 0.003         # 0.3% securities transaction tax (ordinary stock)
SLIPPAGE_RATE = 0.003         # 0.3% slippage (sell-side only)
MIN_COMMISSION = 20.0         # NT$20 minimum commission
LOT_SIZE = 1000               # Regular board lot size (odd lots have worse execution)

# Derived cost fractions
BUY_COST = COMMISSION_RATE

# ── Position defaults ────────────────────────────────────────────────────────
TP_ATR, SL_ATR, MAX_HOLD = 4.0, 3.0, 20
INIT_CAPITAL = 1_000_000.0
MAX_POSITIONS = 5
POSITION_SIZE = 0.15
RESERVE_RATIO = 0.20


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Simulation state (plain dicts – no classes)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _new_state() -> dict[str, Any]:
    return {
        "cash": INIT_CAPITAL,
        "positions": {},   # ticker → {entry, shares, tp, sl, entry_date, day_count, signal_date}
        "pending": [],     # [{ticker, limit_price, signal_date, exec_date, atr}]
        "trades": [],
        "equity_curve": [],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Cost helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _buy_commission(notional: float) -> float:
    """Commission on buy: max(COMMISSION_RATE * notional, MIN_COMMISSION)."""
    return max(notional * COMMISSION_RATE, MIN_COMMISSION)


def _sell_costs(notional: float) -> float:
    """Total sell-side costs: commission + tax + slippage (min commission on brokerage)."""
    commission = max(notional * COMMISSION_RATE, MIN_COMMISSION)
    return commission + notional * (SELL_TAX_RATE + SLIPPAGE_RATE)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Close-position helper (dedup for exits and force-close)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _close_position(state, tkr, exit_price, exit_date, reason):
    """Liquidate position, record trade, update cash."""
    pos = state["positions"][tkr]
    shares = pos["shares"]
    entry = pos["entry"]
    proceeds = exit_price * shares - _sell_costs(exit_price * shares)
    cost_basis = entry * shares + _buy_commission(entry * shares)
    net_pnl = proceeds - cost_basis
    ret_pct = net_pnl / cost_basis if cost_basis > 0 else 0.0
    state["cash"] += proceeds
    state["trades"].append({
        "ticker": tkr, "signal_date": pos["signal_date"],
        "entry_date": pos["entry_date"], "exit_date": exit_date,
        "entry": entry, "exit": exit_price, "shares": shares,
        "days_held": pos["day_count"], "reason": reason,
        "net_pnl": round(net_pnl, 2), "return_pct": round(ret_pct, 4),
    })
    del state["positions"][tkr]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Signal generation wrappers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _baseline_signals(target_dt, close_df, vol_df, top_n=5, exclude_tickers=None):
    from strategy.eval_baseline import filter_baseline_candidates
    return filter_baseline_candidates(target_dt, close_df, vol_df, top_n=top_n, exclude_tickers=exclude_tickers)


def _mr20_signals(target_dt, close_df, vol_df, top_n=5, exclude_tickers=None):
    from strategy.mr20_strategy import filter_mr20_candidates
    as_of = target_dt.strftime("%Y-%m-%d")
    candidates = filter_mr20_candidates(close_df, vol_df, as_of_date=as_of, config=None)
    if exclude_tickers:
        candidates = [c for c in candidates if c.get("ticker") not in exclude_tickers]
    return candidates[:top_n]


SIGNAL_FUNCS = {
    "baseline": _baseline_signals,
    "mr20": _mr20_signals,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Place pending orders for signal day D
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _place_orders(state, candidates, signal_date, next_date, top_n=5):
    """Create limit orders: Limit Price = Close_D, exec date = D+1."""
    held = set(state["positions"].keys())
    pending_tickers = {p["ticker"] for p in state["pending"]}
    # Enforce MAX_POSITIONS: cap new orders at available slots
    max_new = MAX_POSITIONS - len(held) - len(pending_tickers)
    if max_new <= 0:
        return []
    orders = []
    for cand in candidates:
        if len(orders) >= min(top_n, max_new):
            break
        tkr = cand["ticker"]
        if tkr in held or tkr in pending_tickers:
            continue
        held.add(tkr)  # prevent duplicates within this batch
        limit = cand.get("close", 0.0)
        atr = cand.get("atr", 0.0)
        if limit <= 0 or atr <= 0:
            continue
        state["pending"].append({
            "ticker": tkr, "limit_price": limit, "signal_date": signal_date,
            "exec_date": next_date, "atr": atr,
        })
        pending_tickers.add(tkr)
        orders.append({"ticker": tkr, "limit_price": limit, "status": "PENDING"})
    return orders


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Execute due orders (D+1 open)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _execute_orders(state, open_df, exec_date):
    """Fill or cancel pending orders using D+1 open price.

    Sizing uses open prices for held positions (no same-day close look-ahead)
    and enforces MAX_POSITIONS at execution time.
    """
    due = [p for p in state["pending"] if p["exec_date"] == exec_date]
    state["pending"] = [p for p in state["pending"] if p["exec_date"] != exec_date]
    fills = []
    for order in due:
        tkr = order["ticker"]
        limit = order["limit_price"]
        bar_open = open_df.at[exec_date, tkr] if exec_date in open_df.index and tkr in open_df.columns else np.nan
        decision = evaluate_buy_limit_at_open(limit, bar_open)
        if decision.status != "FILLED":
            fills.append({"ticker": tkr, "limit_price": limit, "open_price": bar_open,
                          "status": decision.status, "exec_date": exec_date})
            continue

        fill_price = decision.fill_price
        # MAX_POSITIONS execution-time guard
        if len(state["positions"]) >= MAX_POSITIONS:
            fills.append({"ticker": tkr, "limit_price": limit, "open_price": bar_open,
                          "status": "CANCELLED_MAX_POSITIONS", "exec_date": exec_date})
            continue
        # Sizing: equal-weight slot using open prices (no same-day close look-ahead)
        # When a held position has NaN open, fall back to its entry price.
        mkt_val = 0.0
        for t, p in state["positions"].items():
            if exec_date in open_df.index and t in open_df.columns:
                px = open_df.at[exec_date, t]
                if math.isfinite(px):
                    mkt_val += px * p["shares"]
                else:
                    mkt_val += p["entry"] * p["shares"]
            else:
                mkt_val += p["entry"] * p["shares"]
        equity_now = state["cash"] + mkt_val
        sizing = size_position(
            equity=equity_now,
            position_size=POSITION_SIZE,
            fill_price=fill_price,
            cash=state["cash"],
            reserve=INIT_CAPITAL * RESERVE_RATIO,
            lot_size=LOT_SIZE,
            min_commission=MIN_COMMISSION,
            buy_cost_rate=BUY_COST,
        )
        if not sizing.ok:
            fills.append({"ticker": tkr, "limit_price": limit, "open_price": bar_open,
                          "status": sizing.status, "exec_date": exec_date})
            continue

        shares = sizing.shares
        cost = sizing.commission
        atr = order["atr"]
        tp = fill_price + TP_ATR * atr
        sl = fill_price - SL_ATR * atr
        state["cash"] -= (sizing.trade_amount + cost)
        state["positions"][tkr] = {
            "entry": fill_price, "shares": shares, "tp": tp, "sl": sl,
            "entry_date": exec_date, "day_count": 0,
            "signal_date": order["signal_date"],
        }
        fills.append({"ticker": tkr, "limit_price": limit, "fill_price": fill_price,
                       "shares": shares, "tp": tp, "sl": sl, "status": "FILLED",
                       "exec_date": exec_date})
    return fills


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Daily MTM + SL/TP/TIME exits  (SL > TP > TIME priority)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _daily_mtm(state, close_df, open_df, high_df, low_df, date_str):
    """Check exits, update day_count, record equity."""
    to_remove = []
    for tkr, pos in state["positions"].items():
        if pos["entry_date"] == date_str:
            continue  # no exit on fill day
        if date_str not in close_df.index or tkr not in close_df.columns:
            continue
        close_px = close_df.at[date_str, tkr]
        open_px = open_df.at[date_str, tkr] if tkr in open_df.columns else np.nan
        high_px = high_df.at[date_str, tkr] if tkr in high_df.columns else np.nan
        low_px = low_df.at[date_str, tkr] if tkr in low_df.columns else np.nan

        pos["day_count"] += 1
        reason = None
        exit_price = close_px

        # SL check (gap-down: fill at open)
        if pd.notna(low_px) and low_px <= pos["sl"]:
            reason = "SL"
            exit_price = open_px if (pd.notna(open_px) and open_px < pos["sl"]) else pos["sl"]
        # TP check (gap-up: fill at open)
        elif pd.notna(high_px) and high_px >= pos["tp"]:
            reason = "TP"
            exit_price = open_px if (pd.notna(open_px) and open_px > pos["tp"]) else pos["tp"]
        # TIME exit
        elif pos["day_count"] >= MAX_HOLD:
            reason = "TIME"
            exit_price = close_px

        if reason:
            to_remove.append((tkr, exit_price, date_str, reason))

    for tkr, exit_price, exit_date, reason in to_remove:
        _close_position(state, tkr, exit_price, exit_date, reason)

    # Record equity
    mkt_val = sum(
        close_df.at[date_str, t] * p["shares"]
        for t, p in state["positions"].items()
        if date_str in close_df.index and t in close_df.columns
    )
    equity = state["cash"] + mkt_val
    state["equity_curve"].append({"date": date_str, "equity": round(equity, 2)})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Run back-test
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def run_backtest(
    close_df, open_df, high_df, low_df, vol_df,
    strategy: str = "baseline", days: int = 180, top_n: int = 5,
) -> dict[str, Any]:
    sig_fn = SIGNAL_FUNCS[strategy]
    state = _new_state()
    all_dates = close_df.index

    # Select the last `days` trading sessions, keeping warmup before
    total_bars = len(all_dates)
    end_idx = total_bars  # last available bar
    start_idx = max(0, end_idx - days)
    sim_dates = all_dates[start_idx: end_idx]

    order_log, fill_log = [], []

    for dt in sim_dates:
        dt_str = dt.strftime("%Y-%m-%d")

        # 1) Execute due orders on D (placed yesterday)
        fills = _execute_orders(state, open_df, dt_str)
        fill_log.extend(fills)

        # 2) Daily MTM (exits) — updates capacity before new signals
        _daily_mtm(state, close_df, open_df, high_df, low_df, dt_str)

        # 3) Signal on D (after exits free up capacity)
        # Pass held+pending set to signal fn for pre-filtering before truncation
        held = set(state["positions"].keys())
        pending_tickers = {p["ticker"] for p in state["pending"]}
        candidates = sig_fn(dt, close_df, vol_df, top_n=top_n, exclude_tickers=held | pending_tickers)
        if candidates:
            # Find next trading day for exec
            next_idx = all_dates.get_loc(dt) + 1
            if next_idx < len(all_dates):
                next_dt = all_dates[next_idx]
                next_str = next_dt.strftime("%Y-%m-%d")
                orders = _place_orders(state, candidates, dt_str, next_str, top_n=top_n)
                order_log.extend(orders)

    # Cancel any remaining pending orders (final day has no execution window)
    for p in state["pending"]:
        fill_log.append({
            "ticker": p["ticker"], "limit_price": p["limit_price"],
            "status": "CANCELLED_EXPIRED", "exec_date": p["exec_date"],
        })
    state["pending"].clear()

    # Force-close remaining positions at last close
    last_dt = sim_dates[-1].strftime("%Y-%m-%d") if len(sim_dates) > 0 else None
    for tkr in list(state["positions"]):
        pos = state["positions"][tkr]
        if last_dt and last_dt in close_df.index and tkr in close_df.columns:
            exit_px = close_df.at[last_dt, tkr]
        else:
            exit_px = pos["entry"]
        _close_position(state, tkr, exit_px, last_dt or "", "FORCE_CLOSE")

    # Record terminal equity after forced liquidation (replace if same date)
    if last_dt and state["equity_curve"] and state["equity_curve"][-1]["date"] == last_dt:
        state["equity_curve"][-1]["equity"] = round(state["cash"], 2)
    else:
        state["equity_curve"].append({"date": last_dt or "", "equity": round(state["cash"], 2)})

    # Compute cancel counts once
    cancel_reasons = {}
    for f in fill_log:
        st = f.get("status", "")
        if "CANCELLED" in st:
            cancel_reasons[st] = cancel_reasons.get(st, 0) + 1

    return {
        "state": state, "order_log": order_log, "fill_log": fill_log,
        "strategy": strategy, "days_simulated": len(sim_dates),
        "cancel_reasons": cancel_reasons,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Report generation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _build_report(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    state = result["state"]

    # Equity DataFrame
    eq_df = pd.DataFrame(state["equity_curve"])
    if eq_df.empty:
        eq_df = pd.DataFrame({"date": [], "equity": []})
    eq_df["date"] = pd.to_datetime(eq_df["date"])
    eq_df = eq_df.set_index("date")
    eq_df["Equity"] = eq_df["equity"]

    # Trades DataFrame
    trades_df = pd.DataFrame(state["trades"])
    if not trades_df.empty:
        trades_df = trades_df.rename(columns={
            "entry": "Entry_Price", "exit": "Exit_Price",
            "return_pct": "Return_Pct", "days_held": "Days_Held",
            "reason": "Reason", "ticker": "Ticker",
            "entry_date": "Entry_Date", "exit_date": "Exit_Date",
        })
    else:
        trades_df = pd.DataFrame(columns=["Return_Pct", "Days_Held", "Reason", "Ticker"])

    # Funnel counts
    orders = result["order_log"]
    fills = result["fill_log"]
    orders_placed = len(orders)
    n_filled = sum(1 for f in fills if f.get("status") == "FILLED")
    cancel_reasons = result["cancel_reasons"]
    n_cancelled = sum(cancel_reasons.values())

    # Risk metrics
    if len(eq_df) > 1 and not trades_df.empty:
        metrics = compute_risk_metrics(eq_df, trades_df, initial_capital=INIT_CAPITAL)
    else:
        metrics = {
            "total_return": 0, "ann_return": 0, "ann_volatility": 0,
            "sharpe": 0, "sortino": 0, "calmar": 0,
            "max_drawdown_pct": 0, "total_trades": 0, "win_rate": 0,
            "avg_days_held": 0, "reason_counts": {}, "profit_factor": 0,
        }

    report = {
        "strategy": result["strategy"],
        "days_simulated": result["days_simulated"],
        "funnel": {
            "signals_generated": orders_placed,
            "fills": n_filled,
            "cancelled": n_cancelled,
            "cancel_reasons": cancel_reasons,
        },
        "metrics": {k: round(float(v), 6) if isinstance(v, (int, float, np.floating)) else v
                     for k, v in metrics.items()
                     if k not in ("max_drawdown_date", "max_drawdown_peak")},
    }

    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    eq_df[["Equity"]].to_csv(out_dir / "equity.csv")
    # Always write trades.csv (even empty)
    trades_df.to_csv(out_dir / "trades.csv", index=False)
    # Combine placement, fill, and cancel records into orders.jsonl
    all_records = []
    for o in orders:
        all_records.append(o)
    for f in fills:
        all_records.append(f)
    (out_dir / "orders.jsonl").write_text(
        "\n".join(json.dumps(r) for r in all_records) if all_records else ""
    )

    # Human-readable markdown summary
    lines = [
        f"# Eval Report — {result['strategy']}",
        f"**Simulated days:** {result['days_simulated']}",
        f"**Signals:** {orders_placed} → **Fills:** {n_filled} → **Cancelled:** {n_cancelled}",
        "",
        "## Cancel Reasons",
    ]
    for reason, cnt in sorted(cancel_reasons.items()):
        lines.append(f"- {reason}: {cnt}")
    lines.extend(["", "## Risk Metrics", ""])
    for k in ["total_return", "ann_return", "ann_volatility", "sharpe", "sortino",
              "calmar", "max_drawdown_pct", "total_trades", "win_rate", "avg_days_held"]:
        v = metrics.get(k, 0)
        if isinstance(v, (int, float, np.floating)):
            if "pct" in k or "return" in k or "rate" in k or "volatility" in k:
                lines.append(f"- **{k}**: {float(v)*100:.2f}%")
            else:
                lines.append(f"- **{k}**: {float(v):.4f}")
    lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    ap = argparse.ArgumentParser(description="Eval Trader V2 — headless back-test driver")
    ap.add_argument("--days", type=int, default=180, help="Trading days to simulate")
    ap.add_argument("--strategy", choices=["baseline", "mr20"], default="baseline")
    ap.add_argument("--out", type=str, default="artifacts/eval/latest", help="Output directory")
    ap.add_argument("--tickers-file", type=str, default=None,
                    help="Optional: path to a text file with one ticker per line")
    args = ap.parse_args()

    print(f"🚀 Eval Trader V2 — strategy={args.strategy}, days={args.days}")

    # 1) Universe
    if args.tickers_file:
        tickers = [l.strip() for l in Path(args.tickers_file).read_text().splitlines() if l.strip()]
        print(f"📋 Loaded {len(tickers)} tickers from {args.tickers_file}")
    else:
        from strategy.universe import get_twse_common_stocks
        tickers = get_twse_common_stocks(verbose=True)
        print(f"📋 TWSE universe: {len(tickers)} tickers")

    # 2) Download panel data
    close_df, open_df, high_df, low_df, vol_df = fetch_panel_data(
        tickers, days=args.days + 120  # extra for warmup
    )

    # 3) Run back-test
    result = run_backtest(close_df, open_df, high_df, low_df, vol_df,
                          strategy=args.strategy, days=args.days)

    # 4) Output
    out = Path(args.out)
    _build_report(result, out)
    print(f"\n✅ Report written to {out}/")
    print("   - report.json  (machine-readable metrics)")
    print("   - report.md    (human summary)")
    print("   - equity.csv   (daily equity curve)")
    print("   - trades.csv   (closed trades)")
    print("   - orders.jsonl (order log)")


if __name__ == "__main__":
    main()
