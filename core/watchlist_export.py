#!/usr/bin/env python3
"""A small, name-free summary of the market store for a list of stocks, as JSON.

Built for an outside watchlist tracker (any tool that reads a JSON file per
day): the store stays where it is, read only, and the tracker gets one small
file with what the NSE downloads say about its stocks.

    python3 core/watchlist_export.py --symbols SBIN,BPCL --out DIR
    python3 core/watchlist_export.py --watchlist path/to/watchlist.yaml --out DIR
    python3 core/watchlist_export.py                 # paths from trader/watchlist_export.json

trader/watchlist_export.json (git-ignored, one per user) holds the paths:

    {"watchlist": "/path/to/tracker/config/watchlist.yaml", "out": "/path/to/tracker/local/store"}

The app's evening update runs this when that file exists. Each run writes
DIR/<session>.json and DIR/latest.json, where <session> is the store's last
price date. Per stock:

- price: the NSE bhavcopy close, previous close, change, volume and delivery
  share of the last session, against the 20 sessions before (closes are not
  adjusted for corporate actions, so no longer returns are given);
- results: the latest stored quarter, against the same quarter a year
  earlier, with the filing's XBRL link, and a note when NSE has announced
  results for a later quarter than the stored numbers;
- shareholding: the last two quarters' promoter, FII, DII, mutual fund and
  retail stakes, NSE's pledge answer, and the filing's XBRL link;
- bulk and block deals, insider trades and SAST Regulation 29 disclosures in
  a recent window, as counts and totals.

No names: deal clients, insiders, SAST holders and named shareholders can be
private individuals and stay in the local store (CLAUDE.md). The export
carries only aggregates and promoter yes/no. Money is written as decimal
strings in rupees, percentages as decimal strings.
"""

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bhavcopy  # noqa: E402
import corporate_actions as ca  # noqa: E402
import deals as deals_mod  # noqa: E402

ROOT = bhavcopy.ROOT
CONFIG = os.path.join(ROOT, "trader", "watchlist_export.json")
VERSION = 1
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
YAML_SYMBOL = re.compile(r"""\bsymbol:\s*["']?([A-Za-z0-9&\-]+)""")
ONE_DP, TWO_DP = Decimal("0.1"), Decimal("0.01")
SESSIONS = 20          # delivery and volume are compared with this many sessions before the last
DEAL_DAYS = 14         # calendar days of bulk and block deals
FILING_DAYS = 30       # calendar days of insider and SAST disclosures
RESULTS_DAYS = 10      # calendar days for "results announced recently"
SYMBOL_MATCH_DAYS = 400  # a shareholding filing found by symbol, not ISIN, must be this recent
# Where a reader can check what the store holds. The NSE pages list the same
# disclosures the store reads from NSE's API (core/events.py SOURCES).
PAGES = {
    "insider": "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
    "sast": "https://www.nseindia.com/companies-listing/corporate-filings-regulation-29",
}


class ExportError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def symbols_from_watchlist(text):
    """The `symbol:` values listed under the top-level `stocks:` key of a watchlist YAML.

    Reads only that one key (the standard library has no YAML reader): each
    stock is a mapping with a `symbol` field, inline or on its own line."""
    out, inside = [], False
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped:
            continue
        if not line[0].isspace() and not stripped.startswith("-"):
            inside = stripped.startswith("stocks:")
            continue
        if inside:
            m = YAML_SYMBOL.search(stripped)
            if m:
                out.append(m.group(1).upper())
    if not out:
        raise ExportError("no `symbol:` entries found under `stocks:` in the watchlist")
    return check_symbols(out)


def check_symbols(symbols):
    seen, out = set(), []
    for s in symbols:
        s = s.strip().upper()
        if not s:
            continue
        if not SYMBOL.match(s):
            raise ExportError("%r is not an NSE symbol" % s)
        if s not in seen:
            seen.add(s)
            out.append(s)
    if not out:
        raise ExportError("no symbols given")
    return out


def load_config(path=CONFIG):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for k in ("watchlist", "out"):
        if not cfg.get(k):
            raise ExportError("%s needs %r" % (path, k))
    return {k: os.path.expanduser(cfg[k]) for k in ("watchlist", "out")}


# ---------------------------------------------------------------- reading the store

def connect(path=bhavcopy.DEFAULT_DB):
    if not os.path.exists(path):
        raise ExportError("no market store at %s (python3 research/scan.py update)" % path)
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def _q(db, sql, args=()):
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []  # a store that has not fetched this kind yet
        raise


