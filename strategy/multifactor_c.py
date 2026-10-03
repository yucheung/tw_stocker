"""TWSE Research engine v3 Strategy C signal and order construction."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


BENCHMARK_SYMBOL = "0050.TW"
DEFAULT_TOP_N = 15
DEFAULT_BEAR_EQUITY_EXPOSURE = 0.0
DEFAULT_MARKET_POOL_SIZE = 200
DEFAULT_LIQUIDITY_MIN_TURNOVER = 30_000_000.0


def _naive_index(index: pd.Index) -> pd.Index:
    if getattr(index, "tz", None) is not None:
        return index.tz_localize(None)
    return index


def build_market_membership(
    prices_dict: Dict[str, pd.DataFrame],
    top_n: int = DEFAULT_MARKET_POOL_SIZE,
) -> Dict[int, List[str]]:
    """Build engine v3's yearly pool from the previous year's mean turnover."""
    per_year_turnover: Dict[int, Dict[str, float]] = {}
    for symbol, frame in prices_dict.items():
        if "Turnover" not in frame.columns:
            continue
        dates = _naive_index(frame.index)
        by_year = frame.copy()
        by_year.index = dates
        for year, rows in by_year.groupby(by_year.index.year):
            per_year_turnover.setdefault(int(year), {})[symbol] = float(rows["Turnover"].mean())

    membership: Dict[int, List[str]] = {}
    for year in sorted(per_year_turnover):
        prior = per_year_turnover.get(year - 1)
        if prior is None:
            continue
        ranked = sorted(prior.items(), key=lambda item: item[1], reverse=True)
        membership[year] = [symbol for symbol, _ in ranked[:top_n]]
    return membership


class FactorBuilder:
    """Compute point-in-time technical, revenue, and institutional factors."""

    def __init__(
        self,
        prices_dict: Dict[str, pd.DataFrame],
        revenue_dict: Optional[Dict[str, pd.DataFrame]] = None,
        institutional_dict: Optional[Dict[str, pd.DataFrame]] = None,
        liquidity_min_turnover: float = DEFAULT_LIQUIDITY_MIN_TURNOVER,
        universe_by_year: Optional[Dict[int, List[str]]] = None,
    ):
        self.prices_dict = prices_dict
        self.revenue_dict = revenue_dict or {}
        self.institutional_dict = institutional_dict or {}
        self.liquidity_min_turnover = liquidity_min_turnover
        self.universe_by_year = {year: set(symbols) for year, symbols in (universe_by_year or {}).items()}
        self.benchmark_df = self.prices_dict.get(BENCHMARK_SYMBOL)

    def get_rebalance_dates(
        self,
        start_date: str = "2020-01-01",
        end_date: str = "2026-09-30",
    ) -> List[pd.Timestamp]:
        """Return the first available benchmark session on or after each month's 10th."""
        if self.benchmark_df is None:
            raise ValueError(f"Benchmark {BENCHMARK_SYMBOL} not found in price data.")

        dates = _naive_index(self.benchmark_df.index)
        start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
        filtered = [date for date in dates if start <= date <= end]
        if not filtered:
            return []
        grouped = pd.DataFrame({"trade_date": filtered})
        grouped["year"] = grouped["trade_date"].dt.year
        grouped["month"] = grouped["trade_date"].dt.month
        grouped["day"] = grouped["trade_date"].dt.day

        rebalance_dates = []
        for _, month in grouped.groupby(["year", "month"]):
            after_tenth = month[month["day"] >= 11]
            if not after_tenth.empty:
                rebalance_dates.append(after_tenth.iloc[0]["trade_date"])
            else:
                rebalance_dates.append(month.iloc[-1]["trade_date"])
        return sorted(rebalance_dates)

    def is_market_bullish(self, as_of_date: pd.Timestamp | str) -> bool:
        """Use 0050 close >= its trailing 60-session mean; warm-up defaults bullish."""
        if self.benchmark_df is None:
            return True
        cutoff = pd.Timestamp(as_of_date)
        dates = _naive_index(self.benchmark_df.index)
        history = self.benchmark_df[dates <= cutoff]
        if len(history) < 60:
            return True
        return bool(history.iloc[-1]["Close"] >= history["Close"].tail(60).mean())

    def compute_factors_snapshot(self, as_of_date: pd.Timestamp | str) -> pd.DataFrame:
        """Compute an engine v3-compatible factor row per eligible symbol."""
        as_of = pd.Timestamp(as_of_date)
        records: list[dict[str, Any]] = []
        members = self.universe_by_year.get(int(as_of.year)) if self.universe_by_year else None

        for symbol, price_frame in self.prices_dict.items():
            if symbol == BENCHMARK_SYMBOL or (members is not None and symbol not in members):
                continue
            price_dates = _naive_index(price_frame.index)
            history = price_frame[price_dates <= as_of]
            if len(history) < 60:
                continue

            latest = history.iloc[-1]
            close = float(latest["Close"])
            volumes = history["Volume"].tail(20).values
            turnover_20 = (
                float(history["Turnover"].tail(20).mean())
                if "Turnover" in history.columns
                else close * float(np.mean(volumes))
            )
            ma20 = float(history["Close"].tail(20).mean())
            ma60 = float(history["Close"].tail(60).mean())

            revenue_yoy = np.nan
            revenue_3m_yoy = np.nan
            revenue_new_high = False
            revenue_frame = self.revenue_dict.get(symbol)
            if revenue_frame is not None and not revenue_frame.empty:
                revenue = revenue_frame.copy()
                revenue["date"] = pd.to_datetime(revenue["date"])
                public_revenue = revenue[(revenue["date"] + pd.Timedelta(days=9)) <= as_of]
                if len(public_revenue) >= 12:
                    latest_revenue = public_revenue.iloc[-1]
                    revenue_yoy = float(latest_revenue.get("revenue_YoY", np.nan))
                    revenue_3m_yoy = float(latest_revenue.get("revenue_3m_YoY", np.nan))
                    prior_12m_max = public_revenue["revenue"].tail(12).iloc[:-1].max()
                    revenue_new_high = bool(latest_revenue["revenue"] >= prior_12m_max)

            trust_net_buy = 0.0
            trust_ratio = 0.0
            foreign_net_buy = 0.0
            institutional_frame = self.institutional_dict.get(symbol)
            if institutional_frame is not None and not institutional_frame.empty:
                institution = institutional_frame.copy()
                institution["date"] = pd.to_datetime(institution["date"])
                institution = institution[institution["date"] <= as_of]
                if not institution.empty:
                    recent = institution.tail(100)
                    trust_rows = recent[recent["name"] == "Investment_Trust"].tail(20)
                    if not trust_rows.empty:
                        trust_net_buy = float(trust_rows["net_buy"].sum())
                        total_volume = float(np.sum(volumes)) if np.sum(volumes) > 0 else 1.0
                        trust_ratio = trust_net_buy / total_volume * 100.0
                    foreign_rows = recent[recent["name"] == "Foreign_Investor"].tail(20)
                    if not foreign_rows.empty:
                        foreign_net_buy = float(foreign_rows["net_buy"].sum())

            records.append(
                {
                    "symbol": symbol,
                    "close": close,
                    "turnover_20d": turnover_20,
                    "is_liquid": turnover_20 >= self.liquidity_min_turnover,
                    "above_ma20": close > ma20,
                    "ma_bullish": close > ma20 and ma20 > ma60,
                    "ret_20d": float((close / history["Close"].iloc[-20] - 1.0) * 100.0),
                    "ret_60d": float((close / history["Close"].iloc[-60] - 1.0) * 100.0),
                    "rev_yoy": revenue_yoy,
                    "rev_3m_yoy": revenue_3m_yoy,
                    "rev_new_high": revenue_new_high,
                    "trust_net_buy_20d": trust_net_buy,
                    "trust_ratio_20d": trust_ratio,
                    "foreign_net_buy_20d": foreign_net_buy,
                }
            )
        return pd.DataFrame(records)


