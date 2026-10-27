#!/usr/bin/env python3
"""NSE daily market data into a local SQLite store: the research history.

Sources, all public NSE archives:

  bhavcopy, old format   content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip
                         (to 2024-07-05; ISIN column from late 2011)
  bhavcopy, UDiFF format content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
                         (from 2024-07-08)
  delivery position      archives/equities/mto/MTO_DDMMYYYY.DAT
  index closes           content/indices/ind_close_all_DDMMYYYY.csv
                         (Nifty 50, Nifty 500, sector and size indices, India
                         VIX, with P/E, P/B and dividend yield; from 2013)

Every listed security is kept, including the ones that later delist, so a
backtest sees the market as it was (no survivorship bias). Prices are
stored as integer paise, exact and compact. A day with no file is recorded
as missing (weekend or holiday), so a re-run fetches nothing twice.

Prices here are exactly as NSE published them, unadjusted. NSE does not
adjust its previous close on an ex-date either, so splits and bonuses show
as price jumps until corporate_actions.py applies its factors.

  python3 core/bhavcopy.py backfill --start 2012-01-01 --end 2026-10-27
  python3 core/bhavcopy.py indices --start 2013-01-01
  python3 core/bhavcopy.py status
  python3 core/bhavcopy.py series INE002A01018

The store is data/market.db by default (git-ignored). A full backfill is
about 3,700 trading days; at the default pause it takes a few hours.
"""

import argparse
import csv
import datetime as dt
import io
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import zipfile
from decimal import ROUND_HALF_UP, Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "market.db")
ARCHIVE = "https://nsearchives.nseindia.com"
UDIFF_FROM = dt.date(2024, 7, 8)
ISIN_FROM = dt.date(2011, 11, 1)  # old-format files carry ISIN from about here
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
  date TEXT NOT NULL, isin TEXT NOT NULL, symbol TEXT NOT NULL, series TEXT NOT NULL,
  open INTEGER, high INTEGER, low INTEGER, close INTEGER, last INTEGER, prev_close INTEGER,
  volume INTEGER, value INTEGER, trades INTEGER, deliv_qty INTEGER, deliv_bp INTEGER,
  PRIMARY KEY (date, isin, series));
CREATE INDEX IF NOT EXISTS prices_isin ON prices (isin, date);
CREATE TABLE IF NOT EXISTS index_prices (
  date TEXT NOT NULL, name TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL,
  pe REAL, pb REAL, div_yield REAL, PRIMARY KEY (date, name));
CREATE TABLE IF NOT EXISTS fetched (
  date TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, rows INTEGER, url TEXT,
  PRIMARY KEY (date, kind));
"""


# ---------------------------------------------------------------- parsing

def paise(s):
    """'581.1' -> 58110. Empty -> None."""
    s = (s or "").strip()
    if not s or s == "-":
        return None
    return int((Decimal(s) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _int(s):
    s = (s or "").strip()
    return int(Decimal(s)) if s and s != "-" else None


def _clean(row):
    return {(k or "").strip(): (v or "").strip() for k, v in row.items()}


def old_date(s):
    """'13-JUL-2020' or, in some 2020 files, '13-Jul-20' -> '2020-07-13'."""
    for fmt in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("unreadable bhavcopy date %r" % s)


def parse_old(text):
    """Old-format bhavcopy -> rows. Files before late 2011 have no ISIN and give no rows."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = _clean(r)
        isin = r.get("ISIN", "")
        if not isin:
            continue
        out.append({
            "date": old_date(r["TIMESTAMP"]),
            "isin": isin, "symbol": r["SYMBOL"], "series": r["SERIES"],
            "open": paise(r["OPEN"]), "high": paise(r["HIGH"]), "low": paise(r["LOW"]),
            "close": paise(r["CLOSE"]), "last": paise(r["LAST"]), "prev_close": paise(r["PREVCLOSE"]),
            "volume": _int(r["TOTTRDQTY"]), "value": paise(r["TOTTRDVAL"]), "trades": _int(r.get("TOTALTRADES")),
        })
    return out


