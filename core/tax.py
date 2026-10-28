#!/usr/bin/env python3
"""Income tax on listed equity gains and dividends, financial year by year.

Rules come from core/tax_rates.json, each dated and quoted from the Income
Tax Department's own text; a date the file does not cover is an error, never
a default. For a resident individual, securities transaction tax paid:

  * sells are matched to buys oldest first (core/lots.py); held more than
    12 months is long term
  * short-term gains at the section 111A rate of the transfer date
  * long-term gains exempt before 1 April 2018 (section 10(38)), then at the
    section 112A rate above the year's exemption limit; a share bought before
    1 February 2018 costs at least its 31 January 2018 value (section
    55(2)(ac)), which the caller supplies per ISIN
  * losses set off in the year (section 70), the rest carried forward eight
    years (section 74): short-term against any gain, long-term only against
    long-term
  * dividends exempt to 31 March 2020 (section 10(34), with section 115BBDA
    above Rs 10 lakh), then at the holder's slab rate
  * cess on the tax; surcharge and the rest of tax_rates.json's left_out are
    not applied

The slab rate is typed in when running (an argument or a prompt) and is never
stored or written anywhere. This is arithmetic on stated rules, not tax
advice; the return filed is for a tax professional.

  python3 core/tax.py tradebook-1.xlsx tradebook-2.xlsx [--fmv fmv.json] [--dividends div.csv]

fmv.json: {"ISIN": "price on 31 Jan 2018"}; div.csv: date,isin,amount (rupees received)
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lots  # noqa: E402
from money import D, paise  # noqa: E402

RATES_PATH = os.path.join(HERE, "tax_rates.json")
ZERO = Decimal(0)
HUNDRED = Decimal(100)


def load_rates(path=RATES_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _day(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _fy_start(fy):
    """'FY2018-19' -> 2018."""
    return int(fy[2:6])


def _check_covered(day, rates, what):
    c = rates["covers"]
    if not _day(c["from"]) <= day <= _day(c["to"]):
        raise ValueError("%s on %s is outside the dates core/tax_rates.json covers (%s to %s): %s" % (
            what, day, c["from"], c["to"], c["_note"]))


def _dated(rules, day, what):
    """The last rule whose 'from' is on or before day."""
    hit = None
    for r in rules:
        if _day(r["from"]) <= day:
            hit = r
    if hit is None:
        raise ValueError("no %s rule in core/tax_rates.json for %s" % (what, day))
    return hit


def _by_fy(rules, fy, what):
    """The last rule whose fy_from is on or before fy."""
    hit = None
    for r in rules:
        if _fy_start(r["fy_from"]) <= _fy_start(fy):
            hit = r
    if hit is None:
        raise ValueError("no %s rule in core/tax_rates.json for %s" % (what, fy))
    return hit


def classify(realized, rates, fmv=None):
    """Each matched sale -> {"fy", "kind": "short"|"long"|"exempt", "rate", "gain", ...}.

    fmv: {isin: price} on 31 January 2018, needed for long-term sales of
    buys made before 1 February 2018 and sold from 1 April 2018."""
    fmv = fmv or {}
    gf = rates["grandfathering"]
    gf_before, lt_from = _day(gf["acquired_before"]), None
    for r in rates["long_term"]:
        if not r.get("exempt"):
            lt_from = _day(r["from"]) if lt_from is None else lt_from
    out = []
    for r in realized:
        if r["gain"] is None:
            raise ValueError("%s: %s shares sold on %s with no recorded buy; their cost is unknown" % (
                r["symbol"], r["qty"], r["sell_date"]))
        sell = r["sell_date"]
        _check_covered(sell, rates, "a sale of %s" % r["symbol"])
        cost, proceeds = r["cost"], r["proceeds"]
        if r["term"] == "long":
            rule = _dated(rates["long_term"], sell, "long-term gains")
            if rule.get("exempt"):
                kind, rate = "exempt", ZERO
            else:
                kind, rate = "long", D(rule["rate_pct"])
                if r["buy_date"] < gf_before and sell >= lt_from:
                    if r["isin"] not in fmv:
                        raise ValueError("%s (%s) bought %s and sold long term on %s: needs its fair market value on %s "
                                         "(section 55(2)(ac))" % (r["symbol"], r["isin"], r["buy_date"], sell, gf["fmv_date"]))
                    cost = max(cost, min(r["qty"] * D(fmv[r["isin"]]), proceeds))
        else:
            kind, rate = "short", D(_dated(rates["short_term"], sell, "short-term gains")["rate_pct"])
        out.append({"fy": r["fy"], "isin": r["isin"], "symbol": r["symbol"], "qty": r["qty"], "sell_date": sell,
                    "kind": kind, "rate": rate, "cost": cost, "proceeds": proceeds, "gain": proceeds - cost})
    return out


def _absorb(loss, buckets, keys):
    """Set loss against buckets[k] for k in keys, highest rate first. Returns what is left."""
    for k in sorted(keys, key=lambda k: -k[1]):
        if loss <= 0:
            break
        take = min(loss, buckets[k])
        buckets[k] -= take
        loss -= take
    return loss


def _dividend_tax(fy, items, rates, slab_pct):
    """(income, tax before cess, note) for one year's dividends [(date, amount)]."""
    income = sum((a for _, a in items), ZERO)
    if not items:
        return ZERO, ZERO, ""
    rules = {}
    for day, amount in items:
        _check_covered(day, rates, "a dividend")
        r = _dated(rates["dividends"], day, "dividends")
        rules.setdefault(r["holder"] + r.get("above", ""), (r, []))[1].append(amount)
    tax, notes = ZERO, []
    for r, amounts in rules.values():
        total = sum(amounts, ZERO)
        if r["holder"] == "slab":
            if slab_pct is None:
                raise ValueError("%s has dividends taxed at the slab rate: type the slab rate in (--slab-pct)" % fy)
            if not ZERO <= D(slab_pct) <= HUNDRED:
                raise ValueError("slab rate must be a percent between 0 and 100")
            tax += total * D(slab_pct) / HUNDRED
            notes.append("slab")
        elif "above" in r:
            tax += max(total - D(r["above"]), ZERO) * D(r["rate_pct"]) / HUNDRED
            notes.append("exempt to Rs %s, then %s%%" % (r["above"], r["rate_pct"]))
        else:
            notes.append("exempt")
    return income, tax, ", ".join(notes)


