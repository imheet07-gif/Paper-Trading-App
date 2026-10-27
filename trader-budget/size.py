#!/usr/bin/env python3
"""Position sizing for trader-budget, India edition. Standard library only, no network.

Turns target weights (the portfolio you want) and your rules (budget, cash
buffer, a cap on any one stock, a minimum order) into whole shares per NSE
stock, then into an order sheet you enter by hand in your broker. It never
places anything: it writes proposals to trader/orders.json, which
trader-risk checks.

    python3 size.py --prices trader/prices.json            # trader/targets.json + trader/config.json
    python3 size.py --budget 100000 --write                # write trader/orders.json
    python3 size.py --weights RELIANCE=30,TCS=25,HDFCBANK=25,ITC=20 --prices p.json
    python3 size.py --json

How the money is split:

  1. cash buffer   budget x cash_buffer_pct stays in cash, never invested
  2. weights       renormalised to 100 over the stocks you keep (blocked
                   symbols are dropped first; --top N keeps the N biggest)
  3. cap           no stock gets more than max_position_pct of the budget;
                   anything over the cap is shared out to the others by
                   weight, again and again until nobody is over
  4. whole shares  India has no fractional shares: each slice buys the whole
                   shares it can, then what is left buys one more share at a
                   time for the stock furthest below its slice, while the
                   cash lasts and the cap holds
  5. charges       with a "broker" in the config, the buy charges from
                   core/charges.py are estimated and kept inside the
                   investable money (a share comes off if they do not fit)
  6. min order     a slice under min_order_inr is dropped and its weight
                   shared out, so you never place a Rs 40 order

Money that cannot be invested (every stock at its cap, or whole shares that
do not divide evenly) is reported as "left idle", never hidden.

Left out on purpose (not yet in core/ data, read from the publisher's file):
tick sizes, price bands and circuit limits, F&O lot sizes. Orders are market
orders for delivery; a limit price is yours to set on the instrument's tick.
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

DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 25,
    "max_order_inr": 50000,
    "min_order_inr": 1000,
    "broker": None,
    "exchange": "NSE",
    "paper": True,
    "blocked": [],
    "allow_options": False,
}
ZERO = Decimal(0)
HUNDRED = Decimal(100)


class SizeError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "config"))
    return cfg


def _dec(x, what):
    try:
        v = D(str(x).strip()) if not isinstance(x, (int, float, Decimal)) else D(x)
    except Exception:
        raise SizeError("%s is not a number: %r" % (what, x))
    if not v.is_finite():
        raise SizeError("%s is not a number: %r" % (what, x))
    return v


def parse_weights(data):
    """Accept targets.json, a {symbol: weight} dict or a list of rows.

    Returns [{"symbol", "isin", "name", "weight"}] with weight as a Decimal."""
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    rows = []
    if isinstance(data, dict):
        for s, w in data.items():
            rows.append({"symbol": s, "isin": "", "name": "", "weight": w})
    elif isinstance(data, list):
        for i, r in enumerate(data):
            if not isinstance(r, dict):
                raise SizeError("weights[%d] is not an object" % i)
            s = r.get("symbol") or r.get("ticker")
            w = r.get("weight", r.get("weight_pct", r.get("pct")))
            if not s or w is None:
                raise SizeError("weights[%d] needs a symbol and a weight" % i)
            rows.append({"symbol": s, "isin": r.get("isin", ""), "name": r.get("name", ""), "weight": w})
    else:
        raise SizeError("weights must be a list or an object")
    out = []
    for r in rows:
        sym = str(r["symbol"]).strip().upper()
        out.append({"symbol": sym, "isin": str(r["isin"] or "").strip().upper(), "name": r["name"] or "",
                    "weight": _dec(r["weight"], "weight for %s" % sym)})
    return out


def weights_from_arg(text):
    """RELIANCE=30,TCS=25 -> {symbol: weight}"""
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SizeError("--weights wants SYMBOL=WEIGHT pairs, got %r" % part)
        s, w = part.split("=", 1)
        out[s.strip().upper()] = w.strip()
    return out


def load_prices(path):
    """{symbol: Decimal} from {"prices": {...}} or a bare dict; prices that are
    not positive numbers are left out (and then reported as missing)."""
    if not path:
        return {}, ""
    data = read_json(path, "prices")
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    out = {}
    for s, p in prices.items():
        if isinstance(p, bool):
            continue
        try:
            v = D(str(p)) if not isinstance(p, (int, float)) else D(p)
        except Exception:
            continue
        if v.is_finite() and v > 0:
            out[str(s).upper()] = v
    return out, (data.get("as_of", "") if isinstance(data, dict) else "")


# ---------------------------------------------------------------- the maths

def normalise(rows, blocked=(), top=None):
    """Drop blocked and non-positive weights, keep the top N, renormalise to 100."""
    blocked = {str(b).upper() for b in (blocked or [])}
    dropped, keep, seen, isins = [], [], set(), {}
    for r in rows:
        if r["symbol"] in seen:
            raise SizeError("%s is listed twice in the weights" % r["symbol"])
        seen.add(r["symbol"])
        if r["isin"]:
            if r["isin"] in isins:
                raise SizeError("%s and %s share the ISIN %s; one stock, one row"
                                % (isins[r["isin"]], r["symbol"], r["isin"]))
            isins[r["isin"]] = r["symbol"]
        if r["symbol"] in blocked or (r["isin"] and r["isin"] in blocked):
            dropped.append({"symbol": r["symbol"], "reason": "on your blocked list"})
        elif r["weight"] <= 0:
            dropped.append({"symbol": r["symbol"], "reason": "weight is zero or negative"})
        else:
            keep.append(dict(r))
    keep.sort(key=lambda r: (-r["weight"], r["symbol"]))
    if top:
        for r in keep[top:]:
            dropped.append({"symbol": r["symbol"], "reason": "outside the top %d" % top})
        keep = keep[:top]
    return keep, dropped


def allocate(rows, investable, cap):
    """Exact rupees per symbol with the cap enforced.

    Proportional to weight; any stock over the cap is pinned at the cap and
    the rest of the money is shared over the others, repeated until no stock
    is over. Returns ({symbol: Decimal}, set of capped symbols)."""
    alloc, capped = {}, set()
    active = list(rows)
    money = investable
    while active:
        wsum = sum(r["weight"] for r in active)
        trial = {r["symbol"]: money * r["weight"] / wsum for r in active}
        over = [r for r in active if trial[r["symbol"]] > cap]
        if not over:
            alloc.update(trial)
            break
        for r in over:
            alloc[r["symbol"]] = cap
            capped.add(r["symbol"])
            money -= cap
        active = [r for r in active if r["symbol"] not in capped]
        if money <= 0:
            for r in active:
                alloc[r["symbol"]] = ZERO
            break
    return alloc, capped


def buy_charges(broker, qty, price, exchange, rates=None):
    """Estimated charges on one delivery buy, or None without a broker."""
    if not broker or qty <= 0:
        return None
    import charges
    try:
        c = charges.trade_charges(broker, "buy", qty, price, "delivery", exchange, rates)
    except KeyError as e:
        raise SizeError("charges: %s (core/charges.json)" % e.args[0])
    return c


def whole_shares(slices, prices, order, cap, money, cost=None):
    """Floor each slice to whole shares, then buy one more share at a time for
    the stock furthest below its slice while the money lasts and the cap holds.

    cost(symbol, qty) -> rupees including charges; plain qty x price without one."""
    cost = cost or (lambda s, q: q * prices[s])
    shares = {}
    for s in order:
        shares[s] = int((slices[s] / prices[s]).to_integral_value(rounding=ROUND_DOWN)) if slices[s] > 0 else 0
    # charges can push the floor over the money: take a share off the most over-slice stock
    while sum(cost(s, shares[s]) for s in order) > money:
        held = [s for s in order if shares[s] > 0]
        if not held:
            break
        worst = max(held, key=lambda s: (shares[s] * prices[s] - slices[s], s))
        shares[worst] -= 1
    while True:
        spent = sum(cost(s, shares[s]) for s in order)
        best, gap = None, None
        for s in order:
            n = shares[s] + 1
            if (n * prices[s] > cap) or (spent - cost(s, shares[s]) + cost(s, n) > money):
                continue
            g = slices[s] - shares[s] * prices[s]
            if best is None or g > gap:
                best, gap = s, g
        if best is None:
            break
        shares[best] += 1
    return shares


def size(rows, cfg, budget=None, prices=None, top=None, rates=None):
    """Pure function: weights + rules + prices -> the full sizing result (a dict)."""
    budget = D(budget if budget is not None else (cfg.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json (rupees)")
    buf_pct = D(cfg.get("cash_buffer_pct", 10))
    cap_pct = D(cfg.get("max_position_pct", 25))
    min_order = D(cfg.get("min_order_inr", 0) or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    if not 0 < cap_pct <= 100:
        raise SizeError("max_position_pct must be above 0 and at most 100")
    broker = cfg.get("broker") or None
    exchange = str(cfg.get("exchange") or "NSE").upper()
    prices = prices or {}

    keep, dropped = normalise(rows, cfg.get("blocked"), top)
    if not keep:
        raise SizeError("nothing to size: no weights left after the blocked list")
    missing = [r["symbol"] for r in keep if not prices.get(r["symbol"])]
    if missing:
        raise SizeError("whole shares need a price for: %s (add them to trader/prices.json "
                        "as {\"prices\": {\"SYMBOL\": price}}); prices are never guessed" % ", ".join(missing))

    cash_buffer = paise(budget * buf_pct / HUNDRED)
    investable = budget - cash_buffer
    cap = paise(budget * cap_pct / HUNDRED)
    memo = {}

    def cost(s, q):
        if q <= 0:
            return ZERO
        c = memo.get((s, q))
        if c is None:
            ch = buy_charges(broker, q, prices[s], exchange, rates)
            c = q * prices[s] + (ch["total"] if ch else ZERO)
            memo[(s, q)] = c
        return c

    while True:
        live = [dict(r) for r in keep]
        tot = sum(r["weight"] for r in live)
        for r in live:
            r["weight"] = HUNDRED * r["weight"] / tot
        exact, capped = allocate(live, investable, cap)
        order = [r["symbol"] for r in sorted(live, key=lambda r: (-exact[r["symbol"]], r["symbol"]))]
        shares = whole_shares(exact, prices, order, cap, investable, cost)
        value = {s: shares[s] * prices[s] for s in order}
        small = [r for r in live if value[r["symbol"]] < min_order or shares[r["symbol"]] == 0]
        if not small:
            break
        worst = min(small, key=lambda r: (r["weight"], r["symbol"]))
        s = worst["symbol"]
        if shares[s] == 0 and prices[s] > exact[s]:
            reason = "one share costs %s, more than its %s slice" % (inr(prices[s]), inr(exact[s]))
        else:
            reason = "its slice (%s) is under your %s minimum order" % (inr(value[s]), inr(min_order))
        dropped.append({"symbol": s, "reason": reason})
        keep = [r for r in keep if r["symbol"] != s]
        if not keep:
            raise SizeError("every slice is under your minimum order or one share; raise the budget or hold fewer stocks")

    positions = []
    total_charges = ZERO
    verified = True
    for r in sorted(live, key=lambda r: (-r["weight"], r["symbol"])):
        s = r["symbol"]
        ch = buy_charges(broker, shares[s], prices[s], exchange, rates)
        if ch:
            total_charges += ch["total"]
            verified = verified and ch["verified"]
        positions.append({
            "symbol": s, "isin": r["isin"], "name": r["name"], "weight": r["weight"].quantize(Decimal("0.01")),
            "price": prices[s], "shares": shares[s], "value": paise(value[s]),
            "charges": ch["total"] if ch else None, "capped": s in capped,
            "slice": paise(exact[s]),
        })
    invested = sum((p["value"] for p in positions), ZERO)
    return {
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer,
        "investable": investable, "max_position_pct": cap_pct, "cap": cap, "min_order_inr": min_order,
        "broker": broker, "exchange": exchange, "positions": positions, "invested": invested,
        "charges": total_charges if broker else None, "charges_verified": verified if broker else None,
        "idle": investable - invested - total_charges, "dropped": dropped,
    }


# ---------------------------------------------------------------- orders

def orders_from(result, source=""):
    """The order sheet: one delivery market buy per position, whole shares."""
    out = []
    for p in result["positions"]:
        if p["shares"] <= 0:
            continue
        why = "%s%% of the stock sleeve" % p["weight"]
        if source:
            why = "%s, from %s" % (why, source)
        if p["capped"]:
            why += "; held at the %s%% cap of the %s budget" % (_n(result["max_position_pct"]), inr(result["budget"], 0))
        o = {"symbol": p["symbol"]}
        if p["isin"]:
            o["isin"] = p["isin"]
        o.update({"exchange": result["exchange"], "side": "buy", "qty": p["shares"], "product": "delivery",
                  "order_type": "market", "ref_price": str(p["price"]), "value": str(p["value"])})
        if p["charges"] is not None:
            o["est_charges"] = str(p["charges"])
        o["reason"] = why
        out.append(o)
    return out


def write_orders(path, orders, result, cfg, source="", prices_as_of=""):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {
        "created_by": "trader-budget",
        "created_at": datetime.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "status": "proposed",
        "paper": bool(cfg.get("paper", True)),
        "currency": "INR",
        "note": ("An order sheet, not orders. Nothing is placed by this project: trader-risk checks each "
                 "line, then you enter the ones you choose by hand in your broker."),
        "source": source,
        "prices_as_of": prices_as_of,
        "orders": orders,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------- report

def _n(x):
    x = D(x)
    return str(x.to_integral_value()) if x == x.to_integral_value() else str(x.normalize())


def report(result, orders, source="", prices_as_of=""):
    r = result
    lines = []
    if source:
        lines.append("Targets: %s" % source)
    if prices_as_of:
        lines.append("Prices as of %s" % prices_as_of)
    lines.append("Budget %s, cash buffer %s%% = %s, investing %s, cap %s%% = %s per stock"
                 % (inr(r["budget"], 0), _n(r["cash_buffer_pct"]), inr(r["cash_buffer"]),
                    inr(r["investable"]), _n(r["max_position_pct"]), inr(r["cap"])))
    lines.append("")
    lines.append("%-12s %8s %12s %7s %14s  note" % ("symbol", "weight", "price", "shares", "value"))
    for p in r["positions"]:
        row = "%-12s %7s%% %12s %7d %14s" % (p["symbol"], p["weight"], inr(p["price"]), p["shares"], inr(p["value"]))
        if p["capped"]:
            row += "  at the %s%% cap" % _n(r["max_position_pct"])
        lines.append(row)
    lines.append("%-12s %8s %12s %7s %14s" % ("invested", "", "", "", inr(r["invested"])))
    if r["charges"] is not None:
        lines.append("%-12s %8s %12s %7s %14s  %s" % ("charges", "", "", "", inr(r["charges"]),
                     "estimated, %s rates" % r["broker"] + ("" if r["charges_verified"] else ", UNVERIFIED")))
    lines.append("%-12s %8s %12s %7s %14s" % ("cash buffer", "", "", "", inr(r["cash_buffer"])))
    lines.append("%-12s %8s %12s %7s %14s" % ("left idle", "", "", "", inr(r["idle"])))
    lines.append("")
    if r["charges"] is None:
        lines.append("Charges not estimated: no \"broker\" in the config (core/charges.json has the rates).")
    for d in r["dropped"]:
        lines.append("Dropped %s: %s" % (d["symbol"], d["reason"]))
    lines.append("")
    lines.append("Order sheet (delivery, market, whole shares; nothing has been placed):")
    for o in orders:
        lines.append("  BUY %d %s on %s, about %s" % (o["qty"], o["symbol"], o["exchange"], inr(o["value"])))
    lines.append("")
    lines.append("Not checked here: tick sizes, price bands and circuit limits. A market order can fill away "
                 "from the reference price.")
    lines.append("Next: trader-risk checks every line; you enter the ones you choose in your broker yourself.")
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
    ap = argparse.ArgumentParser(description="Size positions to your budget in whole shares (trader-budget).")
    ap.add_argument("--targets", default="trader/targets.json", help="target weights (default trader/targets.json)")
    ap.add_argument("--weights", help="inline weights instead of a file: RELIANCE=30,TCS=25")
    ap.add_argument("--config", default="trader/config.json", help="rules (default trader/config.json)")
    ap.add_argument("--budget", help="override the budget in the config (rupees)")
    ap.add_argument("--top", type=int, help="keep only the N biggest weights")
    ap.add_argument("--prices", default="trader/prices.json", help="prices JSON (default trader/prices.json)")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the order sheet (default trader/orders.json)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the table")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        source = ""
        if a.weights:
            rows = parse_weights(weights_from_arg(a.weights))
        else:
            data = read_json(a.targets, "targets file (pass --weights, or write trader/targets.json)")
            rows = parse_weights(data)
            if isinstance(data, dict):
                source = data.get("source", "")
        prices, as_of = load_prices(a.prices)
        budget = _dec(a.budget, "--budget") if a.budget is not None else None
        result = size(rows, cfg, budget, prices, a.top)
        orders = orders_from(result, source)
        if a.write:
            write_orders(a.write, orders, result, cfg, source, as_of)
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(_jsonable({"result": result, "orders": orders}), indent=2, ensure_ascii=False))
    else:
        print(report(result, orders, source, as_of))
        if a.write:
            print("Wrote %d order line(s) to %s" % (len(orders), a.write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
