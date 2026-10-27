#!/usr/bin/env python3
"""Rebalancer for trader-rebalance, India edition. Standard library only, no network.

Compares what you hold (trader/portfolio.json at trader/prices.json) with
your target weights (trader/targets.json, capped by your rules) and writes
an order sheet, in whole shares, that brings every stock outside the band
back to its target. You enter the lines you choose by hand in your broker.

    python3 rebalance.py                      # report only
    python3 rebalance.py --band 5 --write     # write trader/orders.json (an order sheet)
    python3 rebalance.py --sell-untracked     # also sell holdings that are not in the targets
    python3 rebalance.py --json

The rules:
  value         account = cash + every holding at its price
  targets       weights renormalised to 100 over the stock sleeve, which is the
                account minus your cash buffer; no stock over max_position_pct
                of the account (the excess is shared out, like trader-budget)
  drift         current % of the account minus target %, in percentage points
  band          only stocks drifting MORE than the band trade (default 5
                points, or "rebalance_band_pct" in the config); a trade takes
                the stock back to its target, in whole shares
  sells first   sells are listed before buys; their proceeds, less estimated
                charges, count as cash for the buys (settlement is T+1: whether
                your broker lets you spend them the same day is its rule)
  cash buffer   buys, with estimated charges, only spend cash above the
                buffer; if there is not enough, every buy is scaled down by
                the same share, then rounded down to whole shares
  min / max     lines under min_order_inr are skipped; a line over
                max_order_inr is cut to the max (run it again after it fills)
  untracked     holdings not in the targets are left alone and listed, unless
                --sell-untracked

Not used, because the facts are not yet in core/ data read from the
publisher's file: tick sizes, price bands and circuit limits.

Exit code 0 when there are lines to propose, 1 when nothing is outside the band.
"""

import argparse
import datetime
import json
import os
import sys
from decimal import ROUND_DOWN, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
from money import D, inr, paise  # noqa: E402

DEFAULTS = {"cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 50000, "min_order_inr": 1000,
            "blocked": [], "rebalance_band_pct": 5, "paper": True, "broker": None, "exchange": "NSE"}
ZERO = Decimal(0)
HUNDRED = Decimal(100)
POINT = Decimal("0.01")


class RebalanceError(Exception):
    pass


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RebalanceError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RebalanceError("%s is not valid JSON: %s" % (path, e))


def _dec(x, what):
    if isinstance(x, bool):
        raise RebalanceError("%s is not a number: %r" % (what, x))
    try:
        v = D(x) if isinstance(x, (int, float, Decimal)) else D(str(x).strip().replace(",", ""))
    except Exception:
        raise RebalanceError("%s is not a number: %r" % (what, x))
    if not v.is_finite():
        raise RebalanceError("%s is not a number: %r" % (what, x))
    return v


def _sym(x):
    return str(x or "").strip().upper()


def floor_shares(rupees, price):
    if rupees <= 0:
        return 0
    return int((rupees / price).to_integral_value(rounding=ROUND_DOWN))


def parse_prices(doc):
    raw = doc.get("prices", doc) if isinstance(doc, dict) else {}
    out = {}
    for s, p in (raw or {}).items():
        try:
            v = _dec(p, "price")
        except RebalanceError:
            continue
        if v > 0:
            out[_sym(s)] = v
    return out


def parse_targets(doc, blocked=()):
    rows = doc.get("weights", doc) if isinstance(doc, dict) else doc
    out = {}
    if isinstance(rows, dict):
        rows = [{"symbol": k, "weight": v} for k, v in rows.items()]
    blocked = {_sym(b) for b in blocked or []}
    for r in rows or []:
        s = _sym(r.get("symbol") or r.get("ticker"))
        w = _dec(r.get("weight", 0) or 0, "weight for %s" % s)
        if s and w > 0 and s not in blocked and _sym(r.get("isin")) not in blocked:
            out[s] = out.get(s, ZERO) + w
    tot = sum(out.values(), ZERO)
    if not tot:
        raise RebalanceError("no target weights (write trader/targets.json)")
    return {s: HUNDRED * w / tot for s, w in out.items()}