def parse_udiff(text):
    """UDiFF bhavcopy -> rows, cash-market securities only (FinInstrmTp STK)."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = _clean(r)
        if r.get("FinInstrmTp") != "STK" or not r.get("ISIN"):
            continue
        out.append({
            "date": r["TradDt"], "isin": r["ISIN"], "symbol": r["TckrSymb"], "series": r["SctySrs"],
            "open": paise(r["OpnPric"]), "high": paise(r["HghPric"]), "low": paise(r["LwPric"]),
            "close": paise(r["ClsPric"]), "last": paise(r["LastPric"]), "prev_close": paise(r["PrvsClsgPric"]),
            "volume": _int(r["TtlTradgVol"]), "value": paise(r["TtlTrfVal"]), "trades": _int(r.get("TtlNbOfTxsExctd")),
        })
    return out


def parse_mto(text):
    """Delivery position file -> {(symbol, series): (deliverable qty, delivery % in basis points)}."""
    out = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7 or parts[0] != "20":
            continue
        try:
            out[(parts[2], parts[3])] = (int(parts[5]), int((Decimal(parts[6]) * 100).to_integral_value()))
        except Exception:
            continue  # "NA" rows for securities with no delivery
    return out


def _real(s):
    s = (s or "").strip()
    try:
        return float(s) if s and s != "-" else None
    except ValueError:
        return None


def parse_indices(text):
    """Index close file -> rows. Answers that are not CSV (an HTML error page) give no rows."""
    if not text.lstrip().startswith("Index Name"):
        return []
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = _clean(r)
        try:
            d = dt.datetime.strptime(r["Index Date"], "%d-%m-%Y").date().isoformat()
        except (KeyError, ValueError):
            continue
        out.append({"date": d, "name": r["Index Name"], "open": _real(r.get("Open Index Value")),
                    "high": _real(r.get("High Index Value")), "low": _real(r.get("Low Index Value")),
                    "close": _real(r.get("Closing Index Value")), "pe": _real(r.get("P/E")),
                    "pb": _real(r.get("P/B")), "div_yield": _real(r.get("Div Yield"))})
    return out


def unzip_one(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(z.namelist()[0]).decode("utf-8", "replace")


# ---------------------------------------------------------------- fetching

def urls(d):
    """{"prices": url, "delivery": url} for a date."""
    if d >= UDIFF_FROM:
        p = "%s/content/cm/BhavCopy_NSE_CM_0_0_0_%s_F_0000.csv.zip" % (ARCHIVE, d.strftime("%Y%m%d"))
    else:
        mon = MONTHS[d.month - 1]
        p = "%s/content/historical/EQUITIES/%d/%s/cm%02d%s%dbhav.csv.zip" % (ARCHIVE, d.year, mon, d.day, mon, d.year)
    return {"prices": p, "delivery": "%s/archives/equities/mto/MTO_%s.DAT" % (ARCHIVE, d.strftime("%d%m%Y")),
            "indices": "%s/content/indices/ind_close_all_%s.csv" % (ARCHIVE, d.strftime("%d%m%Y"))}


def http_get(url, tries=4):
    """Bytes, or None for a 404 (no file that day). Retries other failures with backoff."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.nseindia.com/"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == tries - 1:
                raise
        time.sleep(2 ** (i + 1))


# ---------------------------------------------------------------- store

def connect(path=DEFAULT_DB):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def done(db, d, kind):
    row = db.execute("SELECT status FROM fetched WHERE date=? AND kind=?", (d.isoformat(), kind)).fetchone()
    return row is not None


def store_day(db, rows, delivery=None):
    delivery = delivery or {}
    db.executemany(
        "INSERT OR REPLACE INTO prices VALUES (:date,:isin,:symbol,:series,:open,:high,:low,:close,:last,"
        ":prev_close,:volume,:value,:trades,:deliv_qty,:deliv_bp)",
        [dict(r, deliv_qty=delivery.get((r["symbol"], r["series"]), (None, None))[0],
              deliv_bp=delivery.get((r["symbol"], r["series"]), (None, None))[1]) for r in rows])


def fetch_day(d, get=http_get):
    """Download one date's files: (prices bytes or None, delivery bytes or None)."""
    u = urls(d)
    blob = get(u["prices"])
    return blob, (get(u["delivery"]) if blob is not None else None)


def store_fetched(db, d, blob, deliv_blob):
    """Parse and store one date's downloads. Returns rows stored (0 for no file)."""
    u = urls(d)
    if blob is None:
        db.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)", (d.isoformat(), "prices", "missing", 0, u["prices"]))
        db.commit()
        return 0
    text = unzip_one(blob)
    rows = parse_udiff(text) if d >= UDIFF_FROM else parse_old(text)
    delivery = parse_mto(deliv_blob.decode("utf-8", "replace")) if deliv_blob else {}
    store_day(db, rows, delivery)
    db.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)", (d.isoformat(), "prices", "ok", len(rows), u["prices"]))
    db.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)",
               (d.isoformat(), "delivery", "ok" if deliv_blob else "missing", len(delivery), u["delivery"]))
    db.commit()
    return len(rows)


