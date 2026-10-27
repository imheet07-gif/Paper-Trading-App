"""Event signals: insider buying and the reaction to quarterly results.

Both key on when the event became public, never on when it happened:

  * an insider disclosure counts from the trading day after the calendar
    day NSE published it (conservative even for a morning disclosure);
  * results count from the close of the second session of the reaction
    (the release day, or the next session when released after the close,
    and the session after it).

A stock with no qualifying event in the window has no score (NaN): these
are event strategies, holding only stocks where something happened.
"""

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import corporate_actions as ca  # noqa: E402

INSIDERS = ("Promoters", "Promoter Group", "Director", "Key Managerial Personnel")
MODES = {"Market Purchase": 1, "Market Sale": -1}
INSIDER_WINDOW = 63  # trading days, about 90 calendar days
RESULTS_WINDOW = 63  # trading days a results reaction stays live, about one quarter
MARKET_CLOSE = "15:30"


def _sid_map(p):
    link_map = getattr(p, "link_map", None)
    cache = {}

    def sid(isin):
        if isin not in cache:
            cache[isin] = ca.chain(isin, link_map)[-1] if link_map is not None else isin
        return cache[isin]
    return sid


def _row_after(dates, day):
    """Index of the first trading day strictly after calendar `day`, or None
    when that day falls outside the panel (an older event must not land on row 0)."""
    t = pd.Timestamp(day)
    if t < dates[0]:
        return None
    i = dates.searchsorted(t, side="right")
    return i if i < len(dates) else None


def insider_events(p):
    """Qualifying insider market trades: [(row index when usable, sid, signed rupee value)]."""
    db = sqlite3.connect(p.db_path)
    rows = db.execute(
        "SELECT isin, mode, value, disclosed_at FROM insider WHERE isin IS NOT NULL AND value > 0 "
        "AND category IN (%s) AND mode IN ('Market Purchase','Market Sale') AND sec_type LIKE 'Equity%%'"
        % ",".join("?" * len(INSIDERS)), INSIDERS).fetchall()
    db.close()
    sid = _sid_map(p)
    out = []
    for isin, mode, value, at in rows:
        i = _row_after(p.dates, at[:10])
        if i is not None:
            out.append((i, sid(isin), MODES[mode] * float(value)))
    return out


def insider_buying(p, window=INSIDER_WINDOW):
    """Net insider market buying disclosed over the last `window` trading days,
    in days of the stock's normal traded value. NaN without any such trade."""
    shape = p.close.shape
    F, Sn = np.zeros(shape), np.zeros(shape)  # numpy first: pandas copy-on-write hands out read-only views
    col = {s: k for k, s in enumerate(p.close.columns)}
    for i, s, v in insider_events(p):
        k = col.get(s)
        if k is not None:
            F[i, k] += v
            Sn[i, k] += 1
    flows = pd.DataFrame(F, index=p.dates, columns=p.close.columns)
    seen = pd.DataFrame(Sn, index=p.dates, columns=p.close.columns)
    net = flows.rolling(window, min_periods=1).sum()
    count = seen.rolling(window, min_periods=1).sum()
    typical = p.value.rolling(60, min_periods=40).median()
    return (net / typical).where(count > 0)


def reaction_session(dates, released_at):
    """Row of the first session that can react to a release at `released_at` (IST, ISO):
    the release day itself when it is a trading day and the release came before the
    close; the next session when it came after the close; the first session after a
    holiday or weekend release."""
    d, t = released_at[:10], released_at[11:16]
    i = dates.searchsorted(pd.Timestamp(d), side="left")  # first session on or after the release day
    if i >= len(dates):
        return None
    if dates[i].date().isoformat() == d and t >= MARKET_CLOSE:
        i += 1
    return i if i < len(dates) else None


def results_events(p):
    """First public broadcast of each company's results for each quarter: [(sid, release row index)]."""
    db = sqlite3.connect(p.db_path)
    rows = db.execute("SELECT isin, period_to, MIN(broadcast_at) FROM results WHERE isin != '' "
                      "GROUP BY isin, period_to").fetchall()
    db.close()
    sid = _sid_map(p)
    out = []
    for isin, _, at in rows:
        i = reaction_session(p.dates, at)
        if i is not None and 0 < i < len(p.dates) - 1:
            out.append((sid(isin), i))
    return out


def results_reaction(p, window=RESULTS_WINDOW):
    """Abnormal two-session return around the latest results release (against
    the equal-weight eligible universe), live for `window` trading days after
    it is known. Positive surprises tend to keep drifting (post-earnings drift)."""
    import panel as panel_mod
    r = p.returns()
    elig = panel_mod.universe(p).shift(1, fill_value=False)
    market = r.where(elig).mean(axis=1)
    c = p.close.to_numpy()
    m2 = (1 + market).rolling(2).apply(np.prod, raw=True).to_numpy() - 1  # market over sessions i-1..i
    S = np.full(p.close.shape, np.nan)
    col = {s: k for k, s in enumerate(p.close.columns)}
    for s, i in results_events(p):
        k = col.get(s)
        if k is None or i + 1 >= len(p.dates):
            continue
        before, after = c[i - 1, k], c[i + 1, k]
        if np.isnan(before) or np.isnan(after) or before <= 0:
            continue
        ar = after / before - 1 - m2[i + 1]
        S[i + 1, k] = ar  # known at the close of the second session
    return pd.DataFrame(S, index=p.dates, columns=p.close.columns).ffill(limit=window)