def capped_targets(weights, sleeve, cap):
    """Rupee target per symbol, proportional to weight, none above the cap."""
    alloc, active, money_left = {}, dict(weights), sleeve
    while active:
        wsum = sum(active.values(), ZERO)
        trial = {s: money_left * w / wsum for s, w in active.items()}
        over = [s for s, v in trial.items() if v > cap]
        if not over:
            alloc.update(trial)
            break
        for s in over:
            alloc[s] = cap
            money_left -= cap
            del active[s]
        if money_left <= 0:
            for s in active:
                alloc[s] = ZERO
            break
    return alloc


def holdings_of(portfolio):
    out, isin = {}, {}
    for p in (portfolio or {}).get("positions", []):
        s = _sym(p.get("symbol") or p.get("ticker"))
        if not s:
            continue
        n = p.get("qty", p.get("shares"))
        if n is None:
            n = sum((_dec(l.get("qty", l.get("shares", 0)), "lot qty") for l in p.get("lots", [])), ZERO)
        n = _dec(n, "qty of %s" % s)
        if n != n.to_integral_value():
            raise RebalanceError("%s holds %s shares; Indian holdings are whole shares" % (s, n))
        out[s] = out.get(s, 0) + int(n)
        if p.get("isin"):
            isin[s] = _sym(p["isin"])
    return out, isin


def charges_for(cfg, side, qty, price):
    """Estimated delivery charges on one line, or None without a broker."""
    broker = cfg.get("broker")
    if not broker or qty <= 0:
        return None
    import charges
    try:
        return charges.trade_charges(broker, side, qty, price, "delivery", str(cfg.get("exchange") or "NSE").upper())
    except KeyError as e:
        raise RebalanceError("charges: %s (core/charges.json)" % e.args[0])


