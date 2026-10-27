#!/usr/bin/env python3
"""The Reverse Engineer for trader-copycat, India edition. Standard library only, no network.

Finds and follows named investors in India's public filings, from the
market store the user's machine builds:

  shareholding_holders   every holder each company's quarterly shareholding
                         filing names: promoters, and public holders of more
                         than 1% (core/shareholding.py, with --xbrl-since)
  deals                  bulk and block deals, named, daily (core/deals.py)
  prices                 closes, to put a rupee value on a holding

    python3 copycat.py discover                         # who holds the most, latest quarter
    python3 copycat.py discover --who individuals --top 40
    python3 copycat.py search jhunjhunwala              # every spelling that matches
    python3 copycat.py follow add "RAKESH R JHUNJHUNWALA" --as "Rakesh Jhunjhunwala"
    python3 copycat.py follow alias "Rakesh Jhunjhunwala" "RAKESH RADHESHYAM JHUNJHUNWALA"
    python3 copycat.py follow list
    python3 copycat.py show "Rakesh Jhunjhunwala"      # holdings, changes, recent deals
    python3 copycat.py copy "Rakesh Jhunjhunwala" --top 10 --write

What a filing can and cannot say:

  * a public holder is named only above 1%: a holder who disappears from a
    company's filing sold, or fell below 1%. The report says exactly that.
  * holdings are as of the quarter end and known from the filing's
    broadcast time, often weeks later. A copy is always of an old portfolio.
  * names are the only link between filings (NSE masks PANs). Spellings are
    grouped by a normalised key; a person can still appear under two keys,
    so `follow alias` joins the ones the user confirms are the same. Nothing
    is joined automatically.

The follow list lives in trader/copycat.json. Names never leave the user's
machine.
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
import shareholding as sh  # noqa: E402
from money import D, inr, inr_short  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, "data", "market.db")
FOLLOWS = "trader/copycat.json"
HUNDRED = Decimal(100)
SERIES = ("EQ", "BE", "BZ", "SM", "ST")
INSTITUTION_GROUPS = ("MutualFunds", "Insurance", "ForeignPortfolio", "AlternativeInvestment", "Banks",
                      "ProvidentFunds", "SovereignWealth", "InstitutionsForeign", "OtherInstitutions",
                      "NBFCs", "OtherFinancialInstitutions", "VentureCapital", "ForeignVenture")


class CopyError(Exception):
    pass


# ---------------------------------------------------------------- store

def connect(path):
    if not os.path.exists(path):
        raise CopyError("no market store at %s (core/shareholding.py fetch --xbrl-since ... builds the holders)" % path)
    db = sqlite3.connect(path)
    try:
        db.execute("SELECT 1 FROM shareholding_holders LIMIT 1")
    except sqlite3.OperationalError:
        raise CopyError("the store has no named holders yet: run core/shareholding.py fetch --xbrl-since YYYY-MM-DD")
    return db


def latest_quarter(db):
    """The quarter end most filings in the store are for (the newest one is still filling up early on)."""
    rows = db.execute("SELECT period_end, COUNT(DISTINCT isin) FROM shareholding_holders GROUP BY period_end "
                      "ORDER BY period_end DESC LIMIT 2").fetchall()
    if not rows:
        raise CopyError("no named holders in the store yet")
    if len(rows) == 2 and rows[0][1] < rows[1][1] * 0.5:
        return rows[1][0]  # the newest quarter has under half the filings: not yet complete
    return rows[0][0]


def close_on(db, isin, day):
    """The last close on or before `day` for this ISIN, in rupees, or None."""
    r = db.execute("SELECT close FROM prices WHERE isin = ? AND date <= ? AND series IN (%s) AND close > 0 "
                   "ORDER BY date DESC LIMIT 1" % ",".join("?" * len(SERIES)), (isin, day) + SERIES).fetchone()
    return Decimal(r[0]) / HUNDRED if r else None


def kind_of(group, person):
    if person:
        return "individual"
    if any(group.startswith(g) for g in INSTITUTION_GROUPS):
        return "institution"
    return "corporate or other"


def holdings(db, keys, quarter, public_only=False):
    """One row per company for these holder keys in this quarter. Only each company's
    latest filing for the quarter counts (a revision replaces the original), and one
    investor named more than once in it (two groups, two spellings) is added up."""
    q = ("SELECT h.isin, s.symbol, h.holder, h.holder_group, h.person, h.promoter, h.pct, h.shares, "
         "h.broadcast_at, h.record_id FROM shareholding_holders h JOIN shareholding s ON s.record_id = h.record_id "
         "WHERE h.period_end = ? AND h.holder_key IN (%s)" % ",".join("?" * len(keys)))
    if public_only:
        q += " AND h.promoter = 0"
    found = db.execute(q, (quarter,) + tuple(keys)).fetchall()
    latest = {}
    for r in found:
        if r[0] not in latest or r[8] > latest[r[0]][0]:
            latest[r[0]] = (r[8], r[9])
    rows = {}
    for isin, symbol, holder, group, person, promoter, pct, shares, at, rid in found:
        if rid != latest[isin][1]:
            continue
        row = rows.get(isin)
        if row is None:
            row = rows[isin] = {"isin": isin, "symbol": symbol, "holder": holder, "group": group, "person": person,
                                "promoter": promoter, "pct": Decimal(0), "shares": 0, "broadcast_at": at}
        row["pct"] += D(pct) if pct else Decimal(0)
        row["shares"] += int(shares or 0)
    for row in rows.values():
        px = close_on(db, row["isin"], quarter)
        row["price"] = px
        row["value"] = (px * row["shares"]) if px is not None else None
    return sorted(rows.values(), key=lambda r: (-(r["value"] or 0), r["symbol"] or ""))


def previous_quarter(db, isin, quarter):
    r = db.execute("SELECT MAX(period_end) FROM shareholding WHERE isin = ? AND period_end < ?", (isin, quarter)).fetchone()
    return r[0] if r else None


def changes(db, keys, quarter):
    """Per company: what the holding was in that company's previous filed quarter and what it is now."""
    now = {r["isin"]: r for r in holdings(db, keys, quarter)}
    isins = set(now)
    # companies where they were named last quarter, to find the ones they left
    for (isin,) in db.execute("SELECT DISTINCT isin FROM shareholding_holders WHERE holder_key IN (%s) AND period_end < ?"
                              % ",".join("?" * len(keys)), tuple(keys) + (quarter,)):
        isins.add(isin)
    out = []
    for isin in isins:
        prev_q = previous_quarter(db, isin, quarter)
        prev = None
        if prev_q:
            p = holdings(db, keys, prev_q)
            prev = next((x for x in p if x["isin"] == isin), None)
        cur = now.get(isin)
        if not cur and not prev:
            continue  # named in an older quarter only
        filed_now = db.execute("SELECT 1 FROM shareholding WHERE isin = ? AND period_end = ?", (isin, quarter)).fetchone()
        if cur and not prev:
            what = "new" if prev_q else "held (no earlier filing stored)"
        elif prev and not cur:
            what = "gone: sold, or now below 1%" if filed_now else "no filing for this quarter yet"
        elif cur["shares"] > prev["shares"]:
            what = "added"
        elif cur["shares"] < prev["shares"]:
            what = "trimmed"
        else:
            what = "unchanged"
        ref = cur or prev
        out.append({"isin": isin, "symbol": ref["symbol"], "change": what,
                    "shares_before": prev["shares"] if prev else None, "shares_now": cur["shares"] if cur else None,
                    "pct_before": prev["pct"] if prev else None, "pct_now": cur["pct"] if cur else None,
                    "value_now": cur["value"] if cur else None, "known_from": cur["broadcast_at"] if cur else None})
    order = {"new": 0, "added": 1, "trimmed": 2}
    return sorted(out, key=lambda r: (order.get(r["change"], 3 if r["change"].startswith("gone") else 4),
                                      -(r["value_now"] or 0), r["symbol"] or ""))


