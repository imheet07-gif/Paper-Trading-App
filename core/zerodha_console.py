#!/usr/bin/env python3
"""Read Zerodha Console exports: holdings statements and the equity tradebook.

Console (console.zerodha.com) downloads these as .xlsx or .csv. Each sheet
starts with a header block (client ID, a title such as "Equity Holdings
Statement as on 2026-10-27", a summary) and then a table whose first column
is Symbol. Sections are found by their title row, not by sheet name, so a
one-sheet CSV and a three-sheet XLSX read the same way.

Apple Numbers files are not read. To redact a client ID, edit the download
in Numbers and use File > Export To > Excel (or CSV); that file reads fine,
including dates the export turns into spreadsheet serial numbers.

  python3 core/zerodha_console.py holdings holdings.xlsx
  python3 core/zerodha_console.py tradebook tradebook-EQ.xlsx
  python3 core/zerodha_console.py charges tradebook-EQ.xlsx --date 2026-08-06

Holdings come with warnings for anything that needs a person: a quantity
held at zero average price (usually a demerger or other corporate action
whose cost was not split yet), discrepant or pledged quantities.
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from money import D  # noqa: E402

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


# ---------------------------------------------------------------- reading files

def _col_index(ref):
    """'C12' -> 2."""
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    """{sheet name: [rows]} from an .xlsx, values as strings (or None)."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = {r.get("Id"): r.get("Target") for r in rels}
        out = {}
        for sh in wb.find("m:sheets", NS):
            t = target[sh.get(REL)].lstrip("/")
            part = t if t.startswith("xl/") else "xl/" + t
            root = ET.fromstring(z.read(part))
            rows = []
            for row in root.iter("{%s}row" % NS["m"]):
                cells = {}
                for c in row.findall("m:c", NS):
                    v = c.find("m:v", NS)
                    kind = c.get("t")
                    if kind == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
                    elif v is None:
                        val = None
                    elif kind == "s":
                        val = shared[int(v.text)]
                    elif kind == "b":
                        val = "TRUE" if v.text == "1" else "FALSE"
                    else:
                        val = v.text
                    cells[_col_index(c.get("r"))] = val
                width = max(cells) + 1 if cells else 0
                rows.append([cells.get(i) for i in range(width)])
            out[sh.get("name")] = rows
        return out


def read_any(path):
    low = path.lower()
    if low.endswith(".xlsx"):
        return read_xlsx(path)
    if low.endswith(".csv"):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return {os.path.basename(path): list(csv.reader(fh))}
    if low.endswith(".numbers"):
        raise ValueError("Apple Numbers files are not supported: in Numbers use File > Export To > Excel (or CSV)")
    raise ValueError("expected a Console .xlsx or .csv export: %s" % path)


# ---------------------------------------------------------------- cleaning values

