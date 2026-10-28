#!/usr/bin/env python3
"""The Earnings Analyst, India edition. Standard library only, no network.

Reads the quarterly results the store holds (core/financials.py, from the
XBRL each company filed with NSE) and lines them up quarter by quarter:
revenue, profit, margins and EPS, each against the same quarter a year
earlier, with the last four quarters added up. Banks are read on their own
lines: interest earned and expended, operating profit and provisions.

    python3 results.py TCS
    python3 results.py TCS INFY HDFCBANK --quarters 8
    python3 results.py TCS INFY --metrics trader/metrics.json     # for trader-screener
    python3 results.py TCS --standalone --json

Numbers are as filed, in rupees; nothing is estimated. The store's XBRL ends
with results published in February 2025: a company with later results says
so, with the date they were published, and those numbers are not shown.
"""

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "core"))
from money import D, inr, inr_short  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, "data", "market.db")
CRORE = Decimal("10000000")
ONE_OFF_SHARE = Decimal("0.25")  # other income above this share of profit before tax is pointed out
COLUMNS = ("period_end", "nature", "audited", "revenue", "other_income", "total_income", "pbt", "tax", "net_profit",
           "net_profit_owners", "eps_basic", "eps_diluted", "interest_earned", "interest_expended",
           "operating_profit", "provisions", "broadcast_at")


class AnalystError(Exception):
    pass


def connect(path):
    if not os.path.exists(path):
        raise AnalystError("no market store at %s (core/financials.py fetch builds the results)" % path)
    return sqlite3.connect(path)


def _q(db, sql, args=()):
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def resolve(db, name):
    """(symbol, isin) for an NSE symbol or an ISIN, through the results and prices tables."""
    n = name.strip().upper()
    if len(n) == 12 and n[:2].isalpha() and n[2:].isalnum() and not n.isalpha():
        row = _q(db, "SELECT symbol FROM financials WHERE isin = ? ORDER BY period_end DESC LIMIT 1", (n,))
        return (row[0][0] if row else n), n
    row = _q(db, "SELECT isin FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 1", (n,)) or \
        _q(db, "SELECT isin FROM financials WHERE symbol = ? ORDER BY period_end DESC LIMIT 1", (n,))
    if not row:
        raise AnalystError("%s: no stored prices or results under that symbol" % n)
    return n, row[0][0]


def _d(x):
    return D(x) if x not in (None, "") else None


def quarters(db, isin, nature=None):
    """The company's quarters, newest first, all of one nature: consolidated when the newest
    quarter has it (or when asked), else standalone."""
    rows = [dict(zip(COLUMNS, r)) for r in _q(db, "SELECT %s FROM financials WHERE isin = ? ORDER BY period_end DESC"
                                                % ", ".join(COLUMNS), (isin,))]
    if not rows:
        return [], None
    if nature is None:
        newest = rows[0]["period_end"]
        nature = "Consolidated" if any(r["period_end"] == newest and r["nature"] == "Consolidated" for r in rows) \
            else "Standalone"
    out, seen = [], set()
    for r in rows:
        if r["nature"] == nature and r["period_end"] not in seen:
            seen.add(r["period_end"])
            out.append({k: (_d(v) if k not in ("period_end", "nature", "audited", "broadcast_at") else v)
                        for k, v in r.items()})
    return out, nature


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return ((a / b - 1) * 100 * (1 if b > 0 else -1)).quantize(Decimal("0.1"), ROUND_HALF_UP)


def _margin(a, b):
    if a is None or not b:
        return None
    return (a / b * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


def _near(a, b, days):
    return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days - days) <= 10


def is_bank(q):
    return q.get("revenue") is None and q.get("interest_earned") is not None


def profit(q):
    return q.get("net_profit_owners") if q.get("net_profit_owners") is not None else q.get("net_profit")


def top_line(q):
    return q.get("interest_earned") if is_bank(q) else q.get("revenue")


