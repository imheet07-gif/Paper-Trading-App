"""Fundamentals signals (F6, settings fixed in FINDINGS.md 2026-10-29 before any run).

Each signal is a score frame shaped like the panel (dates x securities),
higher is better, built from the `financials` table point in time:

  * a filing counts from the trading session after the calendar day of its
    broadcast_at; a revision replaces the original from its own broadcast;
  * per quarter, consolidated figures when filed by then, else standalone;
  * lenders (interest earned, no revenue) get no score where revenue or
    finance costs are needed;
  * a score whose latest quarter ended more than 365 days before the date is
    dropped; the earnings surprise also 120 days after its broadcast;
  * per-share figures and share counts are restated through the corporate
    action factors (the panel's adjusted over unadjusted close).

Loss-makers sort last: a ratio whose denominator is zero or negative gives
WORST, not no score. Every metric is computed once per panel and cached.
"""

import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import corporate_actions as ca  # noqa: E402
import panel as panel_mod  # noqa: E402

WORST, BEST = -1e18, 1e18
STALE_DAYS = 365
SURPRISE_DAYS = 120
FIELDS = ("revenue", "other_income", "pbt", "net_profit", "net_profit_owners", "eps_basic", "eps_diluted",
          "interest_earned", "finance_costs", "depreciation", "exceptional", "face_value", "paid_up", "reserves")
METRICS = ("revenue_growth_q", "revenue_growth_ttm", "eps_growth_q", "eps_growth_ttm", "surprise",
           "operating_margin", "net_margin", "roe", "interest_coverage", "profit_share_8q", "margin_stability",
           "other_income_share", "exceptional_share")
PRICED = ("earnings_yield", "book_to_price", "sales_to_price", "small_size")  # need today's market cap
COMPOSITES = {"quality": ("roe", "interest_coverage", "profit_share_8q"),
              "value": ("earnings_yield", "book_to_price", "sales_to_price"),
              "growth": ("revenue_growth_q", "eps_growth_q", "surprise")}
DESCRIPTIONS = {
    "revenue_growth_q": "Revenue growth, latest quarter on the same quarter a year earlier",
    "revenue_growth_ttm": "Revenue growth, last four quarters on the four before",
    "eps_growth_q": "Restated EPS growth, latest quarter on a year earlier",
    "eps_growth_ttm": "Restated EPS growth, last four quarters on the four before",
    "surprise": "EPS change on a year earlier over its usual size (up to 8 quarters), under 120 days old",
    "operating_margin": "Operating margin, last four quarters",
    "net_margin": "Net margin, last four quarters",
    "roe": "Return on equity: last four quarters' profit over year-end net worth",
    "interest_coverage": "Interest coverage, last four quarters",
    "profit_share_8q": "Share of the last 8 quarters with a profit",
    "margin_stability": "Steadier quarterly operating margin over the last 8 quarters",
    "other_income_share": "Less of profit before tax from other income",
    "exceptional_share": "Less of profit before tax from exceptional items",
    "earnings_yield": "Last four quarters' profit over market cap",
    "book_to_price": "Year-end net worth over market cap",
    "sales_to_price": "Last four quarters' revenue over market cap",
    "small_size": "Smaller market cap first",
    "quality": "Average rank of ROE, interest coverage and the share of profitable quarters",
    "value": "Average rank of earnings yield, book to price and sales to price",
    "growth": "Average rank of revenue growth, EPS growth and the earnings surprise",
}


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _days(a, b):
    return (pd.Timestamp(b) - pd.Timestamp(a)).days


def _consecutive(qs):
    return all(80 <= _days(qs[i]["period_end"], qs[i + 1]["period_end"]) <= 100 for i in range(len(qs) - 1))


def _ratio(num, den, worst=WORST):
    if num is None or den is None:
        return None
    return worst if den <= 0 else num / den


def _sum(qs, key):
    vals = [q.get(key) for q in qs]
    return None if any(v is None for v in vals) else sum(vals)


def _profit(q):
    return q["net_profit_owners"] if q.get("net_profit_owners") is not None else q.get("net_profit")


def _eps(q):
    """EPS restated to the latest share basis: per-share figures scale like adjusted prices."""
    e = q["eps_diluted"] if q.get("eps_diluted") is not None else q.get("eps_basic")
    return None if e is None or q.get("m") is None else e * q["m"]


def _op(q):
    parts = [q.get(k) for k in ("pbt", "finance_costs", "depreciation", "other_income")]
    return None if any(v is None for v in parts) else parts[0] + parts[1] + parts[2] - parts[3]


def _year_ago(qs, i):
    for j in range(i - 1, -1, -1):
        d = _days(qs[j]["period_end"], qs[i]["period_end"])
        if 355 <= d <= 375:
            return j
        if d > 375:
            break
    return None


