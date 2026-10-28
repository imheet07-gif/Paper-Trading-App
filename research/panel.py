"""Load the market store into date x security panels for research.

One column per security, named by its latest ISIN (older ISINs from splits
are joined into it). Prices are back-adjusted with the corporate action
factors, so returns across bonuses and splits are real returns; volumes are
scaled the opposite way. Raw closes are kept for price filters.

This is the analytics layer: numpy and pandas are allowed here, never in
core/.
"""

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import bhavcopy  # noqa: E402
import corporate_actions as ca  # noqa: E402

SERIES = ("EQ", "BE", "BZ")
FIELDS = ("open", "high", "low", "close", "volume", "value", "deliv")
ISSUE_DAYS = 252  # trading days a failed corporate action check keeps a security out


class Panel:
    """Frames indexed by trading date (DatetimeIndex), columns = security id."""

    def __init__(self, frames, names, eq, data_issue=None):
        for k, v in frames.items():
            setattr(self, k, v)
        self.names = names  # id -> latest symbol
        self.eq = eq  # True where the security traded in the EQ series that day
        # True where a corporate action failed its price check within the past year:
        # the adjusted prices there cannot be trusted, so the security is not eligible
        self.data_issue = data_issue if data_issue is not None else eq & False
        self.dates = self.close.index

    def returns(self):
        return self.close.pct_change(fill_method=None)


def multipliers(factor_rows, dates, ids):
    """Back-adjustment multipliers: for each date, the product of the factors
    of every ex-date after it (1 where there are none)."""
    f = pd.DataFrame(1.0, index=dates, columns=ids)
    for sid, ex, fac in factor_rows:
        if sid not in f.columns:
            continue
        pos = dates.searchsorted(pd.Timestamp(ex))
        if pos >= len(dates):
            continue
        f.iat[pos, f.columns.get_loc(sid)] *= fac
    # multiplier(t) = prod of factors on ex-dates strictly after t
    return f.iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1.0)


def load(db_path=bhavcopy.DEFAULT_DB, start=None, end=None):
    db = sqlite3.connect(db_path)
    link_map = ca.links(db)
    final = {}

    def sid(isin):
        if isin not in final:
            final[isin] = ca.chain(isin, link_map)[-1]
        return final[isin]

    q = ("SELECT date, isin, symbol, series, open, high, low, close, volume, value, deliv_bp FROM prices "
         "WHERE series IN ('EQ','BE','BZ')")
    args = []
    if start:
        q += " AND date >= ?"
        args.append(start)
    if end:
        q += " AND date <= ?"
        args.append(end)
    df = pd.read_sql_query(q, db, params=args)
    df["sid"] = df["isin"].map(sid)
    df["date"] = pd.to_datetime(df["date"])
    df["is_eq"] = df["series"] == "EQ"
    # one row per security and day; EQ wins if a stock printed in two series
    df = df.sort_values(["date", "sid", "is_eq"]).drop_duplicates(["date", "sid"], keep="last")
    names = df.sort_values("date").groupby("sid")["symbol"].last().to_dict()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] / 100.0
    df["value"] = df["value"] / 100.0
    df["deliv"] = df["deliv_bp"] / 10000.0

    wide = {c: df.pivot(index="date", columns="sid", values=c).astype("float64") for c in FIELDS}
    eq = df.pivot(index="date", columns="sid", values="is_eq").fillna(False).astype(bool)
    dates, ids = wide["close"].index, wide["close"].columns

    rows = [(sid(i), d, float(f)) for i, d, f in db.execute(
        "SELECT isin, ex_date, factor FROM corporate_actions WHERE isin IS NOT NULL AND factor IS NOT NULL")
        if float(f) != 1.0]
    m = multipliers(rows, dates, ids)
    frames = {"raw_close": wide["close"].copy()}
    for c in ("open", "high", "low", "close"):
        frames[c] = wide[c] * m
    frames["volume"] = wide["volume"] / m
    frames["value"] = wide["value"]
    frames["deliv"] = wide["deliv"]
    issue = pd.DataFrame(False, index=dates, columns=ids)
    for isin, ex in ca.failed_checks(db):
        s = sid(isin)
        if s in issue.columns:
            lo = dates.searchsorted(pd.Timestamp(ex))
            issue.iloc[lo:lo + ISSUE_DAYS, issue.columns.get_loc(s)] = True
    db.close()
    out = Panel(frames, names, eq, issue)
    out.db_path = db_path  # event signals read their events from the same store
    out.link_map = link_map
    return out


def dividends(p, db_path=None):
    """Cash dividends per share, in rupees on the shares as they traded that day, on each ex-date:
    a frame shaped like p.close, 0 where there is none. From corporate_actions.dividend, through
    the same ISIN chains as the prices."""
    out = pd.DataFrame(0.0, index=p.dates, columns=p.close.columns)
    db = sqlite3.connect(db_path or p.db_path)
    try:
        rows = db.execute("SELECT ex_date, COALESCE(isin, isin_reported), dividend FROM corporate_actions "
                          "WHERE dividend IS NOT NULL AND dividend != ''").fetchall()
    except sqlite3.OperationalError:
        rows = []
    db.close()
    link_map = getattr(p, "link_map", {}) or {}
    for ex, isin, div in rows:
        if not isin:
            continue
        sid = ca.chain(isin, link_map)[-1]
        if sid not in out.columns:
            continue
        k = p.dates.searchsorted(pd.Timestamp(ex))
        if k < len(p.dates) and p.dates[k] == pd.Timestamp(ex):
            out.iat[k, out.columns.get_loc(sid)] += float(div)
    return out


def universe(p, min_price=20.0, min_value=2e7, value_days=60, min_history=250):
    """Boolean frame: eligible to buy at each date's close, using only data to that date.

    a company share (ISIN starting INE: ETFs and fund units, INF, trade in
    the EQ series too and are left out), EQ series that day (trade-for-trade
    and surveillance series excluded),
    no failed corporate action check in the past year,
    unadjusted close at least min_price, median traded value over
    value_days at least min_value rupees, and min_history days of prices.
    """
    med_value = p.value.rolling(value_days, min_periods=int(value_days * 0.8)).median()
    history = p.close.notna().cumsum()
    company = pd.Series([str(c).startswith("INE") for c in p.close.columns], index=p.close.columns)
    return (p.eq & (p.raw_close >= min_price) & (med_value >= min_value) & (history >= min_history)
            & ~p.data_issue & company)


def month_ends(dates):
    """Last trading day of each month."""
    s = pd.Series(dates, index=dates)
    return pd.DatetimeIndex(s.groupby([dates.year, dates.month]).last().values)


def nan_to_none(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else x
