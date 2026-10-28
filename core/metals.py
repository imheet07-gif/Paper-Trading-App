#!/usr/bin/env python3
"""Gold and silver: what is listed on NSE, and the physical metal the owner bought. Standard library only.

Two parts, both offline:

  listed   Sovereign Gold Bonds (NSE series GB) from the market store, and
           gold and silver ETFs named in NSE's ETF list, a file the owner
           downloads (archives' eq_etfseclist.csv, to verify on the Mac):
           last close, its date, and the return over 1, 3 and 12 months
  ledger   physical gold and silver the owner bought (coins, bars,
           jewellery), kept in trader/metals.json: grams, purity as
           fineness in parts per thousand (999, 995, 916, ...), price
           paid, making charges and GST, each in rupees as on the bill;
           valued at rates the owner records in trader/metal_rates.json
           from a published source (for example IBJA), dated and named

    python3 core/metals.py listed
    python3 core/metals.py listed --etf-list ~/Downloads/eq_etfseclist.csv
    python3 core/metals.py ledger add --date 2026-10-25 --metal gold --form coin --grams 10 \\
        --fineness 999 --price 78000 --making 1200 --gst 2376 --seller "the shop"
    python3 core/metals.py ledger list
    python3 core/metals.py ledger value

Nothing here buys or sells anything; the ledger records what the owner
bought. No rate, purity convention or scheme term is assumed: a rate must be
recorded for the exact metal and fineness held, or valuation says it is
missing. Money is Decimal.
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import sqlite3
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from money import D, inr, paise  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "market.db")
LEDGER = "trader/metals.json"
RATES = "trader/metal_rates.json"
METALS = ("gold", "silver")
FORMS = ("coin", "bar", "jewellery", "other")
SGB_SERIES = "GB"
THOUSAND = Decimal(1000)
LOOKBACKS = {"1m": 21, "3m": 63, "12m": 252}


class MetalsError(Exception):
    pass


# ---------------------------------------------------------------- listed: SGBs and ETFs from the store

def etf_symbols(path):
    """Gold and silver ETFs in NSE's ETF list: {symbol: (metal, name)}. The list's column names
    are matched loosely (symbol, and a name or underlying column), since the file is to verify."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(io.StringIO(fh.read())))
    if not rows:
        raise MetalsError("%s has no rows" % path)
    cols = {k.strip().lower(): k for k in rows[0] if k}
    sym = next((cols[c] for c in cols if c == "symbol"), None)
    text_cols = [cols[c] for c in cols if c in ("underlying", "securityname", "security name", "name", "assetclass")]
    if not sym or not text_cols:
        raise MetalsError("%s: expected a Symbol column and a name or underlying column, found %s"
                          % (path, ", ".join(cols)))
    out = {}
    for r in rows:
        blob = " ".join((r.get(c) or "") for c in text_cols).upper()
        metal = "gold" if "GOLD" in blob else "silver" if "SILVER" in blob else None
        if metal and (r.get(sym) or "").strip():
            out[r[sym].strip().upper()] = (metal, " ".join((r.get(c) or "").strip() for c in text_cols).strip())
    return out


def listed(db, etfs=None, as_of=None):
    """[{kind, symbol, isin, metal, close, date, return_1m, return_3m, return_12m}] for SGBs and the given ETFs.
    Returns are on closes and ignore distributions and interest; prices in rupees."""
    etfs = etfs or {}
    where = "series = ?"
    args = [SGB_SERIES]
    if etfs:
        where = "(series = ? OR (series = 'EQ' AND symbol IN (%s)))" % ",".join("?" * len(etfs))
        args += sorted(etfs)
    if as_of:
        where += " AND date <= ?"
        args.append(as_of)
    q = "SELECT isin, symbol, series, date, close FROM prices WHERE %s ORDER BY isin, date" % where
    hist = {}
    for isin, sym, series, day, close in db.execute(q, args):
        hist.setdefault((isin, series), []).append((day, sym, close))
    out = []
    for (isin, series), rows in hist.items():
        day, sym, close = rows[-1]
        r = {"kind": "SGB" if series == SGB_SERIES else "ETF", "symbol": sym, "isin": isin,
             "metal": "gold" if series == SGB_SERIES else etfs.get(sym, ("?",))[0],
             "close": str(Decimal(close) / 100), "date": day}
        for k, n in LOOKBACKS.items():
            r["return_" + k] = (str(((Decimal(close) / Decimal(rows[-1 - n][2]) - 1) * 100).quantize(Decimal("0.1")))
                                if len(rows) > n and rows[-1 - n][2] else None)
        out.append(r)
    out.sort(key=lambda r: (r["kind"], r["symbol"]))
    return out


