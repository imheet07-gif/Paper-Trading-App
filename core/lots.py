#!/usr/bin/env python3
"""FIFO lots and realized gains from a broker-neutral list of trades.

Shares held in demat are matched first-in, first-out (the method Indian
tax practice applies to demat holdings). Every sell is matched against the
oldest open buys of the same ISIN; what is left over had no recorded buy
(bought before the history starts, transferred in, or received through a
corporate action such as a demerger) and is reported as unmatched, never
given an invented cost.

Gains here are price arithmetic: quantity x (sell price - buy price),
before charges. Long term means sold more than 12 months after the buy
(listed equity). Whether and how this is taxed is for a tax professional.

  python3 core/lots.py tradebook-1.xlsx tradebook-2.xlsx ...

trades: [{"date", "time"?, "isin", "symbol", "side", "qty", "price"}]
"""

import datetime as dt
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from money import D  # noqa: E402


def financial_year(d):
    """date -> 'FY2026-27' (April to March)."""
    start = d.year if d.month >= 4 else d.year - 1
    return "FY%d-%02d" % (start, (start + 1) % 100)


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last))


def is_long_term(buy, sell):
    return sell > add_months(buy, 12)


def _day(s):
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def match(trades):
    """Returns {"open": {isin: [lots]}, "realized": [...], "symbols": {isin: symbol}}."""
    rows = sorted(trades, key=lambda t: (str(t["date"]), str(t.get("time") or ""), 0 if t["side"] == "buy" else 1))
    open_lots, realized, symbols = {}, [], {}
    for t in rows:
        isin, qty, price, day = t["isin"], int(t["qty"]), D(t["price"]), _day(t["date"])
        symbols[isin] = t.get("symbol") or symbols.get(isin, isin)
        q = open_lots.setdefault(isin, [])
        if t["side"] == "buy":
            q.append({"date": day, "qty": qty, "price": price})
            continue
        left = qty
        while left and q:
            lot = q[0]
            take = min(left, lot["qty"])
            realized.append({
                "isin": isin, "symbol": symbols[isin], "qty": take,
                "buy_date": lot["date"], "sell_date": day, "buy_price": lot["price"], "sell_price": price,
                "cost": take * lot["price"], "proceeds": take * price, "gain": take * (price - lot["price"]),
                "term": "long" if is_long_term(lot["date"], day) else "short",
                "fy": financial_year(day),
            })
            lot["qty"] -= take
            left -= take
            if not lot["qty"]:
                q.pop(0)
        if left:
            realized.append({
                "isin": isin, "symbol": symbols[isin], "qty": left,
                "buy_date": None, "sell_date": day, "buy_price": None, "sell_price": price,
                "cost": None, "proceeds": left * price, "gain": None, "term": None,
                "fy": financial_year(day),
            })
    return {"open": {k: v for k, v in open_lots.items() if v}, "realized": realized, "symbols": symbols}


def by_year(realized):
    """Realized results per financial year, matched and unmatched kept apart."""
    out = {}
    for r in realized:
        y = out.setdefault(r["fy"], {"fy": r["fy"], "short": Decimal(0), "long": Decimal(0), "gain": Decimal(0),
                                    "proceeds": Decimal(0), "unmatched_qty": 0, "unmatched_proceeds": Decimal(0),
                                    "unmatched_symbols": set()})
        y["proceeds"] += r["proceeds"]
        if r["gain"] is None:
            y["unmatched_qty"] += r["qty"]
            y["unmatched_proceeds"] += r["proceeds"]
            y["unmatched_symbols"].add(r["symbol"])
        else:
            y[r["term"]] += r["gain"]
            y["gain"] += r["gain"]
    for y in out.values():
        y["unmatched_symbols"] = sorted(y["unmatched_symbols"])
    return [out[k] for k in sorted(out)]


def reconcile(open_lots, holdings):
    """Open FIFO quantity per ISIN against a holdings list [{"isin", "symbol", "quantity"}]."""
    held = {}
    for h in holdings:
        held[h["isin"]] = (h.get("symbol"), D(h["quantity"]))
    lines = []
    for isin in sorted(set(held) | set(open_lots)):
        lots_qty = sum(l["qty"] for l in open_lots.get(isin, []))
        sym, hq = held.get(isin, (None, None))
        if hq is None:
            status = "in trades, not held"
        elif lots_qty == hq:
            status = "match"
        elif lots_qty == 0:
            status = "held, no recorded buy"
        else:
            status = "quantity differs"
        lines.append({"isin": isin, "symbol": sym or "", "lots": lots_qty, "held": hq, "status": status})
    return lines


def main(argv=None):
    import zerodha_console
    from money import inr
    paths = (argv if argv is not None else sys.argv[1:])
    if not paths:
        print("usage: lots.py TRADEBOOK [TRADEBOOK ...]", file=sys.stderr)
        return 2
    trades = []
    for p in paths:
        trades += zerodha_console.parse_tradebook(zerodha_console.read_any(p))["trades"]
    m = match(trades)
    for y in by_year(m["realized"]):
        line = "%s  matched gain %s (short %s, long %s)" % (y["fy"], inr(y["gain"]), inr(y["short"]), inr(y["long"]))
        if y["unmatched_qty"]:
            line += "; %d shares sold with no recorded buy: %s" % (y["unmatched_qty"], ", ".join(y["unmatched_symbols"]))
        print(line)
    for isin, lots in sorted(m["open"].items()):
        print("open  %-12s %-12s %d shares in %d lot(s)" % (m["symbols"][isin], isin, sum(l["qty"] for l in lots), len(lots)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
