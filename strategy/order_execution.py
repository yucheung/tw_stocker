"""Shared, side-effect-free buy-limit fill decision logic.

Implements the "signal-close limit, next-open fill or 09:30 cancel" model
(entry_model = "signal_close_limit_next_open_v1"). No pandas/yfinance/date
imports here on purpose: callers own market-calendar and data concerns.
"""

import math
from dataclasses import dataclass
from typing import Literal, Optional

OpenDecisionStatus = Literal[
    "FILLED",
    "CANCELLED_OPEN_ABOVE_LIMIT",
    "CANCELLED_NO_OPEN_PRICE",
]

TerminalOrderStatus = Literal[
    "FILLED",
    "CANCELLED_OPEN_ABOVE_LIMIT",
    "CANCELLED_NO_OPEN_PRICE",
    "CANCELLED_NO_CAPACITY",
    "CANCELLED_INSUFFICIENT_CASH",
    "CANCELLED_INVALID_LIMIT",
]


@dataclass(frozen=True)
class OpenLimitDecision:
    status: OpenDecisionStatus
    limit_price: float
    open_price: Optional[float]
    fill_price: Optional[float]

    @property
    def filled(self) -> bool:
        return self.status == "FILLED"


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