def num(v):
    """A cell as Decimal. Spreadsheet float noise (178.54999999999998) is
    rounded at 6 decimals, which keeps NAV units (3 dp) and prices (4 dp)."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-"):
        return None
    return D(s).quantize(Decimal("0.000001")).normalize() + 0  # +0 turns -0 into 0


def _text(v):
    return "" if v is None else str(v).strip()


EXCEL_EPOCH = dt.datetime(1899, 12, 30)


def when(v, what="date"):
    """An ISO date or datetime cell, or an Excel serial number (what a
    spreadsheet re-export may write), as ISO text. Anything else is an error."""
    s = _text(v)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?", s):
        return s.replace(" ", "T")
    try:
        serial = Decimal(s)
    except Exception:
        raise ValueError("unreadable %s %r" % (what, s))
    if not 20000 < serial < 80000:  # 1954 to 2119
        raise ValueError("unreadable %s %r" % (what, s))
    t = EXCEL_EPOCH + dt.timedelta(seconds=int((serial * 86400).to_integral_value()))
    return t.date().isoformat() if serial == serial.to_integral_value() else t.isoformat()


def key(header):
    """'Quantity Pledged (Margin)' and 'quantity_pledged_margin' -> 'quantity_pledged_margin'.

    Console's XLSX uses Title Case headers and its CSV snake_case; both map here.
    """
    return re.sub(r"[^a-z0-9]+", "_", _text(header).lower()).strip("_")


def _find_header(rows):
    for i, r in enumerate(rows):
        keys = [key(c) for c in r]
        if "symbol" in keys and "isin" in keys:
            return i, keys.index("symbol"), keys
    return None, None, None


def _table(rows):
    """Rows under the Symbol/ISIN header as dicts keyed by normalised header."""
    i, start, header = _find_header(rows)
    if i is None:
        return []
    out = []
    for r in rows[i + 1:]:
        cells = list(r) + [None] * (len(header) - len(r))
        if not _text(cells[start]):
            continue
        out.append({h: cells[j] for j, h in enumerate(header) if h})
    return out


def _title(rows, pattern):
    for r in rows:
        for c in r:
            m = re.search(pattern, _text(c))
            if m:
                return m
    return None


# ---------------------------------------------------------------- holdings

EQUITY_TITLE = r"Equity Holdings Statement as on (\d{4}-\d{2}-\d{2})"
MF_TITLE = r"Mutual Funds Holdings Statement as on (\d{4}-\d{2}-\d{2})"


def _holding(row, kind):
    h = {
        "kind": kind,
        "symbol": _text(row.get("symbol")),
        "isin": _text(row.get("isin")),
        "quantity": num(row.get("quantity_available")) or Decimal(0),  # includes discrepant units
        "quantity_discrepant": num(row.get("quantity_discrepant")) or Decimal(0),
        "quantity_pledged": (num(row.get("quantity_pledged_margin")) or 0) + (num(row.get("quantity_pledged_loan")) or 0),
        "average_price": num(row.get("average_price")),
        "previous_close": num(row.get("previous_closing_price")),
    }
    if kind == "equity":
        h["sector"] = _text(row.get("sector"))
        h["quantity_long_term"] = num(row.get("quantity_long_term"))
    else:
        h["category"] = _text(row.get("instrument_type"))
    return h


def holdings_warnings(h):
    w = []
    if h["quantity"] > 0 and not h["average_price"]:
        w.append("%s: %s held at zero average price. The cost basis is missing, usually after a demerger "
                 "or other corporate action; P&L on it is overstated until the cost is split." % (h["symbol"], h["quantity"]))
    if h["quantity_discrepant"]:
        w.append("%s: %s of the %s units are marked discrepant in Console; check them before relying on them."
                 % (h["symbol"], h["quantity_discrepant"], h["quantity"]))
    if h["quantity_pledged"]:
        w.append("%s: %s units pledged." % (h["symbol"], h["quantity_pledged"]))
    return w


def parse_holdings(sheets):
    """{sheet: rows} -> {"as_of", "equity": [...], "mutual_funds": [...], "warnings": [...]}.

    The Combined sheet repeats the other two and is skipped.
    """
    out = {"as_of": None, "equity": [], "mutual_funds": [], "warnings": []}
    for rows in sheets.values():
        found = None
        for pattern, section, kind in ((EQUITY_TITLE, "equity", "equity"), (MF_TITLE, "mutual_funds", "mutual_fund")):
            m = _title(rows, pattern)
            if m:
                found = (section, kind)
                if out["as_of"] and out["as_of"] != m.group(1):
                    out["warnings"].append("sheets are from different dates: %s and %s" % (out["as_of"], m.group(1)))
                out["as_of"] = out["as_of"] or m.group(1)
                break
        if found is None:
            if _title(rows, r"Combined Holdings Statement"):
                continue
            _, _, header = _find_header(rows)
            if header and "sector" in header and "instrument_type" not in header:
                found = ("equity", "equity")
            elif header and "instrument_type" in header and "sector" not in header:
                found = ("mutual_funds", "mutual_fund")
            else:
                continue
        for row in _table(rows):
            out[found[0]].append(_holding(row, found[1]))
    if out["as_of"] is None:
        out["warnings"].append("no statement date in the file: the holdings date is unknown")
    for h in out["equity"] + out["mutual_funds"]:
        out["warnings"].extend(holdings_warnings(h))
    if not out["equity"] and not out["mutual_funds"]:
        raise ValueError("no Console holdings statement found (looked for its title row)")
    return out


def value(h):
    """Cost and value at the previous close, where each is known.

    Quantity Available already includes the discrepant units (Console's own
    present value is quantity x previous close).
    """
    q = h["quantity"]
    cost = q * h["average_price"] if h["average_price"] else None
    val = q * h["previous_close"] if h["previous_close"] is not None else None
    return cost, val


# ---------------------------------------------------------------- tradebook

TRADEBOOK_TITLE = r"Tradebook for Equity from (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})"


def parse_tradebook(sheets):
    """{sheet: rows} -> {"from", "to", "trades": [...]}, one entry per fill, oldest first."""
    trades, period = [], None
    for rows in sheets.values():
        m = _title(rows, TRADEBOOK_TITLE)
        if m:
            period = period or (m.group(1), m.group(2))
        for r in _table(rows):
            side = _text(r.get("trade_type")).lower()
            if side not in ("buy", "sell"):
                raise ValueError("unknown trade type %r for %s" % (side, r.get("symbol")))
            qty = num(r.get("quantity"))
            if qty != qty.to_integral_value():
                raise ValueError("fractional equity quantity %s for %s" % (qty, r.get("symbol")))
            trades.append({
                "date": when(r.get("trade_date"))[:10],
                "time": when(r.get("order_execution_time"), "execution time") if _text(r.get("order_execution_time")) else "",
                "symbol": _text(r.get("symbol")),
                "isin": _text(r.get("isin")),
                "exchange": _text(r.get("exchange")),
                "segment": _text(r.get("segment")),
                "series": _text(r.get("series")),
                "side": side,
                "qty": int(qty),
                "price": num(r.get("price")),
                "trade_id": _text(r.get("trade_id")),
                "order_id": _text(r.get("order_id")),
                "auction": _text(r.get("auction")).upper() == "TRUE",
            })
    if period is None and not trades:
        raise ValueError("no Console tradebook found (looked for its title row)")
    trades.sort(key=lambda t: (t["date"], t["time"], t["trade_id"]))
    return {"from": period[0] if period else None, "to": period[1] if period else None, "trades": trades}


def orders(trades):
    """Fills grouped by order: one row per order with total qty and average price."""
    by = {}
    for t in trades:
        o = by.setdefault(t["order_id"], dict(t, qty=0, value=Decimal(0), fills=0))
        o["qty"] += t["qty"]
        o["value"] += t["qty"] * t["price"]
        o["fills"] += 1
    for o in by.values():
        o["price"] = (o["value"] / o["qty"]).quantize(Decimal("0.0001"))
    return sorted(by.values(), key=lambda o: (o["date"], o["time"]))


def day_charges(trades, date, broker="zerodha"):
    """Expected contract-note charges for one trading day, fill by fill.

    Exchange charges and STT are on turnover, so fills and orders give the
    same result; the DP charge follows the broker's basis (Zerodha: once
    per scrip sold that day).
    """
    import charges
    day = [t for t in trades if t["date"] == date]
    if not day:
        raise ValueError("no trades on %s" % date)
    rows = [{"date": t["date"], "symbol": t["isin"], "side": t["side"], "qty": t["qty"],
             "price": t["price"], "exchange": t["exchange"]} for t in day]
    return charges.batch_charges(rows, broker)


# ---------------------------------------------------------------- CLI

def _js(o):
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read Zerodha Console holdings and tradebook exports.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("holdings", "tradebook"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("--json", action="store_true")
    c = sub.add_parser("charges", help="expected contract-note charges for one day of the tradebook")
    c.add_argument("file")
    c.add_argument("--date", required=True)
    a = ap.parse_args(argv)
    try:
        sheets = read_any(a.file)
        if a.cmd == "holdings":
            h = parse_holdings(sheets)
            if a.json:
                print(json.dumps(h, default=_js, indent=1))
                return 0
            print("Holdings as on %s: %d stocks, %d mutual funds" % (h["as_of"], len(h["equity"]), len(h["mutual_funds"])))
            for w in h["warnings"]:
                print("  check:", w)
        elif a.cmd == "tradebook":
            tb = parse_tradebook(sheets)
            if a.json:
                print(json.dumps(tb, default=_js, indent=1))
                return 0
            o = orders(tb["trades"])
            print("Tradebook %s to %s: %d fills in %d orders, %d buys, %d sells" % (
                tb["from"], tb["to"], len(tb["trades"]), len(o),
                sum(x["side"] == "buy" for x in o), sum(x["side"] == "sell" for x in o)))
        else:
            from money import inr
            r = day_charges(parse_tradebook(sheets)["trades"], a.date)
            keys = ("brokerage", "stt", "exchange_txn", "sebi_fee", "stamp_duty", "gst", "dp_charge")
            tot = {k: sum(x["charges"][k] for x in r["rows"]) for k in keys}
            print("Expected charges for %s (%d fills):" % (a.date, len(r["rows"])))
            for k in keys:
                print("  %-13s %12s" % (k, inr(tot[k])))
            print("  %-13s %12s" % ("total", inr(r["total"])))
            print("Compare with the contract note for %s. STT on a contract note is rounded per scrip." % a.date)
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        print("error:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
