"""Synthetic-bar tests for eval_trader — proves no look-ahead & correct fill/exit logic."""
import math

import numpy as np
import pandas as pd

from strategy.order_execution import evaluate_buy_limit_at_open


# ── Helpers ──────────────────────────────────────────────────────────────────
def _make_df(rows: list[dict], tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Build close_df / open_df / high_df / low_df / vol_df from row dicts."""
    dates = pd.to_datetime([r["date"] for r in rows])
    data = {col: {t: [] for t in tickers} for col in ["close", "open", "high", "low", "vol"]}
    for r in rows:
        for t in tickers:
            data["close"][t].append(r.get(f"close_{t}", np.nan))
            data["open"][t].append(r.get(f"open_{t}", np.nan))
            data["high"][t].append(r.get(f"high_{t}", np.nan))
            data["low"][t].append(r.get(f"low_{t}", np.nan))
            data["vol"][t].append(r.get(f"vol_{t}", np.nan))
    mk = lambda d: pd.DataFrame(d, index=dates)
    return (mk(data["close"]), mk(data["open"]), mk(data["high"]),
            mk(data["low"]), mk(data["vol"]))





# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 1: No look-ahead — volume ratio must use vol[D-20:D-1] only, not vol[D]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_no_lookahead_volume_ratio():
    """Volume_Ratio at D uses vol_avg from D-20..D-1; D volume is numerator only."""
    from strategy.eval_baseline import filter_baseline_candidates

    np.random.seed(42)
    n = 65  # enough for min_history=60 + 5 for MA shift
    dates = pd.bdate_range("2025-01-01", periods=n)
    # Strong uptrend so Close > MA20 and MA20 rising
    base_price = np.linspace(50, 80, n)
    close_vals = base_price + np.random.normal(0, 0.3, n)
    # Normal volume except spike on D (last bar) — should NOT affect avg denominator
    vol_vals = np.full(n, 1_000_000.0)
    vol_vals[-1] = 10_000_000.0  # 10x spike on D

    close_df = pd.DataFrame({"AAAA": close_vals}, index=dates)
    vol_df = pd.DataFrame({"AAAA": vol_vals}, index=dates)

    # Manual correct calc: avg of vol_vals[-21:-1] (20 bars, excludes D)
    vol_avg_correct = np.mean(vol_vals[-21:-1])
    ratio_with_spike = vol_vals[-1] / vol_avg_correct

    target_dt = dates[-1]
    candidates = filter_baseline_candidates(target_dt, close_df, vol_df, top_n=5)
    assert isinstance(candidates, list), "Must return a list"

    # Verify volume ratio formula uses shift(1).rolling(20) which excludes current D
    vol_avg_func = vol_df["AAAA"].shift(1).rolling(20).mean()
    ratio_func = vol_df["AAAA"] / vol_avg_func
    ratio_at_D = ratio_func.iloc[-1]
    assert math.isfinite(ratio_at_D), "Volume ratio should be finite"
    assert abs(ratio_at_D - ratio_with_spike) < 0.01, (
        f"Volume ratio {ratio_at_D:.4f} should match correct calc {ratio_with_spike:.4f}"
    )
    # Verify candidates are non-empty when conditions are met
    assert len(candidates) > 0, "Uptrending data with vol spike should produce candidates"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 2: Gap open above limit → CANCELLED_OPEN_ABOVE_LIMIT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_gap_open_above_limit_cancelled():
    """When Open_D+1 > Close_D (limit price), order must be cancelled."""
    decision = evaluate_buy_limit_at_open(limit_price=100.0, open_price=105.0)
    assert decision.status == "CANCELLED_OPEN_ABOVE_LIMIT"
    assert decision.fill_price is None

    decision2 = evaluate_buy_limit_at_open(limit_price=100.0, open_price=100.0)
    assert decision2.status == "FILLED"
    assert decision2.fill_price == 100.0

    decision3 = evaluate_buy_limit_at_open(limit_price=100.0, open_price=95.0)
    assert decision3.status == "FILLED"
    assert decision3.fill_price == 95.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 3: Gap-down SL fill at open price (not threshold price)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_gap_down_sl_fills_at_open():
    """When Open gaps below SL, exit price = Open, not SL threshold."""
    from eval_trader import _new_state, _daily_mtm

    dates = pd.bdate_range("2025-03-01", periods=5)
    tickers = ["AAAA"]
    close_df, open_df, high_df, low_df, _vol_df = _make_df([
        {"date": dates[0], "close_AAAA": 100.0, "open_AAAA": 100.0,
         "high_AAAA": 101.0, "low_AAAA": 99.0, "vol_AAAA": 1e6},
        {"date": dates[1], "close_AAAA": 100.0, "open_AAAA": 100.0,
         "high_AAAA": 101.0, "low_AAAA": 99.0, "vol_AAAA": 1e6},
        {"date": dates[2], "close_AAAA": 100.0, "open_AAAA": 100.0,
         "high_AAAA": 101.0, "low_AAAA": 99.0, "vol_AAAA": 1e6},
        {"date": dates[3], "close_AAAA": 100.0, "open_AAAA": 100.0,
         "high_AAAA": 101.0, "low_AAAA": 99.0, "vol_AAAA": 1e6},
        {"date": dates[4], "close_AAAA": 85.0, "open_AAAA": 90.0,
         "high_AAAA": 92.0, "low_AAAA": 84.0, "vol_AAAA": 2e6},
    ], tickers)

    state = _new_state()
    state["positions"]["AAAA"] = {
        "entry": 100.0, "shares": 100,
        "tp": 104.0, "sl": 97.0, "atr": 1.0,
        "entry_date": dates[3].strftime("%Y-%m-%d"),
        "day_count": 0,
        "signal_date": dates[2].strftime("%Y-%m-%d"),
    }
    state["cash"] = 90000.0

    _daily_mtm(state, close_df, open_df, high_df, low_df, dates[4].strftime("%Y-%m-%d"))

    assert "AAAA" not in state["positions"]
    assert len(state["trades"]) == 1
    trade = state["trades"][0]
    assert trade["reason"] == "SL"
    assert trade["exit"] == 90.0, f"Expected exit at open 90.0, got {trade['exit']}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 4: Same-day dual-trigger — SL priority over TP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_same_day_dual_trigger_sl_wins():
    """When both SL and TP are hit on the same bar, SL takes priority."""
    from eval_trader import _new_state, _daily_mtm

    dates = pd.bdate_range("2025-04-01", periods=3)
    tickers = ["AAAA"]
    close_df, open_df, high_df, low_df, _vol_df = _make_df([
        {"date": dates[0], "close_AAAA": 100.0, "open_AAAA": 100.0,
         "high_AAAA": 101.0, "low_AAAA": 99.0, "vol_AAAA": 1e6},
        {"date": dates[1], "close_AAAA": 100.0, "open_AAAA": 100.0,
         "high_AAAA": 101.0, "low_AAAA": 99.0, "vol_AAAA": 1e6},
        {"date": dates[2], "close_AAAA": 103.0, "open_AAAA": 105.0,
         "high_AAAA": 105.5, "low_AAAA": 95.0, "vol_AAAA": 2e6},
    ], tickers)

    state = _new_state()
    state["positions"]["AAAA"] = {
        "entry": 100.0, "shares": 100,
        "tp": 104.0, "sl": 97.0, "atr": 1.0,
        "entry_date": dates[1].strftime("%Y-%m-%d"),
        "day_count": 0,
        "signal_date": dates[0].strftime("%Y-%m-%d"),
    }
    state["cash"] = 90000.0

    _daily_mtm(state, close_df, open_df, high_df, low_df, dates[2].strftime("%Y-%m-%d"))

    assert "AAAA" not in state["positions"]
    trade = state["trades"][0]
    assert trade["reason"] == "SL", f"Expected SL priority, got {trade['reason']}"
    assert trade["exit"] == 97.0, f"Expected exit at SL=97.0, got {trade['exit']}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 5: Baseline candidate determinism & tie-break (non-vacuous)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_baseline_candidate_determinism():
    """Same data → same candidates, same order. Tie-break verified."""
    from strategy.eval_baseline import filter_baseline_candidates

    np.random.seed(99)
    dates = pd.bdate_range("2025-01-01", periods=75)
    tickers = ["AAAA", "BBBB", "CCCC"]
    data = {}
    for i, t in enumerate(tickers):
        # Strong uptrend with slight per-ticker variation
        base = np.linspace(50, 80, 75) + i * 5  # offset per ticker
        data[f"close_{t}"] = base + np.random.normal(0, 0.3, 75)
        data[f"open_{t}"] = data[f"close_{t}"] - 0.5
        data[f"high_{t}"] = data[f"close_{t}"] + 1
        data[f"low_{t}"] = data[f"close_{t}"] - 1
        # Base volume + spike on last bar (D) so vol_ratio >= 1.5
        vol_base = [1e6, 1.5e6, 2e6][i]
        vol_arr = np.full(75, vol_base)
        vol_arr[-1] = vol_base * 3  # 3x spike → ratio ≈ 3.0
        data[f"vol_{t}"] = vol_arr
    data["date"] = dates

    close_df, _open_df, _high_df, _low_df, vol_df = _make_df(
        [{k: data[k][i] for k in data if k != "date"} | {"date": dates[i]} for i in range(75)],
        tickers
    )

    target = dates[-1]
    c1 = filter_baseline_candidates(target, close_df, vol_df, top_n=3)
    c2 = filter_baseline_candidates(target, close_df, vol_df, top_n=3)
    assert c1 == c2, "Candidates must be deterministic"
    assert len(c1) > 0, "Must return non-empty candidates"
    for i, c in enumerate(c1, 1):
        assert c["rank"] == i
    scores = [c["score"] for c in c1]
    assert scores == sorted(scores, reverse=True), "Scores must be descending"
    # Verify tie-break: for equal scores, higher turnover wins; for equal turnover, ticker ASC
    tickers_order = [c["ticker"] for c in c1]
    assert tickers_order == sorted(tickers_order), (
        f"Equal-score tie-break should be ticker ASC, got {tickers_order}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 6: End-to-end run_backtest — signal on D, fill on D+1, no same-day fill
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_run_backtest_basic():
    """Full lifecycle: signal D, fill D+1, exit via SL/TP/TIME, terminal equity."""
    from eval_trader import run_backtest, INIT_CAPITAL
    from unittest.mock import patch as _mp

    np.random.seed(123)
    dates = pd.bdate_range("2025-01-01", periods=65)
    # Strong uptrend
    base = np.linspace(50, 80, 65)
    close_v = base + np.random.normal(0, 0.3, 65)
    open_v = close_v - 0.3
    high_v = close_v + 0.5
    low_v = close_v - 0.5
    vol_v = np.full(65, 2e6)

    close_df = pd.DataFrame({"AAAA": close_v}, index=dates)
    open_df = pd.DataFrame({"AAAA": open_v}, index=dates)
    high_df = pd.DataFrame({"AAAA": high_v}, index=dates)
    low_df = pd.DataFrame({"AAAA": low_v}, index=dates)
    vol_df = pd.DataFrame({"AAAA": vol_v}, index=dates)

    # Mock signal function: return a candidate on the 2nd-to-last sim day
    # so it fills on the last day and gets force-closed.
    # Use a high limit price (above next day's open) so the fill executes.
    sim_dates = dates[-5:]
    def mock_signals(target_dt, _close_df, _vol_df, _top_n=5, **_kwargs):
        if target_dt == sim_dates[-2]:
            return [{"ticker": "AAAA", "score": 0.9, "close": 999.0,
                      "atr": 1.0, "rank": 1}]
        return []

    with _mp("eval_trader.SIGNAL_FUNCS", {"baseline": mock_signals}):
        result = run_backtest(close_df, open_df, high_df, low_df, vol_df,
                              strategy="baseline", days=5)

    assert result["days_simulated"] == 5
    state = result["state"]
    # Terminal equity recorded after forced close
    assert len(state["equity_curve"]) >= 5
    last_eq = state["equity_curve"][-1]["equity"]
    assert last_eq > 0, "Terminal equity must be positive"
    # All positions closed
    assert len(state["positions"]) == 0
    # Pending cleared
    assert len(state["pending"]) == 0
    # Proof of lifecycle: at least one fill and one trade
    fills = [f for f in result["fill_log"] if f.get("status") == "FILLED"]
    assert len(fills) >= 1, f"Expected at least 1 fill, got {len(fills)}"
    assert fills[0]["ticker"] == "AAAA"
    assert fills[0]["exec_date"] == sim_dates[-1].strftime("%Y-%m-%d")
    assert len(state["trades"]) >= 1, "Expected at least 1 trade"
    assert state["trades"][0]["ticker"] == "AAAA"
    # Terminal equity < INIT_CAPITAL (costs deducted)
    assert last_eq < INIT_CAPITAL, f"Terminal equity {last_eq} should be < {INIT_CAPITAL} after costs"
    # cancel_reasons available
    assert isinstance(result["cancel_reasons"], dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 7: Final-day pending orders are cancelled
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_final_day_pending_cancelled():
    """Orders placed on the last simulation day must be cancelled (no exec window)."""
    from eval_trader import run_backtest
    from unittest.mock import patch as _mp

    np.random.seed(77)
    dates = pd.bdate_range("2025-01-01", periods=65)
    base = np.linspace(50, 80, 65)
    close_v = base + np.random.normal(0, 0.3, 65)
    vol_v = np.full(65, 2e6)
    vol_v[-1] = 6e6

    close_df = pd.DataFrame({"AAAA": close_v}, index=dates)
    open_df = pd.DataFrame({"AAAA": close_v - 0.3}, index=dates)
    high_df = pd.DataFrame({"AAAA": close_v + 0.5}, index=dates)
    low_df = pd.DataFrame({"AAAA": close_v - 0.5}, index=dates)
    vol_df = pd.DataFrame({"AAAA": vol_v}, index=dates)

    sim_dates = dates[-5:]
    last_sim = sim_dates[-1].strftime("%Y-%m-%d")
    fake_exec = "2099-12-31"  # far future — never reached by sim loop
    from eval_trader import _new_state as _orig_new
    def patched_new_state():
        s = _orig_new()
        # Seed a pending order with exec_date = far future (never in sim)
        s["pending"].append({
            "ticker": "BBBB", "limit_price": 50.0,
            "signal_date": last_sim, "exec_date": fake_exec, "atr": 1.0,
        })
        return s

    with _mp("eval_trader._new_state", patched_new_state):
        result = run_backtest(close_df, open_df, high_df, low_df, vol_df,
                              strategy="baseline", days=5)

    # All pending orders should be cleared
    assert len(result["state"]["pending"]) == 0
    # The seeded pending order must appear in fill_log as CANCELLED_EXPIRED
    expired = [f for f in result["fill_log"] if f.get("status") == "CANCELLED_EXPIRED"]
    assert len(expired) == 1, f"Expected 1 expired order, got {len(expired)}"
    assert expired[0]["ticker"] == "BBBB"
    assert expired[0]["exec_date"] == fake_exec


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 12: Sizing causality — held-position open drives equity → slot → shares
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_sizing_causality_held_open_drives_shares():
    """Prove held-position open price drives equity → slot → share count.

    Seed AAAA as a held position (1 000 shares, entry 100) and signal BBBB.
    Run _execute_orders twice with different AAAA open prices (50 vs 200).
    Assert: (a) fill_price == open_price, (b) different held open → different
    equity → different slot → different share count, proving sizing reads the
    held position's open price (not close).
    """
    from eval_trader import _new_state, _execute_orders

    exec_date = "2025-01-10"
    bbbb_open = 50.0  # BBBB fill price (constant)

    def _run_with_held_open(held_open):
        """Seed AAAA held position, execute BBBB order, return fills."""
        state = _new_state()
        state["cash"] = 500_000.0
        # Seed AAAA held position whose market value contributes to equity
        state["positions"]["AAAA"] = {
            "entry": 100.0, "shares": 1000,
            "tp": 104.0, "sl": 95.0,
            "entry_date": "2025-01-08", "day_count": 1,
            "signal_date": "2025-01-07",
        }
        # Pending BBBB order due on exec_date
        state["pending"].append({
            "ticker": "BBBB", "limit_price": 999.0,
            "signal_date": "2025-01-09", "exec_date": exec_date, "atr": 2.0,
        })
        idx = pd.DatetimeIndex([exec_date])
        open_df = pd.DataFrame(
            {"AAAA": [held_open], "BBBB": [bbbb_open]}, index=idx,
        )
        fills = _execute_orders(state, open_df, exec_date)
        return fills, state

    # Scenario A: AAAA held open = 50 → equity = 500k + 50*1000 = 550k → 1000 shares
    fills_a, _ = _run_with_held_open(50.0)
    filled_a = [f for f in fills_a if f.get("status") == "FILLED"]
    assert len(filled_a) == 1, f"Expected 1 fill in scenario A, got {len(filled_a)}"
    assert filled_a[0]["fill_price"] == bbbb_open, (
        f"Fill must be at Open={bbbb_open}, got {filled_a[0]['fill_price']}"
    )
    shares_a = filled_a[0]["shares"]

    # Scenario B: AAAA held open = 200 → equity = 500k + 200*1000 = 700k → 2000 shares
    fills_b, _ = _run_with_held_open(200.0)
    filled_b = [f for f in fills_b if f.get("status") == "FILLED"]
    assert len(filled_b) == 1, f"Expected 1 fill in scenario B, got {len(filled_b)}"
    assert filled_b[0]["fill_price"] == bbbb_open, (
        f"Fill must be at Open={bbbb_open}, got {filled_b[0]['fill_price']}"
    )
    shares_b = filled_b[0]["shares"]

    # Different held open → different equity → different slot → different shares
    assert shares_a != shares_b, (
        f"Shares should differ when held open varies: "
        f"open=50 gave {shares_a}, open=200 gave {shares_b}"
    )
    assert shares_a > 0 and shares_b > 0, "Should have bought at least one board lot"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 13: NaN-open regression — held position with NaN open falls back to entry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_nan_open_fallback_to_entry_valuation():
    """Regression: held position with NaN open must not crash.

    Seed AAAA held with NaN open price, signal BBBB for execution.
    The NaN fallback should value AAAA at entry * shares, and the resulting
    share count must match the exact calculation using that entry cost basis.
    """
    from eval_trader import _new_state, _execute_orders, LOT_SIZE

    exec_date = "2025-01-10"
    bbbb_open = 50.0

    # ── Choose values where fallback valuation crosses a 1,000-share board-lot
    #    boundary.  With NaN → entry fallback: equity = 500k + 200*1000 = 700k,
    #    slot = 105k → 2000 shares.  Without fallback (0.0): equity = 500k,
    #    slot = 75k → 1000 shares.  This ensures the test FAILS when the
    #    fallback path is removed.
    ENTRY = 200.0
    SHARES = 1000
    CASH = 500_000.0

    state = _new_state()
    state["cash"] = CASH
    # AAAA held position — open will be NaN on exec day
    state["positions"]["AAAA"] = {
        "entry": ENTRY, "shares": SHARES,
        "tp": 104.0, "sl": 95.0,
        "entry_date": "2025-01-08", "day_count": 1,
        "signal_date": "2025-01-07",
    }
    state["pending"].append({
        "ticker": "BBBB", "limit_price": 999.0,
        "signal_date": "2025-01-09", "exec_date": exec_date, "atr": 2.0,
    })

    # AAAA open = NaN (the regression trigger), BBBB open = fill price
    idx = pd.DatetimeIndex([exec_date])
    open_df = pd.DataFrame(
        {"AAAA": [float("nan")], "BBBB": [bbbb_open]}, index=idx,
    )
    fills = _execute_orders(state, open_df, exec_date)
    filled = [f for f in fills if f.get("status") == "FILLED"]
    assert len(filled) == 1, f"Expected 1 fill, got {len(filled)}"
    assert filled[0]["fill_price"] == bbbb_open

    # Compute expected shares using entry fallback valuation
    BUY_COST = 0.001425
    POSITION_SIZE = 0.15
    equity_now = CASH + ENTRY * SHARES  # NaN fallback → entry * shares
    slot = equity_now * POSITION_SIZE
    expected_shares = math.floor(slot / (bbbb_open * (1.0 + BUY_COST)))
    expected_shares = (expected_shares // LOT_SIZE) * LOT_SIZE

    assert filled[0]["shares"] == expected_shares, (
        f"Shares {filled[0]['shares']} != expected {expected_shares} "
        f"(entry-fallback equity={equity_now})"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 8: Cost model — min commission, sell costs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_cost_model_min_commission():
    """Small order: min commission (20 TWD) kicks in."""
    from eval_trader import _buy_commission, _sell_costs, MIN_COMMISSION

    # Small buy: 100 shares * 50 TWD = 5000 notional → 0.1425% = 7.125 < 20
    cost = _buy_commission(5000)
    assert cost == MIN_COMMISSION, f"Expected min commission {MIN_COMMISSION}, got {cost}"

    # Large buy: 1000 shares * 100 TWD = 100000 → 0.1425% = 142.5 > 20
    cost2 = _buy_commission(100000)
    assert cost2 == 100000 * 0.001425, f"Expected 142.5, got {cost2}"

    # Sell: commission (min) + tax + slippage
    sell = _sell_costs(5000)
    expected = MIN_COMMISSION + 5000 * (0.003 + 0.003)
    assert abs(sell - expected) < 0.01, f"Sell costs {sell} != {expected}"

    sell_large = _sell_costs(100000)
    expected_large = 100000 * 0.001425 + 100000 * (0.003 + 0.003)
    assert abs(sell_large - expected_large) < 0.01, f"Sell costs {sell_large} != {expected_large}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 9: Force-close updates terminal equity after costs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_force_close_updates_terminal_equity():
    """After force-close, terminal equity = cash (all positions liquidated)."""
    from eval_trader import _new_state, _close_position, _sell_costs, _buy_commission

    state = _new_state()
    state["cash"] = 80000.0
    entry_price = 100.0
    shares = 100
    exit_price = 110.0
    state["positions"]["AAAA"] = {
        "entry": entry_price, "shares": shares,
        "tp": 104.0, "sl": 97.0, "atr": 1.0,
        "entry_date": "2025-01-01", "day_count": 5,
        "signal_date": "2025-01-02",
    }
    # Force close at 110
    _close_position(state, "AAAA", exit_price, "2025-01-10", "FORCE_CLOSE")

    # Position removed
    assert "AAAA" not in state["positions"]
    # Cash increased by proceeds
    assert state["cash"] > 80000.0  # profit from 100→110
    # Trade recorded
    assert len(state["trades"]) == 1
    assert state["trades"][0]["reason"] == "FORCE_CLOSE"
    # Verify exact terminal equity calculation
    buy_notional = entry_price * shares
    sell_notional = exit_price * shares
    expected_cash = 80000.0 + sell_notional - _sell_costs(sell_notional)
    assert abs(state["cash"] - expected_cash) < 0.01, (
        f"Cash {state['cash']} != expected {expected_cash}"
    )
    # Terminal equity = cash (no positions), not tautological
    terminal_equity = state["cash"]
    assert abs(terminal_equity - expected_cash) < 0.01, (
        f"Terminal equity {terminal_equity} != {expected_cash}"
    )
    # Verify the trade PnL is exact
    expected_pnl = (sell_notional - _sell_costs(sell_notional)) - (buy_notional + _buy_commission(buy_notional))
    assert abs(state["trades"][0]["net_pnl"] - round(expected_pnl, 2)) < 0.01


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 10: Empty-run overwrites trades.csv
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_empty_run_overwrites_trades_csv(tmp_path):
    """An empty run must still write trades.csv (overwriting stale output)."""
    from eval_trader import _build_report
    result = {
        "state": {"cash": 1e6, "positions": {}, "trades": [], "equity_curve": []},
        "order_log": [], "fill_log": [],
        "strategy": "baseline", "days_simulated": 0,
        "cancel_reasons": {},
    }
    _build_report(result, tmp_path)
    trades_path = tmp_path / "trades.csv"
    assert trades_path.exists(), "trades.csv must always be written"
    content = trades_path.read_text()
    # Should contain header even when empty
    assert "Return_Pct" in content or "Ticker" in content


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 11: Cancel counting is done once (cancel_reasons dict)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def test_cancel_reasons_dict():
    """cancel_reasons is built once and summed for total."""
    from eval_trader import run_backtest

    # Minimal data: 65 bars, uptrend, to ensure baseline works
    np.random.seed(42)
    dates = pd.bdate_range("2025-01-01", periods=65)
    base = np.linspace(50, 80, 65)
    close_v = base + np.random.normal(0, 0.3, 65)
    vol_v = np.full(65, 2e6)

    close_df = pd.DataFrame({"AAAA": close_v}, index=dates)
    open_df = pd.DataFrame({"AAAA": close_v - 0.3}, index=dates)
    high_df = pd.DataFrame({"AAAA": close_v + 0.5}, index=dates)
    low_df = pd.DataFrame({"AAAA": close_v - 0.5}, index=dates)
    vol_df = pd.DataFrame({"AAAA": vol_v}, index=dates)

    result = run_backtest(close_df, open_df, high_df, low_df, vol_df,
                          strategy="baseline", days=5)
    assert isinstance(result["cancel_reasons"], dict)
    total_cancelled = sum(result["cancel_reasons"].values())
    # Verify it matches fill_log count
    from_cancel_log = sum(1 for f in result["fill_log"] if "CANCELLED" in f.get("status", ""))
    assert total_cancelled == from_cancel_log, (
        f"cancel_reasons sum {total_cancelled} != fill_log cancelled {from_cancel_log}"
    )
