"""Shared, side-effect-free position sizing (docs/EVAL_STRATEGY_20260910.md A1).

Single source of truth for share-count rounding and commission so
independent_sim.py, paper_tracker.py, and eval_trader.py stop disagreeing on
what "how many shares can I buy" means. Two distinct zero-share outcomes are
surfaced so a too-small position slot and genuinely insufficient cash are no
longer indistinguishable in the diagnostics:

- CANCELLED_BELOW_LOT_SIZE: the position-size slot itself can't afford one
  board lot at this price, regardless of actual cash on hand.
- CANCELLED_INSUFFICIENT_CASH: the slot could afford a lot, but available
  cash (after reserve) can't.
"""

import math
from dataclasses import dataclass
from typing import Optional

DEFAULT_LOT_SIZE = 1000
DEFAULT_MIN_COMMISSION = 20.0
DEFAULT_BUY_COST_RATE = 0.001425


@dataclass(frozen=True)
class SizingResult:
    shares: int
    trade_amount: float
    commission: float
    status: str  # "OK" | "CANCELLED_BELOW_LOT_SIZE" | "CANCELLED_INSUFFICIENT_CASH"

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def compute_commission(
    notional: float,
    buy_cost_rate: float = DEFAULT_BUY_COST_RATE,
    min_commission: float = DEFAULT_MIN_COMMISSION,
) -> float:
    """Commission with a NT$20 minimum floor, applied to either side of a trade."""
    if notional is None or notional <= 0:
        return 0.0
    return max(notional * buy_cost_rate, min_commission)


def _max_affordable_shares(
    budget: float,
    fill_price: float,
    buy_cost_rate: float,
    min_commission: float,
) -> int:
    """Largest raw (pre-lot-rounding) share count whose notional + commission fits budget."""
    if budget is None or budget <= 0 or fill_price is None or fill_price <= 0:
        return 0
    shares_rate = math.floor(budget / (fill_price * (1.0 + buy_cost_rate)))
    if shares_rate > 0 and shares_rate * fill_price * buy_cost_rate >= min_commission:
        return shares_rate
    # Minimum commission floor binds: solve shares*price + min_commission <= budget
    shares_floor_comm = math.floor((budget - min_commission) / fill_price)
    return max(shares_floor_comm, 0)


def size_position(
    equity: float,
    position_size: float,
    fill_price: float,
    cash: Optional[float] = None,
    reserve: float = 0.0,
    lot_size: int = DEFAULT_LOT_SIZE,
    min_commission: float = DEFAULT_MIN_COMMISSION,
    buy_cost_rate: float = DEFAULT_BUY_COST_RATE,
) -> SizingResult:
    """Compute a lot-rounded share count for a single new position.

    `cash` defaults to `equity` (pure position-size affordability check) when
    the caller doesn't need a separate cash constraint.
    """
    eff_cash = equity if cash is None else cash
    slot_amount = max(0.0, equity * position_size)
    available_cash = max(0.0, eff_cash - reserve)

    slot_raw_shares = _max_affordable_shares(slot_amount, fill_price, buy_cost_rate, min_commission)
    slot_shares = (slot_raw_shares // lot_size) * lot_size
    if slot_shares < lot_size:
        return SizingResult(0, 0.0, 0.0, "CANCELLED_BELOW_LOT_SIZE")

    target_amount = min(slot_amount, available_cash)
    raw_shares = _max_affordable_shares(target_amount, fill_price, buy_cost_rate, min_commission)
    shares = (raw_shares // lot_size) * lot_size
    if shares < lot_size:
        return SizingResult(0, 0.0, 0.0, "CANCELLED_INSUFFICIENT_CASH")

    notional = shares * fill_price
    commission = compute_commission(notional, buy_cost_rate, min_commission)
    return SizingResult(int(shares), round(notional, 2), round(commission, 2), "OK")