def _ttm(qs, i):
    """The four consecutive quarters ending at index i, or None."""
    return qs[i - 3:i + 1] if i >= 3 and _consecutive(qs[i - 3:i + 1]) else None


def compute(qs):
    """Metric values from the quarters known at one moment (oldest first), plus what the priced
    metrics need: {"profit_ttm", "revenue_ttm", "net_worth", "shares"} (shares restated)."""
    out = {}
    if not qs:
        return out
    i, last = len(qs) - 1, qs[-1]
    lender = last.get("revenue") is None and last.get("interest_earned") is not None
    y = _year_ago(qs, i)
    four, four_before = _ttm(qs, i), (_ttm(qs, y) if y is not None else None)
    if not lender:
        if y is not None and last.get("revenue") is not None and qs[y].get("revenue") is not None:
            out["revenue_growth_q"] = _ratio(last["revenue"] - qs[y]["revenue"], qs[y]["revenue"])
        if four and four_before:
            r1, r0 = _sum(four, "revenue"), _sum(four_before, "revenue")
            if r1 is not None and r0 is not None:
                out["revenue_growth_ttm"] = _ratio(r1 - r0, r0)
    e1, e0 = _eps(last), (_eps(qs[y]) if y is not None else None)
    if e1 is not None and e0 is not None:
        out["eps_growth_q"] = WORST if e0 == 0 else (e1 - e0) / abs(e0)
    if four and four_before:
        t1, t0 = [_eps(q) for q in four], [_eps(q) for q in four_before]
        if None not in t1 and None not in t0:
            out["eps_growth_ttm"] = WORST if sum(t0) == 0 else (sum(t1) - sum(t0)) / abs(sum(t0))
    if e1 is not None and e0 is not None:
        changes = []
        for j in range(i - 1, max(-1, i - 9), -1):
            yj = _year_ago(qs, j)
            if yj is None:
                continue
            a, b = _eps(qs[j]), _eps(qs[yj])
            if a is not None and b is not None:
                changes.append(a - b)
        if len(changes) >= 4:
            sd = float(np.std(changes, ddof=1))
            if sd > 0:
                out["surprise"] = (e1 - e0) / sd
    if four:
        rev, pbt, fc = _sum(four, "revenue"), _sum(four, "pbt"), _sum(four, "finance_costs")
        prof = sum(_profit(q) for q in four) if all(_profit(q) is not None for q in four) else None
        ops = [_op(q) for q in four]
        if not lender:
            if None not in ops and rev is not None:
                out["operating_margin"] = _ratio(sum(ops), rev)
            if prof is not None and rev is not None:
                out["net_margin"] = _ratio(prof, rev)
            if pbt is not None and fc is not None and fc >= 0:
                out["interest_coverage"] = BEST if fc == 0 else (pbt + fc) / fc
        oi, ex = _sum(four, "other_income"), _sum(four, "exceptional")
        if oi is not None and pbt is not None:
            out["other_income_share"] = WORST if pbt <= 0 else -oi / pbt
        if ex is not None and pbt is not None:
            out["exceptional_share"] = WORST if pbt <= 0 else -abs(ex) / pbt
        out["profit_ttm"], out["revenue_ttm"] = prof, (None if lender else rev)
    year_end = next((q for q in reversed(qs) if q.get("reserves") is not None and q.get("paid_up") is not None), None)
    if year_end is not None:
        nw = year_end["paid_up"] + year_end["reserves"]
        out["net_worth"] = nw
        if four and out.get("profit_ttm") is not None:
            out["roe"] = WORST if nw <= 0 else out["profit_ttm"] / nw
    eight = qs[-8:]
    if len(eight) == 8 and _consecutive(eight):
        prof8 = [_profit(q) for q in eight]
        if None not in prof8:
            out["profit_share_8q"] = sum(1 for v in prof8 if v > 0) / 8.0
        if not lender:
            margins = [(_op(q) / q["revenue"]) if _op(q) is not None and q.get("revenue") else None for q in eight]
            if None not in margins:
                out["margin_stability"] = -float(np.std(margins, ddof=1))
    sh = next((q for q in reversed(qs) if q.get("paid_up") and q.get("face_value") and q.get("m")), None)
    if sh is not None:
        out["shares"] = sh["paid_up"] / sh["face_value"] / sh["m"]  # restated: shares on today's basis
    return out