def plan(portfolio, prices, weights, cfg, band=None, sell_untracked=False):
    """Pure function: the drift table and the proposed order sheet."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    band = _dec(c["rebalance_band_pct"] if band is None else band, "band")
    cash = _dec((portfolio or {}).get("cash", 0) or 0, "cash")
    held, isins = holdings_of(portfolio)
    need = sorted(set(held) | set(weights), key=lambda s: (-weights.get(s, ZERO), s))
    missing = [s for s in need if not prices.get(s)]
    if missing:
        raise RebalanceError("no price for %s: add to trader/prices.json; prices are never guessed"
                             % ", ".join(missing))
    value = {s: held.get(s, 0) * prices[s] for s in need}
    total = cash + sum(value.values(), ZERO)
    if total <= 0:
        raise RebalanceError("the account is empty: nothing to rebalance (trader-budget sizes a first buy)")
    buf = total * _dec(c["cash_buffer_pct"], "cash_buffer_pct") / HUNDRED
    cap = total * _dec(c["max_position_pct"], "max_position_pct") / HUNDRED
    target = capped_targets(weights, total - buf, cap)
    min_o = _dec(c.get("min_order_inr") or 0, "min_order_inr")
    max_o = _dec(c.get("max_order_inr") or 0, "max_order_inr")
    exchange = str(c.get("exchange") or "NSE").upper()

    rows, sells, buys, notes = [], [], [], []
    for s in need:
        tracked = s in weights
        tgt = target.get(s, ZERO)
        cur_pct = HUNDRED * value[s] / total
        tgt_pct = HUNDRED * tgt / total
        drift = cur_pct - tgt_pct
        row = {"symbol": s, "shares": held.get(s, 0), "price": prices[s], "value": paise(value[s]),
               "current_pct": cur_pct.quantize(POINT), "target_pct": tgt_pct.quantize(POINT),
               "drift": drift.quantize(POINT), "target_inr": paise(tgt), "tracked": tracked, "action": "hold"}
        rows.append(row)
        if not tracked:
            if sell_untracked and held.get(s, 0) > 0:
                sells.append({"symbol": s, "qty": held[s], "all": True,
                              "why": "not in your targets; selling the whole position (--sell-untracked)"})
                row["action"] = "sell all"
            else:
                row["action"] = "untracked, left alone"
            continue
        if abs(drift) <= band:
            continue
        delta = tgt - value[s]
        why = "drifted %s points from its %s%% target (band %s)" % (
            ("+" if drift > 0 else "") + str(drift.quantize(Decimal("0.1"))), tgt_pct.quantize(Decimal("0.1")), _n(band))
        if delta < 0:
            sells.append({"symbol": s, "qty": None, "rupees": -delta, "all": False, "why": why})
            row["action"] = "sell"
        else:
            buys.append({"symbol": s, "rupees": delta, "why": why})
            row["action"] = "buy"

    lines = []
    cash_after = cash
    charges_total = ZERO
    unverified = False

    def line(s, side, qty, why):
        nonlocal charges_total, unverified
        ch = charges_for(c, side, qty, prices[s])
        o = {"symbol": s}
        if isins.get(s):
            o["isin"] = isins[s]
        o.update({"exchange": exchange, "side": side, "qty": qty, "product": "delivery", "order_type": "market",
                  "ref_price": str(prices[s]), "value": str(paise(qty * prices[s]))})
        if ch:
            o["est_charges"] = str(ch["total"])
            charges_total += ch["total"]
            unverified = unverified or not ch["verified"]
        o["reason"] = why
        return o, (ch["total"] if ch else ZERO)

    for sl in sells:
        s, p = sl["symbol"], prices[sl["symbol"]]
        if sl["all"]:
            qty = sl["qty"]
        else:
            rupees = sl["rupees"]
            if max_o and rupees > max_o:
                notes.append("%s sell cut to your %s max order; run again after it fills" % (s, inr(max_o, 0)))
                rupees = max_o
            # round to the nearest whole share, never more than you hold
            qty = min(int((rupees / p).to_integral_value()), held[s])
            if max_o and qty * p > max_o:
                qty = floor_shares(max_o, p)
        if qty <= 0 or qty * p < min_o:
            notes.append("%s sell of %s is under your %s minimum order or one share: skipped"
                         % (s, inr(qty * p), inr(min_o, 0)))
            continue
        o, ch = line(s, "sell", qty, sl["why"])
        cash_after += qty * p - ch
        lines.append(o)

    spendable = max(cash_after - buf, ZERO)
    want = sum((b["rupees"] for b in buys), ZERO)
    scale = Decimal(1)
    if want > spendable:
        scale = spendable / want if want else ZERO
        notes.append("buys scaled to %s%%: only %s is free above your %s cash buffer"
                     % ((HUNDRED * scale).quantize(Decimal("1")), inr(spendable), inr(buf)))
    for b in buys:
        s, p = b["symbol"], prices[b["symbol"]]
        rupees = b["rupees"] * scale
        if max_o and rupees > max_o:
            notes.append("%s buy cut to your %s max order; run again after it fills" % (s, inr(max_o, 0)))
            rupees = max_o
        qty = floor_shares(rupees, p)
        # keep the charges inside the money above the buffer too
        while qty > 0:
            ch = charges_for(c, "buy", qty, p)
            if qty * p + (ch["total"] if ch else ZERO) <= spendable:
                break
            qty -= 1
        if qty < 1 or qty * p < min_o:
            notes.append("%s buy of %s does not reach one whole share or your %s minimum: skipped"
                         % (s, inr(rupees), inr(min_o, 0)))
            continue
        o, ch = line(s, "buy", qty, b["why"])
        spendable -= qty * p + ch
        cash_after -= qty * p + ch
        lines.append(o)

    return {"account_value": paise(total), "cash": paise(cash), "cash_buffer": paise(buf), "cap": paise(cap),
            "band": band, "rows": rows, "orders": lines, "notes": notes,
            "charges": charges_total if c.get("broker") else None, "charges_verified": not unverified,
            "cash_after": paise(cash_after)}


def _n(x):
    x = D(x)
    return str(x.to_integral_value()) if x == x.to_integral_value() else str(x.normalize())


def report(res):
    lines = ["Account %s (cash %s, buffer %s, cap %s per stock), band %s points"
             % (inr(res["account_value"]), inr(res["cash"]), inr(res["cash_buffer"]), inr(res["cap"]),
                _n(res["band"])), ""]
    lines.append("%-12s %7s %12s %14s %8s %9s %8s  %s" % ("symbol", "shares", "price", "value", "now %",
                                                         "target %", "drift", "action"))
    for r in res["rows"]:
        lines.append("%-12s %7d %12s %14s %7s%% %8s%% %8s  %s"
                     % (r["symbol"], r["shares"], inr(r["price"]), inr(r["value"]), r["current_pct"],
                        r["target_pct"], ("+" if r["drift"] > 0 else "") + str(r["drift"]), r["action"]))
    lines.append("")
    if res["orders"]:
        lines.append("Order sheet, sells first (delivery, market, whole shares; nothing has been placed):")
        for o in res["orders"]:
            lines.append("  %s %d %s on %s, about %s" % (o["side"].upper(), o["qty"], o["symbol"], o["exchange"],
                                                         inr(o["value"])))
        if res["charges"] is not None:
            lines.append("Estimated charges: %s%s." % (inr(res["charges"]),
                                                       "" if res["charges_verified"] else " (rates UNVERIFIED)"))
        else:
            lines.append("Charges not estimated: no \"broker\" in the config.")
        lines.append("Cash after, if all of them fill: about %s." % inr(res["cash_after"]))
    else:
        lines.append("Nothing is outside the band. No trades.")
    for n in res["notes"]:
        lines.append("Note: " + n)
    lines.append("")
    lines.append("Not checked here: tick sizes, price bands and circuit limits. Sale proceeds settle T+1; "
                 "whether you can spend them today is your broker's rule.")
    lines.append("Selling can realise a taxable gain. Then trader-risk checks every line, and you enter the ones "
                 "you choose by hand in your broker.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def _jsonable(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_jsonable(v) for v in x]
    return x


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bring the portfolio back to its target weights (trader-rebalance).")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--targets", default="trader/targets.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--band", help="drift band in percentage points (default 5)")
    ap.add_argument("--sell-untracked", action="store_true", help="sell holdings that are not in the targets")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the order sheet (default trader/orders.json)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio (your holdings and cash)")
        pdoc = read_json(a.prices, "prices") or {}
        prices = parse_prices(pdoc)
        weights = parse_targets(read_json(a.targets, "targets"), cfg.get("blocked"))
        res = plan(portfolio, prices, weights, cfg, a.band, a.sell_untracked)
        if a.write and res["orders"]:
            d = os.path.dirname(a.write)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(a.write, "w", encoding="utf-8") as fh:
                json.dump({"created_by": "trader-rebalance",
                           "created_at": datetime.datetime.now().astimezone().replace(microsecond=0).isoformat(),
                           "status": "proposed", "paper": bool(cfg.get("paper", True)), "currency": "INR",
                           "note": ("An order sheet, not orders, sells first. Nothing is placed by this project: "
                                    "trader-risk checks each line, then you enter the ones you choose by hand."),
                           "prices_as_of": pdoc.get("as_of", "") if isinstance(pdoc, dict) else "",
                           "orders": res["orders"]}, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
    except RebalanceError as e:
        print("rebalance.py: %s" % e, file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(_jsonable(res), indent=2, ensure_ascii=False))
    else:
        print(report(res))
        if a.write and res["orders"]:
            print("Wrote %d order line(s) to %s." % (len(res["orders"]), a.write))
    return 0 if res["orders"] else 1


if __name__ == "__main__":
    sys.exit(main())