class StrategyC:
    """Engine v3 Strategy C scoring with equal weights and a defensive regime."""

    def __init__(
        self,
        top_n: int = DEFAULT_TOP_N,
        bear_equity_exposure: float = DEFAULT_BEAR_EQUITY_EXPOSURE,
    ):
        self.name = "Strategy C: Multifactor"
        self.top_n = top_n
        self.bear_equity_exposure = bear_equity_exposure

    def select_portfolio(
        self,
        factor_snapshot: pd.DataFrame,
        is_market_bullish: bool = True,
    ) -> Dict[str, float]:
        if factor_snapshot.empty:
            return {}
        factors = factor_snapshot[factor_snapshot["is_liquid"] == True].copy()
        if factors.empty:
            return {}

        above_ma20 = factors[factors["above_ma20"] == True]
        if len(above_ma20) >= self.top_n:
            factors = above_ma20.copy()

        revenue_score = (
            factors["rev_yoy"].fillna(-10).rank(pct=True) * 0.6
            + factors["rev_3m_yoy"].fillna(-10).rank(pct=True) * 0.4
        )
        institutional_score = (
            factors["trust_ratio_20d"].rank(pct=True) * 0.5
            + factors["trust_net_buy_20d"].rank(pct=True) * 0.3
            + factors["foreign_net_buy_20d"].rank(pct=True) * 0.2
        )
        momentum_score = (
            factors["ret_20d"].rank(pct=True) * 0.4
            + factors["ret_60d"].rank(pct=True) * 0.4
            + factors["ma_bullish"].astype(float) * 0.2
        )
        factors["total_score"] = 0.4 * revenue_score + 0.3 * institutional_score + 0.3 * momentum_score
        selected = factors.sort_values("total_score", ascending=False).head(self.top_n)
        if selected.empty:
            return {}
        exposure = 1.0 if is_market_bullish else self.bear_equity_exposure
        weight = exposure / len(selected)
        return {str(symbol): weight for symbol in selected["symbol"]}


