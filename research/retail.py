#!/usr/bin/env python3
"""The retail rules every idea is tested under (Phase 1 of the engine).

  rules        research/retail.json: capital levels, no leverage, no short
               selling, the liquidity limit, circuits, orders, T+1, charges
               and tax, each with its source
  min capital  for an idea's picks on a date, the smallest capital at which
               every position is at least one whole share and the fixed DP
               charge per scrip sold (core/charges.json) is at most a stated
               share of the position
  inventory    what the market store holds that a retail account can buy:
               company shares, ETFs and fund units, Sovereign Gold Bonds, and
               every other series as stored, with counts and date ranges.
               Read-only on data/market.db

  python3 research/retail.py rules
  python3 research/retail.py inventory [--db data/market.db] [--json]
  python3 research/retail.py min-capital --positions 20 --prices 812.5 1490 230

Standard library only, so skills can use it later. Money is Decimal here.
"""

import argparse
import json
import os
import sqlite3
import sys
from decimal import ROUND_CEILING, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "core"))
import charges as charges_mod  # noqa: E402
from money import D, inr  # noqa: E402

RULES_PATH = os.path.join(HERE, "retail.json")
DEFAULT_DB = os.path.join(ROOT, "data", "market.db")
REQUIRED = ("capital_levels", "leverage", "short_selling", "liquidity", "circuits", "orders", "settlement",
            "candidates", "cash", "charges", "tax", "min_capital", "walk_forward", "bar")


# ---------------------------------------------------------------- rules

def load(path=RULES_PATH):
    """research/retail.json, checked: every block present with a source, no leverage, no shorts,
    the holdout never among the walk-forward periods. Unknown or missing means an error."""
    with open(path, encoding="utf-8") as fh:
        r = json.load(fh)
    missing = [k for k in REQUIRED if k not in r]
    if missing:
        raise ValueError("retail.json lacks: %s" % ", ".join(missing))
    unsourced = [k for k in REQUIRED if not str(r[k].get("source", "")).strip()]
    if unsourced:
        raise ValueError("retail.json blocks without a source: %s" % ", ".join(unsourced))
    if r["leverage"]["value"] is not False or r["short_selling"]["value"] is not False:
        raise ValueError("retail.json must keep leverage and short selling off")
    levels = r["capital_levels"]["value"]
    if not levels or any(D(x) <= 0 for x in levels):
        raise ValueError("capital levels must be positive amounts")
    if set(r["walk_forward"]["periods"]) - {"dev"}:
        raise ValueError("walk-forward runs on dev only")
    share = D(r["min_capital"]["max_fixed_charge_share"])
    if not 0 < share < 1:
        raise ValueError("max_fixed_charge_share must be between 0 and 1")
    return r


def capital_levels(rules):
    return [D(x) for x in rules["capital_levels"]["value"]]


def dp_charge(rules, rates=None):
    """The fixed charge per scrip sold, from core/charges.json (never copied into retail.json)."""
    rates = rates or charges_mod.load_rates()
    b = rates["brokers"][rules["charges"]["broker"]]
    return D(b["dp_charge_incl_gst"])


# ---------------------------------------------------------------- minimum capital

def min_slice(price, rules, dp=None):
    """The smallest money for one position at `price`: whole shares, at least one, and the DP
    charge at most max_fixed_charge_share of the position."""
    price = D(price)
    if price <= 0:
        raise ValueError("price must be positive")
    floor_value = (dp if dp is not None else dp_charge(rules)) / D(rules["min_capital"]["max_fixed_charge_share"])
    shares = max(Decimal(1), (floor_value / price).to_integral_value(rounding=ROUND_CEILING))
    return shares * price


def min_capital(prices, rules, positions=None, dp=None):
    """Smallest capital at which equal money per position buys every pick: the number of
    positions times the largest pick's minimum slice. prices: the buy price of each pick
    (the limit price, raw rupees). positions defaults to the number of picks."""
    prices = [D(x) for x in prices]
    if not prices:
        return None
    dp = dp if dp is not None else dp_charge(rules)
    n = positions or len(prices)
    return n * max(min_slice(x, rules, dp) for x in prices)