def _in(members):
    return ",".join("?" * len(members))


def _d(x):
    if x is None or x == "":
        return None
    return Decimal(str(x))


def _s(x, q=None):
    """A Decimal as a plain string ("1234.5"), or None."""
    if x is None:
        return None
    if q is not None:
        x = x.quantize(q, ROUND_HALF_UP)
    return format(x.normalize() if q is None else x, "f")


def _rupees(paise):
    return None if paise is None else Decimal(paise) / 100


def _pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1) * 100 * (1 if old > 0 else -1)


def last_session(db, on=None):
    row = _q(db, "SELECT max(date) FROM prices" + (" WHERE date <= ?" if on else ""), (on,) if on else ())
    if not row or not row[0][0]:
        raise ExportError("the market store holds no prices%s" % (" on or before " + on if on else ""))
    return row[0][0]


LOOKBACK_DAYS = 31  # a symbol not traded in this window counts as not in the store


def resolve(db, symbol, session):
    """(isin, last date traded under the symbol in the last month), EQ series first, or (None, None).

    Searched by date (the primary key), since prices has no symbol index."""
    since = (dt.date.fromisoformat(session) - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = _q(db, "SELECT isin, max(date) FROM prices WHERE date > ? AND date <= ? AND symbol = ? "
                  "GROUP BY isin ORDER BY max(date) DESC, isin", (since, session, symbol))
    if not rows:
        return None, None
    return rows[0]


def price(db, isin, session):
    rows = _q(db, "SELECT date, series, close, prev_close, volume, deliv_bp FROM prices WHERE isin = ? AND date <= ? "
                  "ORDER BY date DESC, series != 'EQ' LIMIT ?", (isin, session, (SESSIONS + 1) * 3))
    by_date = []
    for r in rows:  # one row per date, EQ first
        if not by_date or by_date[-1][0] != r[0]:
            by_date.append(r)
    if not by_date:
        return None
    day, series, close, prev, vol, deliv = by_date[0]
    before = by_date[1:SESSIONS + 1]
    avg = lambda xs: (sum(Decimal(x) for x in xs) / len(xs)) if xs else None
    vols = [r[4] for r in before if r[4] is not None]
    delivs = [r[5] for r in before if r[5] is not None]
    avg_vol = avg(vols)
    out = {
        "date": day, "series": series, "close": _s(_rupees(close), TWO_DP), "prev_close": _s(_rupees(prev), TWO_DP),
        "change_pct": _s(_pct_change(_rupees(close), _rupees(prev)), TWO_DP), "volume": vol,
        "volume_vs_avg": _s(Decimal(vol) / avg_vol, TWO_DP) if vol is not None and avg_vol else None,
        "delivery_pct": _s(Decimal(deliv) / 100, TWO_DP) if deliv is not None else None,
        "delivery_pct_avg": _s(avg(delivs) / 100, TWO_DP) if delivs else None,
        "sessions_compared": len(before), "sources": [],
    }
    for kind in ("prices", "delivery"):
        src = _q(db, "SELECT url FROM fetched WHERE date = ? AND kind = ? AND status = 'ok'", (day, kind))
        if src and src[0][0]:
            out["sources"].append(src[0][0])
    return out


FIN = ("period_end", "nature", "audited", "revenue", "net_profit", "net_profit_owners", "eps_basic",
       "interest_earned", "interest_expended", "provisions", "broadcast_at", "xbrl", "derived")


def _near(a, b, days):
    return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days - days) <= 10