def deals_for(db, keys, since):
    try:
        rows = db.execute("SELECT kind, date, symbol, client, side, qty, price FROM deals WHERE date > ? "
                          "ORDER BY date DESC", (since,)).fetchall()
    except sqlite3.OperationalError:
        return []
    keys = set(keys)
    return [{"kind": k, "date": d, "symbol": s, "client": c, "side": sd, "qty": q, "price": D(p)}
            for k, d, s, c, sd, q, p in rows if sh.holder_key(c) in keys]


# ---------------------------------------------------------------- discover and search

def discover(db, quarter=None, who="all", promoters=False, top=25, by="value"):
    """The holders with the largest (or most) holdings in a quarter, grouped by name key."""
    quarter = quarter or latest_quarter(db)
    q = ("SELECT h.holder_key, h.holder, h.holder_group, h.person, h.isin, h.shares, h.broadcast_at FROM shareholding_holders h "
         "WHERE h.period_end = ?")
    if not promoters:
        q += " AND h.promoter = 0"
    groups = {}
    for key, name, group, person, isin, shares, at in db.execute(q, (quarter,)):
        kind = kind_of(group, person)
        if who == "individuals" and kind != "individual":
            continue
        if who == "institutions" and kind != "institution":
            continue
        g = groups.setdefault(key, {"key": key, "names": {}, "kinds": set(), "companies": {}, "value": Decimal(0)})
        g["names"][name] = g["names"].get(name, 0) + 1
        g["kinds"].add(kind)
        cur = g["companies"].get(isin)
        if cur is None or at > cur[1]:
            g["companies"][isin] = (int(shares or 0), at)
    out = []
    for g in groups.values():
        value, priced = Decimal(0), 0
        for isin, (shares, _) in g["companies"].items():
            px = close_on(db, isin, quarter)
            if px is not None:
                value += px * shares
                priced += 1
        out.append({"key": g["key"], "name": max(g["names"], key=g["names"].get), "spellings": sorted(g["names"]),
                    "kind": "/".join(sorted(g["kinds"])), "companies": len(g["companies"]), "priced": priced,
                    "value": value})
    keyf = (lambda r: (-r["companies"], -r["value"], r["key"])) if by == "count" else \
        (lambda r: (-r["value"], -r["companies"], r["key"]))
    return quarter, sorted(out, key=keyf)[:top]


