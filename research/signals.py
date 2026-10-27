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


def insider_buying(p):
    import event_signals
    return event_signals.insider_buying(p)


def results_reaction(p):
    import event_signals
    return event_signals.results_reaction(p)


SIGNALS["insider_buying"] = insider_buying
SIGNALS["results_reaction"] = results_reaction


DESCRIPTIONS = {
    "momentum_12_1": "Return over the past 12 months, skipping the most recent month",
    "momentum_6_1": "Return over the past 6 months, skipping the most recent month",
    "high_52w": "Close as a share of the 52-week high (1.0 = at the high)",
    "low_volatility": "Calmer stocks first: minus the one-year volatility of daily returns",
    "delivery_accumulation": "Delivery share over 20 days above its one-year normal, while the price rose",
    "momentum_quality": "12-1 momentum divided by one-year volatility: steadier trends first",
    "momentum_lowvol": "Average of the ranks of momentum_quality and low_volatility",
    "insider_buying": "Net open-market buying by promoters, directors and key managers disclosed in the last 90 days, in days of normal traded value. Failed dev (FINDINGS.md)",
    "results_reaction": "Two-session return around the latest quarterly results, above the market's, while it is under a quarter old. Failed dev (FINDINGS.md)",
}


def composite(p, weights):
    """A scoring matrix with weights fixed in advance: the weighted average of
    each signal's cross-sectional percentile rank (0 to 1) on each date.

    weights: {signal name: weight}. A stock needs every component to score.
    Weights are chosen before testing and the whole composite is validated as
    one strategy; tuning weights on results is exactly the overfitting the
    protocol exists to stop.
    """
    if not weights or any(w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError("weights must be non-negative and sum to more than zero")
    total, acc = float(sum(weights.values())), None
    for name, w in weights.items():
        if name not in SIGNALS:
            raise KeyError("unknown signal %r" % name)
        r = SIGNALS[name](p).rank(axis=1, pct=True) * (w / total)
        acc = r if acc is None else acc + r
    return acc


def scores(p, spec):
    """Score frame for a strategy spec: {"signal": name} or {"composite": {name: weight}}."""
    if spec.get("composite"):
        return composite(p, spec["composite"])
    return SIGNALS[spec["signal"]](p)


def describe(spec):
    if spec.get("composite"):
        w = spec["composite"]
        tot = sum(w.values())
        return " + ".join("%.0f%% %s" % (v / tot * 100, k) for k, v in w.items())
    return spec["signal"]
