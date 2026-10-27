#!/usr/bin/env python3
"""Bulk and block deals from NSE into the market store, collected daily.

Sources (NSE's file archive, the latest session only, no history):

  https://nsearchives.nseindia.com/content/equities/bulk.csv
  https://nsearchives.nseindia.com/content/equities/block.csv

Columns: Date, Symbol, Security Name, Client Name, Buy/Sell, Quantity
Traded, Trade Price / Wght. Avg. Price, and Remarks (bulk only). A day with
no deals is one row reading NO RECORDS.

A bulk deal is a client's trades in a stock that add up to more than 0.5% of
its shares in a session; a block deal is a single large trade in the block
window. NSE publishes both after the close, so a deal dated D is usable from
the session after D.

The files hold only the latest session: a session not collected that
evening is gone from this source. Each file is cached as published under
data/deals_raw/<kind>/<date>.csv, so the table can be rebuilt.

Client names are as NSE publishes them and include private individuals;
they stay in the local store (data/ is never committed). Test fixtures
replace individuals' names.

  python3 core/deals.py fetch        # after about 7 pm IST, with the day's other files
  python3 core/deals.py rebuild
  python3 core/deals.py status
"""

import argparse
import csv
import datetime as dt
import io
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bhavcopy  # noqa: E402
import events  # noqa: E402

URLS = {"bulk": "https://nsearchives.nseindia.com/content/equities/bulk.csv",
        "block": "https://nsearchives.nseindia.com/content/equities/block.csv"}
RAW = os.path.join(bhavcopy.ROOT, "data", "deals_raw")
SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
  kind TEXT NOT NULL, date TEXT NOT NULL, symbol TEXT NOT NULL, isin TEXT, security TEXT, client TEXT NOT NULL,
  side TEXT NOT NULL, qty INTEGER NOT NULL, price TEXT NOT NULL, remarks TEXT,
  PRIMARY KEY (kind, date, symbol, client, side, qty, price));
CREATE INDEX IF NOT EXISTS deals_isin ON deals (isin, date);
"""
SIDES = {"BUY": "buy", "SELL": "sell"}


def _col(row, *names):
    for n in names:
        for k, v in row.items():
            if (k or "").strip().lower() == n:
                return (v or "").strip()
    return ""


def parse(text, kind):
    """One published file -> (session date or None, [deal rows]). Rows that do not
    read as a deal (a bad date, side, quantity or price) are dropped."""
    if kind not in URLS:
        raise ValueError("kind must be bulk or block")
    out, day = [], None
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        d = events.day(_col(r, "date").title())
        if not d:
            continue  # the NO RECORDS row, or a footer
        side = SIDES.get(_col(r, "buy/sell").upper())
        qty_text = _col(r, "quantity traded").replace(",", "")
        price_text = _col(r, "trade price / wght. avg. price").replace(",", "")
        try:
            qty, price = int(qty_text), Decimal(price_text)
        except (ValueError, ArithmeticError):
            continue
        if not side or qty <= 0 or not price.is_finite() or price <= 0:
            continue
        day = day or d
        remarks = _col(r, "remarks")
        out.append({"kind": kind, "date": d, "symbol": _col(r, "symbol").upper(),
                    "security": _col(r, "security name"), "client": _col(r, "client name"), "side": side,
                    "qty": qty, "price": format(price.normalize(), "f"),
                    "remarks": None if remarks in ("", "-") else remarks})
    return day, out


def fetch(get=bhavcopy.http_get, raw=RAW, log=print):
    """Save today's two files under data/deals_raw/<kind>/<session date>.csv. A file
    with no deals is saved under no date and changes nothing. Returns {kind: deals}."""
    found = {}
    for kind, url in URLS.items():
        blob = get(url)
        if not blob:
            found[kind] = 0
            continue
        text = blob.decode("utf-8-sig", "replace")
        day, rows = parse(text, kind)
        found[kind] = len(rows)
        if day:
            path = os.path.join(raw, kind, day + ".csv")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        if log:
            log("%s deals: %d%s" % (kind, len(rows), (" for " + day) if day else " (none published)"))
    return found


def connect(path=bhavcopy.DEFAULT_DB):
    db = bhavcopy.connect(path)
    db.executescript(SCHEMA)
    return db


def rebuild(db, raw=RAW, log=print):
    """Every cached file into the deals table, each symbol given the ISIN it traded
    under that day (core/events.py's symbol index from the prices table)."""
    db.executescript("DROP TABLE IF EXISTS deals;" + SCHEMA)
    index = events.symbol_index(db)
    n, unmapped = 0, 0
    for kind in URLS:
        folder = os.path.join(raw, kind)
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            with open(os.path.join(folder, f), encoding="utf-8") as fh:
                _, rows = parse(fh.read(), kind)
            for r in rows:
                r["isin"] = events.isin_for(index, r["symbol"], r["date"])
                unmapped += r["isin"] is None
                db.execute("INSERT OR REPLACE INTO deals VALUES (:kind,:date,:symbol,:isin,:security,:client,:side,"
                           ":qty,:price,:remarks)", r)
                n += 1
    db.commit()
    if log:
        log("deals: %d stored (%d without a matching ISIN)" % (n, unmapped))
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE bulk and block deals into the store.")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    sub.add_parser("rebuild")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    db = connect(a.db)
    if a.cmd == "fetch":
        fetch()
        rebuild(db)
    elif a.cmd == "rebuild":
        rebuild(db)
    else:
        for r in db.execute("SELECT kind, COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM deals GROUP BY kind"):
            print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
