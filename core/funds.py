#!/usr/bin/env python3
"""Mutual fund NAVs from AMFI into the store. Standard library only.

AMFI publishes every scheme's daily NAV:

  today's list    https://www.amfiindia.com/spages/NAVAll.txt
  history         https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=DD-Mon-YYYY&todt=DD-Mon-YYYY
                  (every scheme for the dates; both URLs and their layout to verify on
                  the owner's machine: this cloud session cannot reach AMFI)

Both are semicolon-separated text with a header line, broken by AMC and
category lines that have no semicolons. Columns are found by their header
names, so a reordered file still reads; an unknown layout is an error.

    python3 core/funds.py list                          # download today's list to data/funds/NAVAll.txt
    python3 core/funds.py find "nifty 50 index" --direct --growth
    python3 core/funds.py fetch --scheme 120716 --start 2013-01-01
    python3 core/funds.py import report.txt --scheme 120716      # a history file saved by hand
    python3 core/funds.py show 120716

The store keeps fund_nav(scheme_code, date, nav, name, isin): NAV in rupees
as published, keyed on the NAV date. A fund's NAV already has its
dividends reinvested (growth option) and its expenses taken out.
"""

import argparse
import datetime as dt
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bhavcopy  # noqa: E402

LIST_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=%s&todt=%s"
RAW = os.path.join(bhavcopy.ROOT, "data", "funds")
WINDOW_DAYS = 30  # history is asked for in windows, every scheme at once: 90 days timed out (2026-10-29)
TIMEOUT = 300     # seconds; AMFI is slow to build a window's report
SCHEMA = """
CREATE TABLE IF NOT EXISTS fund_nav (
  scheme_code TEXT NOT NULL, date TEXT NOT NULL, nav TEXT NOT NULL, name TEXT, isin TEXT,
  PRIMARY KEY (scheme_code, date));
"""
COLUMNS = {"scheme code": "code", "scheme name": "name", "nav name": "name", "net asset value": "nav", "date": "date",
           "plan": "plan", "option": "option",
           "isin div payout/ isin growth": "isin", "isin div payout/isin growth": "isin",
           "isin div payout/isingrowth": "isin"}


class FundsError(Exception):
    pass


def _day(text):
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse(text):
    """AMFI's semicolon text (today's list or a history report) -> [{"code", "name", "isin", "nav", "date"}].
    Lines with no semicolon (AMC and category headings, blanks) are skipped; rows whose NAV is not a
    number ("N.A.") are dropped."""
    header, out = None, []
    for line in text.splitlines():
        if ";" not in line:
            continue
        cells = [c.strip() for c in line.split(";")]
        if header is None:
            header = {COLUMNS[c.lower()]: i for i, c in enumerate(cells) if c.lower() in COLUMNS}
            missing = {"code", "name", "nav", "date"} - set(header)
            if missing:
                raise FundsError("not an AMFI NAV file: no %s column in %r" % (", ".join(sorted(missing)), line[:120]))
            continue
        if cells[header["code"]].lower() == "scheme code":
            continue  # the header repeated further down
        try:
            nav = Decimal(cells[header["nav"]].replace(",", ""))
        except (InvalidOperation, IndexError):
            continue
        day = _day(cells[header["date"]]) if len(cells) > header["date"] else None
        if nav <= 0 or not day:
            continue
        cell = lambda k: cells[header[k]] if k in header and len(cells) > header[k] else ""
        # today's list has Plan and Option columns (checked 2026-10-28); without them they are in the name
        name = cell("name")
        for extra in (cell("plan"), cell("option")):
            if extra and extra != "-" and extra.lower() not in name.lower():
                name += " - " + extra
        out.append({"code": cell("code"), "name": name, "isin": cell("isin"),
                    "nav": format(nav.normalize(), "f"), "date": day})
    if header is None:
        raise FundsError("not an AMFI NAV file: no header line with semicolons")
    return out


def find(rows, words, direct=False, growth=False):
    """Schemes whose name contains the phrase (case and spacing ignored), so "nifty 50 index" does not
    match a Nifty Next 50 fund: [(code, name)] sorted by name."""
    want = " ".join(words.lower().split())
    seen = {}
    for r in rows:
        n = " ".join(r["name"].lower().split())
        if want in n and (not direct or "direct" in n) and (not growth or "growth" in n):
            seen[r["code"]] = r["name"]
    return sorted(seen.items(), key=lambda kv: kv[1])


BUSY_MS = 60000  # wait up to a minute for another writer (a download in another window) before failing


def connect(path=bhavcopy.DEFAULT_DB):
    db = bhavcopy.connect(path)
    db.execute("PRAGMA busy_timeout = %d" % BUSY_MS)
    db.executescript(SCHEMA)
    return db


def store(db, rows, codes=None):
    keep = [r for r in rows if not codes or r["code"] in codes]
    db.executemany("INSERT OR REPLACE INTO fund_nav VALUES (:code, :date, :nav, :name, :isin)", keep)
    db.commit()
    return len(keep)


