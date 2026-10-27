#!/usr/bin/env python3
"""Quarterly results from the XBRL filed with each NSE results announcement,
keyed on when the results became public.

Where the files are: NSE's results feed (corporates-financial-results, cached
by core/events.py under data/events_raw/results/) carries an `xbrl` link per
filing, nsearchives.nseindia.com/corporate/xbrl/<INDAS|BANKING|...>_*.xml,
from 2013 to February 2025, when that feed stopped. The announcements that
carry results since then flag `hasXbrl` but give no link; where those files
live is still open (HANDOVER.md), so quarters from March 2025 are not here.

What is read, for the quarter only (not the year to date):

  revenue (RevenueFromOperations), other_income, total_income (Income),
  pbt (ProfitBeforeTax; banks ProfitLossFromOrdinaryActivitiesBeforeTax),
  tax, net_profit (ProfitLossForPeriod; banks ProfitLossForThePeriod),
  net_profit_owners (attributable to owners of the parent, consolidated),
  eps_basic, eps_diluted, and for banks interest_earned, interest_expended,
  operating_profit (before provisions) and provisions.

Amounts are rupees, exact as filed (the files carry full rupee values;
LevelOfRoundingUsedInFinancialStatements says how the company rounded).
EPS is rupees per share.

The quarter is found from the filing itself: each context's period comes
from its declaration, or, in the older files that reference undeclared
contexts (OneD, FourD), from the DateOfStartOfReportingPeriod and
DateOfEndOfReportingPeriod facts filed in that context. The quarter is the
context ending on the reporting period end and starting 80 to 100 days
before it; a bank file that states only the end date of a single context
named in ReportingQuarter as a quarter is taken as that quarter.

  python3 core/financials.py fetch --since 2024-01-01          # XBRL for results filed since then
  python3 core/financials.py fetch --symbols TCS INFY
  python3 core/financials.py rebuild
  python3 core/financials.py show TCS
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bhavcopy  # noqa: E402
import events  # noqa: E402

RAW = os.path.join(bhavcopy.ROOT, "data", "financials_raw")
RESULTS_RAW = os.path.join(events.RAW, "results")
SCHEMA = """
CREATE TABLE IF NOT EXISTS financials (
  seq TEXT PRIMARY KEY, symbol TEXT, isin TEXT, nature TEXT, audited TEXT, period_start TEXT, period_end TEXT,
  revenue TEXT, other_income TEXT, total_income TEXT, pbt TEXT, tax TEXT, net_profit TEXT, net_profit_owners TEXT,
  eps_basic TEXT, eps_diluted TEXT, interest_earned TEXT, interest_expended TEXT, operating_profit TEXT,
  provisions TEXT, rounding TEXT, broadcast_at TEXT, xbrl TEXT);
