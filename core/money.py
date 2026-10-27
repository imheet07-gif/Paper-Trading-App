#!/usr/bin/env python3
"""Rupees, Indian digit grouping, and whole-share arithmetic.

Indian grouping puts the first comma after three digits and every comma
after that at two: 1,23,45,678.90. Money is Decimal throughout, never float,
so a contract note can be matched to the paisa.

  python3 core/money.py 12345678.9          # Rs 1,23,45,678.90  (Rs 1.23 crore)
  python3 core/money.py --shares 50000 --price 2875.4
"""

import argparse
import sys
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, Decimal

PAISA = Decimal("0.01")
LAKH = Decimal(100000)
CRORE = Decimal(10000000)


def D(x):
    """Decimal from an int, str or Decimal. Floats go through str, so 0.1 stays 0.1."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(x)


def paise(x):
    """Round to the paisa, half up, the way contract notes round."""
    return D(x).quantize(PAISA, rounding=ROUND_HALF_UP)


def group_indian(digits):
    """'12345678' -> '1,23,45,678'."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def inr(x, decimals=2, symbol="₹"):
    """1234567.891 -> '₹12,34,567.89'. Negative -> '-₹...'."""
    v = D(x)
    q = Decimal(1).scaleb(-decimals) if decimals else Decimal(1)
    v = v.quantize(q, rounding=ROUND_HALF_UP)
    sign = "-" if v < 0 else ""
    whole, _, frac = format(abs(v), "f").partition(".")
    out = sign + symbol + group_indian(whole)
    return out + ("." + frac if decimals else "")


def inr_short(x, symbol="₹"):
    """Lakh and crore shorthand: 1.23 crore, 4.5 lakh, under a lakh in full."""
    v = D(x)
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= CRORE:
        return "%s%s%s crore" % (sign, symbol, _trim(a / CRORE))
    if a >= LAKH:
        return "%s%s%s lakh" % (sign, symbol, _trim(a / LAKH))
    return inr(v, decimals=0, symbol=symbol)


def _trim(v):
    s = format(v.quantize(PAISA, rounding=ROUND_HALF_UP), "f")
    return s.rstrip("0").rstrip(".")


def whole_shares(amount, price):
    """How many whole shares `amount` buys at `price`. India has no fractional shares."""
    p = D(price)
    if p <= 0:
        raise ValueError("price must be positive")
    return int((D(amount) / p).to_integral_value(rounding=ROUND_DOWN))


def round_to_tick(price, tick, mode="nearest"):
    """Snap a price to the instrument's tick size.

    mode: nearest (half up), down (for a buy limit you do not want to
    overpay on) or up (for a sell limit).
    """
    p, t = D(price), D(tick)
    if t <= 0:
        raise ValueError("tick must be positive")
    rounding = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}[mode]
    return ((p / t).to_integral_value(rounding=rounding) * t).quantize(t)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rupee formatting and whole-share sizing.")
    ap.add_argument("amount", nargs="?")
    ap.add_argument("--shares", help="amount to invest; prints whole shares at --price")
    ap.add_argument("--price")
    a = ap.parse_args(argv)
    if a.shares:
        if not a.price:
            ap.error("--shares needs --price")
        n = whole_shares(a.shares, a.price)
        spent = D(a.price) * n
        print("%d shares at %s = %s, %s left over" % (
            n, inr(a.price), inr(spent), inr(D(a.shares) - spent)))
        return 0
    if a.amount is None:
        ap.error("give an amount")
    print("%s  (%s)" % (inr(a.amount), inr_short(a.amount)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
