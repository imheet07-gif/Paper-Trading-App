#!/usr/bin/env python3
"""Company events from NSE into the market store, stamped with the moment
they became public: insider trades (SEBI Prohibition of Insider Trading
disclosures) and quarterly results announcements.

Sources (www.nseindia.com/api, fetched month by month):

  corporates-pit                 insider trades: person, category (promoter,
                                 director, key managerial personnel, ...),
                                 mode (market purchase, ESOP, pledge, ...),
                                 quantity, value, trade dates, and `date`,
                                 the time NSE published the disclosure
                                 (full history from 2016; sparse before)
  corporates-financial-results   results filings with `broadCastDate`, the
                                 second they were disseminated (2013 to
                                 February 2025, when Integrated Filing
                                 replaced it)
  corporate-announcements        results from 2025 on: "Outcome of Board
                                 Meeting" and the older "Financial Result
                                 Updates" headings, text "has submitted to
                                 the Exchange, the financial results for the
                                 period ended <date>", stamped `an_dt`;
                                 only those headings are cached
  corporate-sast-reg29           Takeover Regulation 29 disclosures: a
                                 holder crossing 5% or moving 2%, with a
                                 promoter flag and the mode (from 2013)

The insider feed is empty after 2 May 2026 and has no live replacement
(research/FINDINGS.md).

Point in time is the whole point: insider buys are disclosed a median of
two days after the trade and one in ten takes four or more, so every
signal must key on the publication time, never the trade date. Raw
monthly answers are cached under data/events_raw/ so a parser change is a
rebuild, not a refetch.

  python3 core/events.py fetch --start 2013-01 --end 2026-10
  python3 core/events.py rebuild
  python3 core/events.py status
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

API = "https://www.nseindia.com/api/"
RAW = os.path.join(bhavcopy.ROOT, "data", "events_raw")
SOURCES = {
    "insider": "corporates-pit?index=equities&from_date=%s&to_date=%s",
    "results": "corporates-financial-results?index=equities&from_date=%s&to_date=%s&period=Quarterly",
    "announcements": "corporate-announcements?index=equities&from_date=%s&to_date=%s",
    "sast": "corporate-sast-reg29?index=equities&from_date=%s&to_date=%s",
}
FIRST = {"announcements": "2025-01"}  # 15 MB a month; the results feed covers the years before
RESULT_HEADINGS = ("Outcome of Board Meeting", "Financial Result Updates", "Integrated Filing- Financial")
KEEP = {"announcements": lambda r: r.get("desc") in RESULT_HEADINGS}
SCHEMA = """
CREATE TABLE IF NOT EXISTS insider (
  did TEXT PRIMARY KEY, pid TEXT, symbol TEXT, isin TEXT, person TEXT, category TEXT, mode TEXT, txn TEXT,
  sec_type TEXT, qty INTEGER, value INTEGER, trade_from TEXT, trade_to TEXT, disclosed_at TEXT);
CREATE INDEX IF NOT EXISTS insider_isin ON insider (isin, disclosed_at);
CREATE TABLE IF NOT EXISTS results (
  seq TEXT PRIMARY KEY, symbol TEXT, isin TEXT, period_from TEXT, period_to TEXT, relating_to TEXT,
  consolidated TEXT, audited TEXT, broadcast_at TEXT);
CREATE INDEX IF NOT EXISTS results_isin ON results (isin, broadcast_at);
CREATE TABLE IF NOT EXISTS sast (
  app_no TEXT PRIMARY KEY, symbol TEXT, isin TEXT, holder TEXT, promoter INTEGER, mode TEXT, side TEXT, reg TEXT,
  shares_acquired INTEGER, shares_sold INTEGER, shares_after INTEGER, pct_acquired TEXT, pct_sold TEXT,
  pct_after TEXT, trade_from TEXT, trade_to TEXT, disclosed_at TEXT);