def quantile(values, q):
    """The nearest-rank quantile (no interpolation): the smallest value covering a share q of them."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(1, int((D(q) * len(vals)).to_integral_value(rounding=ROUND_CEILING)))
    return vals[min(k, len(vals)) - 1]


def idea_minimum(per_date, rules):
    """An idea's minimum capital from its per-date minima, with which capital levels it fails.
    Returns {"minimum", "median", "max", "dates", "covered": {level: share of dates}, "fails": [levels]}."""
    vals = [v for v in per_date if v is not None]
    q = rules["min_capital"]["coverage_quantile"]
    m = quantile(vals, q)
    levels = capital_levels(rules)
    covered = {str(lv): (sum(1 for v in vals if v <= lv) / len(vals) if vals else None) for lv in levels}
    return {"minimum": m, "median": quantile(vals, "0.5"), "max": max(vals) if vals else None, "dates": len(vals),
            "quantile": q, "covered": covered, "fails": [lv for lv in levels if m is None or m > lv]}


# ---------------------------------------------------------------- inventory

KINDS = [
    # (kind, series, ISIN prefix or None, label): labels only as far as the repo already states them
    ("equity", "EQ", "INE", "company shares, EQ series (the research universe's series)"),
    ("etf", "EQ", "INF", "ETFs and fund units trading in the EQ series (INF ISIN; panel.universe leaves them out)"),
    ("sgb", "GB", None, "Sovereign Gold Bonds, series GB (core/metals.py)"),
    ("equity_t2t", "BE", "INE", "company shares, BE series (trade-for-trade; not bought by research)"),
    ("equity_bz", "BZ", "INE", "company shares, BZ series (not bought by research)"),
]


def _ro(db_path):
    if not os.path.exists(db_path):
        raise FileNotFoundError("no market store at %s (core/bhavcopy.py builds it)" % db_path)
    return sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db_path), uri=True)


def inventory(db_path=DEFAULT_DB):
    """Every series and ISIN prefix in the prices table: instruments, rows, first and last date,
    and how many traded on the latest stored session. Opens the store read-only."""
    db = _ro(db_path)
    try:
        last = db.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        rows = db.execute(
            "SELECT series, substr(isin, 1, 3), COUNT(DISTINCT isin), COUNT(*), MIN(date), MAX(date), "
            "COUNT(DISTINCT CASE WHEN date = ? THEN isin END) FROM prices GROUP BY series, substr(isin, 1, 3)",
            (last,)).fetchall()
    finally:
        db.close()
    groups = []
    for series, prefix, n, count, first, latest, now in rows:
        kind, label = "other", "series %s, ISIN prefix %s: not classified here" % (series, prefix)
        for k, s, pre, lab in KINDS:
            if series == s and (pre is None or prefix == pre):
                kind, label = k, lab
                break
        groups.append({"kind": kind, "series": series, "isin_prefix": prefix, "label": label, "instruments": n,
                       "rows": count, "first": first, "last": latest, "on_last_session": now})
    order = {k[0]: j for j, k in enumerate(KINDS)}
    groups.sort(key=lambda g: (order.get(g["kind"], len(order)), -g["rows"]))
    return {"last_session": last, "groups": groups}


def render_inventory(inv, show_all=False):
    out = ["Tradable instruments in the store (latest session %s)" % inv["last_session"],
           "%-11s %-6s %-6s %11s %11s %11s  %-10s %-10s  %s" % (
               "kind", "series", "prefix", "instruments", "on last day", "rows", "first", "last", "what")]
    shown = [g for g in inv["groups"] if show_all or g["kind"] != "other"]
    rest = [g for g in inv["groups"] if g not in shown]
    for g in shown:
        out.append("%-11s %-6s %-6s %11d %11d %11d  %-10s %-10s  %s" % (
            g["kind"], g["series"], g["isin_prefix"], g["instruments"], g["on_last_session"], g["rows"],
            g["first"], g["last"], g["label"]))
    if rest:
        out.append("%-11s %-6s %-6s %11d %11d %11d  %-10s %-10s  %d other series as stored (--all lists them)" % (
            "other", "", "", sum(g["instruments"] for g in rest), sum(g["on_last_session"] for g in rest),
            sum(g["rows"] for g in rest), min(g["first"] for g in rest), max(g["last"] for g in rest),
            len({g["series"] for g in rest})))
    out.append("Only 'equity' is in the tested universe today; ETFs and SGBs need their own ideas (Phase 3). "
               "'other' series are listed as stored, not described.")
    return "\n".join(out)


def render_rules(r):
    out = ["Retail rules (research/retail.json, version %s)" % r.get("version")]
    for k in REQUIRED:
        block = {x: y for x, y in r[k].items() if x != "source"}
        out.append("  %-13s %s" % (k, json.dumps(block, ensure_ascii=False)))
        out.append("  %-13s source: %s" % ("", r[k]["source"]))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Retail rules, the minimum-capital check and the store's inventory.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules", help="print the rules and their sources")
    inv = sub.add_parser("inventory", help="what the store holds, read-only")
    inv.add_argument("--db", default=DEFAULT_DB)
    inv.add_argument("--json", action="store_true")
    inv.add_argument("--all", action="store_true", help="list every other series too")
    mc = sub.add_parser("min-capital", help="minimum capital for picks at these buy prices")
    mc.add_argument("--prices", nargs="+", required=True, help="buy price of each pick, in rupees")
    mc.add_argument("--positions", type=int, help="number of positions (default: one per price)")
    a = ap.parse_args(argv)
    rules = load()
    if a.cmd == "rules":
        print(render_rules(rules))
    elif a.cmd == "inventory":
        try:
            res = inventory(a.db)
        except FileNotFoundError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        print(json.dumps(res, indent=1) if a.json else render_inventory(res, a.all))
    else:
        m = min_capital(a.prices, rules, a.positions)
        dp = dp_charge(rules)
        print("minimum capital %s for %d positions (whole shares; DP charge %s at most %s%% of a position)" % (
            inr(m), a.positions or len(a.prices), inr(dp), D(rules["min_capital"]["max_fixed_charge_share"]) * 100))
        for lv in capital_levels(rules):
            print("  at %s: %s" % (inr(lv, 0), "ok" if m <= lv else "fails"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
