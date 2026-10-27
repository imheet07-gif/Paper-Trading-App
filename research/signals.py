"""Signal definitions. Each takes a Panel and returns a score frame (date x
security) computed only from data up to each date's close; higher is better.

These are the starting candidates, well documented in the literature and
in Indian data. None of them is trusted until it passes the validation
protocol in backtest.py on data it was not developed on.
"""

import numpy as np


def momentum_12_1(p):
    """Return over the past 12 months, skipping the last month (Jegadeesh-Titman)."""
    c = p.close
    return c.shift(21) / c.shift(252) - 1


def momentum_6_1(p):
    c = p.close
    return c.shift(21) / c.shift(126) - 1


def high_52w(p):
    """Closeness to the 52-week high (George and Hwang): 1.0 is at the high."""
    c = p.close
    return c / c.rolling(252, min_periods=200).max()


def low_volatility(p):
    """Negative one-year daily volatility: calmer stocks score higher."""
    return -p.returns().rolling(252, min_periods=200).std()


def delivery_accumulation(p):
    """Delivery share over the last 20 days against its own one-year normal,
    counted only when the price also rose over those 20 days. Delivery
    percentage is NSE-specific: the part of volume that actually changed
    hands rather than being squared off the same day."""
    d20 = p.deliv.rolling(20, min_periods=15).mean()
    d250 = p.deliv.rolling(250, min_periods=200).mean()
    up = p.close / p.close.shift(20) - 1
    score = d20 - d250
    return score.where(up > 0)


def momentum_quality(p):
    """12-1 momentum divided by one-year volatility: steadier trends first."""
    vol = p.returns().rolling(252, min_periods=200).std() * np.sqrt(252)
    return momentum_12_1(p) / vol


def momentum_lowvol(p):
    """Average of the percentile ranks of momentum per unit of volatility
    and of low volatility: trend, but in calmer stocks."""
    a = momentum_quality(p).rank(axis=1, pct=True)
    b = low_volatility(p).rank(axis=1, pct=True)
    return (a + b) / 2


SIGNALS = {
    "momentum_12_1": momentum_12_1,
    "momentum_6_1": momentum_6_1,
    "high_52w": high_52w,
    "low_volatility": low_volatility,
    "delivery_accumulation": delivery_accumulation,
    "momentum_quality": momentum_quality,
    "momentum_lowvol": momentum_lowvol,
}