def assess(trades, dividends=(), slab_pct=None, fmv=None, rates=None):
    """Tax per financial year, oldest first.

    trades: broker-neutral [{"date", "isin", "symbol", "side", "qty", "price"}]
    (qty whole shares, or Decimal for research's fractional units);
    dividends: [(date, rupees received)]; slab_pct: the holder's slab rate,
    needed only when a dividend falls on or after 1 April 2020. Returns a
    list of {"fy", "short", "long", "exempt", "set_off", "taxable",
    "carried_forward", "gains_tax", "dividends", "dividend_tax", "cess",
    "tax"}: money in Decimal rupees, tax rounded to the paisa."""
    rates = rates or load_rates()
    sales = classify(lots.match(trades)["realized"], rates, fmv)
    divs = {}
    for day, amount in dividends:
        day = _day(day)
        divs.setdefault(lots.financial_year(day), []).append((day, D(amount)))
    years = sorted(set(s["fy"] for s in sales) | set(divs), key=_fy_start)
    if not years:
        return []
    keep = rates["set_off"]["carry_forward_years"]
    carried = []  # [{"fy", "kind", "amount"}], oldest first
    out = []
    for fy in [("FY%d-%02d" % (y, (y + 1) % 100)) for y in range(_fy_start(years[0]), _fy_start(years[-1]) + 1)]:
        mine = [s for s in sales if s["fy"] == fy]
        buckets, loss = {}, {"short": ZERO, "long": ZERO}
        exempt = ZERO
        for s in mine:
            if s["kind"] == "exempt":
                exempt += s["gain"]  # losses here are dropped (tax_rates.json left_out)
                continue
            if s["gain"] >= 0:
                k = (s["kind"], s["rate"])
                buckets[k] = buckets.get(k, ZERO) + s["gain"]
            else:
                loss[s["kind"]] -= s["gain"]
        gross = {"short": sum((v for k, v in buckets.items() if k[0] == "short"), ZERO),
                 "long": sum((v for k, v in buckets.items() if k[0] == "long"), ZERO)}
        long_keys = [k for k in buckets if k[0] == "long"]
        before = sum(buckets.values(), ZERO)
        # section 70: this year's losses
        left_short = _absorb(loss["short"], buckets, list(buckets))
        left_long = _absorb(loss["long"], buckets, long_keys)
        # section 74: losses brought forward, oldest first, within eight years
        carried = [c for c in carried if _fy_start(fy) - _fy_start(c["fy"]) <= keep]
        for c in carried:
            c["amount"] = _absorb(c["amount"], buckets, list(buckets) if c["kind"] == "short" else long_keys)
        carried = [c for c in carried if c["amount"] > 0]
        set_off = before - sum(buckets.values(), ZERO)
        for kind, left in (("short", left_short), ("long", left_long)):
            if left > 0:
                carried.append({"fy": fy, "kind": kind, "amount": left})
        # section 112A: the year's exemption limit on what long-term gain is left
        limit = ZERO
        if long_keys:
            limit = D(_by_fy(rates["long_term_exemption"], fy, "long-term exemption")["amount"])
            _absorb(limit, buckets, long_keys)
        gains_tax = sum((v * k[1] / HUNDRED for k, v in buckets.items()), ZERO)
        div_income, div_tax, div_note = _dividend_tax(fy, divs.get(fy, []), rates, slab_pct)
        cess_pct = D(_by_fy(rates["cess"], fy, "cess")["rate_pct"]) if (gains_tax or div_tax) else ZERO
        cess = (gains_tax + div_tax) * cess_pct / HUNDRED
        out.append({
            "fy": fy, "short": gross["short"] - loss["short"], "long": gross["long"] - loss["long"], "exempt": exempt,
            "set_off": set_off, "long_exemption": limit,
            "taxable": {"%s %s%%" % (k[0], format(k[1].normalize(), "f")): v for k, v in sorted(buckets.items()) if v},
            "carried_forward": [dict(c) for c in carried],
            "gains_tax": paise(gains_tax), "dividends": div_income, "dividend_tax": paise(div_tax),
            "dividend_rule": div_note, "cess": paise(cess), "tax": paise(gains_tax + div_tax + cess),
        })
    return out