def history(db, code):
    """[(date, Decimal nav)] for one scheme, oldest first."""
    return [(d, Decimal(n)) for d, n in db.execute(
        "SELECT date, nav FROM fund_nav WHERE scheme_code = ? ORDER BY date", (code,))]


def slow_get(url, tries=3, timeout=TIMEOUT):
    """Bytes, or None for a 404, with a long timeout: AMFI takes minutes for a large window."""
    req = urllib.request.Request(url, headers={"User-Agent": bhavcopy.UA})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == tries - 1:
                raise
        except (urllib.error.URLError, OSError):
            if i == tries - 1:
                raise
        time.sleep(5 * (i + 1))


def fetch(db, codes, start, end=None, get=slow_get, pause=1.0, log=print, raw=RAW):
    """Download the history in windows and keep the given schemes: (NAVs stored, windows that failed).
    Each window's raw answer is cached in data/funds/; a window already cached and fully in the past
    is not asked for again. A window that fails is logged and skipped; the next run asks for it again."""
    a = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end) if end else dt.date.today()
    n, failed = 0, []
    os.makedirs(raw, exist_ok=True)
    while a <= end_d:
        b = min(a + dt.timedelta(days=WINDOW_DAYS - 1), end_d)
        path = os.path.join(raw, "history_%s_%s.txt" % (a.isoformat(), b.isoformat()))
        if os.path.exists(path) and b < dt.date.today() - dt.timedelta(days=7):
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            try:
                blob = get(HISTORY_URL % (a.strftime("%d-%b-%Y"), b.strftime("%d-%b-%Y")))
            except (urllib.error.URLError, OSError) as e:
                failed.append("%s to %s" % (a, b))
                if log:
                    log("%s to %s: failed (%s); run again to retry" % (a, b, e))
                a = b + dt.timedelta(days=1)
                continue
            text = (blob or b"").decode("utf-8", errors="replace")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            time.sleep(pause)
        got = store(db, parse(text), set(codes)) if text.strip() else 0
        n += got
        if log:
            log("%s to %s: %d NAVs kept" % (a, b, got))
        a = b + dt.timedelta(days=1)
    return n, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description="AMFI mutual fund NAVs into the store.")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="download today's NAV list to data/funds/NAVAll.txt")
    f = sub.add_parser("find", help="search today's list for a phrase in the scheme name")
    f.add_argument("words")
    f.add_argument("--direct", action="store_true")
    f.add_argument("--growth", action="store_true")
    f.add_argument("--file", default=os.path.join(RAW, "NAVAll.txt"))
    g = sub.add_parser("fetch", help="download NAV history for schemes")
    g.add_argument("--scheme", nargs="+", required=True)
    g.add_argument("--start", required=True)
    g.add_argument("--end")
    i = sub.add_parser("import", help="store a NAV file saved by hand")
    i.add_argument("file")
    i.add_argument("--scheme", nargs="*", default=[])
    s = sub.add_parser("show", help="a scheme's stored NAV history in brief")
    s.add_argument("scheme")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            blob = bhavcopy.http_get(LIST_URL)
            if not blob:
                raise FundsError("AMFI returned nothing for %s" % LIST_URL)
            os.makedirs(RAW, exist_ok=True)
            with open(os.path.join(RAW, "NAVAll.txt"), "wb") as fh:
                fh.write(blob)
            print("%d schemes in today's list" % len({r["code"] for r in parse(blob.decode("utf-8", "replace"))}))
            return 0
        if a.cmd == "find":
            if not os.path.exists(a.file):
                raise FundsError("no list at %s: run `funds.py list` first" % a.file)
            with open(a.file, encoding="utf-8", errors="replace") as fh:
                hits = find(parse(fh.read()), a.words, a.direct, a.growth)
            for code, name in hits:
                print("%-8s %s" % (code, name))
            print("%d scheme(s)" % len(hits))
            return 0
        db = connect(a.db)
        if a.cmd == "fetch":
            n, failed = fetch(db, a.scheme, a.start, a.end)
            print("%d NAVs stored%s" % (n, "; %d window(s) failed, run the same command again to retry them" % len(failed)
                                        if failed else ""))
        elif a.cmd == "import":
            with open(a.file, encoding="utf-8", errors="replace") as fh:
                n = store(db, parse(fh.read()), set(a.scheme))
            print("%d NAVs stored" % n)
        else:
            h = history(db, a.scheme)
            if not h:
                raise FundsError("no NAVs stored for scheme %s" % a.scheme)
            name = db.execute("SELECT name FROM fund_nav WHERE scheme_code = ? ORDER BY date DESC LIMIT 1",
                              (a.scheme,)).fetchone()[0]
            print("%s %s: %d NAVs from %s (%s) to %s (%s)" % (a.scheme, name, len(h), h[0][0], h[0][1], h[-1][0], h[-1][1]))
        db.close()
        return 0
    except FundsError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
