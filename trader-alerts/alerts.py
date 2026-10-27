#!/usr/bin/env python3
"""Price alerts and the NSE clock for trader-alerts, India edition. Standard library only, no network.

    python3 alerts.py clock                            # is the NSE open? the next open, in IST
    python3 alerts.py quote TCS ITC --write            # last prices -> trader/prices.json
    python3 alerts.py quote --holdings --write         # every symbol in your portfolio and targets
    python3 alerts.py add --symbol TCS --above 3200    # rules live in trader/alerts.json
    python3 alerts.py add --symbol ITC --stop 380
    python3 alerts.py add --symbol INFY --pct 3
    python3 alerts.py add --opening-bell
    python3 alerts.py check                            # rings what hit, once; exit 1 if nothing rang
    python3 alerts.py list
    python3 alerts.py reset tcs-above-3200

Rule types:
  above         the price is at or above "price" (a target)
  below / stop  the price is at or below "price" (a stop or a dip to buy)
  pct_move      the price moved "pct" percent or more from the previous close
                (or from "ref" if you give one); "direction": "up" or "down"
  opening_bell  the NSE normal market opened today (09:15 IST; rings once a day)

An above / below alert rings ONCE and then stays quiet until you reset it,
unless the rule has "rearm": true (it re-arms when the price crosses back).
A pct_move alert rings at most once per day.

Prices (no network: this tool never fetches a quote):
  store   the last close in your own market store (data/market.db, built by
          core/bhavcopy.py), with NSE's previous close; end of day only
  file    a prices JSON you supply ({"as_of": ..., "prices": {"TCS": "3150.5"}}),
          for example from your broker's read-only quote tool

Not here, because the facts are not yet in core/ data read from the
publisher's file, or need a live feed: intraday quotes, price bands and
circuit limits. NSE's previous close is not adjusted on ex-dates, so a
pct_move on an ex-date can be the corporate action, not the market.
"""

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "core"))
import nse_calendar  # noqa: E402
from money import D, inr, paise  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, "data", "market.db")
SERIES = ("EQ", "BE", "BZ", "SM", "ST")  # the cash-market series a stock can trade in, preferred first
HUNDRED = Decimal(100)


class AlertError(Exception):
    pass


# ---------------------------------------------------------------- time

def parse_now(text):
    """ISO time -> aware datetime. A time without an offset is taken as IST."""
    if not text:
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    try:
        v = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise AlertError("--now wants an ISO time like 2026-10-28T09:20 (IST) or ...+00:00")
    return v if v.tzinfo else v.replace(tzinfo=nse_calendar.IST)


def market_status(now):
    try:
        st = nse_calendar.status(now)
    except nse_calendar.UnknownYear as e:
        raise AlertError("%s (core/nse_calendar.py update-holidays)" % e)
    st["date"] = nse_calendar.to_ist(now).date().isoformat()
    st["open"] = st["phase"] in ("open", "special_session")
    return st


# ---------------------------------------------------------------- prices