# ---------------------------------------------------------------- ledger: physical metal

def _dec(x, what, positive=True):
    try:
        v = D(x)
    except (InvalidOperation, TypeError, ValueError):
        raise MetalsError("%s must be a number in rupees or grams, not %r" % (what, x))
    if v < 0 or (positive and v == 0):
        raise MetalsError("%s must be %s" % (what, "above zero" if positive else "zero or more"))
    return v


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise MetalsError("%s could not be read: %s" % (path, e))


def save_json(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def purchase(date, metal, form, grams, fineness, price, making="0", gst="0", seller="", note=""):
    """One bill as a ledger entry. price is the metal's price on the bill, before making and GST."""
    try:
        day = dt.date.fromisoformat(date).isoformat()
    except (TypeError, ValueError):
        raise MetalsError("date must be YYYY-MM-DD")
    if metal not in METALS:
        raise MetalsError("metal must be gold or silver")
    if form not in FORMS:
        raise MetalsError("form must be one of %s" % ", ".join(FORMS))
    g, f = _dec(grams, "grams"), _dec(fineness, "fineness")
    if f > THOUSAND:
        raise MetalsError("fineness is parts per thousand, at most 1000 (999 for 24 carat bullion)")
    return {"date": day, "metal": metal, "form": form, "grams": str(g), "fineness": str(f),
            "price": str(paise(_dec(price, "price"))), "making": str(paise(_dec(making, "making", False))),
            "gst": str(paise(_dec(gst, "gst", False))), "seller": seller, "note": note}


def cost_basis(e):
    """Total paid, fine metal in grams, and the cost per fine gram, from one entry."""
    total = D(e["price"]) + D(e["making"]) + D(e["gst"])
    fine = D(e["grams"]) * D(e["fineness"]) / THOUSAND
    return {"paid": paise(total), "fine_grams": fine.quantize(Decimal("0.0001")),
            "per_fine_gram": paise(total / fine), "extra_pct": ((D(e["making"]) + D(e["gst"])) / D(e["price"]) * 100
                                                               ).quantize(Decimal("0.1"), ROUND_HALF_UP)}


def rate_for(rates, metal, fineness):
    """The recorded rate per gram for this metal at exactly this fineness: (Decimal, date, source) or None."""
    for r in (rates or {}).get("rates", []):
        if r.get("metal") == metal and D(r.get("fineness", "0")) == D(fineness):
            return D(r["per_gram"]), rates.get("date"), rates.get("source")
    return None


def value(entries, rates):
    """Each entry at the recorded rate for its metal and fineness, and the totals.
    The value is the metal at the published rate: what a seller is paid is usually less."""
    rows, paid, worth, missing = [], Decimal(0), Decimal(0), []
    for i, e in enumerate(entries, 1):
        c = cost_basis(e)
        rt = rate_for(rates, e["metal"], e["fineness"])
        r = dict(e, n=i, **{k: str(v) for k, v in c.items()})
        paid += c["paid"]
        if rt is None:
            r["value"] = None
            missing.append("%s %s" % (e["metal"], e["fineness"]))
        else:
            v = paise(D(e["grams"]) * rt[0])
            r["value"], r["gain"] = str(v), str(v - c["paid"])
            worth += v
        rows.append(r)
    return {"rows": rows, "paid": str(paise(paid)), "value": str(paise(worth)) if not missing else None,
            "rates_date": (rates or {}).get("date"), "rates_source": (rates or {}).get("source"),
            "missing_rates": sorted(set(missing))}


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description="Listed gold and silver (SGBs, ETFs) and the owner's physical metal.")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    li = sub.add_parser("listed", help="SGBs and gold or silver ETFs from the market store")
    li.add_argument("--db", default=DEFAULT_DB)
    li.add_argument("--etf-list", help="NSE's ETF list (CSV) to pick out gold and silver ETFs")
    li.add_argument("--as-of")
    lg = sub.add_parser("ledger", help="physical gold and silver bought")
    lg.add_argument("--file", default=LEDGER)
    lg.add_argument("--rates", default=RATES)
    lsub = lg.add_subparsers(dest="action", required=True)
    add = lsub.add_parser("add")
    for k in ("date", "metal", "form", "grams", "fineness", "price"):
        add.add_argument("--" + k, required=True)
    for k in ("making", "gst"):
        add.add_argument("--" + k, default="0")
    add.add_argument("--seller", default="")
    add.add_argument("--note", default="")
    rm = lsub.add_parser("remove")
    rm.add_argument("n", type=int, help="the entry's number from 'ledger list'")
    lsub.add_parser("list")
    lsub.add_parser("value")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "listed":
            if not os.path.exists(a.db):
                raise MetalsError("no market store at %s" % a.db)
            db = sqlite3.connect(a.db)
            etfs = etf_symbols(a.etf_list) if a.etf_list else {}
            rows = listed(db, etfs, a.as_of)
            db.close()
            if a.json:
                print(json.dumps(rows, indent=1))
                return 0
            for r in rows:
                print("%-4s %-12s %-6s %s on %s  1m %s%%  3m %s%%  12m %s%%" % (
                    r["kind"], r["symbol"], r["metal"], inr(r["close"]), r["date"],
                    r["return_1m"] or "-", r["return_3m"] or "-", r["return_12m"] or "-"))
            if not etfs:
                print("ETFs: pass --etf-list with NSE's ETF list to include gold and silver ETFs")
            print("Returns are on closing prices: SGB interest and ETF costs are not in them.")
            return 0
        doc = load_json(a.file, {"entries": []})
        entries = doc.setdefault("entries", [])
        if a.action == "add":
            entries.append(purchase(a.date, a.metal, a.form, a.grams, a.fineness, a.price, a.making, a.gst,
                                    a.seller, a.note))
            save_json(a.file, doc)
            c = cost_basis(entries[-1])
            print("recorded #%d: %s g of %s at %s fineness, paid %s, %s a fine gram (%s%% over the metal price)" % (
                len(entries), entries[-1]["grams"], a.metal, entries[-1]["fineness"], inr(c["paid"]),
                inr(c["per_fine_gram"]), c["extra_pct"]))
            return 0
        if a.action == "remove":
            if not 1 <= a.n <= len(entries):
                raise MetalsError("no entry #%d" % a.n)
            gone = entries.pop(a.n - 1)
            save_json(a.file, doc)
            print("removed #%d (%s, %s g %s)" % (a.n, gone["date"], gone["grams"], gone["metal"]))
            return 0
        res = value(entries, load_json(a.rates, None) if a.action == "value" else None)
        if a.json:
            print(json.dumps(res, indent=1, ensure_ascii=False))
            return 0
        for r in res["rows"]:
            line = "#%d %s %s %s, %s g at %s, paid %s (%s a fine gram, %s%% making and GST)" % (
                r["n"], r["date"], r["metal"], r["form"], r["grams"], r["fineness"], inr(r["paid"]),
                inr(r["per_fine_gram"]), r["extra_pct"])
            if a.action == "value" and r["value"]:
                line += ", worth %s at the recorded rate (%s)" % (inr(r["value"]), inr(r["gain"]))
            print(line)
        print("paid in total %s" % inr(res["paid"]))
        if a.action == "value":
            if res["missing_rates"]:
                print("no recorded rate for: %s (add them to %s with the date and source)" % (
                    ", ".join(res["missing_rates"]), a.rates))
            else:
                print("worth %s at %s's rates from %s; a buyer usually pays less than the published rate" % (
                    inr(res["value"]), res["rates_date"], res["rates_source"]))
        return 0
    except MetalsError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