def read_dividends(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [(_day(r["date"]), D(r["amount"])) for r in csv.DictReader(fh)]


def slab_from_prompt(args_value, needed):
    """The slab rate from --slab-pct, or asked for when needed. Never stored."""
    if args_value is not None or not needed:
        return args_value
    v = input("Your income tax slab rate in percent (used for dividends, not saved): ").strip()
    return D(v)


def main(argv=None):
    import zerodha_console
    from money import inr
    ap = argparse.ArgumentParser(description="Tax per financial year on listed equity gains and dividends.")
    ap.add_argument("tradebooks", nargs="+")
    ap.add_argument("--fmv", help="JSON {ISIN: price on 31 January 2018} for buys before 1 February 2018")
    ap.add_argument("--dividends", help="CSV with date,isin,amount (rupees received)")
    ap.add_argument("--slab-pct", type=D, help="your slab rate for dividends from 1 April 2020; asked if left out")
    a = ap.parse_args(argv)
    trades = []
    for p in a.tradebooks:
        trades += zerodha_console.parse_tradebook(zerodha_console.read_any(p))["trades"]
    fmv = None
    if a.fmv:
        with open(a.fmv, encoding="utf-8") as fh:
            fmv = json.load(fh)
    divs = read_dividends(a.dividends) if a.dividends else []
    slab = slab_from_prompt(a.slab_pct, any(d >= dt.date(2020, 4, 1) for d, _ in divs))
    try:
        years = assess(trades, divs, slab, fmv)
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    for y in years:
        print("%s  short %s  long %s  set off %s  tax on gains %s  dividends %s (tax %s)  cess %s  total %s" % (
            y["fy"], inr(y["short"]), inr(y["long"]), inr(y["set_off"]), inr(y["gains_tax"]), inr(y["dividends"]),
            inr(y["dividend_tax"]), inr(y["cess"]), inr(y["tax"])))
        for c in y["carried_forward"]:
            print("    carried forward: %s loss of %s from %s" % (c["kind"], inr(c["amount"]), c["fy"]))
    print("Before surcharge; rules and what is left out: core/tax_rates.json. Not tax advice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