def results(db, members, cutoff):
    """The latest quarter published by `cutoff`, consolidated when it has one, against a year earlier."""
    rows = [dict(zip(FIN, r)) for r in _q(db, "SELECT %s FROM financials WHERE isin IN (%s) AND broadcast_at <= ? "
                                              "ORDER BY period_end DESC, broadcast_at DESC"
                                          % (", ".join(FIN), _in(members)), (*members, cutoff))]
    out = {"latest": None, "notes": []}
    if rows:
        newest = rows[0]["period_end"]
        nature = "Consolidated" if any(r["period_end"] == newest and r["nature"] == "Consolidated" for r in rows) \
            else "Standalone"
        qs, seen = [], set()
        for r in rows:  # a revision is stored beside its original: the latest published wins
            if r["nature"] == nature and r["period_end"] not in seen:
                seen.add(r["period_end"])
                qs.append(r)
        q = qs[0]
        year_ago = next((p for p in qs[1:] if _near(q["period_end"], p["period_end"], 365)), None)
        bank = q["revenue"] is None and q["interest_earned"] is not None
        top = lambda r: _d(r["interest_earned"] if bank else r["revenue"])
        prof = lambda r: _d(r["net_profit_owners"] if r["net_profit_owners"] is not None else r["net_profit"])
        latest = {
            "quarter_end": q["period_end"], "nature": nature, "audited": q["audited"], "bank": bank,
            "top_line": "interest earned" if bank else "revenue", "top_line_rs": _s(top(q)),
            "net_profit_rs": _s(prof(q)), "eps_basic": _s(_d(q["eps_basic"])),
            "published": q["broadcast_at"], "source": q["xbrl"],
            "year_ago_quarter": year_ago["period_end"] if year_ago else None,
            "top_line_yoy_pct": _s(_pct_change(top(q), top(year_ago)), ONE_DP) if year_ago else None,
            "net_profit_yoy_pct": _s(_pct_change(prof(q), prof(year_ago)), ONE_DP) if year_ago else None,
        }
        if bank and q["interest_expended"] is not None:
            latest["net_interest_income_rs"] = _s(_d(q["interest_earned"]) - _d(q["interest_expended"]))
            latest["provisions_rs"] = _s(_d(q["provisions"]))
        if q["derived"]:
            latest["derived"] = q["derived"]
            out["notes"].append("the quarter is worked out from a half-year filing (core/financials.py)")
        out["latest"] = latest
    later = _q(db, "SELECT period_to, broadcast_at FROM results WHERE isin IN (%s) AND period_to > ? "
                   "AND broadcast_at <= ? ORDER BY period_to DESC, broadcast_at LIMIT 1" % _in(members),
               (*members, out["latest"]["quarter_end"] if out["latest"] else "", cutoff))
    if later:
        out["notes"].append("NSE lists results for the quarter ending %s, published %s, which the store's numbers do "
                            "not include yet" % (later[0][0], later[0][1][:10]))
    since = (dt.date.fromisoformat(cutoff[:10]) - dt.timedelta(days=RESULTS_DAYS)).isoformat()
    recent = _q(db, "SELECT DISTINCT period_to, substr(broadcast_at, 1, 10) FROM results WHERE isin IN (%s) "
                    "AND broadcast_at >= ? AND broadcast_at <= ? ORDER BY 2 DESC" % _in(members),
                (*members, since, cutoff))
    out["announced_recently"] = [{"quarter_end": p, "published": b} for p, b in recent]
    return out


SH = ("period_end", "broadcast_at", "xbrl", "promoter", "fii", "fpi_cat1", "fpi_cat2", "dii", "mutual_funds",
      "insurance", "retail_small", "retail_large", "pledged")


SH_SELECT = ("SELECT s.period_end, s.broadcast_at, s.xbrl, coalesce(d.promoter, s.promoter_pct), d.fii, d.fpi_cat1, "
             "d.fpi_cat2, d.dii, "
             "d.mutual_funds, d.insurance, d.retail_small, d.retail_large, d.pledged, s.isin FROM shareholding s "
             "LEFT JOIN shareholding_detail d ON d.record_id = s.record_id ")


def shareholding(db, members, symbol, cutoff):
    """The last two quarters' stakes. Some NSE shareholding filings carry an ISIN that never traded (a
    partly paid or old one, or none); those are found by the current symbol, among recent filings only,
    and say so (`matched_on`)."""
    matched = "isin"
    rows = _q(db, SH_SELECT + "WHERE s.isin IN (%s) AND s.broadcast_at <= ? "
                              "ORDER BY s.period_end DESC, s.broadcast_at DESC" % _in(members), (*members, cutoff))
    if not rows:
        since = (dt.date.fromisoformat(cutoff[:10]) - dt.timedelta(days=SYMBOL_MATCH_DAYS)).isoformat()
        rows = _q(db, SH_SELECT + "WHERE s.symbol = ? AND s.broadcast_at >= ? AND s.broadcast_at <= ? "
                                  "ORDER BY s.period_end DESC, s.broadcast_at DESC", (symbol, since, cutoff))
        matched = "symbol"
    qs, seen = [], set()
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            qs.append(dict(zip(SH + ("isin",), r)))
        if len(qs) == 2:
            break
    if not qs:
        return None

    def stakes(q):
        small, large = _d(q["retail_small"]), _d(q["retail_large"])
        fpi1, fpi2 = _d(q["fpi_cat1"]), _d(q["fpi_cat2"])
        # fii is every foreign institution (FPI category I and II and others); fpi is the two categories only
        return {"promoter": _d(q["promoter"]), "fii": _d(q["fii"]),
                "fpi": fpi1 + fpi2 if fpi1 is not None and fpi2 is not None else fpi1, "dii": _d(q["dii"]),
                "mutual_funds": _d(q["mutual_funds"]), "insurance": _d(q["insurance"]),
                "retail": small + large if small is not None and large is not None else None}
    now = stakes(qs[0])
    out = {"quarter_end": qs[0]["period_end"], "published": qs[0]["broadcast_at"], "source": qs[0]["xbrl"],
           "pct": {k: _s(v, TWO_DP) for k, v in now.items()},
           "promoter_pledged": {1: True, 0: False}.get(qs[0]["pledged"]),
           "previous_quarter": None, "change_pp": None, "matched_on": matched}
    if matched == "symbol":
        out["filing_isin"] = qs[0]["isin"]
    if len(qs) > 1:
        was = stakes(qs[1])
        out["previous_quarter"] = qs[1]["period_end"]
        out["change_pp"] = {k: _s(now[k] - was[k], TWO_DP) if now[k] is not None and was[k] is not None else None
                            for k in now}
    return out


