"""Shared, side-effect-free buy-limit fill decision logic.

Implements the "signal-close limit, next-open fill or 09:30 cancel" model
(entry_model = "signal_close_limit_next_open_v1"). No pandas/yfinance/date
imports here on purpose: callers own market-calendar and data concerns.
"""

import math
from dataclasses import dataclass
from typing import Literal, Optional

DEFAULT_TP_SL = {
    'tp_atr_mult': 4.0,
    'sl_atr_mult': 3.0,
    'tp_pct': 0.15,
    'sl_pct': 0.08,
}

OpenDecisionStatus = Literal[
    "FILLED",
    "FILLED_INTRADAY_ESTIMATED",
    "CANCELLED_OPEN_ABOVE_LIMIT",
    "CANCELLED_NO_OPEN_PRICE",
    "CANCELLED_EXPIRED",
]

TerminalOrderStatus = Literal[
    "FILLED",
    "FILLED_INTRADAY_ESTIMATED",
    "CANCELLED_OPEN_ABOVE_LIMIT",
    "CANCELLED_NO_OPEN_PRICE",
    "CANCELLED_NO_CAPACITY",
    "CANCELLED_INSUFFICIENT_CASH",
    "CANCELLED_BELOW_LOT_SIZE",
    "CANCELLED_INVALID_LIMIT",
    "CANCELLED_EXPIRED",
]


@dataclass(frozen=True)
class OpenLimitDecision:
    status: OpenDecisionStatus
    limit_price: float
    open_price: Optional[float]
    fill_price: Optional[float]
    is_estimated: bool = False

    @property
    def filled(self) -> bool:
        return self.status in ("FILLED", "FILLED_INTRADAY_ESTIMATED")


def _is_valid_open_price(open_price: Optional[float]) -> bool:
    if open_price is None:
        return False
    if isinstance(open_price, bool):
        return False
    if not isinstance(open_price, (int, float)):
        return False
    if not math.isfinite(open_price):
        return False
    return open_price > 0


def evaluate_buy_limit_at_open(
    limit_price: float,
    open_price: Optional[float],
) -> OpenLimitDecision:
    """Evaluate the opening-auction portion of a one-day buy limit order."""
    if not isinstance(limit_price, (int, float)) or isinstance(limit_price, bool):
        raise ValueError("limit_price must be a finite positive number")
    if not math.isfinite(limit_price) or limit_price <= 0:
        raise ValueError("limit_price must be a finite positive number")

    if not _is_valid_open_price(open_price):
        return OpenLimitDecision(
            status="CANCELLED_NO_OPEN_PRICE",
            limit_price=limit_price,
            open_price=None,
            fill_price=None,
        )

    if open_price <= limit_price:
        return OpenLimitDecision(
            status="FILLED",
            limit_price=limit_price,
            open_price=open_price,
            fill_price=open_price,
        )

    return OpenLimitDecision(
        status="CANCELLED_OPEN_ABOVE_LIMIT",
        limit_price=limit_price,
        open_price=open_price,
        fill_price=None,
    )


def evaluate_buy_limit_until_0930(
    limit_price: float,
    open_price: Optional[float],
    low_price: Optional[float] = None,
    high_price: Optional[float] = None,
    intraday_bars: Optional[list] = None,
    **kwargs,
) -> OpenLimitDecision:
    """Evaluate a DAY_UNTIL_0930 buy limit order.

    If open <= limit: filled at open price (opening auction).
    If open > limit:
      - If intraday_bars provided (5m bars up to 09:30): check if any bar low <= limit.
        If touched, filled at min(open_price, limit_price).
      - If low_price (or day_low) provided: check if low_price <= limit.
        If touched, filled at limit_price, status='FILLED_INTRADAY_ESTIMATED', is_estimated=True.
      - Otherwise cancelled: 'CANCELLED_OPEN_ABOVE_LIMIT'.
    """
    if not isinstance(limit_price, (int, float)) or isinstance(limit_price, bool):
        raise ValueError("limit_price must be a finite positive number")
    if not math.isfinite(limit_price) or limit_price <= 0:
        raise ValueError("limit_price must be a finite positive number")

    if not _is_valid_open_price(open_price):
        return OpenLimitDecision(
            status="CANCELLED_NO_OPEN_PRICE",
            limit_price=limit_price,
            open_price=None,
            fill_price=None,
            is_estimated=False,
        )

    # 1. Opening auction check
    if open_price <= limit_price:
        return OpenLimitDecision(
            status="FILLED",
            limit_price=limit_price,
            open_price=open_price,
            fill_price=open_price,
            is_estimated=False,
        )

    # 2. Intraday 5m bars check (09:00 - 09:30)
    if intraday_bars:
        touched = False
        for b in intraday_bars:
            b_time = b.get("time") or b.get("datetime") or ""
            if b_time and "09:30" < str(b_time)[-5:]:
                continue
            b_low = b.get("low")
            if b_low is not None and isinstance(b_low, (int, float)) and math.isfinite(b_low) and b_low <= limit_price:
                touched = True
                break
        if touched:
            return OpenLimitDecision(
                status="FILLED",
                limit_price=limit_price,
                open_price=open_price,
                fill_price=min(open_price, limit_price),
                is_estimated=False,
            )

    # 3. Conservative daily bar approximation (day_low)
    eff_low = low_price
    if eff_low is None and "day_low" in kwargs:
        eff_low = kwargs["day_low"]

    if eff_low is not None and isinstance(eff_low, (int, float)) and not isinstance(eff_low, bool):
        if math.isfinite(eff_low) and eff_low > 0 and eff_low <= limit_price:
            return OpenLimitDecision(
                status="FILLED_INTRADAY_ESTIMATED",
                limit_price=limit_price,
                open_price=open_price,
                fill_price=limit_price,
                is_estimated=True,
            )

    return OpenLimitDecision(
        status="CANCELLED_OPEN_ABOVE_LIMIT",
        limit_price=limit_price,
        open_price=open_price,
        fill_price=None,
        is_estimated=False,
    )