CREATE INDEX IF NOT EXISTS sast_isin ON sast (isin, disclosed_at);
"""


def months(start, end):
    """'2013-01', '2013-02', ... up to `end` inclusive."""
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield "%04d-%02d" % (y, m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def month_range(ym):
    y, m = map(int, ym.split("-"))
    first = dt.date(y, m, 1)
    last = (dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1) - dt.timedelta(days=1))
    return first, last


def stamp(text):
    """'31-Jan-2024 21:54' or '31-Jan-2024 21:25:51' -> '2024-01-31T21:54:00' (IST). None if unreadable."""
    text = (text or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).isoformat()
        except ValueError:
            pass
    return None


def day(text):
    try:
        return dt.datetime.strptime((text or "").strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def _int(text):
    t = (text or "").replace(",", "").strip()
    try:
        return int(D(t))
    except Exception:
        return None


# ---------------------------------------------------------------- fetch

def raw_path(source, ym, raw=RAW):
    return os.path.join(raw, source, ym + ".json")


def fetch(start, end, get=bhavcopy.http_get, pause=0.6, today=None, log=print, sources=SOURCES, raw=RAW):
    """Cache each month's raw answer. Months already cached are skipped,
    except the current one and the one before, which are still filling up."""
    today = today or dt.date.today()
    fresh = {today.strftime("%Y-%m"), (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")}
    n = 0
    for source, path in sources.items():
        for ym in months(max(start, FIRST.get(source, start)), end):
            out = raw_path(source, ym, raw)
            if os.path.exists(out) and ym not in fresh:
                continue
            a, b = month_range(ym)
            blob = get(API + path % (a.strftime("%d-%m-%Y"), b.strftime("%d-%m-%Y")))
            if blob is None:
                continue
            data = json.loads(blob)
            rows = data.get("data", []) if isinstance(data, dict) else data
            if source in KEEP:
                rows = [r for r in rows if KEEP[source](r)]
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
            n += 1
            if log:
                log("%s %s: %d rows" % (source, ym, len(rows)))
            time.sleep(pause)
    return n


# ---------------------------------------------------------------- parse and store

def connect(path=bhavcopy.DEFAULT_DB):
    db = bhavcopy.connect(path)
    db.executescript(SCHEMA)
    return db


def symbol_index(db):
    """{symbol: [(first date, last date, isin), ...]} from the prices table, read once."""
    out = {}
    for sym, isin, a, b in db.execute("SELECT symbol, isin, MIN(date), MAX(date) FROM prices GROUP BY symbol, isin"):
        out.setdefault(sym, []).append((a, b, isin))
    for spans in out.values():
        spans.sort()
    return out


def isin_for(index, symbol, on):
    """The ISIN that traded as `symbol` most recently on or before `on` (insider
    rows carry no ISIN); the first one ever when the symbol only traded later."""
    spans = index.get(symbol)
    if not spans:
        return None
    before = [s for s in spans if s[0] <= on]
    return max(before, key=lambda s: min(s[1], on))[2] if before else spans[0][2]


def insider_row(r):
    at = stamp(r.get("date"))
    if not at or not r.get("did"):
        return None
    return {"did": str(r["did"]), "pid": str(r.get("pid") or ""), "symbol": (r.get("symbol") or "").strip(), "person": r.get("acqName"),
            "category": (r.get("personCategory") or "").strip(), "mode": (r.get("acqMode") or "").strip(),
            "txn": (r.get("tdpTransactionType") or "").strip(), "sec_type": (r.get("secType") or "").strip(),
            "qty": _int(r.get("secAcq")), "value": _int(r.get("secVal")),
            "trade_from": day(r.get("acqfromDt")), "trade_to": day(r.get("acqtoDt")), "disclosed_at": at}


def results_row(r):
    at = stamp(r.get("broadCastDate"))
    if not at or not r.get("seqNumber"):
        return None
    return {"seq": str(r["seqNumber"]), "symbol": (r.get("symbol") or "").strip(), "isin": (r.get("isin") or "").strip(),
            "period_from": day(r.get("fromDate")), "period_to": day(r.get("toDate")),
            "relating_to": r.get("relatingTo"), "consolidated": r.get("consolidated"), "audited": r.get("audited"),
            "broadcast_at": at}


PERIOD_ENDED = re.compile(
    r"(?:period|quarter|half[- ]year|year|months?)\s+ended\s+(?:on\s+)?"
    r"([A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4}"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{4})", re.I)


def period_end(text):
    """'... financial results for the period ended June 30, 2026' -> '2026-06-30'.
    Only a quarter end counts; anything else is None."""
    m = PERIOD_ENDED.search(text or "")
    if not m:
        return None
    t = re.sub(r"(\d)(st|nd|rd|th)", r"\1", m.group(1))
    t = " ".join(re.sub(r"[,./-]", " ", t).split())  # 'June 30 2026', '30 June 2026', '30 09 2026'
    for fmt in ("%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y", "%d %m %Y"):
        try:
            d = dt.datetime.strptime(t, fmt).date()
            break
        except ValueError:
            continue
    else:
        return None
    nxt = d + dt.timedelta(days=1)
    return d.isoformat() if d.month in (3, 6, 9, 12) and nxt.day == 1 else None


def announcement_result(r):
    """A results release from the announcements feed, or None for any other announcement."""
    text = r.get("attchmntText") or ""
    if r.get("desc") not in RESULT_HEADINGS or "financial result" not in text.lower():
        return None
    at, end = stamp(r.get("an_dt")), period_end(text)
    if not at or not end or not r.get("seq_id"):
        return None
    return {"seq": "A" + str(r["seq_id"]), "symbol": (r.get("symbol") or "").strip(),
            "isin": (r.get("sm_isin") or "").strip(), "period_from": None, "period_to": end,
            "relating_to": r.get("desc"), "consolidated": None, "audited": None, "broadcast_at": at}


def _pct(text):
    t = (text or "").strip()
    try:
        return str(D(t))
    except Exception:
        return None


def sast_row(r):
    at = stamp((r.get("timestamp") or "").title())
    if not at or not r.get("application_no"):
        return None
    span = [day(x.title()) for x in (r.get("acquirerDate") or "").split(" to ")]
    return {"app_no": str(r["application_no"]), "symbol": (r.get("symbol") or "").strip(),
            "holder": r.get("acquirerName"), "promoter": {"Y": 1, "N": 0}.get(r.get("promoterType")),
            # NSE writes the same mode as 'Open market' and 'Open Market' in different years
            "mode": (r.get("acquisitionMode") or "").strip().capitalize(),
            "side": (r.get("acqSaleType") or "").strip().capitalize(),
            "reg": r.get("regType"), "shares_acquired": _int(r.get("noOfShareAcq")),
            "shares_sold": _int(r.get("noOfShareSale")), "shares_after": _int(r.get("noOfShareAft")),
            "pct_acquired": _pct(r.get("totAcqShare")), "pct_sold": _pct(r.get("totSaleShare")),
            "pct_after": _pct(r.get("totAftShare")), "trade_from": span[0] if span else None,
            "trade_to": span[-1] if span else None, "disclosed_at": at}


def rebuild(db, raw=RAW, log=print):
    """Re-parse every cached month into the insider and results tables.

    `did` identifies one disclosed transaction (`pid` is the company). NSE lists
    some transactions more than once, a minute apart; the earliest listing is
    when it became public, so that one is kept. Results filings are kept the same way."""
    db.executescript("DROP TABLE IF EXISTS insider; DROP TABLE IF EXISTS results; DROP TABLE IF EXISTS sast;" + SCHEMA)
    counts = {"insider": 0, "results": 0, "announcements": 0, "sast": 0, "unmapped": 0}  # rows read, duplicates included
    index = symbol_index(db)
    live = {}  # isin: (first, last) traded
    for spans in index.values():
        for a, b, i in spans:
            lo, hi = live.get(i, (a, b))
            live[i] = (min(lo, a), max(hi, b))
    for source in ("insider", "results", "announcements", "sast"):
        folder = os.path.join(raw, source)
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            with open(os.path.join(folder, f), encoding="utf-8") as fh:
                rows = json.load(fh)
            for r in rows:
                if source == "insider":
                    x = insider_row(r)
                    if not x:
                        continue
                    x["isin"] = isin_for(index, x["symbol"], x["disclosed_at"][:10])
                    if not x["isin"]:
                        counts["unmapped"] += 1
                    db.execute("INSERT INTO insider VALUES (:did,:pid,:symbol,:isin,:person,:category,:mode,:txn,"
                               ":sec_type,:qty,:value,:trade_from,:trade_to,:disclosed_at) ON CONFLICT(did) DO UPDATE "
                               "SET disclosed_at=excluded.disclosed_at WHERE excluded.disclosed_at < disclosed_at", x)
                elif source == "sast":
                    x = sast_row(r)
                    if not x:
                        continue
                    x["isin"] = isin_for(index, x["symbol"], x["disclosed_at"][:10])
                    if not x["isin"]:
                        counts["unmapped"] += 1
                    db.execute("INSERT INTO sast VALUES (:app_no,:symbol,:isin,:holder,:promoter,:mode,:side,:reg,"
                               ":shares_acquired,:shares_sold,:shares_after,:pct_acquired,:pct_sold,:pct_after,"
                               ":trade_from,:trade_to,:disclosed_at) ON CONFLICT(app_no) DO UPDATE "
                               "SET disclosed_at=excluded.disclosed_at WHERE excluded.disclosed_at < disclosed_at", x)
                else:
                    x = results_row(r) if source == "results" else announcement_result(r)
                    if not x:
                        continue
                    # NSE keeps a company's old ISIN on results for years after a split changed it
                    on, span = x["broadcast_at"][:10], live.get(x["isin"])
                    if not span or not span[0] <= on <= span[1]:
                        x["isin"] = isin_for(index, x["symbol"], on) or x["isin"]
                    db.execute("INSERT INTO results VALUES (:seq,:symbol,:isin,:period_from,:period_to,"
                               ":relating_to,:consolidated,:audited,:broadcast_at) ON CONFLICT(seq) DO UPDATE "
                               "SET broadcast_at=excluded.broadcast_at WHERE excluded.broadcast_at < broadcast_at", x)
                counts[source] += 1
    db.commit()
    if log:
        log("rows read: insider %(insider)d, results %(results)d, results from announcements %(announcements)d, "
            "sast %(sast)d (%(unmapped)d insider or sast rows without a matching ISIN)" % counts)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE insider trades and results announcements into the store.")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--start", default="2013-01")
    f.add_argument("--end", default=dt.date.today().strftime("%Y-%m"))
    sub.add_parser("rebuild")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    db = connect(a.db)
    if a.cmd == "fetch":
        fetch(a.start, a.end)
        rebuild(db)
    elif a.cmd == "rebuild":
        rebuild(db)
    else:
        for t in ("insider", "results", "sast"):
            col = "broadcast_at" if t == "results" else "disclosed_at"
            print(t, db.execute("SELECT COUNT(*), MIN(%s), MAX(%s) FROM %s" % (col, col, t)).fetchone())
    return 0


if __name__ == "__main__":
    sys.exit(main())