def search(db, text, limit=30):
    words = sh.holder_key(text).split()
    if not words:
        raise CopyError("search for a name")
    q = "SELECT holder_key, holder, COUNT(DISTINCT isin), MAX(period_end) FROM shareholding_holders WHERE " + \
        " AND ".join("holder_key LIKE ?" for _ in words) + " GROUP BY holder_key, holder ORDER BY 3 DESC LIMIT ?"
    return db.execute(q, tuple("%" + w + "%" for w in words) + (limit,)).fetchall()


# ---------------------------------------------------------------- follows

def load_follows(path):
    if not os.path.exists(path):
        return {"follow": []}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_follows(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def find_follow(doc, name):
    k = sh.holder_key(name)
    for f in doc["follow"]:
        if f["display"].lower() == name.strip().lower() or k in f["keys"]:
            return f
    return None


def keys_for(db, doc, name):
    """The keys to look up for a name: a followed investor's confirmed keys, or the name's own key."""
    f = find_follow(doc, name)
    if f:
        return f["display"], f["keys"]
    k = sh.holder_key(name)
    if not db.execute("SELECT 1 FROM shareholding_holders WHERE holder_key = ? LIMIT 1", (k,)).fetchone():
        raise CopyError("nobody is named %r in the stored filings; try: copycat.py search %s" % (name, name.split()[-1]))
    return name, [k]


# ---------------------------------------------------------------- copy

def targets(rows, top, source, as_of):
    """Weights in proportion to each holding's rupee value, top N, for trader-budget."""
    priced = [r for r in rows if r.get("value_now") or r.get("value")]
    priced = sorted(priced, key=lambda r: -(r.get("value_now") or r.get("value")))[:top]
    total = sum((r.get("value_now") or r.get("value")) for r in priced)
    if not priced or not total:
        raise CopyError("no priced holdings to copy (the store needs closes for these stocks)")
    weights = [{"symbol": r["symbol"], "isin": r["isin"],
                "weight": str(((r.get("value_now") or r.get("value")) * HUNDRED / total).quantize(Decimal("0.01")))}
               for r in priced]
    return {"source": source, "as_of": as_of, "weights": weights,
            "note": "Copied from public shareholding filings: holdings as of the quarter end, known from each "
                    "filing's broadcast time. Not a recommendation."}


# ---------------------------------------------------------------- commands

def _pct(x):
    return "%s%%" % x.quantize(Decimal("0.01")) if x is not None else "-"


def _val(x):
    return inr_short(x) if x is not None else "no price"


def cmd_discover(a, db):
    quarter, rows = discover(db, a.quarter, a.who, a.promoters, a.top, a.by)
    print("Named holders, quarter ended %s (%s%s), by %s:" % (quarter, a.who, ", promoters included" if a.promoters
                                                               else ", public holders only", a.by))
    for i, r in enumerate(rows, 1):
        alt = (" (also: %s)" % "; ".join(s for s in r["spellings"] if s != r["name"])[:80]) if len(r["spellings"]) > 1 else ""
        print("%3d. %-44s %-18s %3d companies  %12s%s" % (i, r["name"][:44], r["kind"][:18], r["companies"],
                                                         _val(r["value"]), alt))
    print("\nValues are shares at the quarter-end close. Follow one with: copycat.py follow add \"<name>\"")
    return 0 if rows else 1


def cmd_search(a, db):
    rows = search(db, a.text)
    for key, name, n, last in rows:
        print("%-50s key %-45s %3d companies, latest %s" % (name[:50], key[:45], n, last))
    if not rows:
        print("No stored holder matches %r." % a.text)
    return 0 if rows else 1


def cmd_follow(a, db):
    doc = load_follows(a.follows)
    if a.action == "list":
        for f in doc["follow"]:
            print("%-30s keys: %s" % (f["display"], "; ".join(f["keys"])))
        if not doc["follow"]:
            print("Following nobody yet: copycat.py discover, then follow add \"<name>\".")
        return 0
    if a.action == "add":
        key = sh.holder_key(a.name)
        if not db.execute("SELECT 1 FROM shareholding_holders WHERE holder_key = ? LIMIT 1", (key,)).fetchone():
            raise CopyError("nobody is named %r in the stored filings; try: copycat.py search ..." % a.name)
        if find_follow(doc, a.name) or find_follow(doc, a.display or a.name):
            raise CopyError("already following %s" % (a.display or a.name))
        doc["follow"].append({"display": a.display or a.name, "keys": [key],
                              "added": dt.date.today().isoformat()})
        save_follows(a.follows, doc)
        print("Following %s (key %s)." % (a.display or a.name, key))
        return 0
    f = find_follow(doc, a.name)
    if not f:
        raise CopyError("not following %r" % a.name)
    if a.action == "remove":
        doc["follow"].remove(f)
        save_follows(a.follows, doc)
        print("Stopped following %s." % f["display"])
        return 0
    # alias: join another spelling the user confirms is the same investor
    key = sh.holder_key(a.other)
    if not db.execute("SELECT 1 FROM shareholding_holders WHERE holder_key = ? LIMIT 1", (key,)).fetchone():
        raise CopyError("nobody is named %r in the stored filings" % a.other)
    if key not in f["keys"]:
        f["keys"].append(key)
    save_follows(a.follows, doc)
    print("%s now also covers %s." % (f["display"], key))
    return 0


def cmd_show(a, db):
    doc = load_follows(a.follows)
    display, keys = keys_for(db, doc, a.name)
    quarter = a.quarter or latest_quarter(db)
    rows = changes(db, keys, quarter)
    total = sum((r["value_now"] or 0) for r in rows)
    print("%s, quarter ended %s: %d companies named, about %s at the quarter-end close." % (
        display, quarter, sum(1 for r in rows if r["shares_now"]), _val(total)))
    print("%-12s %-36s %10s %10s %12s  %s" % ("symbol", "change", "% before", "% now", "value", "known from"))
    for r in rows:
        print("%-12s %-36s %10s %10s %12s  %s" % (r["symbol"] or r["isin"], r["change"][:36], _pct(r["pct_before"]),
                                                  _pct(r["pct_now"]), _val(r["value_now"]), (r["known_from"] or "")[:10]))
    deals = deals_for(db, keys, quarter)
    if deals:
        print("\nBulk and block deals since the quarter end:")
        for d in deals:
            print("  %s %-5s %-4s %s %s at %s" % (d["date"], d["kind"], d["side"].upper(), d["qty"], d["symbol"], inr(d["price"])))
    print("\nA public holder is named only above 1%: \"gone\" means sold or fell below 1%. Holdings are as of the "
          "quarter end, public only from each filing's date. Not financial advice.")
    return 0 if rows else 1


def cmd_copy(a, db):
    doc = load_follows(a.follows)
    display, keys = keys_for(db, doc, a.name)
    quarter = a.quarter or latest_quarter(db)
    rows = [r for r in holdings(db, keys, quarter, public_only=not a.promoters)]
    rows = [dict(r, value_now=r["value"]) for r in rows]
    known = max((r["broadcast_at"] for r in rows), default="")
    t = targets(rows, a.top, "%s's named holdings, quarter ended %s (shareholding filings to %s)" % (
        display, quarter, known[:10]), quarter)
    for w in t["weights"]:
        print("%-12s %6s%%" % (w["symbol"], w["weight"]))
    if a.write:
        save_follows(a.write, t)
        print("Wrote %d weights to %s. Next: trader-budget sizes them, trader-risk checks them, and you say yes "
              "to each line." % (len(t["weights"]), a.write))
    print("Not financial advice. A copy of a portfolio that is already weeks old.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find and follow named investors in India's filings (trader-copycat).")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--follows", default=FOLLOWS)
    sub = ap.add_subparsers(dest="cmd")
    d = sub.add_parser("discover", help="who holds the most in the latest quarter")
    d.add_argument("--quarter", help="YYYY-MM-DD quarter end (default: the latest complete one)")
    d.add_argument("--who", choices=["all", "individuals", "institutions"], default="all")
    d.add_argument("--promoters", action="store_true", help="include promoters and the promoter group")
    d.add_argument("--by", choices=["value", "count"], default="value")
    d.add_argument("--top", type=int, default=25)
    s = sub.add_parser("search", help="find a name and its spellings")
    s.add_argument("text")
    f = sub.add_parser("follow", help="add, alias, remove or list followed investors")
    f.add_argument("action", choices=["add", "alias", "remove", "list"])
    f.add_argument("name", nargs="?")
    f.add_argument("other", nargs="?", help="for alias: another spelling of the same investor")
    f.add_argument("--as", dest="display", help="the name to show for a new follow")
    sh_ = sub.add_parser("show", help="holdings and changes since the previous quarter")
    sh_.add_argument("name")
    sh_.add_argument("--quarter")
    c = sub.add_parser("copy", help="their holdings as target weights for trader-budget")
    c.add_argument("name")
    c.add_argument("--quarter")
    c.add_argument("--top", type=int, default=10)
    c.add_argument("--promoters", action="store_true", help="include holdings where they are a promoter")
    c.add_argument("--write", nargs="?", const="trader/targets.json", metavar="PATH")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        if a.cmd == "follow" and a.action != "list" and not a.name:
            raise CopyError("follow %s needs a name" % a.action)
        if a.cmd == "follow" and a.action == "alias" and not a.other:
            raise CopyError("follow alias needs the followed name and the other spelling")
        db = connect(a.db)
        return {"discover": cmd_discover, "search": cmd_search, "follow": cmd_follow, "show": cmd_show,
                "copy": cmd_copy}[a.cmd](a, db)
    except CopyError as e:
        print("copycat.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
