"""Stops, targets and position size from the average true range.

Risk limits for the trade finder, with the settings fixed in FINDINGS.md
(2026-10-28) before any test: no edge is claimed for them until the exit
test passes.

  ATR       simple mean of the true range over 14 sessions, on adjusted prices
  stop      entry - 2 ATR
  target    entry + 2 x (entry - stop), so 1 to 2 risk to reward
  quantity  whole shares whose loss at the stop, round-trip charges included,
            stays within the profile's share of the amount, and whose cost
            fits the stock's slice of the amount

atr() works on the research panel (float, numpy and pandas); plan() is
accounting and works in Decimal rupees.
"""

import os
import sys
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
from money import D, paise  # noqa: E402

ATR_DAYS = 14
STOP_ATR = Decimal(2)
TARGET_R = Decimal(2)
RISK_SHARE = {"low": Decimal("0.005"), "medium": Decimal("0.01"), "high": Decimal("0.02")}
PROFILE_STOCKS = {"low": 5, "medium": 3, "high": 1}


def true_range(high, low, close):
    """The true range per session, on aligned frames or series: the largest of high
    minus low, high minus the previous close, and the previous close minus low. On a
    stock's first session (no previous close) it is high minus low."""
    prev = close.shift(1)
    parts = [(high - low).to_numpy(), (high - prev).abs().to_numpy(), (low - prev).abs().to_numpy()]
    tr = np.fmax(np.fmax(parts[0], parts[1]), parts[2])  # fmax ignores a missing previous close
    if isinstance(high, pd.Series):
        return pd.Series(tr, index=high.index)
    return pd.DataFrame(tr, index=high.index, columns=high.columns)


def atr(p, days=ATR_DAYS):
    """ATR per stock per session from the panel: the mean true range of the last `days`
    sessions, NaN until a stock has that many."""
    tr = true_range(p.high, p.low, p.close)
    return tr.rolling(days, min_periods=days).mean()


def _tick(x, tick, mode):
    t = D(tick)
    rounding = {"down": ROUND_FLOOR, "up": ROUND_CEILING}[mode]
    return ((D(x) / t).to_integral_value(rounding=rounding) * t).quantize(t)


def plan(entry, atr_value, amount, risk="medium", stocks=None, tick=None, charges=None):
    """Stop, target and quantity for one stock.

    entry, atr_value, amount: rupees. risk: low, medium or high. stocks: how many
    stocks share the amount (default: the profile's). tick: the instrument's tick
    size; without one, prices are rounded to the paisa and the plan says so.
    charges(side, qty, price) -> Decimal rupees, for the round trip; None = no
    charges counted (and said). Returns a dict, or raises ValueError."""
    if risk not in RISK_SHARE:
        raise ValueError("risk must be low, medium or high")
    entry, a, amount = D(entry), D(atr_value), D(amount)
    if entry <= 0 or a <= 0 or amount <= 0:
        raise ValueError("entry, ATR and amount must be positive")
    stocks = stocks or PROFILE_STOCKS[risk]
    stop_raw = entry - STOP_ATR * a
    if stop_raw <= 0:
        raise ValueError("the stop would be at or below zero: the stock moves too much for this rule")
    if tick:
        stop, target = _tick(stop_raw, tick, "down"), _tick(entry + TARGET_R * (entry - stop_raw), tick, "up")
    else:
        stop, target = paise(stop_raw), paise(entry + TARGET_R * (entry - stop_raw))
    budget_loss = paise(amount * RISK_SHARE[risk])
    slice_money = amount / stocks
    per_share = entry - stop
    qty = int(min(budget_loss / per_share, slice_money / entry).to_integral_value(rounding=ROUND_DOWN))

    def round_trip(q, exit_price):
        if not charges or q <= 0:
            return Decimal(0)
        return D(charges("buy", q, entry)) + D(charges("sell", q, exit_price))
    while qty > 0 and qty * per_share + round_trip(qty, stop) > budget_loss:
        qty -= 1  # charges count against the loss limit too
    if qty == 0:
        raise ValueError("not even one share fits: one share risks %s at the stop, the limit is %s"
                         % (paise(per_share), budget_loss))
    return {"entry": paise(entry), "atr": paise(a), "stop": stop, "target": target, "qty": qty,
            "cost": paise(qty * entry), "loss_at_stop": paise(qty * per_share + round_trip(qty, stop)),
            "gain_at_target": paise(qty * (target - entry) - round_trip(qty, target)),
            "risk": risk, "risk_limit": budget_loss, "stocks": stocks, "slice": paise(slice_money),
            "tick_checked": bool(tick), "charges_counted": bool(charges)}