def analyse(qs):
    """Each quarter with its margins and its change against a year earlier and the quarter before."""
    by_end = {q["period_end"]: q for q in qs}
    out = []
    for i, q in enumerate(qs):
        r = dict(q)
        year_ago = next((p for e, p in by_end.items() if _near(q["period_end"], e, 365)), None)
        before = qs[i + 1] if i + 1 < len(qs) and _near(q["period_end"], qs[i + 1]["period_end"], 91) else None
        r["bank"] = is_bank(q)
        r["profit"] = profit(q)
        r["net_margin"] = _margin(r["profit"], top_line(q))
        r["pbt_margin"] = _margin(q.get("pbt"), top_line(q))
        if r["bank"] and q.get("interest_earned") is not None and q.get("interest_expended") is not None:
            r["net_interest_income"] = q["interest_earned"] - q["interest_expended"]
        for name, f in (("top_line", top_line), ("profit", profit), ("eps", lambda x: x.get("eps_basic"))):
            r[name + "_yoy"] = _pct(f(q), f(year_ago)) if year_ago else None
            r[name + "_qoq"] = _pct(f(q), f(before)) if before else None
        r["year_ago"] = year_ago["period_end"] if year_ago else None
        flags = []
        if q.get("other_income") is not None and q.get("pbt") and q["pbt"] > 0 and \
                q["other_income"] > ONE_OFF_SHARE * q["pbt"]:
            flags.append("other income is %s%% of profit before tax: part of the profit is outside the business"
                         % (q["other_income"] / q["pbt"] * 100).quantize(Decimal("1")))
        if r["profit"] is not None and r["profit"] < 0:
            flags.append("a loss")
        r["flags"] = flags
        out.append(r)
    return out


def ttm(qs):
    """The last four quarters added up, when they are four consecutive quarters; else None."""
    if len(qs) < 4 or not all(_near(qs[i]["period_end"], qs[i + 1]["period_end"], 91) for i in range(3)):
        return None
    four = qs[:4]

    def total(f):
        vals = [f(q) for q in four]
        return sum(vals) if all(v is not None for v in vals) else None
    return {"from": four[-1]["period_end"], "to": four[0]["period_end"], "top_line": total(top_line),
            "profit": total(profit), "eps_basic": total(lambda q: q.get("eps_basic"))}


def newer_results(db, isin, latest_end):
    """Results the exchange published for a later quarter than the store's numbers: (quarter end, published) or None."""
    row = _q(db, "SELECT period_to, broadcast_at FROM results WHERE isin = ? AND period_to > ? "
                 "ORDER BY period_to DESC, broadcast_at LIMIT 1", (isin, latest_end or ""))
    return row[0] if row else None


def company(db, name, n=4, nature=None):
    symbol, isin = resolve(db, name)
    qs, nat = quarters(db, isin, nature)
    if not qs:
        raise AnalystError("%s: no quarterly results stored (core/financials.py fetch)" % symbol)
    rows = analyse(qs)
    out = {"symbol": symbol, "isin": isin, "nature": nat, "bank": rows[0]["bank"], "quarters": rows[:n],
           "ttm": ttm(qs), "notes": []}
    later = newer_results(db, isin, qs[0]["period_end"])
    if later:
        out["notes"].append("results for the quarter ending %s were published on %s and are not in the store's "
                            "numbers yet (NSE's results XBRL after February 2025 is not read): read them from the "
                            "exchange before summarising" % (later[0], later[1][:10]))
    return out


def metrics_row(c):
    """One row for trader-screener: money in crore, percentages as numbers."""
    q, t = c["quarters"][0], c["ttm"] or {}
    cr = lambda x: float((x / CRORE).quantize(Decimal("0.01"))) if x is not None else None
    num = lambda x: float(x) if x is not None else None
    return {"symbol": c["symbol"], "isin": c["isin"], "quarter": q["period_end"], "nature": c["nature"],
            "bank": c["bank"], "revenue_ttm_cr": cr(t.get("top_line")), "net_profit_ttm_cr": cr(t.get("profit")),
            "eps_ttm": num(t.get("eps_basic")), "net_margin": num(q["net_margin"]), "pbt_margin": num(q["pbt_margin"]),
            "revenue_growth_yoy": num(q["top_line_yoy"]), "profit_growth_yoy": num(q["profit_yoy"]),
            "eps_growth_yoy": num(q["eps_yoy"]), "published": (q["broadcast_at"] or "")[:10]}


