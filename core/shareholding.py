#!/usr/bin/env python3
"""Quarterly shareholding patterns from NSE into the market store, stamped
with when they became public.

Source (www.nseindia.com/api):

  corporate-share-holdings-master?index=equities
        every listed company's latest quarter (one row each)
  corporate-share-holdings-master?index=equities&symbol=TCS
        one company's history, a row per quarter (from 2021 for TCS)

Each row carries the quarter end (`date`), the promoter and promoter group
stake (`pr_and_prgrp`), the public stake (`public_val`) and shares held by
employee trusts (`employeeTrusts`), all in percent; `broadcastDate`, the
second NSE disseminated it; `submissionDate`, the day the company filed;
and a link to the full XBRL filing.

The XBRL filing (nsearchives.nseindia.com/corporate/xbrl/SHP_...xml) has
the full pattern. Only the category totals are read: facts whose context
has the CategoryOfShareholdersAxis alone. Facts with a typed member are
per-shareholder rows (names, with PANs masked by NSE); they are never read
or stored. What is kept per filing, as category facts in a small JSON
under data/shareholding_raw/xbrl/ (the raw file is 0.2 to 1.5 MB and is
not kept), then in the `shareholding_detail` table:

  promoter, public, fii (foreign institutions: FPI category I and II and
  others), fpi_cat1, fpi_cat2, dii (domestic institutions), mutual_funds,
  insurance, retail_small (resident individuals up to Rs 2 lakh nominal),
  retail_large (above Rs 2 lakh), employee_trusts: percent of total shares
  total_shares
  pledged, ndu, other_encumbrance: NSE's yes/no answers on whether any
  promoter shares are encumbered that way. The pledged quantity is not read
  yet: no sample with pledges has been seen.

Point in time, and the one trap: a revised filing REPLACES the original in
NSE's list, with the revision's broadcast time. The original submission day
survives in `submissionDate`. So the store keeps both: `broadcast_at` (this
row's dissemination) and `submitted_on` (the company's first filing day).
For a revised row the numbers are the revised ones, which were not public
on `submitted_on`; signals that care must use `broadcast_at` for revised
rows, or accept that small look-ahead and say so.

Raw answers are cached under data/shareholding_raw/ so a parser change is
a rebuild, not a refetch.

  python3 core/shareholding.py fetch                    # the latest quarter, all companies
  python3 core/shareholding.py fetch --history TCS INFY # full history for these symbols
  python3 core/shareholding.py fetch --history-all      # every symbol in the latest list (slow: one request each)
  python3 core/shareholding.py fetch --xbrl-since 2026-09-30   # plus FII, MF, pledge flags for that quarter on
  python3 core/shareholding.py rebuild
  python3 core/shareholding.py status
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
from money import D  # noqa: E402

API = "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities"
RAW = os.path.join(bhavcopy.ROOT, "data", "shareholding_raw")
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS shareholding (
  record_id TEXT PRIMARY KEY, symbol TEXT, isin TEXT, period_end TEXT, promoter_pct TEXT, public_pct TEXT,
  employee_trusts_pct TEXT, revised INTEGER, submitted_on TEXT, revised_on TEXT, broadcast_at TEXT, xbrl TEXT);
CREATE INDEX IF NOT EXISTS shareholding_isin ON shareholding (isin, period_end);
CREATE TABLE IF NOT EXISTS shareholding_detail (
  record_id TEXT PRIMARY KEY, isin TEXT, period_end TEXT, promoter TEXT, public TEXT, fii TEXT, fpi_cat1 TEXT,
  fpi_cat2 TEXT, dii TEXT, mutual_funds TEXT, insurance TEXT, retail_small TEXT, retail_large TEXT,
  employee_trusts TEXT, total_shares INTEGER, pledged INTEGER, ndu INTEGER, other_encumbrance INTEGER);
"""
# CategoryOfShareholdersAxis members -> our names (NSE's taxonomy, read from the filings of 2026-09)
CATEGORIES = {
    "ShareholdingOfPromoterAndPromoterGroupMember": "promoter",
    "PublicShareholdingMember": "public",
    "InstitutionsForeignMember": "fii",
    "InstitutionsForeignPortfolioInvestorCategoryOneMember": "fpi_cat1",
    "InstitutionsForeignPortfolioInvestorCategoryTwoMember": "fpi_cat2",
    "InstitutionsDomesticMember": "dii",
    "MutualFundsOrUTIMember": "mutual_funds",
    "InsuranceCompaniesMember": "insurance",
    "ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakhMember": "retail_small",
    "ResidentIndividualShareholdersHoldingNominalShareCapitalInExcessOfRsTwoLakhMember": "retail_large",
    "EmployeeBenefitsTrustsMember": "employee_trusts",
}
FLAGS = {
    "WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged": "pledged",
    "WhetherAnySharesHeldByPromotersAreEncumberedUnderNonDisposalUndertaking": "ndu",
    "WhetherAnySharesHeldByPromotersAreEncumberedOtherThanByWayOfPledgeOrNDU": "other_encumbrance",
}
HUNDRED = Decimal(100)
# the facts kept per category: enough for the columns above and for later ones (holder counts)
KEEP = ("ShareholdingAsAPercentageOfTotalNumberOfShares", "NumberOfShares", "NumberOfShareholders")


