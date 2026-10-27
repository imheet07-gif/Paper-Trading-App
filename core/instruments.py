#!/usr/bin/env python3
"""One record per listed stock, keyed by ISIN.

Three public files are joined on the NSE symbol:

  NSE equity list   https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
                    symbol, name, series, listing date, ISIN, face value
  Kite instruments  https://api.kite.trade/instruments  (no login needed)
                    instrument_token, exchange_token, tick_size, lot_size
  Angel One scrips  https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
                    SmartAPI token; tick_size there is in paise

ISIN is the key because symbols change (renames, mergers) and the same
company has different codes on NSE, BSE and each broker. The broker files
carry no ISIN, so they join through the NSE symbol and series. BSE codes
need BSE's own scrip list, which carries ISIN; that join comes later.

Download the three files into private/ (or anywhere), then:

  python3 core/instruments.py build --nse EQUITY_L.csv --kite instruments.csv \
      --angel OpenAPIScripMaster.json --out data/instruments.json
  python3 core/instruments.py find RELIANCE
  python3 core/instruments.py find INE002A01018
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "data", "instruments.json")


def _clean_header(row):
    return {(k or "").strip().upper(): (v or "").strip() for k, v in row.items()}


def parse_nse_equity_list(text):
    """EQUITY_L.csv -> {isin: {...}}. Its headers carry stray spaces; they are stripped."""
    out = {}
    for raw in csv.DictReader(io.StringIO(text)):
        r = _clean_header(raw)
        isin = r.get("ISIN NUMBER", "")
        sym = r.get("SYMBOL", "")
        if not isin or not sym:
            continue
        listed = r.get("DATE OF LISTING", "")
        try:
            listed = dt.datetime.strptime(listed, "%d-%b-%Y").date().isoformat()
        except ValueError:
            pass
        out[isin] = {
            "isin": isin,
            "nse_symbol": sym,
            "name": r.get("NAME OF COMPANY", ""),
            "series": r.get("SERIES", ""),
            "listed": listed,
            "face_value": r.get("FACE VALUE", ""),
        }
    return out


def parse_kite_instruments(text):
    """Kite's instrument dump -> {(tradingsymbol): row} for NSE cash-market equities."""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("exchange") != "NSE" or r.get("segment") != "NSE" or r.get("instrument_type") != "EQ":
            continue
        out[r["tradingsymbol"]] = {
            "kite_token": int(r["instrument_token"]),
            "kite_exchange_token": int(r["exchange_token"]),
            "tick_size": r["tick_size"],
            "lot_size": int(r["lot_size"] or 1),
        }
    return out


def parse_angel_scrips(text):
    """Angel One's scrip master -> {symbol like 'RELIANCE-EQ': row} for NSE. Tick size is paise."""
    out = {}
    for r in json.loads(text):
        if r.get("exch_seg") != "NSE" or r.get("instrumenttype"):
            continue
        sym = r.get("symbol", "")
        if "-" not in sym:
            continue
        tick_paise = Decimal(r.get("tick_size") or "0")
        out[sym] = {
            "angel_token": r["token"],
            "angel_tick_size": str((tick_paise / 100).normalize()) if tick_paise else None,
            "angel_lot_size": int(Decimal(r.get("lotsize") or "1")),
        }
    return out


def kite_symbol(nse_symbol, series):
    """Kite lists EQ series under the bare symbol and others as SYMBOL-SERIES."""
    return nse_symbol if series == "EQ" else "%s-%s" % (nse_symbol, series)


def build(nse_text, kite_text=None, angel_text=None):
    stocks = parse_nse_equity_list(nse_text)
    kite = parse_kite_instruments(kite_text) if kite_text else {}
    angel = parse_angel_scrips(angel_text) if angel_text else {}
    problems = []
    for isin, s in stocks.items():
        k = kite.get(kite_symbol(s["nse_symbol"], s["series"]))
        if k:
            s.update(k)
        elif kite_text:
            problems.append("%s %s: not in Kite's list" % (isin, s["nse_symbol"]))
        a = angel.get("%s-%s" % (s["nse_symbol"], s["series"]))
        if a:
            s.update(a)
            if k and a["angel_tick_size"] and Decimal(a["angel_tick_size"]) != Decimal(k["tick_size"]):
                problems.append("%s %s: tick size differs, Kite %s vs Angel %s" % (
                    isin, s["nse_symbol"], k["tick_size"], a["angel_tick_size"]))
        elif angel_text:
            problems.append("%s %s: not in Angel One's list" % (isin, s["nse_symbol"]))
    return {"built": dt.date.today().isoformat(), "count": len(stocks),
            "sources": [n for n, t in (("nse", nse_text), ("kite", kite_text), ("angel", angel_text)) if t],
            "instruments": stocks, "problems": problems}


def find(registry, query):
    q = query.strip().upper()
    inst = registry["instruments"]
    if q in inst:
        return [inst[q]]
    exact = [s for s in inst.values() if s["nse_symbol"] == q]
    if exact:
        return exact
    return [s for s in inst.values() if q in s["name"].upper()][:20]


def _read(path):
    if not path:
        return None
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="ISIN-keyed instrument registry for NSE stocks.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--nse", required=True, help="EQUITY_L.csv")
    b.add_argument("--kite", help="Kite instruments CSV")
    b.add_argument("--angel", help="Angel One OpenAPIScripMaster.json")
    b.add_argument("--out", default=DEFAULT_OUT)
    f = sub.add_parser("find")
    f.add_argument("query", help="ISIN, NSE symbol or part of the name")
    f.add_argument("--registry", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        reg = build(_read(a.nse), _read(a.kite), _read(a.angel))
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(reg, fh, indent=1, sort_keys=True)
        print("%d instruments from %s -> %s" % (reg["count"], "+".join(reg["sources"]), a.out))
        for p in reg["problems"][:20]:
            print("  check:", p)
        if len(reg["problems"]) > 20:
            print("  ... and %d more" % (len(reg["problems"]) - 20))
        return 0
    if not os.path.exists(a.registry):
        print("error: no registry at %s; run build first" % a.registry, file=sys.stderr)
        return 2
    with open(a.registry, encoding="utf-8") as fh:
        reg = json.load(fh)
    hits = find(reg, a.query)
    for s in hits:
        print(json.dumps(s, sort_keys=True))
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
