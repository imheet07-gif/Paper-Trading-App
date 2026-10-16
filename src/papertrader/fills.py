"""Fill rules. Pure functions: quote in, price (or refusal) out."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from .models import Instrument, Quote, Side


@dataclass(frozen=True)
class FillRules:
    slippage_ticks: int = 1
    max_quote_age: timedelta = timedelta(seconds=10)
    max_spread_pct: Decimal = Decimal("0.10")  # of mid
    conservative_limits: bool = False  # limit fills need a strict cross, at the limit


def quote_problem(q: Quote | None, now: datetime, rules: FillRules) -> str | None:
    """Why this quote can't be traded against, or None if it is usable."""
    if q is None:
        return "no quote"
    if q.bid <= 0 or q.ask <= 0:
        return "no two-sided market"
    if q.ask < q.bid:
        return "crossed quote"
    if now - q.ts > rules.max_quote_age:
        return "stale quote"
    if (q.ask - q.bid) / q.mid > rules.max_spread_pct:
        return "spread too wide"
    return None


def on_tick(price: Decimal, tick: Decimal, round_up: bool) -> Decimal:
    mode = ROUND_CEILING if round_up else ROUND_FLOOR
    return (price / tick).to_integral_value(rounding=mode) * tick


def market_price(side: Side, q: Quote, inst: Instrument, rules: FillRules) -> Decimal:
    slip = rules.slippage_ticks * inst.tick_size
    if side is Side.BUY:
        return on_tick(q.ask + slip, inst.tick_size, round_up=True)
    return max(on_tick(q.bid - slip, inst.tick_size, round_up=False), inst.tick_size)


def limit_price(side: Side, limit: Decimal, q: Quote, rules: FillRules) -> Decimal | None:
    """Fill price if the quote crosses the limit, else None. Never better than
    the quote, never worse than the limit."""
    if side is Side.BUY:
        crossed = q.ask < limit if rules.conservative_limits else q.ask <= limit
        return (limit if rules.conservative_limits else q.ask) if crossed else None
    crossed = q.bid > limit if rules.conservative_limits else q.bid >= limit
    return (limit if rules.conservative_limits else q.bid) if crossed else None
