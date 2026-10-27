#!/usr/bin/env python3
"""Paper trader for trader-broker, India edition. Standard library only, no network.

This tool never talks to a broker and moves no money. It takes the order
sheet written by trader-budget or trader-rebalance, shows each line as a
plain ticket, records the user's explicit yes or no for each one, and fills
the approved lines into a PAPER portfolio (trader/paper_portfolio.json) at a
price the user gives or the line's reference price, with the charges a real
fill would cost (core/charges.py). The user's real account is never touched.

  show      every line with its trader-risk verdict; READY only if trader-risk
            passed THIS exact line today (matched by fingerprint)
  approve   records an explicit yes for ONE line, the reply word for word; it
            refuses "yes to all", "sure", "ok", a yes naming another stock or
            another quantity, and any line that is not READY
  decline   records a no
  fill      paper-fills an approved line: cash, a FIFO lot for a buy, the
            oldest lots relieved for a sell, charges deducted
  log       today's decisions and fills

    python3 ticket.py show
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py fill --index 1                 # at the line's reference price
    python3 ticket.py fill --index 1 --price 1398.5  # at the price you would have got
    python3 ticket.py log

Decisions and fills go to trader/state/paper_broker.json (append only).
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import nse_calendar  # noqa: E402
from money import D, inr, paise  # noqa: E402

# the same fields and hash as trader-risk/check.py: the exact line that was checked
ORDER_FIELDS = ("symbol", "isin", "exchange", "side", "qty", "product", "order_type", "limit_price")
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}
ZERO = Decimal(0)


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def now_ist():
    return dt.datetime.now(nse_calendar.IST).replace(microsecond=0).isoformat()


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the line

def fingerprint(order):
    """Same function as trader-risk/check.py."""
    core = {k: str(order[k]) for k in ORDER_FIELDS if k in order and order[k] not in (None, "")}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def plain(order):
    """BUY 17 RELIANCE on NSE, delivery, market order"""
    side = str(order.get("side", "")).upper()
    q = order.get("qty")
    what = "%s share%s of %s" % (q, "" if str(q) == "1" else "s", str(order.get("symbol", "?")).upper())
    if str(order.get("order_type", "market")) == "limit":
        how = "limit order at %s" % inr(order.get("limit_price") or 0)
    else:
        how = "market order"
    return "%s %s on %s, %s, %s" % (side, what, order.get("exchange", "NSE"), order.get("product", "delivery"), how)


def numbers_in(order):
    out = []
    for k in ("qty", "limit_price"):
        try:
            if order.get(k) not in (None, ""):
                out.append(D(str(order[k])))
        except Exception:
            pass
    return out


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one line? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one line at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("symbol", "")).upper()
    tokens = set(re.findall(r"[A-Z0-9][A-Z0-9&\-]*", raw.upper()))
    named = tokens & ({s.upper() for s in batch_symbols} - {sym})
    if named:
        return False, "the reply names %s, but this line is for %s" % (", ".join(sorted(named)), sym)
    nums = [D(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    nums = [n for n in nums if str(n) != sym]
    if nums:
        want = numbers_in(order)
        if not any(n == w for n in nums for w in want):
            return False, "the reply says %s, but this line is %s" % (", ".join(str(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "paper broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked this sheet (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact line was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk result, latest decision)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    rec = latest(state, fp, today)
    if rec and rec.get("status") == "filled":
        return False, "DONE: paper-filled at %s" % rec.get("at"), rv, rec
    if rec and rec.get("status") == "declined":
        return False, "DONE: declined at %s" % rec.get("at"), rv, rec
    if rv is None:
        return False, "NOT READY: " + why, None, rec
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv, rec
    if rec and rec.get("status") == "approved":
        return False, "APPROVED by you at %s, waiting for the paper fill" % rec.get("at"), rv, rec
    return True, "READY: trader-risk PASS, waiting for your yes", rv, rec


def render(order, idx, total, risk, state, today):
    ready, label, rv, _ = status_of(order, risk, state, today)
    lines = ["LINE %d of %d, PAPER (no money moves)" % (idx, total), "  " + plain(order)]
    if order.get("ref_price"):
        lines.append("  reference price %s, about %s" % (inr(order["ref_price"]),
                                                         inr(D(str(order["ref_price"])) * D(str(order.get("qty", 0))))))
    if order.get("reason"):
        lines.append("  why: " + order["reason"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    if ready:
        lines.append("  Reply \"yes\" to paper-trade this one line, or \"no\" to skip it.")
    return "\n".join(lines), ready


# ---------------------------------------------------------------- paper fills

def fill_charges(cfg, side, qty, price, exchange):
    broker = cfg.get("broker")
    if not broker:
        return ZERO, None
    import charges
    try:
        c = charges.trade_charges(broker, side, qty, price, "delivery", exchange)
    except KeyError as e:
        raise TicketError("charges: %s (core/charges.json)" % e.args[0])
    return c["total"], c["verified"]


def apply_fill(pf, order, price, cfg, day):
    """Fill one line into the paper portfolio dict (changed in place). Returns the fill record."""
    sym, side = str(order["symbol"]).upper(), str(order["side"]).lower()
    qty = int(order["qty"])
    exchange = str(order.get("exchange") or "NSE").upper()
    value = paise(qty * price)
    cost, verified = fill_charges(cfg, side, qty, price, exchange)
    cash = D(str(pf.get("cash", "0")))
    pos = next((p for p in pf.setdefault("positions", []) if str(p.get("symbol", "")).upper() == sym), None)
    if side == "buy":
        if value + cost > cash:
            raise TicketError("the paper account has %s; this buy needs %s with charges" % (inr(cash), inr(value + cost)))
        if pos is None:
            pos = {"symbol": sym, "qty": 0, "lots": []}
            if order.get("isin"):
                pos["isin"] = order["isin"]
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (sym, len(lots) + 1), "date": day, "qty": qty,
                     "cost": str(paise(value + cost))})  # charges belong in the cost of a buy
        pos["qty"] = int(pos.get("qty", 0)) + qty
        cash -= value + cost
    else:
        if pos is None or int(pos.get("qty", 0)) < qty:
            raise TicketError("the paper account holds %s %s, not %d to sell" % (pos.get("qty", 0) if pos else 0, sym, qty))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left == 0:
                break
            take = min(left, int(lot["qty"]))
            per = D(str(lot["cost"])) / int(lot["qty"])
            lot["cost"] = str(paise(D(str(lot["cost"])) - per * take))
            lot["qty"] = int(lot["qty"]) - take
            left -= take
        pos["lots"] = [l for l in pos["lots"] if int(l["qty"]) > 0]
        pos["qty"] = int(pos["qty"]) - qty
        if pos["qty"] == 0:
            pf["positions"].remove(pos)
        cash += value - cost
    pf["cash"] = str(paise(cash))
    return {"symbol": sym, "side": side, "qty": qty, "price": str(price), "value": str(value), "charges": str(cost),
            "charges_verified": verified, "date": day}


# ---------------------------------------------------------------- commands

def _load(a):
    doc = read_json(a.orders, "order sheet (run trader-budget or trader-rebalance first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no lines in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    today = a.today or dt.datetime.now(nse_calendar.IST).date().isoformat()
    return orders, cfg, risk, state, today


def _pick(orders, index):
    if index is None:
        raise TicketError("say which line: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no line %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def _record(a, state, order, status, extra):
    rec = {"at": now_ist(), "date": a.today or dt.datetime.now(nse_calendar.IST).date().isoformat(),
           "fingerprint": fingerprint(order), "symbol": order.get("symbol"), "side": order.get("side"),
           "qty": order.get("qty"), "status": status}
    rec.update(extra)
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    return rec


def cmd_show(a):
    orders, cfg, risk, state, today = _load(a)
    picked = range(1, len(orders) + 1) if a.index is None else [a.index]
    any_ready = False
    out = []
    for i in picked:
        text, ready = render(_pick(orders, i), i, len(orders), risk, state, today)
        any_ready = any_ready or ready
        out.append(text)
    print("\n\n".join(out))
    print("\nPaper only: nothing here reaches a broker and no money moves. Not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    ready, label, _, _ = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("line %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s." % why)
        return 1
    _record(a, state, order, "approved", {"reply": a.reply})
    print("Recorded your yes for line %d: %s. Paper-fill it with: ticket.py fill --index %d" % (a.index, plain(order), a.index))
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    _record(a, state, order, "declined", {"reply": a.reply or "no"})
    print("Recorded: line %d declined." % a.index)
    return 0


def cmd_fill(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    _, label, _, rec = status_of(order, risk, state, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("line %d has no yes recorded today (%s); approve it first" % (a.index, label))
    price = D(a.price) if a.price else (D(str(order["ref_price"])) if order.get("ref_price") else None)
    if price is None or not price.is_finite() or price <= 0:
        raise TicketError("no price: give --price, the price you would have got")
    pf = read_json(a.portfolio, "paper portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % a.portfolio)
        pf = {"paper": True, "cash": str(paise(D(str(budget)))), "positions": []}
    if not pf.get("paper", False):
        raise TicketError("%s is not marked as a paper portfolio; this tool only fills paper" % a.portfolio)
    f = apply_fill(pf, order, price, cfg, today)
    write_json(a.portfolio, pf)
    _record(a, state, order, "filled", {"fill": f})
    print("PAPER FILL: %s %d %s at %s = %s, charges %s%s. Paper cash now %s." % (
        f["side"].upper(), f["qty"], f["symbol"], inr(f["price"]), inr(f["value"]), inr(f["charges"]),
        "" if f["charges_verified"] in (True, None) else " (rates UNVERIFIED)", inr(pf["cash"])))
    if not a.price:
        print("Filled at the line's reference price; a real market order fills wherever the market is.")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    today = a.today or dt.datetime.now(nse_calendar.IST).date().isoformat()
    recs = [r for r in state.get("orders", []) if rec_day(r) == today]
    for r in recs:
        print("%s  %-9s %s %s %s%s" % (r["at"][11:16], r["status"], str(r.get("side", "")).upper(), r.get("qty"),
                                      r.get("symbol"), ("  reply: %r" % r["reply"]) if r.get("reply") else ""))
    print("%d decision(s) today, %d paper-filled." % (len(recs), sum(1 for r in recs if r["status"] == "filled")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paper trader: tickets, your yes, paper fills (trader-broker).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p, index=True):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/paper_broker.json")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")
        if index:
            p.add_argument("--index", type=int)

    common(sub.add_parser("show", help="every line as a ticket"))
    p = sub.add_parser("approve", help="record your yes for one line")
    common(p)
    p.add_argument("--reply", required=True, help="your reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--reply")
    p = sub.add_parser("fill", help="paper-fill an approved line")
    common(p)
    p.add_argument("--price", help="the price you would have got (default: the line's reference price)")
    p.add_argument("--portfolio", default="trader/paper_portfolio.json")
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/paper_broker.json")
    p.add_argument("--today")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "fill": cmd_fill,
                "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