def deals(db, members, session):
    since = (dt.date.fromisoformat(session) - dt.timedelta(days=DEAL_DAYS)).isoformat()
    rows = _q(db, "SELECT kind, date, side, qty, price FROM deals WHERE isin IN (%s) AND date > ? AND date <= ?"
              % _in(members), (*members, since, session))
    groups = {}
    for kind, day, side, qty, px in rows:
        g = groups.setdefault((kind, side), {"kind": kind, "side": side, "deals": 0, "shares": 0, "value": Decimal(0),
                                             "dates": set()})
        g["deals"] += 1
        g["shares"] += qty
        g["value"] += Decimal(qty) * Decimal(px)
        g["dates"].add(day)
    out = [{"kind": g["kind"], "side": g["side"], "deals": g["deals"], "shares": g["shares"],
            "value_rs": _s(g["value"], TWO_DP), "dates": sorted(g["dates"])}
           for _, g in sorted(groups.items())]
    sources = sorted({deals_mod.URLS[g["kind"]] for g in out if g["kind"] in deals_mod.URLS})
    return {"from": since, "to": session, "groups": out, "sources": sources}


def insider(db, members, cutoff):
    since = (dt.date.fromisoformat(cutoff[:10]) - dt.timedelta(days=FILING_DAYS)).isoformat()
    rows = _q(db, "SELECT category, txn, mode, qty, value FROM insider WHERE isin IN (%s) AND disclosed_at >= ? "
                  "AND disclosed_at <= ?" % _in(members), (*members, since, cutoff))
    groups = {}
    for cat, txn, mode, qty, value in rows:
        g = groups.setdefault((cat or "", txn or "", mode or ""), [0, 0, 0])
        g[0] += 1
        g[1] += qty or 0
        g[2] += value or 0
    return {"from": since, "groups": [{"category": c, "transaction": t, "mode": m, "filings": n, "shares": q,
                                       "value_rs": str(v)} for (c, t, m), (n, q, v) in sorted(groups.items())],
            "source": PAGES["insider"]}


def sast(db, members, cutoff):
    since = (dt.date.fromisoformat(cutoff[:10]) - dt.timedelta(days=FILING_DAYS)).isoformat()
    rows = _q(db, "SELECT promoter, side, mode, pct_acquired, pct_sold, pct_after, disclosed_at FROM sast "
                  "WHERE isin IN (%s) AND disclosed_at >= ? AND disclosed_at <= ? ORDER BY disclosed_at DESC"
              % _in(members), (*members, since, cutoff))
    return {"from": since, "disclosures": [
        {"promoter": {1: True, 0: False}.get(p), "side": side, "mode": mode, "pct_acquired": a, "pct_sold": s,
         "pct_after": after, "disclosed": at} for p, side, mode, a, s, after, at in rows],
        "source": PAGES["sast"]}


def feed_ends(db):
    """The last disclosure time of each event feed, so a reader can tell 'none' from 'not collected'."""
    out = {}
    for name, sql in (("prices", "SELECT max(date) FROM prices"), ("deals", "SELECT max(date) FROM deals"),
                      ("insider", "SELECT max(disclosed_at) FROM insider"),
                      ("sast", "SELECT max(disclosed_at) FROM sast"),
                      ("results_announcements", "SELECT max(broadcast_at) FROM results"),
                      ("financials", "SELECT max(broadcast_at) FROM financials"),
                      ("shareholding", "SELECT max(broadcast_at) FROM shareholding")):
        row = _q(db, sql)
        out[name] = row[0][0] if row else None
    return out