def _dec(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(x) if isinstance(x, (int, float, Decimal)) else D(str(x).strip().replace(",", ""))
    except Exception:
        return None
    return v if v.is_finite() and v > 0 else None


def store_quotes(symbols, db_path=DEFAULT_DB):
    """{SYMBOL: quote} from the latest stored session of each symbol, and [errors]."""
    if not os.path.exists(db_path):
        raise AlertError("no market store at %s (build it with core/bhavcopy.py backfill)" % db_path)
    db = sqlite3.connect(db_path)
    quotes, errors = {}, []
    try:
        for sym in symbols:
            rows = db.execute("SELECT date, isin, series, close, prev_close FROM prices WHERE symbol = ? "
                              "AND date = (SELECT MAX(date) FROM prices WHERE symbol = ?)", (sym, sym)).fetchall()
            rows = [r for r in rows if r[2] in SERIES and r[3]]
            if not rows:
                errors.append("no price for %s in the market store" % sym)
                continue
            date, isin, series, close, prev = min(rows, key=lambda r: SERIES.index(r[2]))
            quotes[sym] = {"symbol": sym, "isin": isin, "price": paise(Decimal(close) / HUNDRED),
                           "prev_close": paise(Decimal(prev) / HUNDRED) if prev else None,
                           "time": date, "series": series, "source": "store"}
    finally:
        db.close()
    return quotes, errors


def file_quotes(symbols, path):
    """{SYMBOL: quote} from a prices JSON the user supplies, and [errors]."""
    doc = _read(path, None)
    if doc is None:
        raise AlertError("no prices file at %s" % path)
    prices = doc.get("prices", doc) if isinstance(doc, dict) else {}
    detail = doc.get("detail", {}) if isinstance(doc, dict) else {}
    prices = {str(k).upper(): v for k, v in prices.items()}
    quotes, errors = {}, []
    for sym in symbols:
        p = _dec(prices.get(sym))
        if p is None:
            errors.append("no price for %s in %s" % (sym, path))
            continue
        d = detail.get(sym, {}) if isinstance(detail, dict) else {}
        quotes[sym] = {"symbol": sym, "isin": d.get("isin"), "price": p, "prev_close": _dec(d.get("prev_close")),
                       "time": d.get("time") or doc.get("as_of", ""), "source": "file"}
    return quotes, errors


def get_quotes(symbols, source="store", db_path=DEFAULT_DB, prices_file=None):
    if source == "store":
        return store_quotes(symbols, db_path)
    if not prices_file:
        raise AlertError("--source file needs --prices-file PATH")
    return file_quotes(symbols, prices_file)


def write_prices(path, quotes, now, source):
    old = _read(path, {}) or {}
    prices = dict(old.get("prices", {}))
    detail = dict(old.get("detail", {}))
    for s, q in quotes.items():
        prices[s] = str(paise(q["price"]))
        detail[s] = {"isin": q.get("isin"), "prev_close": str(paise(q["prev_close"])) if q.get("prev_close") else None,
                     "time": q.get("time"), "source": q.get("source")}
    doc = {"as_of": nse_calendar.to_ist(now).isoformat(timespec="minutes"), "source": source, "currency": "INR",
           "note": "Last prices for alerts and sizing checks, not live quotes. Your broker's price is the one that fills.",
           "prices": prices, "detail": detail}
    _write(path, doc)
    return doc


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s is not valid JSON: %s" % (path, e))


# ---------------------------------------------------------------- rules

PRICE_TYPES = {"above", "below", "stop", "target", "pct_move", "opening_bell"}


def validate(rule):
    t = rule.get("type")
    if t not in PRICE_TYPES:
        raise AlertError("rule %s: unknown type %r" % (rule.get("id"), t))
    if t != "opening_bell" and not rule.get("symbol"):
        raise AlertError("rule %s needs a symbol" % rule.get("id"))
    if t in ("above", "below", "stop", "target") and _dec(rule.get("price")) is None:
        raise AlertError("rule %s needs a positive price" % rule.get("id"))
    if t == "pct_move" and _dec(rule.get("pct")) is None:
        raise AlertError("rule %s needs a positive pct" % rule.get("id"))


def _stamp(now):
    return nse_calendar.to_ist(now).isoformat(timespec="minutes")


def evaluate(rules, quotes, status, state, now):
    """Pure function. Returns (rings, new_state). Each ring: {id, symbol, message}."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (r.get("symbol", "market"), r["type"])
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "opening_bell":
            if status["phase"] == "open" and rung.get(rid, "")[:10] != today:
                rings.append({"id": rid, "symbol": "",
                              "message": "Opening bell: the NSE normal market is open (%s IST)." % status["now_ist"][11:16]})
                rung[rid] = _stamp(now)
            continue
        sym = r["symbol"].upper()
        q = quotes.get(sym)
        if not q:
            continue
        p = q["price"]
        if typ in ("above", "below"):
            level = _dec(r["price"])
            hit = p >= level if typ == "above" else p <= level
            if hit and rid not in rung:
                word = "at or above" if typ == "above" else "at or below"
                label = {"stop": "stop", "target": "target"}.get(r["type"], "alert level")
                rings.append({"id": rid, "symbol": sym, "message": "%s last %s (%s), %s your %s of %s%s."
                              % (sym, inr(p), q.get("time", ""), word, label, inr(level),
                                 (" (" + r["note"] + ")") if r.get("note") else "")})
                rung[rid] = _stamp(now)
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = _dec(r.get("ref")) or q.get("prev_close")
            if not ref:
                continue
            move = HUNDRED * (p / ref - 1)
            want = r.get("direction")
            hit = abs(move) >= _dec(r["pct"]) and (not want or (want == "up") == (move > 0))
            if hit and rung.get(rid, "")[:10] != today:
                rings.append({"id": rid, "symbol": sym, "message": "%s moved %s%% to %s (from %s)%s."
                              % (sym, ("+" if move > 0 else "") + str(move.quantize(Decimal("0.01"))), inr(p), inr(ref),
                                 "" if r.get("ref") else "; on an ex-date this can be the corporate action")})
                rung[rid] = _stamp(now)
    state["last_check"] = _stamp(now)
    return rings, state


def _n(x):
    x = D(x)
    return str(x.to_integral_value()) if x == x.to_integral_value() else str(x.normalize())


# ---------------------------------------------------------------- commands

def holdings(a):
    syms = []
    pf = _read(a.portfolio, {}) or {}
    for p in pf.get("positions", []):
        syms.append(str(p.get("symbol") or p.get("ticker") or "").upper())
    tg = _read(a.targets, {}) or {}
    for w in tg.get("weights", []) if isinstance(tg, dict) else []:
        syms.append(str(w.get("symbol") or w.get("ticker") or "").upper())
    return [s for s in syms if s]


def _dedupe(items):
    out = []
    for s in items:
        if s and s not in out:
            out.append(s)
    return out


def cmd_clock(a):
    st = market_status(parse_now(a.now))
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        words = {"open": "OPEN (normal market, 09:15 to 15:30)", "pre_open_entry": "pre-open, order entry",
                 "pre_open_matching": "pre-open, order matching", "closing_session": "closing session",
                 "special_session": "OPEN (special session)", "closed": "closed"}
        line = "IST %s: NSE %s" % (st["now_ist"].replace("T", " ")[:16], words.get(st["phase"], st["phase"]))
        if st.get("holiday"):
            line += ", holiday: %s" % st["holiday"]
        print(line + ".")
        if st.get("next_open_ist"):
            print("Next normal-market open: %s IST." % st["next_open_ist"].replace("T", " ")[:16])
    return 0 if st["open"] else 1


def cmd_quote(a):
    syms = _dedupe([s.upper() for s in a.symbols] + (holdings(a) if a.holdings else []))
    if not syms:
        raise AlertError("which symbols? alerts.py quote TCS ITC, or --holdings")
    now = parse_now(a.now)
    quotes, errors = get_quotes(syms, a.source, a.db, a.prices_file)
    if a.write and quotes:
        write_prices(a.write, quotes, now, a.source)
    if a.json:
        print(json.dumps({"quotes": {s: {k: (str(v) if isinstance(v, Decimal) else v) for k, v in q.items()}
                                     for s, q in quotes.items()}, "errors": errors}, indent=2, ensure_ascii=False))
    else:
        for s in syms:
            q = quotes.get(s)
            if q:
                chg = ""
                if q.get("prev_close"):
                    chg = " (%s%% vs prev close %s)" % (
                        str((HUNDRED * (q["price"] / q["prev_close"] - 1)).quantize(Decimal("0.01"))), inr(q["prev_close"]))
                print("%-12s %s%s  %s  [%s]" % (s, inr(q["price"]), chg, q.get("time", ""), q["source"]))
        for e in errors:
            print("error: " + e)
        if a.write and quotes:
            print("Wrote %d price(s) to %s." % (len(quotes), a.write))
        print("Last prices, not live quotes. Your broker's price is the one that fills.")
    return 0 if quotes else 1


def cmd_check(a):
    doc = _read(a.rules, None)
    if not doc or not doc.get("rules"):
        raise AlertError("no rules in %s: add one with alerts.py add --symbol TCS --above 3200" % a.rules)
    rules = doc["rules"]
    now = parse_now(a.now)
    status = market_status(now)
    syms = _dedupe([str(r.get("symbol", "")).upper() for r in rules if r.get("type") != "opening_bell"])
    quotes, errors = (get_quotes(syms, a.source, a.db, a.prices_file) if syms else ({}, []))
    state = _read(a.state, {}) or {}
    rings, state = evaluate(rules, quotes, status, state, now)
    if not a.no_write:
        _write(a.state, state)
        if quotes and a.prices_out:
            write_prices(a.prices_out, quotes, now, a.source)
    if a.json:
        print(json.dumps({"market": status, "rings": rings, "errors": errors,
                          "quotes": {s: str(q["price"]) for s, q in quotes.items()}}, indent=2, ensure_ascii=False))
    else:
        print("NSE %s (%s IST)." % ("OPEN" if status["open"] else status["phase"].replace("_", " "),
                                    status["now_ist"].replace("T", " ")[:16]))
        for r in rings:
            print("ALERT " + r["message"])
        if not rings:
            print("Nothing hit. %d rule(s) watched." % len(rules))
        for e in errors:
            print("error: " + e)
        if rings:
            print("An alert is information, not an order. To act on one, size it (trader-budget or "
                  "trader-rebalance), check it (trader-risk) and enter it yourself in your broker.")
    return 0 if rings else 1


def cmd_add(a):
    doc = _read(a.rules, {"rules": []}) or {"rules": []}
    doc.setdefault("rules", [])
    if a.opening_bell:
        rule = {"id": a.id or "opening-bell", "type": "opening_bell"}
    else:
        if not a.symbol:
            raise AlertError("--symbol is required (or --opening-bell)")
        sym = a.symbol.upper()
        if a.above is not None:
            rule = {"type": "above", "price": a.above}
        elif a.below is not None:
            rule = {"type": "below", "price": a.below}
        elif a.stop is not None:
            rule = {"type": "stop", "price": a.stop}
        elif a.pct is not None:
            rule = {"type": "pct_move", "pct": a.pct}
            if a.direction:
                rule["direction"] = a.direction
        else:
            raise AlertError("give one of --above, --below, --stop or --pct")
        val = rule.get("price", rule.get("pct"))
        if _dec(val) is None:
            raise AlertError("%r is not a positive number" % val)
        rule = dict({"id": a.id or ("%s-%s-%s" % (sym.lower(), rule["type"], _n(val)))}, symbol=sym, **rule)
        if a.rearm:
            rule["rearm"] = True
        if a.note:
            rule["note"] = a.note
    validate(rule)
    if any(r.get("id") == rule["id"] for r in doc["rules"]):
        raise AlertError("a rule with id %s already exists" % rule["id"])
    doc["rules"].append(rule)
    _write(a.rules, doc)
    print("Added %s: %s" % (rule["id"], json.dumps(rule, ensure_ascii=False)))
    return 0


def cmd_list(a):
    doc = _read(a.rules, {"rules": []}) or {"rules": []}
    state = _read(a.state, {}) or {}
    if not doc.get("rules"):
        print("No rules yet.")
        return 1
    for r in doc["rules"]:
        rid = r.get("id")
        rung = state.get("rung", {}).get(rid)
        print("%-24s %s%s" % (rid, json.dumps({k: v for k, v in r.items() if k != "id"}, ensure_ascii=False),
                              ("  (rang %s)" % rung) if rung else ""))
    return 0


def cmd_reset(a):
    state = _read(a.state, {}) or {}
    if a.id not in state.get("rung", {}):
        print("%s has not rung (nothing to reset)." % a.id)
        return 1
    del state["rung"][a.id]
    _write(a.state, state)
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price alerts and the NSE clock (trader-alerts).")
    sub = ap.add_subparsers(dest="cmd")

    def prices(p):
        p.add_argument("--source", default="store", choices=["store", "file"])
        p.add_argument("--db", default=DEFAULT_DB, help="the market store (default data/market.db)")
        p.add_argument("--prices-file", metavar="PATH", help="prices JSON for --source file")
        p.add_argument("--now", help="pretend it is this time (ISO; no offset = IST)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("clock", help="is the NSE open?")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quote", help="last prices")
    prices(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true", help="add symbols from your portfolio and targets")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--targets", default="trader/targets.json")
    p.add_argument("--write", nargs="?", const="trader/prices.json", metavar="PATH",
                   help="save to prices.json (default trader/prices.json)")
    p = sub.add_parser("check", help="check every rule, ring what hit")
    prices(p)
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--prices-out", default="trader/prices.json")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("add", help="add a rule")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--above")
    g.add_argument("--below")
    g.add_argument("--stop")
    g.add_argument("--pct")
    g.add_argument("--opening-bell", action="store_true")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--note")
    p.add_argument("--id")
    p = sub.add_parser("list", help="list the rules")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p = sub.add_parser("reset", help="re-arm a rule that rang")
    p.add_argument("id")
    p.add_argument("--state", default="trader/state/alerts.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "check": cmd_check, "add": cmd_add,
                "list": cmd_list, "reset": cmd_reset}[a.cmd](a)
    except AlertError as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
