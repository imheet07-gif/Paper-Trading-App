#!/usr/bin/env python3
"""The Pelosi Tracker, India edition: what insiders and promoters disclose. Standard library only, no network.

India has no Congress trades to read. What the market must disclose is who
inside a company trades its shares, and who moves a large stake. This reads
them from the market store the user's machine builds:

  insider   trades by promoters, directors, key managerial people and their
            relatives, from NSE's insider trading (SEBI PIT) disclosures
            (core/events.py; NSE's feed is empty after 2026-05-02)
  sast      changes in a large holder's stake under SEBI SAST Regulation 29,
            promoter or not (core/events.py)
  deals     bulk and block deals, named, daily (core/deals.py, collected by
            the scan's evening update)

    python3 insiders.py new                          # disclosures since the last check
    python3 insiders.py new --held                   # only stocks in trader/portfolio.json
    python3 insiders.py company TATAMOTORS --days 180
    python3 insiders.py person "some name"           # every disclosure naming it
    python3 insiders.py status                       # how fresh each source is

Every row is a public disclosure, read after it was published: fast, never
early. Names stay on the user's machine; nothing here fetches or sends.
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
from money import D, inr, inr_short  # noqa: E402
from shareholding import holder_key  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, "data", "market.db")
STATE = "trader/state/pelosi.json"
PORTFOLIO = "trader/portfolio.json"
FIRST_RUN_DAYS = 30
KEEP_SEEN_DAYS = 400
STALE_DAYS = 14
MARKET_MODES = ("market purchase", "market sale", "open market")
KINDS = ("market", "pledge", "other")  # market trades, pledges, and the rest (ESOP, gifts, transfers, allotments)


class InsiderError(Exception):
    pass


# ---------------------------------------------------------------- store

def connect(path):
    if not os.path.exists(path):
        raise InsiderError("no market store at %s (core/events.py fetch and research/scan.py update build it)" % path)
    return sqlite3.connect(path)


def _q(db, sql, args=()):
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError:  # a table this store has not built yet
        return []


def kind_of(mode):
    m = (mode or "").strip().lower()
    if "pledge" in m or "revok" in m or "invoc" in m:
        return "pledge"
    return "market" if m in MARKET_MODES else "other"


def _days(a, b):
    try:
        return (dt.date.fromisoformat(b[:10]) - dt.date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return None


def rows(db, since=None, until=None, isin=None, symbol=None):
    """Every disclosure published in [since, until], as one list of dicts, newest first."""
    lo, hi = since or "0000", (until or "9999") + "T99"
    out = []
    where, args = "", []
    if isin:
        where, args = " AND isin = ?", [isin]
    elif symbol:
        where, args = " AND symbol = ?", [symbol]
    for r in _q(db, "SELECT did, symbol, isin, person, category, mode, txn, sec_type, qty, value, trade_from, trade_to, "
                    "disclosed_at FROM insider WHERE disclosed_at >= ? AND disclosed_at <= ?" + where, [lo, hi] + args):
        did, sym, i, person, cat, mode, txn, sec, qty, value, a, b, at = r
        k = kind_of(mode)
        side = (txn or "").strip().lower()
        out.append({"key": "insider:%s" % did, "source": "insider", "disclosed": at, "symbol": sym, "isin": i,
                    "who": person, "role": cat or "insider", "kind": k,
                    "side": side if side in ("buy", "sell") else (mode or txn or "").strip().lower(),
                    "mode": mode, "security": sec, "qty": qty,
                    "value": str(D(value)) if value else None, "pct": None, "pct_after": None,
                    "traded": a if a == b else "%s to %s" % (a, b), "delay_days": _days(b or a, at)})
    for r in _q(db, "SELECT app_no, symbol, isin, holder, promoter, mode, side, shares_acquired, shares_sold, "
                    "pct_acquired, pct_sold, pct_after, trade_from, trade_to, disclosed_at FROM sast "
                    "WHERE disclosed_at >= ? AND disclosed_at <= ?" + where, [lo, hi] + args):
        app, sym, i, holder, promoter, mode, side, acq, sold, pa, ps, pafter, a, b, at = r
        buy = (side or "").lower().startswith("acq")
        out.append({"key": "sast:%s" % app, "source": "sast", "disclosed": at, "symbol": sym, "isin": i,
                    "who": holder, "role": {1: "promoter", 0: "large holder, not a promoter"}.get(promoter, "large holder"),
                    "kind": kind_of(mode), "side": "buy" if buy else "sell", "mode": mode, "security": "equity shares",
                    "qty": acq if buy else sold, "value": None, "pct": pa if buy else ps, "pct_after": pafter,
                    "traded": a if a == b else "%s to %s" % (a, b), "delay_days": _days(b or a, at)})
    lo_day, hi_day = lo[:10], (until or "9999-12-31")[:10]
    for r in _q(db, "SELECT kind, date, symbol, isin, client, side, qty, price FROM deals "
                    "WHERE date >= ? AND date <= ?" + where, [lo_day, hi_day] + args):
        kind, day, sym, i, client, side, qty, price = r
        out.append({"key": "deal:%s:%s:%s:%s:%s:%s:%s" % (kind, day, sym, client, side, qty, price), "source": "deal",
                    "disclosed": day, "symbol": sym, "isin": i, "who": client, "role": "%s deal" % kind,
                    "kind": "market", "side": side, "mode": "%s deal" % kind, "security": "equity shares", "qty": qty,
                    "value": str((D(price) * qty).quantize(Decimal("1"))), "price": price, "pct": None,
                    "pct_after": None, "traded": day, "delay_days": 0})
    out.sort(key=lambda x: (x["disclosed"] or "", x["key"]), reverse=True)
    return out


def latest(db):
    """The newest publication each source holds: {source: 'YYYY-MM-DD...' or None}."""
    one = lambda sql: (_q(db, sql) or [(None,)])[0][0]
    return {"insider": one("SELECT MAX(disclosed_at) FROM insider"), "sast": one("SELECT MAX(disclosed_at) FROM sast"),
            "deal": one("SELECT MAX(date) FROM deals")}


def stale_notes(db, today):
    notes = []
    for source, at in latest(db).items():
        if not at:
            notes.append("%s: nothing stored yet" % source)
        elif _days(at, today.isoformat()) > STALE_DAYS:
            notes.append("%s: nothing published after %s in this store" % (source, at[:10]) +
                         (" (NSE's insider feed has been empty since 2026-05-02; SAST and deals still arrive)"
                          if source == "insider" else ""))
    return notes


# ---------------------------------------------------------------- filters

def held_from(path=PORTFOLIO):
    """Symbols and ISINs in the user's portfolio file, or (set(), set()) without one."""
    if not path or not os.path.exists(path):
        return set(), set()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise InsiderError("%s could not be read: %s" % (path, e))
    pos = doc.get("positions", []) if isinstance(doc, dict) else []
    syms = {str(p.get("symbol") or "").strip().upper() for p in pos} - {""}
    isins = {str(p.get("isin") or "").strip().upper() for p in pos} - {""}
    return syms, isins