# ---------------------------------------------------------------- export

def build(db, symbols, on=None, now=None):
    """The export for `symbols` as of the store's last session on or before `on`.

    Filings are taken up to `now` (default: the end of `on`, or the clock when
    `on` is not given), so the evening's filings after the close are in."""
    session = last_session(db, on)
    if now is None:
        now = (on + "T23:59:59") if on else dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    link_map = ca.links(db)  # filings are read over each stock's ISIN chain (CONCEPTS.md)
    stocks = []
    for sym in check_symbols(symbols):
        isin, traded = resolve(db, sym, session)
        if isin is None:
            stocks.append({"symbol": sym, "isin": None, "error": "not in the NSE store: no NSE bhavcopy row under this "
                                                                 "symbol in the last %d days (BSE only, renamed, "
                                                                 "suspended or not yet listed)" % LOOKBACK_DAYS})
            continue
        members = ca.chain(isin, link_map)
        entry = {"symbol": sym, "isin": isin, "isin_chain": members, "notes": []}
        if traded != session:
            entry["notes"].append("last traded under this symbol on %s, not on %s (suspended, renamed or delisted)"
                                  % (traded, session))
        entry["price"] = price(db, isin, session)
        entry["results"] = results(db, members, now)
        entry["shareholding"] = shareholding(db, members, sym, now)
        entry["deals"] = deals(db, members, session)
        entry["insider"] = insider(db, members, now)
        entry["sast"] = sast(db, members, now)
        stocks.append(entry)
    return {"version": VERSION, "session": session, "filings_to": now,
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "NSE files in the Paper-Trading-App market store (core/watchlist_export.py)",
            "feeds_end": feed_ends(db),
            "notes": ["Closes are as published in NSE's bhavcopy, not adjusted for corporate actions.",
                      "Bulk and block deals: NSE publishes only the latest session, so evenings the store did not "
                      "collect are missing.",
                      "No names: deal clients, insiders and holders stay in the local store."],
            "stocks": stocks}


def _write(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def export(symbols, out_dir, db_path=bhavcopy.DEFAULT_DB, on=None, now=None):
    db = connect(db_path)
    try:
        doc = build(db, symbols, on, now)
    finally:
        db.close()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, doc["session"] + ".json")
    _write(path, doc)
    _write(os.path.join(out_dir, "latest.json"), doc)
    return path, doc


def run_configured(db_path=bhavcopy.DEFAULT_DB, config=CONFIG, log=print):
    """The export trader/watchlist_export.json asks for, or nothing when that file does not exist."""
    cfg = load_config(config)
    if cfg is None:
        return None
    with open(cfg["watchlist"], encoding="utf-8") as fh:
        symbols = symbols_from_watchlist(fh.read())
    path, doc = export(symbols, cfg["out"], db_path)
    missing = [s["symbol"] for s in doc["stocks"] if s.get("error")]
    log("watchlist export: %d stocks to %s%s" % (len(doc["stocks"]), path,
                                                 ", not in the NSE store: " + ", ".join(missing) if missing else ""))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export a name-free store summary for a watchlist.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--symbols", help="comma-separated NSE symbols")
    g.add_argument("--watchlist", help="a watchlist YAML with `symbol:` entries under `stocks:`")
    ap.add_argument("--out", help="folder for <session>.json and latest.json")
    ap.add_argument("--date", help="export as of this session (YYYY-MM-DD); default the store's last")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    a = ap.parse_args(argv)
    try:
        if not a.symbols and not a.watchlist:
            if a.out or a.date:
                raise ExportError("give --symbols or --watchlist with --out")
            if run_configured(a.db) is None:
                raise ExportError("no %s; give --symbols or --watchlist, and --out" % CONFIG)
            return 0
        if not a.out:
            raise ExportError("--out is required")
        if a.date:
            dt.date.fromisoformat(a.date)
        if a.watchlist:
            with open(a.watchlist, encoding="utf-8") as fh:
                symbols = symbols_from_watchlist(fh.read())
        else:
            symbols = a.symbols.split(",")
        path, doc = export(symbols, a.out, a.db, a.date)
    except (ExportError, OSError, ValueError) as e:
        print("watchlist export: %s" % e, file=sys.stderr)
        return 1
    print("wrote %s: %d stocks, session %s" % (path, len(doc["stocks"]), doc["session"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