def _load(p, db_path=None):
    """{sid: [(row usable from, period_end of the latest quarter, broadcast row, values)]} from the store."""
    db = sqlite3.connect(db_path or p.db_path)
    try:
        rows = db.execute("SELECT isin, nature, period_end, broadcast_at, %s FROM financials "
                          "WHERE period_end IS NOT NULL AND broadcast_at IS NOT NULL ORDER BY broadcast_at"
                          % ", ".join(FIELDS)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    db.close()
    link_map = getattr(p, "link_map", None)
    cache = {}

    def sid(isin):
        if isin not in cache:
            cache[isin] = ca.chain(isin, link_map)[-1] if link_map is not None else isin
        return cache[isin]
    cols = {s: k for k, s in enumerate(p.close.columns)}
    M = (p.close / p.raw_close).ffill().values
    dates = p.dates
    by_sid = defaultdict(list)
    for r in rows:
        if not r[0]:
            continue
        s = sid(r[0])
        if s in cols:
            by_sid[s].append(r)
    events = {}
    for s, rs in by_sid.items():
        k = cols[s]
        known = {}  # period_end -> {"Consolidated": values, "Standalone": values}, latest version each
        out = []
        for isin, nature, pe, at, *vals in rs:
            q = {"period_end": pe, **{f: _f(v) for f, v in zip(FIELDS, vals)}}
            ri = dates.searchsorted(pd.Timestamp(pe), "right") - 1
            q["m"] = float(M[ri, k]) if ri >= 0 and not np.isnan(M[ri, k]) else None
            known.setdefault(pe, {})["Consolidated" if nature == "Consolidated" else "Standalone"] = q
            t = pd.Timestamp(at[:10])
            if t < dates[0]:
                row = 0  # known before the panel starts: usable from its first session
            else:
                row = dates.searchsorted(t, "right")
                if row >= len(dates):
                    continue
            qs = [v.get("Consolidated") or v.get("Standalone") for _, v in sorted(known.items())]
            out.append((row, max(known), row, compute(qs)))
        events[s] = out
    return events


def _frames(p, db_path=None):
    """Every metric's score frame, computed once per panel."""
    cached = getattr(p, "_fundamentals", None)
    if cached is not None:
        return cached
    events = _load(p, db_path)
    dates, n, cols = p.dates, len(p.dates), list(p.close.columns)
    names = METRICS + ("profit_ttm", "revenue_ttm", "net_worth", "shares")
    arr = {m: np.full((n, len(cols)), np.nan) for m in names}
    pe_day = np.full((n, len(cols)), np.nan)
    b_row = np.full((n, len(cols)), np.nan)
    col = {s: k for k, s in enumerate(cols)}
    for s, evs in events.items():
        k = col[s]
        evs = sorted(evs, key=lambda e: e[0])
        for j, (row, pe, brow, vals) in enumerate(evs):
            end = evs[j + 1][0] if j + 1 < len(evs) else n
            if end <= row:
                continue
            for m in names:
                v = vals.get(m)
                if v is not None:
                    arr[m][row:end, k] = v
                else:
                    arr[m][row:end, k] = np.nan
            pe_day[row:end, k] = pd.Timestamp(pe).value / 8.64e13
            b_row[row:end, k] = brow
    today = (dates.values.astype("datetime64[D]").astype(np.int64))[:, None].astype(float)
    stale = (today - pe_day) > STALE_DAYS
    out = {}
    for m in METRICS:
        a = arr[m].copy()
        a[stale] = np.nan
        out[m] = a
    # surprise: 120 calendar days from its broadcast
    brow_day = np.where(np.isnan(b_row), np.nan, 0.0)
    ok = ~np.isnan(b_row)
    idx = np.where(ok, b_row, 0).astype(int)
    brow_day[ok] = today[idx[ok], 0]
    out["surprise"][(today - brow_day) > SURPRISE_DAYS] = np.nan
    close = p.close.values
    mcap = close * arr["shares"]
    mcap[(mcap <= 0) | stale] = np.nan
    with np.errstate(invalid="ignore", divide="ignore"):
        out["earnings_yield"] = arr["profit_ttm"] / mcap
        bp = arr["net_worth"] / mcap
        bp[(arr["net_worth"] <= 0) & ~np.isnan(mcap)] = WORST
        out["book_to_price"] = bp
        out["sales_to_price"] = arr["revenue_ttm"] / mcap
        out["small_size"] = -np.log(mcap)
    frames = {m: pd.DataFrame(v, index=dates, columns=p.close.columns) for m, v in out.items()}
    elig = panel_mod.universe(p)
    for name, parts in COMPOSITES.items():
        ranks = [frames[x].where(elig).rank(axis=1, pct=True) for x in parts]
        count = sum(r.notna().astype(int) for r in ranks)
        total = sum(r.fillna(0.0) for r in ranks)
        frames[name] = (total / count.replace(0, np.nan)).where(count >= 2)
    p._fundamentals = frames
    return frames


def signal(name):
    def fn(p):
        return _frames(p)[name]
    fn.__name__ = "f_" + name
    return fn


ALL = METRICS + PRICED + tuple(COMPOSITES)