def build_rebalance_orders(
    signal_date: str,
    execution_date: str,
    target_weights: Dict[str, float],
    reference_closes: Dict[str, float],
    current_positions: Dict[str, Dict[str, Any]],
    equity: float,
) -> list[dict[str, Any]]:
    """Create whole-share orders for target-weight deltas using shared fields."""
    orders: list[dict[str, Any]] = []
    for ticker, position in sorted(current_positions.items()):
        reference_close = reference_closes.get(ticker) or position.get("last_valid_close") or position.get("entry")
        if reference_close is None or float(reference_close) <= 0:
            continue
        reference_close = float(reference_close)
        shares = int(position["shares"])
        target_weight = target_weights.get(ticker)
        if target_weight is None:
            sell_shares = shares
        else:
            dollar_diff = float(equity) * float(target_weight) - shares * reference_close
            if dollar_diff >= -1_000:
                continue
            sell_shares = min(shares, int(abs(dollar_diff) // reference_close))
            if sell_shares < 1:
                continue
        orders.append(
            {
                "order_id": f"multifactor_c_v1:{signal_date}:{ticker}:sell",
                "signal_date": signal_date,
                "execution_date": execution_date,
                "ticker": ticker,
                "side": "sell",
                "reference_close": reference_close,
                "limit_price": reference_close,
                "time_in_force": "DAY",
                "shares": sell_shares,
                "selection_mode": "monthly_rebalance",
            }
        )

    if target_weights:
        for rank, (ticker, weight) in enumerate(target_weights.items(), start=1):
            reference_close = reference_closes.get(ticker)
            if reference_close is None or float(reference_close) <= 0:
                continue
            reference_close = float(reference_close)
            current = current_positions.get(ticker)
            current_value = (
                int(current["shares"]) * reference_close
                if current is not None
                else 0.0
            )
            target_value = max(0.0, float(equity)) * float(weight)
            slot_amount = target_value - current_value
            if slot_amount <= 1_000:
                continue
            orders.append(
                {
                    "order_id": f"multifactor_c_v1:{signal_date}:{ticker}:buy",
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "ticker": ticker,
                    "side": "buy",
                    "rank": rank,
                    "target_weight": float(weight),
                    "slot_amount": slot_amount,
                    "reference_close": reference_close,
                    "limit_price": reference_close,
                    "time_in_force": "DAY",
                    "selection_mode": "monthly_rebalance",
                }
            )
    return orders