def stamp(text):
    """'27-OCT-2026 15:34:58' -> '2026-10-27T15:34:58' (IST). None if unreadable."""
    try:
        return dt.datetime.strptime((text or "").strip().title(), "%d-%b-%Y %H:%M:%S").isoformat()
    except ValueError:
        return None


def day(text):
    try:
        return dt.datetime.strptime((text or "").strip().title(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def pct(text):
    """'67.55' -> '67.55'; anything that is not a percent between 0 and 100 -> None."""
    t = str(text if text is not None else "").strip()
    try:
        v = D(t)
    except Exception:
        return None
    if not v.is_finite() or v < 0 or v > 100:
        return None
    return str(v)


def row(r):
    """One NSE row -> a store row, or None when it lacks an id, a quarter or a broadcast time."""
    at = stamp(r.get("broadcastDate"))
    period = day(r.get("date"))
    if not at or not period or not r.get("recordId"):
        return None
    revised = str(r.get("revisedData") or "").strip().lower() == "revised"
    return {"record_id": str(r["recordId"]), "symbol": (r.get("symbol") or "").strip().upper(),
            "isin": (r.get("isin") or "").strip().upper() or None, "period_end": period,
            "promoter_pct": pct(r.get("pr_and_prgrp")), "public_pct": pct(r.get("public_val")),
            "employee_trusts_pct": pct(r.get("employeeTrusts")), "revised": int(revised),
            "submitted_on": day(r.get("submissionDate")), "revised_on": day(r.get("revisionDate")) if revised else None,
            "broadcast_at": at, "xbrl": r.get("xbrl") if str(r.get("xbrl") or "").startswith("https://") else None}


# ---------------------------------------------------------------- XBRL

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def xbrl_facts(blob):
    """The category-level facts of a shareholding XBRL filing, as plain JSON:
    {"symbol", "isin", "date", "flags": {name: "true"/"false"},
     "categories": {member: {element: text}}}. Per-shareholder facts (typed
    members) are skipped, so no names are kept."""
    try:
        root = ET.fromstring(blob)
    except ET.ParseError as e:
        raise ValueError("not an XBRL file: %s" % e)
    ctx = {}
    for c in root:
        if _local(c.tag) != "context":
            continue
        if any(_local(m.tag) == "typedMember" for m in c.iter()):
            continue
        dims = [(m.get("dimension", "").split(":")[-1], (m.text or "").split(":")[-1])
                for m in c.iter() if _local(m.tag) == "explicitMember"]
        ctx[c.get("id")] = dims
    out = {"symbol": None, "isin": None, "date": None, "flags": {}, "categories": {}}
    plain = {"Symbol": "symbol", "ISIN": "isin", "DateOfReport": "date"}
    for el in root:
        tag, ref = _local(el.tag), el.get("contextRef")
        if ref not in ctx:
            continue
        text = (el.text or "").strip()
        dims = ctx[ref]
        if not dims:
            if tag in plain:
                out[plain[tag]] = text
            elif tag in FLAGS:
                out["flags"][tag] = text.lower()
        elif len(dims) == 1 and dims[0][0] == "CategoryOfShareholdersAxis" and tag in KEEP:
            out["categories"].setdefault(dims[0][1], {})[tag] = text
    return out


def detail(facts):
    """Category facts -> the shareholding_detail columns (percents as strings, 71.77 not 0.7177)."""
    cats = facts.get("categories", {})
    out = {}
    for member, name in CATEGORIES.items():
        v = cats.get(member, {}).get("ShareholdingAsAPercentageOfTotalNumberOfShares")
        try:
            out[name] = format((Decimal(v) * HUNDRED).normalize(), "f") if v not in (None, "") else None
        except Exception:
            out[name] = None
    total = cats.get("ShareholdingPatternMember", {}).get("NumberOfShares")
    out["total_shares"] = int(Decimal(total)) if total not in (None, "") else None
    for tag, name in FLAGS.items():
        v = facts.get("flags", {}).get(tag)
        out[name] = {"true": 1, "false": 0}.get(v)
    return out


# ---------------------------------------------------------------- fetch

def raw_path(kind, name, raw=RAW):
    return os.path.join(raw, kind, name + ".json")


def _save(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)


def fetch_latest(get=bhavcopy.http_get, raw=RAW, today=None, log=print):
    """The latest quarter for every company, cached under latest/<today>.json. Returns the rows."""
    blob = get(API)
    rows = json.loads(blob) if blob else []
    rows = rows.get("data", []) if isinstance(rows, dict) else rows
    _save(raw_path("latest", (today or dt.date.today()).isoformat(), raw), rows)
    if log:
        log("latest: %d companies" % len(rows))
    return rows


def fetch_history(symbols, get=bhavcopy.http_get, raw=RAW, refresh=False, pause=0.6, log=print):
    """Each symbol's full history, cached under history/<SYMBOL>.json; cached symbols
    are skipped unless refresh. Returns how many were fetched."""
    n = 0
    for sym in symbols:
        sym = sym.strip().upper()
        if not SYMBOL.match(sym):
            raise ValueError("not an NSE symbol: %r" % sym)
        out = raw_path("history", sym, raw)
        if os.path.exists(out) and not refresh:
            continue
        blob = get(API + "&symbol=" + sym.replace("&", "%26"))
        rows = json.loads(blob) if blob else []
        rows = rows.get("data", []) if isinstance(rows, dict) else rows
        _save(out, rows)
        n += 1
        if log:
            log("%s: %d quarters" % (sym, len(rows)))
        if pause:
            time.sleep(pause)
    return n


def xbrl_name(url):
    """The cache name for a filing: its file name, which NSE makes unique per filing."""
    name = url.rsplit("/", 1)[-1]
    if not re.match(r"^[A-Za-z0-9_\-]+\.xml$", name):
        raise ValueError("unexpected XBRL link: %r" % url)
    return name[:-4]


def fetch_xbrl(db, get=bhavcopy.http_get, raw=RAW, since=None, symbols=None, pause=0.6, log=print):
    """Download each stored filing's XBRL not yet cached, keep only its category facts
    (data/shareholding_raw/xbrl/<name>.json). since: only quarters ending on or after
    this date; symbols: only these. Returns how many were fetched."""
    q = "SELECT symbol, period_end, xbrl FROM shareholding WHERE xbrl IS NOT NULL"
    args = []
    if since:
        q += " AND period_end >= ?"
        args.append(since)
    rows = db.execute(q + " ORDER BY period_end DESC, symbol", args).fetchall()
    want = {s.upper() for s in symbols} if symbols else None
    n = 0
    for sym, period, url in rows:
        if want is not None and sym not in want:
            continue
        out = raw_path("xbrl", xbrl_name(url), raw)
        if os.path.exists(out):
            continue
        blob = get(url)
        if not blob:
            continue
        _save(out, xbrl_facts(blob))
        n += 1
        if log:
            log("%s %s: XBRL read" % (sym, period))
        if pause:
            time.sleep(pause)
    return n


def latest_symbols(raw=RAW):
    folder = os.path.join(raw, "latest")
    if not os.path.isdir(folder) or not os.listdir(folder):
        return []
    with open(os.path.join(folder, sorted(os.listdir(folder))[-1]), encoding="utf-8") as fh:
        return sorted({(r.get("symbol") or "").strip().upper() for r in json.load(fh) if r.get("symbol")})


# ---------------------------------------------------------------- store

def connect(path=bhavcopy.DEFAULT_DB):
    db = bhavcopy.connect(path)
    db.executescript(SCHEMA)
    return db


def rebuild(db, raw=RAW, log=print):
    """Re-parse every cached answer into the shareholding table. A record seen in
    both a latest list and a history keeps one row (same recordId)."""
    db.executescript("DROP TABLE IF EXISTS shareholding; DROP TABLE IF EXISTS shareholding_detail;" + SCHEMA)
    counts = {"rows": 0, "skipped": 0}
    for kind in ("history", "latest"):
        folder = os.path.join(raw, kind)
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            with open(os.path.join(folder, f), encoding="utf-8") as fh:
                rows = json.load(fh)
            for r in rows:
                x = row(r)
                if not x:
                    counts["skipped"] += 1
                    continue
                db.execute("INSERT OR REPLACE INTO shareholding VALUES (:record_id,:symbol,:isin,:period_end,"
                           ":promoter_pct,:public_pct,:employee_trusts_pct,:revised,:submitted_on,:revised_on,"
                           ":broadcast_at,:xbrl)", x)
                counts["rows"] += 1
    for record_id, isin, period, url in db.execute(
            "SELECT record_id, isin, period_end, xbrl FROM shareholding WHERE xbrl IS NOT NULL").fetchall():
        path = raw_path("xbrl", xbrl_name(url), raw)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            facts = json.load(fh)
        if facts.get("isin") and isin and facts["isin"] != isin:
            counts["skipped"] += 1  # the filing is for another security: never store it under this one
            continue
        d = detail(facts)
        d.update(record_id=record_id, isin=isin, period_end=period)
        db.execute("INSERT OR REPLACE INTO shareholding_detail VALUES (:record_id,:isin,:period_end,:promoter,:public,"
                   ":fii,:fpi_cat1,:fpi_cat2,:dii,:mutual_funds,:insurance,:retail_small,:retail_large,"
                   ":employee_trusts,:total_shares,:pledged,:ndu,:other_encumbrance)", d)
        counts["detail"] = counts.get("detail", 0) + 1
    db.commit()
    if log:
        log("shareholding detail from XBRL: %d filings" % counts.get("detail", 0))
        n = db.execute("SELECT COUNT(*), COUNT(DISTINCT isin), SUM(revised) FROM shareholding").fetchone()
        log("shareholding: %d filings for %d companies (%d revised); %d rows skipped" % (n[0], n[1], n[2] or 0,
                                                                                         counts["skipped"]))
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE quarterly shareholding patterns into the store.")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--history", nargs="*", default=[], metavar="SYMBOL", help="full history for these symbols")
    f.add_argument("--history-all", action="store_true", help="full history for every symbol in the latest list")
    f.add_argument("--refresh", action="store_true", help="refetch histories already cached")
    f.add_argument("--xbrl-since", metavar="YYYY-MM-DD",
                   help="also read the XBRL filing of every stored quarter ending on or after this date (slow)")
    sub.add_parser("rebuild")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    db = connect(a.db)
    if a.cmd == "fetch":
        fetch_latest()
        syms = list(a.history) + (latest_symbols() if a.history_all else [])
        if syms:
            fetch_history(syms, refresh=a.refresh)
        rebuild(db)
        if a.xbrl_since:
            fetch_xbrl(db, since=a.xbrl_since)
            rebuild(db)
    elif a.cmd == "rebuild":
        rebuild(db)
    else:
        print(db.execute("SELECT COUNT(*), COUNT(DISTINCT isin), MIN(period_end), MAX(period_end), MAX(broadcast_at) "
                         "FROM shareholding").fetchone())
    return 0


if __name__ == "__main__":
    sys.exit(main())