def report(c):
    top = "interest earned" if c["bank"] else "revenue"
    L = ["%s (%s), %s results, as filed" % (c["symbol"], c["isin"], c["nature"].lower())]
    for q in c["quarters"]:
        yoy = lambda k: ("%+s%%" % q[k + "_yoy"]) if q[k + "_yoy"] is not None else "n/a"
        L.append("  quarter ending %s (published %s, %s): %s %s (%s on a year earlier), profit %s (%s), EPS %s (%s)" % (
            q["period_end"], (q["broadcast_at"] or "")[:10], (q["audited"] or "").lower() or "audit unknown", top,
            inr_short(top_line(q)) if top_line(q) is not None else "n/a", yoy("top_line"),
            inr_short(q["profit"]) if q["profit"] is not None else "n/a", yoy("profit"),
            inr(q["eps_basic"]) if q.get("eps_basic") is not None else "n/a", yoy("eps")))
        m = ["net margin %s%%" % q["net_margin"] if q["net_margin"] is not None else None,
             "profit before tax %s%% of %s" % (q["pbt_margin"], top) if q["pbt_margin"] is not None else None]
        if q["bank"]:
            if q.get("net_interest_income") is not None:
                m.append("net interest income %s" % inr_short(q["net_interest_income"]))
            if q.get("provisions") is not None:
                m.append("provisions %s" % inr_short(q["provisions"]))
        L.append("    " + ", ".join(x for x in m if x))
        for f in q["flags"]:
            L.append("    flag: " + f)
    t = c["ttm"]
    if t:
        L.append("  last four quarters (%s to %s): %s %s, profit %s, EPS %s (sum of quarterly basic EPS)" % (
            t["from"], t["to"], top, inr_short(t["top_line"]) if t["top_line"] is not None else "n/a",
            inr_short(t["profit"]) if t["profit"] is not None else "n/a",
            inr(t["eps_basic"]) if t["eps_basic"] is not None else "n/a"))
    for n in c["notes"]:
        L.append("  NOTE: " + n)
    return "\n".join(L)


def _jsonable(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_jsonable(v) for v in x]
    return x


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quarterly results as filed with NSE, quarter by quarter.")
    ap.add_argument("companies", nargs="+", help="NSE symbols or ISINs")
    ap.add_argument("--quarters", type=int, default=4)
    ap.add_argument("--standalone", action="store_true", help="standalone results instead of consolidated")
    ap.add_argument("--metrics", help="write a metrics file for trader-screener")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out, errors = [], []
    try:
        db = connect(a.db)
    except AnalystError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    for name in a.companies:
        try:
            out.append(company(db, name, a.quarters, "Standalone" if a.standalone else None))
        except AnalystError as e:
            errors.append(str(e))
    db.close()
    if a.metrics and out:
        os.makedirs(os.path.dirname(a.metrics) or ".", exist_ok=True)
        with open(a.metrics, "w", encoding="utf-8") as fh:
            json.dump({"source": "trader-analyst/results.py, results as filed with NSE",
                       "written": dt.date.today().isoformat(), "companies": [metrics_row(c) for c in out]}, fh, indent=1)
    if a.json:
        print(json.dumps({"companies": _jsonable(out), "errors": errors}, indent=1))
    else:
        print("\n\n".join(report(c) for c in out))
        if a.metrics and out:
            print("\nwrote %d compan%s to %s" % (len(out), "y" if len(out) == 1 else "ies", a.metrics))
    for e in errors:
        print("error: %s" % e, file=sys.stderr)
    return 0 if out and not errors else 1 if out else 2


if __name__ == "__main__":
    sys.exit(main())
