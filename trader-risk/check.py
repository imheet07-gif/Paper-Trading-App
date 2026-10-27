#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk, India edition. Standard library only, no network.

Reads the order sheet (trader/orders.json) and checks every line against
your rules (trader/config.json), your account (trader/portfolio.json) and
prices (trader/prices.json). Each line gets PASS or BLOCK with the reasons.
Lines are checked in sheet order as if each PASS had filled, so five buys
that are fine one by one but break the cash buffer together are caught.

    python3 check.py                       # the trader/ files, writes trader/state/risk.json
    python3 check.py --orders trader/orders.json --json
    python3 check.py --no-write            # check without recording the verdicts

What blocks a line:

  format        missing symbol or side, a quantity that is not a positive
                whole number of shares, a rupee amount instead of shares,
                an exchange other than NSE or BSE, a product other than
                delivery, a limit order without a limit price
  asset         futures and options (F&O) unless "allow_options": true;
                crypto and anything that does not look like an NSE or BSE
                cash-market symbol
  blocked       a symbol or ISIN on your "blocked" list
  size          under min_order_inr, over max_order_inr
  position      the stock would be over max_position_pct of the account
  cash          the buy (with estimated charges) would dip into your cash
                buffer, or need margin
  short         selling more than you hold, unless "allow_short": true
  duplicate     the same symbol and side twice in this sheet
  sheet limit   more than max_orders_per_day lines in this sheet

Not checked, because the facts are not yet in core/ data read from the
publisher's file: tick sizes, price bands and circuit limits, F&O lot
sizes, and whether your broker lets you spend today's sale proceeds today.

Exit code 0 when every line passes, 1 when any line is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each line,
so an edited line is known to be unchecked.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
from money import D, inr, paise  # noqa: E402

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 50000,
    "min_order_inr": 1000, "max_orders_per_day": 10, "paper": True, "blocked": [], "broker": None,
    "allow_options": False, "allow_margin": False, "allow_short": False,
}
# the fields that make a line what it is; "reason" and the estimates are not part of it
ORDER_FIELDS = ("symbol", "isin", "exchange", "side", "qty", "product", "order_type", "limit_price")
EXCHANGES = {"NSE", "BSE"}
TYPES = {"market", "limit"}
# a loose shape, not NSE's rule: letters and digits, with & and - (M&M, BAJAJ-AUTO)
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
# F&O contract names carry an expiry and end in CE, PE or FUT (NIFTY26OCT25000CE)
DERIVATIVE = re.compile(r"\d{2}[A-Z]{3}.*(CE|PE|FUT)$")
ZERO = Decimal(0)
HUNDRED = Decimal(100)


class RiskError(Exception):
    pass


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """The exact line that was checked: a hash of the fields that define it."""
    core = {k: str(order[k]) for k in ORDER_FIELDS if k in order and order[k] not in (None, "")}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _dec(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(x) if isinstance(x, (int, float, Decimal)) else D(str(x).strip().replace(",", ""))
    except Exception:
        return None
    return v if v.is_finite() else None


def _qty(x):
    """A whole number of shares as an int, or None."""
    v = _dec(x)
    if v is None or v != v.to_integral_value():
        return None
    return int(v)


def _sym(x):
    return str(x or "").strip().upper()


def account(cfg, portfolio, prices):
    """Starting account: cash and, per symbol, shares and value. No portfolio =
    a fresh account funded with the budget."""
    warnings = []
    if portfolio is None:
        budget = _dec(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}, "isin": {}}, warnings
    cash = _dec(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" amount in rupees")
    shares, values, isin = {}, {}, {}
    for p in portfolio.get("positions", []):
        s = _sym(p.get("symbol") or p.get("ticker"))
        if not s:
            continue
        n = _dec(p.get("qty", p.get("shares")))
        if n is None:
            n = sum((_dec(l.get("qty", l.get("shares"))) or ZERO for l in p.get("lots", [])), ZERO)
        shares[s] = shares.get(s, ZERO) + n
        if p.get("isin"):
            isin[s] = _sym(p["isin"])
        if prices.get(s):
            values[s] = shares[s] * prices[s]
        else:
            basis = sum((_dec(l.get("cost")) or ZERO for l in p.get("lots", [])), ZERO)
            values[s] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (s, inr(basis)))
    return {"cash": cash, "shares": shares, "values": values, "isin": isin}, warnings


