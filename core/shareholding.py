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
and a link to the full XBRL filing (FII, mutual fund and other holders,
pledges), which this module does not read yet.

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
"""


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
    db.executescript("DROP TABLE IF EXISTS shareholding;" + SCHEMA)
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
    db.commit()
    if log:
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
    elif a.cmd == "rebuild":
        rebuild(db)
    else:
        print(db.execute("SELECT COUNT(*), COUNT(DISTINCT isin), MIN(period_end), MAX(period_end), MAX(broadcast_at) "
                         "FROM shareholding").fetchone())
    return 0


if __name__ == "__main__":
    sys.exit(main())
