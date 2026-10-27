"""Market regime: how much of the capital to invest at each date.

Built only from data up to each date. A regime filter does not pick
stocks; it decides how exposed to be, aiming at the drawdowns that a
stock-picking signal alone cannot avoid.

Index names changed in November 2015 (CNX 500 became Nifty 500), so the
series are joined across the old and new names.
"""

import os
import sqlite3
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import panel as panel_mod  # noqa: E402

# current name -> names NSE used before (oldest first)
INDEX_ALIASES = {
    "Nifty 50": ("S&P CNX Nifty", "CNX Nifty"),
    "Nifty 500": ("S&P CNX 500", "CNX 500"),
    "Nifty Midcap 100": ("CNX Midcap",),
    "Nifty Smallcap 100": ("CNX Smallcap",),
    "India VIX": (),
}


def index_close(db_path, name):
    """Daily close of an NSE index across its renames, as a Series on dates."""
    names = INDEX_ALIASES.get(name, ()) + (name,)
    db = sqlite3.connect(db_path)
    q = "SELECT date, close FROM index_prices WHERE name IN (%s) ORDER BY date" % ",".join("?" * len(names))
    df = pd.read_sql_query(q, db, params=names)
    db.close()
    s = df.drop_duplicates("date", keep="last").set_index("date")["close"]
    s.index = pd.to_datetime(s.index)
    return s


def trend(p, db_path, index="Nifty 500", days=200, below=0.0):
    """1 when the index closes above its `days`-day average, else `below`."""
    s = index_close(db_path, index).reindex(p.dates).ffill()
    avg = s.rolling(days, min_periods=int(days * 0.9)).mean()
    out = (s > avg).astype(float).where(avg.notna())
    return out.where(out == 1, below).where(avg.notna())


def breadth(p, threshold=0.40, below=0.5, days=200):
    """1 when at least `threshold` of eligible stocks close above their own
    `days`-day average, else `below`."""
    elig = panel_mod.universe(p)
    above = p.close > p.close.rolling(days, min_periods=int(days * 0.9)).mean()
    share = (above & elig).sum(axis=1) / elig.sum(axis=1).replace(0, float("nan"))
    on = (share >= threshold).astype(float)
    return on.where(on == 1, below).where(share.notna())  # unknown before the averages warm up


REGIMES = {
    "none": lambda p, db: None,
    "trend200_cash": lambda p, db: trend(p, db, below=0.0),
    "trend200_half": lambda p, db: trend(p, db, below=0.5),
    "breadth40_half": lambda p, db: breadth(p, 0.40, 0.5),
}