def est_charges(cfg, side, qty, price, exchange):
    """Estimated charges on one delivery fill, or (None, None) without a broker."""
    broker = cfg.get("broker")
    if not broker:
        return None, None
    import charges
    try:
        c = charges.trade_charges(broker, side, qty, price, "delivery", exchange)
    except KeyError as e:
        raise RiskError("charges: %s (core/charges.json)" % e.args[0])
    return c["total"], c["verified"]


def check(orders, cfg, portfolio=None, prices=None, today=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    prices = {_sym(k): _dec(v) for k, v in (prices or {}).items() if _dec(v) and _dec(v) > 0}
    today = today or datetime.date.today().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {_sym(b) for b in c.get("blocked") or []}
    seen = set()
    results = []
    unverified = False
    if not c.get("paper", True):
        top_warn.append("LIVE (\"paper\": false): these lines are for real money")
    if not c.get("broker"):
        top_warn.append("no \"broker\" in the config: charges are not estimated or counted")

    for i, o in enumerate(orders, 1):
        reasons, warns = [], []
        sym = _sym(o.get("symbol"))
        isin = _sym(o.get("isin"))
        side = str(o.get("side", "")).strip().lower()
        exchange = _sym(o.get("exchange") or "NSE")
        product = str(o.get("product", "delivery")).strip().lower()
        typ = str(o.get("order_type", "market")).strip().lower()
        qty = _qty(o.get("qty"))
        value = None
        charge = None

        # format
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        if o.get("notional") is not None or o.get("amount") is not None:
            reasons.append("format: give whole shares in qty, not a rupee amount")
        if qty is None or qty <= 0:
            reasons.append("format: qty must be a positive whole number of shares, got %r" % o.get("qty"))
        if exchange not in EXCHANGES:
            reasons.append("format: exchange must be NSE or BSE, got %r" % o.get("exchange"))
        if product != "delivery":
            reasons.append("format: product %r; this project sizes delivery (CNC) only, "
                           "not intraday, MTF or margin" % o.get("product"))
        if typ not in TYPES:
            reasons.append("format: order_type must be market or limit, got %r" % o.get("order_type"))
        limit = _dec(o.get("limit_price"))
        if typ == "limit" and (limit is None or limit <= 0):
            reasons.append("format: a limit order needs a positive limit_price")
        if typ == "limit" and limit:
            warns.append("limit price not checked against the tick size or the price band")

        # asset
        if sym and (DERIVATIVE.search(sym) or str(o.get("segment", "")).upper() in ("FO", "NFO", "F&O", "BFO")):
            if not c.get("allow_options"):
                reasons.append("asset: %s is a futures or options contract; F&O is off "
                               "(\"allow_options\": false)" % sym)
            else:
                warns.append("F&O line: lot sizes and margins are not checked here")
        elif sym and not SYMBOL.match(sym):
            reasons.append("asset: %s does not look like an NSE or BSE cash-market symbol; "
                           "this project sizes listed shares only" % sym)

        # blocked list
        if sym in blocked or (isin and isin in blocked):
            reasons.append("blocked: %s is on your blocked list" % sym)

        # rupee value of the line
        ref = limit if typ == "limit" and limit else prices.get(sym)
        if ref is None and _dec(o.get("ref_price")):
            ref = _dec(o.get("ref_price"))
            warns.append("valued at the sheet's ref_price %s: no price in trader/prices.json" % inr(ref))
        if qty and qty > 0:
            if ref:
                value = qty * ref
            else:
                reasons.append("price: no price for %s to value %d shares; add it to trader/prices.json" % (sym, qty))

        if value is not None:
            if exchange in EXCHANGES and side in ("buy", "sell"):
                charge, ver = est_charges(c, side, qty, ref, exchange)
                unverified = unverified or (ver is False)
            min_o, max_o = _dec(c.get("min_order_inr")) or ZERO, _dec(c.get("max_order_inr"))
            if value < min_o:
                reasons.append("size: %s is under your %s minimum order" % (inr(value), inr(min_o, 0)))
            if max_o and value > max_o:
                reasons.append("size: %s is over your %s max order" % (inr(value), inr(max_o, 0)))

        # account effects, simulated in order
        cash = acct["cash"]
        held = acct["shares"].get(sym, ZERO)
        held_value = acct["values"].get(sym, ZERO)
        total = cash + sum(acct["values"].values(), ZERO)
        buffer_inr = total * (_dec(c.get("cash_buffer_pct")) or ZERO) / HUNDRED
        cost = (value or ZERO) + (charge or ZERO)
        if value is not None and side == "buy":
            after_cash = cash - cost
            if after_cash < 0 and not c.get("allow_margin"):
                reasons.append("cash: needs %s but only %s cash; that would be margin (borrowed money)"
                               % (inr(cost), inr(cash)))
            elif after_cash < buffer_inr:
                reasons.append("cash: leaves %s cash, under your %s%% buffer of %s"
                               % (inr(max(after_cash, ZERO)), _n(c["cash_buffer_pct"]), inr(buffer_inr)))
            pos_after = held_value + value
            cap = total * (_dec(c.get("max_position_pct")) or HUNDRED) / HUNDRED
            if total > 0 and pos_after > cap:
                reasons.append("position: %s would be %s, %s%% of the %s account, over your %s%% cap"
                               % (sym, inr(pos_after), (HUNDRED * pos_after / total).quantize(Decimal("0.1")),
                                  inr(total), _n(c["max_position_pct"])))
        if side == "sell" and sym and qty:
            if qty > held and not c.get("allow_short"):
                reasons.append("short: selling %d %s but you hold %s; short selling is off" % (qty, sym, _n(held)))

        # duplicates and the sheet limit
        key = (sym, side)
        if key in seen:
            reasons.append("duplicate: a second %s %s in this sheet" % (side, sym))
        seen.add(key)
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and passed_so_far >= cap_n:
            reasons.append("sheet limit: %d lines passed already, your limit is %d a day" % (passed_so_far, cap_n))

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS" and value is not None:
            if side == "buy":
                acct["cash"] -= cost
                acct["values"][sym] = acct["values"].get(sym, ZERO) + value
                acct["shares"][sym] = held + qty
            else:
                acct["cash"] += value - (charge or ZERO)
                acct["values"][sym] = max(acct["values"].get(sym, ZERO) - value, ZERO)
                acct["shares"][sym] = held - qty
        results.append({
            "index": i, "symbol": sym, "side": side, "qty": qty,
            "value_inr": str(paise(value)) if value is not None else None,
            "est_charges": str(charge) if charge is not None else None,
            "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns,
        })

    if unverified:
        top_warn.append("charge rates are UNVERIFIED: not yet matched to a contract note (core/charges.json)")
    return {
        "date": today,
        "paper": bool(c.get("paper", True)),
        "currency": "INR",
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": str(paise(acct["cash"])),
        "not_checked": ["tick sizes", "price bands and circuit limits", "F&O lot sizes",
                        "same-day use of sale proceeds (your broker's rule)"],
    }


def _n(x):
    x = D(x)
    return str(x.to_integral_value()) if x == x.to_integral_value() else str(x.normalize())


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        amt = ("%s shares" % r["qty"]) if r["qty"] is not None else "?"
        if r["value_inr"]:
            amt += ", about %s" % inr(r["value_inr"])
        lines.append("%-5s #%d %s %s %s" % (r["verdict"], r["index"], r["side"], r["symbol"], amt))
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d passed, %d blocked. Cash after the passed lines: %s (%s)."
                 % (res["pass"], res["block"], inr(res["cash_after"]), "paper" if res["paper"] else "LIVE"))
    lines.append("Not checked: %s." % ", ".join(res["not_checked"]))
    if res["block"]:
        lines.append("Do not enter a blocked line. Fix the line or your rules, then check again.")
    else:
        lines.append("Every line passed. A PASS is a rules check, not a yes: you decide each line "
                     "and enter it by hand in your broker.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check an order sheet against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        doc = read_json(a.orders, "orders file (run trader-budget or trader-rebalance first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices = pdoc.get("prices", pdoc) if isinstance(pdoc, dict) else {}
        res = check(orders, cfg, portfolio, prices, a.today)
        if isinstance(pdoc, dict) and pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
            rec["orders_file"] = a.orders
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
