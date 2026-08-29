"""Baseline MA20+Volume strategy for eval_trader (sanity-check control group).

Score = Percentile(Ret_20d) + Percentile(Volume_Ratio)
Top-5 selection.  NO look-ahead: volume denominator uses t-20..t-1 only.
"""
from __future__ import annotations
from typing import Any
import pandas as pd

# v8.5 close-based ATR20 (same formula as mr20_strategy / independent_sim)
def _atr20(close: pd.DataFrame) -> pd.DataFrame:
    return close.pct_change().abs().rolling(20).mean() * close


def filter_baseline_candidates(
    target_dt: pd.Timestamp,
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    top_n: int = 5,
    liquidity_top_n: int = 100,
    min_history: int = 60,
    exclude_tickers: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return up to *top_n* candidates scored by MA20+Volume at *target_dt*.

    Candidate dict keys match the eval_trader contract:
        ticker, score, close, atr, rank
    """
    if target_dt not in close_df.index:
        return []

    t_loc = close_df.index.get_loc(target_dt)
    if isinstance(t_loc, slice):
        t_loc = t_loc.stop - 1
    if t_loc < max(min_history, 25):
        return []

    # --- Universe: tickers with >= min_history bars up to target_dt ----------
    tickers = [
        c for c in close_df.columns
        if close_df[c].iloc[: t_loc + 1].dropna().shape[0] >= min_history
    ]
    if not tickers:
        return []

    # Liquidity: 20-day average turnover (Close * Volume) top-N
    turnover = (close_df[tickers] * vol_df[tickers]).rolling(20).mean()
    liq = turnover.iloc[t_loc].dropna().sort_values(ascending=False)
    universe = list(liq.head(liquidity_top_n).index)
    if not universe:
        return []

    # --- Indicators at target_dt (no future data) ---------------------------
    ma20 = close_df[universe].rolling(20).mean()
    ma20_5ago = ma20.shift(5)
    atr = _atr20(close_df[universe])

    # Volume ratio: Volume[t] / mean(Volume[t-20 : t-1])  — shift(1) excludes today
    vol_avg_20 = vol_df[universe].shift(1).rolling(20).mean()
    vol_ratio = vol_df[universe] / vol_avg_20

    # 20-day return
    ret_20d = close_df[universe] / close_df[universe].shift(20) - 1

    # --- Filter gates -------------------------------------------------------
    c_t = close_df[universe].iloc[t_loc]
    m20 = ma20.iloc[t_loc]
    m20_5 = ma20_5ago.iloc[t_loc]
    vr = vol_ratio.iloc[t_loc]
    r20 = ret_20d.iloc[t_loc]
    atr_t = atr.iloc[t_loc]
    turnover_t = turnover.iloc[t_loc]

    # Trend: Close > MA20 and MA20 rising (MA20[t] > MA20[t-5])
    trend_ok = (c_t > m20) & (m20 > m20_5)
    # Volume: ratio >= 1.5
    vol_ok = vr >= 1.5
    # Valid data
    valid = c_t.notna() & m20.notna() & m20_5.notna() & vr.notna() & r20.notna()

    mask = trend_ok & vol_ok & valid
    eligible = list(mask[mask].index)
    if exclude_tickers:
        eligible = [t for t in eligible if t not in exclude_tickers]
    if not eligible:
        return []

    # --- Percentile scoring --------------------------------------------------
    r20_vals = r20[eligible]
    vr_vals = vr[eligible]
    r20_pct = r20_vals.rank(pct=True)
    # vr_vals are positive; log is monotonic so vr_vals.rank() == log(vr_vals).rank()
    vr_pct = vr_vals.rank(pct=True)

    # Tie-break: Score DESC → Turnover DESC → Ticker ASC
    turnover_elig = turnover_t[eligible]
    order = sorted(eligible, key=lambda t: (-float(r20_pct[t] + vr_pct[t]), -float(turnover_elig.get(t, 0)), t))

    candidates = []
    scores = r20_pct + vr_pct
    for rank_i, ticker in enumerate(order[:top_n], 1):
        candidates.append({
            "ticker": str(ticker),
            "score": round(float(scores[ticker]), 6),
            "close": round(float(c_t[ticker]), 2),
            "atr": round(float(atr_t[ticker]), 4) if pd.notna(atr_t[ticker]) else 0.0,
            "rank": rank_i,
        })
    return candidates
