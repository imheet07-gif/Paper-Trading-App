"""Simulated market for demos and UI development. NOT real prices.

Spot follows a deterministic wave pattern; option prices come from
Black-Scholes at a fixed volatility. Lot sizes and expiry days are
placeholders: the real app loads them from the broker's instrument master.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .models import Instrument, Quote

UNDERLYINGS = {
    # name: (base spot, strike step, lot size, volatility)
    "NIFTY": (Decimal(24000), Decimal(50), 75, 0.14),
    "BANKNIFTY": (Decimal(52000), Decimal(100), 30, 0.18),
}
STRIKES_EACH_SIDE = 20
TICK = Decimal("0.05")


def next_expiries(today: date, n: int = 2, weekday: int = 1) -> list[date]:
    """Next n dates falling on `weekday` (Tuesday=1), today included."""
    d = today + timedelta(days=(weekday - today.weekday()) % 7)
    return [d + timedelta(weeks=i) for i in range(n)]


def symbol(underlying: str, expiry: date, strike: Decimal, kind: str) -> str:
    return f"{underlying}{expiry:%y%m%d}{int(strike)}{kind}"


def build_instruments(today: date) -> dict[str, Instrument]:
    out = {}
    for name, (base, step, lot, _) in UNDERLYINGS.items():
        for expiry in next_expiries(today):
            for i in range(-STRIKES_EACH_SIDE, STRIKES_EACH_SIDE + 1):
                strike = base + step * i
                for kind in ("CE", "PE"):
                    sym = symbol(name, expiry, strike, kind)
                    out[sym] = Instrument(sym, name, kind, lot, TICK, strike, expiry)
    return out


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, years: float, vol: float, kind: str) -> float:
    intrinsic = max(spot - strike, 0.0) if kind == "CE" else max(strike - spot, 0.0)
    if years <= 0:
        return intrinsic
    sd = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if kind == "CE":
        return spot * _ncdf(d1) - strike * _ncdf(d2)
    return strike * _ncdf(-d2) - spot * _ncdf(-d1)


def _tick(x: float, up: bool) -> Decimal:
    d = Decimal(str(x)) / TICK
    return d.to_integral_value(rounding="ROUND_CEILING" if up else "ROUND_FLOOR") * TICK


class SimFeed:
    """QuoteSource producing a fresh synthetic chain for any `now`."""

    def __init__(self, instruments: dict[str, Instrument]):
        self.instruments = instruments
        self._cache: tuple[int, dict[str, Quote]] | None = None

    @staticmethod
    def spot(underlying: str, now: datetime) -> Decimal:
        base = UNDERLYINGS[underlying][0]
        t = now.timestamp()
        wave = (0.004 * math.sin(t / 300) + 0.002 * math.sin(t / 47 + 1)
                + 0.0008 * math.sin(t / 11 + 2))
        return (base * Decimal(str(1 + wave))).quantize(Decimal("0.05"), rounding=ROUND_HALF_UP)

    def quotes_at(self, now: datetime):
        key = int(now.timestamp())
        if self._cache and self._cache[0] == key:
            return now, dict(self._cache[1])
        spots = {u: float(self.spot(u, now)) for u in UNDERLYINGS}
        quotes = {}
        for sym, inst in self.instruments.items():
            years = max((datetime.combine(inst.expiry, datetime.min.time().replace(hour=15, minute=30))
                         - now).total_seconds(), 0) / (365 * 86400)
            mid = bs_price(spots[inst.underlying], float(inst.strike), years,
                           UNDERLYINGS[inst.underlying][3], inst.kind)
            half = max(0.05, mid * 0.004)
            bid, ask = _tick(max(mid - half, 0.05), False), _tick(max(mid + half, 0.10), True)
            quotes[sym] = Quote(sym, now, bid, ask, _tick(mid, False))
        self._cache = (key, quotes)
        return now, dict(quotes)