CREATE INDEX IF NOT EXISTS financials_isin ON financials (isin, period_end);
"""
# our name -> the elements that carry it, first found wins (company Ind-AS first, then banks)
METRICS = {
    "revenue": ("RevenueFromOperations",),
    "other_income": ("OtherIncome",),
    "total_income": ("Income",),
    "pbt": ("ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"),
    "tax": ("TaxExpense",),
    "net_profit": ("ProfitLossForPeriod", "ProfitLossForThePeriod"),
    "net_profit_owners": ("ProfitOrLossAttributableToOwnersOfParent",
                          "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates"),
    "eps_basic": ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                  "BasicEarningsPerShareAfterExtraordinaryItems"),
    "eps_diluted": ("DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                    "DilutedEarningsPerShareAfterExtraordinaryItems"),
    "interest_earned": ("InterestEarned",),
    "interest_expended": ("InterestExpended",),
    "operating_profit": ("OperatingProfitBeforeProvisionAndContingencies",),
    "provisions": ("ProvisionsOtherThanTaxAndContingencies",),
}
TEXT = {"Symbol": "symbol", "ISIN": "isin", "NatureOfReportStandaloneConsolidated": "nature",
        "WhetherResultsAreAuditedOrUnaudited": "audited", "ReportingQuarter": "quarter",
        "LevelOfRoundingUsedInFinancialStatements": "rounding",
        "DateOfStartOfReportingPeriod": "start", "DateOfEndOfReportingPeriod": "end"}
QUARTERS = ("first quarter", "second quarter", "third quarter", "fourth quarter")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _date(text):
    try:
        return dt.date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def _amount(text):
    t = (text or "").strip()
    try:
        v = Decimal(t)
    except Exception:
        return None
    return format(v.normalize(), "f") if v.is_finite() else None  # "344700000000", never "3.447E+11"


def parse(blob):
    """A results XBRL file -> {"symbol", "isin", "nature", "audited", "rounding",
    "period_start", "period_end", metric: str rupees, ...} for the quarter, or
    raise ValueError when the file has no quarter it can be sure of."""
    try:
        root = ET.fromstring(blob)
    except ET.ParseError as e:
        raise ValueError("not an XBRL file: %s" % e)
    declared, dimensioned = {}, set()
    for c in root:
        if _local(c.tag) != "context":
            continue
        if any(_local(m.tag) in ("explicitMember", "typedMember") for m in c.iter()):
            dimensioned.add(c.get("id"))
            continue
        per = {_local(p.tag): _date(p.text) for p in c.iter() if _local(p.tag) in ("startDate", "endDate", "instant")}
        declared[c.get("id")] = (per.get("startDate"), per.get("endDate") or per.get("instant"))
    facts = {}  # context -> {element: text}, undimensioned contexts only
    for el in root:
        ref = el.get("contextRef")
        if not ref or ref in dimensioned:
            continue
        facts.setdefault(ref, {})[_local(el.tag)] = (el.text or "").strip()
    periods = {}
    for ref, f in facts.items():
        start, end = declared.get(ref, (None, None))
        start = _date(f.get("DateOfStartOfReportingPeriod")) or start
        end = _date(f.get("DateOfEndOfReportingPeriod")) or end
        periods[ref] = (start, end)
    texts = {}
    for f in facts.values():
        for el, name in TEXT.items():
            if f.get(el) and name not in texts:
                texts[name] = f[el]
    period_end = _date(texts.get("end"))
    if not period_end:
        raise ValueError("no DateOfEndOfReportingPeriod in the filing")
    quarter = []
    for ref, (start, end) in periods.items():
        if end != period_end:
            continue
        if start and 80 <= (end - start).days <= 100:
            quarter.append((len(facts[ref]), ref, start))
    if not quarter:
        ends = [ref for ref, (start, end) in periods.items() if end == period_end and not start]
        if len(ends) == 1 and (texts.get("quarter") or "").strip().lower() in QUARTERS:
            quarter = [(len(facts[ends[0]]), ends[0], None)]
    if not quarter:
        raise ValueError("no quarter context ending %s" % period_end)
    _, ref, start = max(quarter)
    f = facts[ref]
    out = {"symbol": (texts.get("symbol") or "").upper() or None, "isin": (texts.get("isin") or "").upper() or None,
           "nature": texts.get("nature"), "audited": texts.get("audited"), "rounding": texts.get("rounding"),
           "period_start": start.isoformat() if start else None, "period_end": period_end.isoformat()}
    for name, elements in METRICS.items():
        out[name] = next((_amount(f[e]) for e in elements if e in f and _amount(f[e]) is not None), None)
    return out


# ---------------------------------------------------------------- fetch and store

def xbrl_name(url):
    name = url.rsplit("/", 1)[-1]
    if not re.match(r"^[A-Za-z0-9_\-]+\.xml$", name):
        raise ValueError("unexpected XBRL link: %r" % url)
    return name[:-4]


def filings(results_raw=RESULTS_RAW, since=None, symbols=None):
    """Results feed rows that link an XBRL file: [(seq, symbol, isin, broadcast_at, url)]."""
    want = {s.upper() for s in symbols} if symbols else None
    out = []
    if not os.path.isdir(results_raw):
        return out
    for f in sorted(os.listdir(results_raw)):
        with open(os.path.join(results_raw, f), encoding="utf-8") as fh:
            rows = json.load(fh)
        for r in rows:
            url = str(r.get("xbrl") or "")
            at = events.stamp(r.get("broadCastDate"))
            sym = (r.get("symbol") or "").strip().upper()
            if not url.startswith("https://") or not url.endswith(".xml") or not at or not r.get("seqNumber"):
                continue
            if want is not None and sym not in want:
                continue
            if since and at[:10] < since:
                continue
            out.append((str(r["seqNumber"]), sym, (r.get("isin") or "").upper() or None, at, url))
    return out


def fetch(rows, get=bhavcopy.http_get, raw=RAW, pause=0.6, log=print):
    """Download and parse each filing not yet cached; keep the parsed quarter as
    data/financials_raw/<name>.json, or {"error": ...} when it has none."""
    n = 0
    for seq, sym, isin, at, url in rows:
        out = os.path.join(raw, xbrl_name(url) + ".json")
        if os.path.exists(out):
            continue
        try:
            blob = get(url)
        except Exception as e:  # one failed download must not end a long run; not cached, so retried
            if log:
                log("%s %s: download failed (%s); retried next run" % (sym, at[:10], e))
            continue
        if not blob:
            continue
        try:
            parsed = parse(blob)
        except ValueError as e:
            parsed = {"error": str(e)}
        os.makedirs(raw, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(parsed, fh)
        n += 1
        if log:
            log("%s %s: %s" % (sym, at[:10], parsed.get("error") or "read"))
        if pause:
            time.sleep(pause)
    return n


def connect(path=bhavcopy.DEFAULT_DB):
    db = bhavcopy.connect(path)
    db.executescript(SCHEMA)
    return db


def rebuild(db, rows, raw=RAW, log=print):
    """Every cached, parsed filing into the financials table, stamped with the
    results feed's broadcast time. A filing whose ISIN is not the feed row's is
    refused rather than stored under the wrong company."""
    db.executescript("DROP TABLE IF EXISTS financials;" + SCHEMA)
    counts = {"stored": 0, "no_quarter": 0, "isin_mismatch": 0}
    for seq, sym, isin, at, url in rows:
        path = os.path.join(raw, xbrl_name(url) + ".json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            p = json.load(fh)
        if p.get("error"):
            counts["no_quarter"] += 1
            continue
        if isin and p.get("isin") and p["isin"] != isin:
            counts["isin_mismatch"] += 1
            continue
        row = dict(p, seq=seq, symbol=sym, isin=isin or p.get("isin"), broadcast_at=at, xbrl=url)
        db.execute("INSERT OR REPLACE INTO financials VALUES (:seq,:symbol,:isin,:nature,:audited,:period_start,"
                   ":period_end,:revenue,:other_income,:total_income,:pbt,:tax,:net_profit,:net_profit_owners,"
                   ":eps_basic,:eps_diluted,:interest_earned,:interest_expended,:operating_profit,:provisions,"
                   ":rounding,:broadcast_at,:xbrl)", row)
        counts["stored"] += 1
    db.commit()
    if log:
        log("financials: %(stored)d quarters stored, %(no_quarter)d files without a clear quarter, "
            "%(isin_mismatch)d refused for an ISIN mismatch" % counts)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quarterly results from NSE's results XBRL into the store.")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download and read the XBRL of cached results filings (run events.py fetch first)")
    f.add_argument("--since", metavar="YYYY-MM-DD", help="only filings broadcast on or after this day")
    f.add_argument("--symbols", nargs="*", help="only these symbols")
    sub.add_parser("rebuild")
    s = sub.add_parser("show")
    s.add_argument("symbol")
    a = ap.parse_args(argv)
    db = connect(a.db)
    if a.cmd == "fetch":
        fetch(filings(since=a.since, symbols=a.symbols))
        rebuild(db, filings())
    elif a.cmd == "rebuild":
        rebuild(db, filings())
    else:
        for r in db.execute("SELECT period_end, nature, revenue, net_profit, eps_basic, broadcast_at FROM financials "
                            "WHERE symbol = ? ORDER BY period_end, nature", (a.symbol.upper(),)):
            print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
