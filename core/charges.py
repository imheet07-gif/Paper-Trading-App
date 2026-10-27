#!/usr/bin/env python3
"""What a cash-market equity trade costs in India, charge by charge.

Rates come from charges.json beside this file, never from code, and each
block there says whether it has been matched to a real contract note. The
output says UNVERIFIED until every block used is verified.

  python3 core/charges.py --broker zerodha --side buy --qty 10 --price 2875.40
  python3 core/charges.py --broker zerodha --side sell --qty 10 --price 2875.40 --json
  python3 core/charges.py --broker zerodha --round-trip --qty 10 --price 2875.40

Only the broker you trade through needs rates: Zerodha today.

Components: brokerage, STT, exchange transaction charge, SEBI turnover fee,
stamp duty (buy side), GST at 18% on brokerage + exchange charge + SEBI fee,
and the DP charge on delivery sells.

Contract notes round some components (STT to the rupee, per scrip). Each
component here is rounded to the paisa. When a real contract note is
matched, add its rounding rule here and a test that reproduces it.
"""

import argparse
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from money import D, inr, paise  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RATES = os.path.join(HERE, "charges.json")
CRORE = Decimal(10000000)
HUNDRED = Decimal(100)


def load_rates(path=RATES):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _pct(turnover, pct):
    return turnover * D(pct) / HUNDRED


def brokerage(rule, turnover):
    """pct of turnover, capped at `cap`, raised to `floor`, never above turnover."""
    b = _pct(turnover, rule["pct"])
    if rule.get("cap") is not None:
        b = min(b, D(rule["cap"]))
    if rule.get("floor") is not None and b > 0:
        b = max(b, D(rule["floor"]))
    return min(b, turnover)


def trade_charges(broker, side, qty, price, product="delivery", exchange="NSE",
                  rates=None, include_dp=True):
    """Charges on one executed trade. Returns a dict of Decimals plus `verified`."""
    rates = rates or load_rates()
    side, product, exchange = side.lower(), product.lower(), exchange.upper()
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    if int(qty) != D(qty) or int(qty) <= 0:
        raise ValueError("qty must be a positive whole number of shares")
    if broker not in rates["brokers"]:
        raise KeyError("no rates for broker %r" % broker)
    b = rates["brokers"][broker]
    if product not in b["brokerage"]:
        raise KeyError("%s %s brokerage is not configured in charges.json" % (broker, product))
    st, ex = rates["statutory"], rates["exchange_txn_pct"]
    if exchange not in ex or exchange.startswith("_"):
        raise KeyError("no exchange charge for %r" % exchange)

    turnover = D(qty) * D(price)
    out = {
        "turnover": paise(turnover),
        "brokerage": paise(brokerage(b["brokerage"][product], turnover)),
        "stt": paise(_pct(turnover, st["stt_pct"][product][side])),
        "exchange_txn": paise(_pct(turnover, ex[exchange])),
        "sebi_fee": paise(turnover * D(st["sebi_fee_per_crore"]) / CRORE),
        "stamp_duty": paise(_pct(turnover, st["stamp_pct"][product][side])),
    }
    out["gst"] = paise((out["brokerage"] + out["exchange_txn"] + out["sebi_fee"]) * D(st["gst_pct"]) / HUNDRED)
    out["dp_charge"] = Decimal("0.00")
    if include_dp and product == "delivery" and side == "sell":
        out["dp_charge"] = paise(b["dp_charge_incl_gst"])
    out["total"] = sum(v for k, v in out.items() if k != "turnover")
    out["verified"] = bool(st.get("verified") and ex.get("verified") and b.get("verified"))
    out["dp_charge_basis"] = b["dp_charge_basis"]
    return out


def batch_charges(trades, broker, rates=None):
    """Charges for a day's trades, with the DP charge applied the broker's way.

    trades: [{"date", "symbol", "side", "qty", "price", "product"?, "exchange"?}]
    per_scrip_per_day: one DP charge per symbol sold per day, however many sells.
    per_sell_order: one DP charge per delivery sell order.
    """
    rates = rates or load_rates()
    basis = rates["brokers"][broker]["dp_charge_basis"]
    seen, rows, total = set(), [], Decimal(0)
    for t in trades:
        product = t.get("product", "delivery")
        dp = True
        if basis == "per_scrip_per_day" and t["side"].lower() == "sell" and product == "delivery":
            key = (t["date"], t["symbol"])
            dp = key not in seen
            seen.add(key)
        c = trade_charges(broker, t["side"], t["qty"], t["price"], product,
                          t.get("exchange", "NSE"), rates, include_dp=dp)
        rows.append(dict(t, charges=c))
        total += c["total"]
    return {"rows": rows, "total": total, "verified": all(r["charges"]["verified"] for r in rows)}


def round_trip(broker, qty, price, product="delivery", exchange="NSE", rates=None):
    """Buy then sell the same quantity at the same price: the cost of a round trip."""
    buy = trade_charges(broker, "buy", qty, price, product, exchange, rates)
    sell = trade_charges(broker, "sell", qty, price, product, exchange, rates)
    total = buy["total"] + sell["total"]
    return {"buy": buy, "sell": sell, "total": total,
            "pct_of_turnover": (total / buy["turnover"] * HUNDRED).quantize(Decimal("0.0001")),
            "verified": buy["verified"] and sell["verified"]}


def _jsonable(d):
    return {k: (str(v) if isinstance(v, Decimal) else _jsonable(v) if isinstance(v, dict) else v)
            for k, v in d.items()}


def render(c):
    lines = []
    for k in ("turnover", "brokerage", "stt", "exchange_txn", "sebi_fee", "stamp_duty", "gst", "dp_charge"):
        lines.append("  %-13s %14s" % (k, inr(c[k])))
    lines.append("  %-13s %14s" % ("total", inr(c["total"])))
    if not c["verified"]:
        lines.append("  UNVERIFIED: rates not yet matched to a contract note (see core/charges.json)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Indian equity trade charges.")
    ap.add_argument("--broker", required=True)
    ap.add_argument("--side", choices=["buy", "sell"])
    ap.add_argument("--qty", required=True, type=int)
    ap.add_argument("--price", required=True)
    ap.add_argument("--product", default="delivery", choices=["delivery", "intraday"])
    ap.add_argument("--exchange", default="NSE")
    ap.add_argument("--round-trip", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.round_trip:
            r = round_trip(a.broker, a.qty, a.price, a.product, a.exchange)
            if a.json:
                print(json.dumps({"buy": _jsonable(r["buy"]), "sell": _jsonable(r["sell"]),
                                  "total": str(r["total"]), "pct_of_turnover": str(r["pct_of_turnover"]),
                                  "verified": r["verified"]}, indent=2))
            else:
                print("BUY\n" + render(r["buy"]) + "\nSELL\n" + render(r["sell"]))
                print("Round trip: %s, %s%% of turnover" % (inr(r["total"]), r["pct_of_turnover"]))
            return 0
        if not a.side:
            ap.error("--side is required unless --round-trip")
        c = trade_charges(a.broker, a.side, a.qty, a.price, a.product, a.exchange)
        print(json.dumps(_jsonable(c), indent=2) if a.json else render(c))
    except (KeyError, ValueError) as e:
        print("error:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