def keep(r, kinds, people=None, companies=None, held=None):
    if r["kind"] not in kinds:
        return False
    if companies and (r["symbol"] or "").upper() not in companies:
        return False
    if people and not any(k in holder_key(r["who"]) for k in people):
        return False
    if held is not None and not ((r["symbol"] or "").upper() in held[0] or (r["isin"] or "").upper() in held[1]):
        return False
    return True


# ---------------------------------------------------------------- state

def load_state(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise InsiderError("%s could not be read: %s" % (path, e))


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def new_since(db, state, today, kinds, people=None, companies=None, held=None):
    """(new rows, the state to save). The first check shows the last FIRST_RUN_DAYS days and
    marks everything older as seen, so it never comes back as new."""
    seen = dict((state or {}).get("seen", {}))
    since = (today - dt.timedelta(days=KEEP_SEEN_DAYS)).isoformat()
    first = (today - dt.timedelta(days=FIRST_RUN_DAYS)).isoformat()
    all_rows = rows(db, since=since, until=today.isoformat())
    fresh = [r for r in all_rows if r["key"] not in seen]
    shown = [r for r in fresh if (state or (r["disclosed"] or "") >= first) and keep(r, kinds, people, companies, held)]
    for r in fresh:
        seen[r["key"]] = (r["disclosed"] or "")[:10]  # rows filtered out count as seen too: alerts once
    cutoff = (today - dt.timedelta(days=KEEP_SEEN_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    return shown, {"checked_at": today.isoformat(), "seen": seen}


# ---------------------------------------------------------------- report

def line(r, held=None):
    who = r["who"] or "(no name)"
    what = {"buy": "bought", "sell": "sold"}.get(r["side"], r["side"] or "?")
    size = []
    if r["qty"]:
        size.append("{:,} shares".format(r["qty"]))
    if r.get("price"):
        size.append("at %s" % inr(D(r["price"])))
    if r["value"]:
        size.append("worth %s" % inr_short(D(r["value"])))
    if r["pct"]:
        size.append("%s%% of the company" % r["pct"])
    if r["pct_after"]:
        size.append("now holds %s%%" % r["pct_after"])
    delay = ""
    if r["source"] != "deal" and r["delay_days"] is not None:
        delay = ", disclosed %s, %d day%s after the trade" % (
            (r["disclosed"] or "")[:10], r["delay_days"], "" if r["delay_days"] == 1 else "s")
    flag = ""
    if held and ((r["symbol"] or "").upper() in held[0] or (r["isin"] or "").upper() in held[1]):
        flag = "  [you hold this]"
    mode = "" if r["source"] == "deal" or r["kind"] == "market" else " (%s)" % r["mode"]
    return "- %s %s: %s (%s) %s%s %s, traded %s%s%s" % (
        r["symbol"], {"insider": "insider", "sast": "SAST", "deal": r["role"]}[r["source"]], who, r["role"], what, mode,
        ", ".join(size) or "", r["traded"], delay, flag)


def summary(rs):
    """Market buys and sells per company: {symbol: {"buys", "sells", "buy_value", "sell_value"}}."""
    out = {}
    for r in rs:
        if r["kind"] != "market" or r["side"] not in ("buy", "sell"):
            continue
        s = out.setdefault(r["symbol"], {"buys": 0, "sells": 0, "buy_value": Decimal(0), "sell_value": Decimal(0)})
        s[r["side"] + "s"] += 1
        if r["value"]:
            s[r["side"] + "_value"] += D(r["value"])
    return out


CAVEAT = ("These are public disclosures, read after they were published: fast, never early. They say what "
          "someone did, not what anyone will do next, and no row means anyone traded on inside information.")


def report(rs, title, held=None, notes=()):
    out = [title]
    if not rs:
        out.append("  nothing")
    for r in rs:
        out.append("  " + line(r, held))
    s = summary(rs)
    if len(s) > 1:
        out.append("By company (market trades only):")
        for sym, x in sorted(s.items(), key=lambda kv: -(kv[1]["buy_value"] + kv[1]["sell_value"])):
            out.append("  %s: %d buy%s (%s), %d sell%s (%s)" % (
                sym, x["buys"], "" if x["buys"] == 1 else "s", inr_short(x["buy_value"]),
                x["sells"], "" if x["sells"] == 1 else "s", inr_short(x["sell_value"])))
    for n in notes:
        out.append("note: " + n)
    out.append(CAVEAT)
    return "\n".join(out)


def _jsonable(rs):
    return [dict(r) for r in rs]


# ---------------------------------------------------------------- cli

def main(argv=None, today=None):
    ap = argparse.ArgumentParser(description="Insider, promoter (SAST) and bulk and block deal disclosures, India.")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="disclosures published since the last check (exit 1 when none)")
    n.add_argument("--company", nargs="*", default=[], metavar="SYMBOL")
    n.add_argument("--person", nargs="*", default=[], metavar="NAME")
    n.add_argument("--held", action="store_true", help="only stocks in the portfolio file")
    n.add_argument("--portfolio", default=PORTFOLIO)
    n.add_argument("--state", default=STATE)
    n.add_argument("--all", action="store_true", help="also ESOPs, gifts, transfers and allotments")
    c = sub.add_parser("company", help="one company's disclosures")
    c.add_argument("symbol")
    c.add_argument("--days", type=int, default=180)
    c.add_argument("--all", action="store_true")
    c.add_argument("--portfolio", default=PORTFOLIO)
    p = sub.add_parser("person", help="every disclosure naming someone (any spelling with these words)")
    p.add_argument("name")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--all", action="store_true")
    sub.add_parser("status", help="the newest disclosure each source holds")
    a = ap.parse_args(argv)
    today = today or dt.date.today()
    try:
        db = connect(a.db)
        notes = stale_notes(db, today)
        if a.cmd == "status":
            got = latest(db)
            if a.json:
                print(json.dumps({"latest": got, "notes": notes}, indent=1))
            else:
                for k, v in got.items():
                    print("%-8s %s" % (k, v or "nothing stored"))
                for x in notes:
                    print("note: " + x)
            return 0
        kinds = KINDS if a.all else ("market", "pledge")
        held = held_from(getattr(a, "portfolio", None))
        if a.cmd == "new":
            people = [holder_key(x) for x in a.person if holder_key(x)]
            companies = {x.upper() for x in a.company}
            state = load_state(a.state)
            shown, new_state = new_since(db, state, today, kinds, people, companies, held if a.held else None)
            title = "New disclosures since %s:" % (state["checked_at"] if state else
                                                     (today - dt.timedelta(days=FIRST_RUN_DAYS)).isoformat())
            save_state(a.state, new_state)
        elif a.cmd == "company":
            since = (today - dt.timedelta(days=a.days)).isoformat()
            shown = [r for r in rows(db, since=since, until=today.isoformat(), symbol=a.symbol.upper()) if r["kind"] in kinds]
            title = "%s: disclosures published in the last %d days:" % (a.symbol.upper(), a.days)
        else:
            key = holder_key(a.name)
            if not key:
                raise InsiderError("give a name to search for")
            since = (today - dt.timedelta(days=a.days)).isoformat()
            shown = [r for r in rows(db, since=since, until=today.isoformat()) if keep(r, kinds, [key])]
            title = "Disclosures naming %s in the last %d days:" % (a.name, a.days)
        db.close()
    except InsiderError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"title": title, "rows": _jsonable(shown), "notes": notes, "caveat": CAVEAT}, indent=1))
    else:
        print(report(shown, title, held, notes))
    return 1 if a.cmd == "new" and not shown else 0


if __name__ == "__main__":
    sys.exit(main())