def ingest_day(db, d, get=http_get):
    """Fetch, parse and store one date. None if already done, else rows stored."""
    if done(db, d, "prices"):
        return None
    return store_fetched(db, d, *fetch_day(d, get))


def backfill(db, start, end, pause=0.4, get=http_get, log=print, workers=1):
    """Every weekday in [start, end] not yet fetched. Downloads run on
    `workers` threads; this thread alone writes to the database."""
    todo, d = [], start
    while d <= end:
        if d.weekday() < 5 and not done(db, d, "prices"):
            todo.append(d)
        d += dt.timedelta(days=1)

    def job(day):
        got = fetch_day(day, get)
        time.sleep(pause)
        return got

    if workers <= 1:
        results = ((day, job(day)) for day in todo)
    else:
        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=workers)
        results = zip(todo, pool.map(job, todo))
    n = 0
    try:
        for day, (blob, deliv) in results:
            got = store_fetched(db, day, blob, deliv)
            n += 1
            if log and (got == 0 or n % 50 == 0):
                log("%s %s" % (day, "no file" if got == 0 else "%d rows" % got))
    finally:
        if workers > 1:
            pool.shutdown(wait=True)
    return n


def backfill_indices(db, start, end, pause=0.4, get=http_get, workers=4):
    """Index closes for every trading day already in the store that lacks them."""
    todo = [dt.date.fromisoformat(d) for (d,) in db.execute(
        "SELECT date FROM fetched f WHERE kind='prices' AND status='ok' AND date BETWEEN ? AND ? AND NOT EXISTS "
        "(SELECT 1 FROM fetched g WHERE g.date=f.date AND g.kind='indices') ORDER BY date",
        (start.isoformat(), end.isoformat()))]

    def job(day):
        blob = get(urls(day)["indices"])
        time.sleep(pause)
        return blob

    from concurrent.futures import ThreadPoolExecutor
    n = 0
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        for day, blob in zip(todo, pool.map(job, todo)):
            rows = parse_indices(blob.decode("utf-8", "replace")) if blob else []
            db.executemany("INSERT OR REPLACE INTO index_prices VALUES (:date,:name,:open,:high,:low,:close,:pe,:pb,"
                           ":div_yield)", rows)
            db.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)",
                       (day.isoformat(), "indices", "ok" if rows else "missing", len(rows), urls(day)["indices"]))
            db.commit()
            n += 1
    return n


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE daily market data into SQLite.")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--start", required=True)
    b.add_argument("--end", default=dt.date.today().isoformat())
    b.add_argument("--pause", type=float, default=0.4, help="seconds each download thread waits between days")
    b.add_argument("--workers", type=int, default=4, help="parallel downloads (keep it small: NSE is shared)")
    ix = sub.add_parser("indices", help="index closes for trading days already stored")
    ix.add_argument("--start", default="2013-01-01")
    ix.add_argument("--end", default=dt.date.today().isoformat())
    ix.add_argument("--workers", type=int, default=4)
    sub.add_parser("status")
    s = sub.add_parser("series")
    s.add_argument("isin")
    a = ap.parse_args(argv)
    db = connect(a.db)
    if a.cmd == "backfill":
        start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
        if start < ISIN_FROM:
            print("note: files before %s have no ISIN; those days store no rows" % ISIN_FROM, file=sys.stderr)
        n = backfill(db, start, end, a.pause, workers=a.workers)
        print("fetched %d new day(s)" % n)
    elif a.cmd == "indices":
        n = backfill_indices(db, dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end), workers=a.workers)
        print("fetched index closes for %d day(s)" % n)
    elif a.cmd == "status":
        r = db.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(DISTINCT isin), COUNT(*) FROM prices").fetchone()
        m = db.execute("SELECT COUNT(*) FROM fetched WHERE kind='prices' AND status='missing'").fetchone()[0]
        print("prices %s to %s: %d trading days, %d securities, %d rows; %d weekday(s) with no file" % (r + (m,)))
    else:
        for d, s_, c in db.execute("SELECT date, series, close FROM prices WHERE isin=? ORDER BY date", (a.isin,)):
            print(d, s_, Decimal(c) / 100 if c is not None else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
