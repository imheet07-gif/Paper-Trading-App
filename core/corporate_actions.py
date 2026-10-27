#!/usr/bin/env python3
"""Corporate actions from NSE, turned into price adjustment factors and
checked against the prices themselves.

NSE's bhavcopy does not adjust anything: on a 1:1 bonus ex-date the close
halves and the file's previous close still shows the old price. Without
adjustment every bonus and split looks like a crash, so every signal built
on returns needs this module.

Source: https://www.nseindia.com/api/corporates-corporateActions
(index=equities, from_date/to_date as DD-MM-YYYY), one row per event with
symbol, ISIN, ex-date and a free-text subject.

A factor f multiplies every price before the ex-date (a 1:1 bonus is 0.5).

  bonus a:b                 b / (a + b)
  face value split X to Y   Y / X (a consolidation is the same formula, > 1)
  rights a:b at issue price P, previous close C
                            (b*C + a*P) / ((a + b) * C)   theoretical ex-rights
  demerger, capital reduction, scheme
                            no factor in the subject: the price move on the
                            ex-date is taken as the factor and flagged
                            "price-implied", which makes that day's return 0
  dividends                 recorded (rupees per share), not applied: the
                            price series is price-only, not total return

Every factor is checked against the actual move: close(ex) / close(before)
divided by the factor must look like a normal day (within +-25%). Big
one-day moves with no event behind them are reported too. Nothing is
silently assumed.

  python3 core/corporate_actions.py fetch --start 2012-01-01 --end 2026-10-27
  python3 core/corporate_actions.py check
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bhavcopy  # noqa: E402

API = "https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=%s&to_date=%s"
NORMAL_DAY = Decimal("0.25")  # widest band move a stock can make in a day, with margin
JUMP = Decimal("0.40")  # an unexplained one-day move this large is reported

SCHEMA = """
CREATE TABLE IF NOT EXISTS corporate_actions (
  ex_date TEXT NOT NULL, symbol TEXT NOT NULL, isin_reported TEXT, isin TEXT, series TEXT,
  subject TEXT NOT NULL, kind TEXT NOT NULL, factor TEXT, method TEXT, dividend TEXT, check_note TEXT,
  PRIMARY KEY (ex_date, symbol, subject));
"""

NUM = r"(\d+(?:\.\d+)?)"
MONEY = r"R[se]\.?\s*" + NUM


def parse_subject(subject):
    """Free-text subject -> {"kind", "ratio"?, "fv"?, "premium"?, "dividend"?}."""
    s = " ".join(subject.split())
    out = {"kind": "other"}
    bonus = re.search(r"Bonus\s*" + NUM + r"\s*:\s*" + NUM, s, re.I)
    # "Split From Rs.10/- To Re.1/-", "Split Rs.10/- To Rs.5/-", "From 10/- To Face Value 2/-"
    split = re.search(r"(Split|Sub-Division|Consolidation)\D*?(?:From\s*)?(?:R[se]\.?\s*)?" + NUM +
                      r".*?\bTo\s*(?:Face Value\s*)?(?:R[se]\.?\s*)?" + NUM, s, re.I)
    if bonus or split:
        p = {}
        if bonus:
            p["ratio"] = (Decimal(bonus.group(1)), Decimal(bonus.group(2)))
        if split:
            p["fv"] = (Decimal(split.group(2)), Decimal(split.group(3)))
        consolidation = bool(split) and split.group(1).lower() == "consolidation"
        p["kind"] = "+".join(k for k, on in (("bonus", bool(bonus)),
                                              ("consolidation" if consolidation else "split", bool(split))) if on)
        return p
    m = re.search(r"Rights\s+" + NUM + r"\s*:\s*" + NUM + r"\s*@\s*Premium\s*" + MONEY, s, re.I)
    if m:
        return {"kind": "rights", "ratio": (Decimal(m.group(1)), Decimal(m.group(2))), "premium": Decimal(m.group(3))}
    if re.search(r"Demerger|Capital Reduction|Scheme Of Arrangement|Amalgamation", s, re.I):
        return {"kind": "restructure"}
    if re.search(r"Dividend", s, re.I) and not s.lower().startswith("distribution"):
        amounts = [Decimal(x) for x in re.findall(r"Dividend\s*-?\s*" + MONEY, s, re.I)]
        if amounts:
            return {"kind": "dividend", "dividend": sum(amounts)}
    return out


def factor(parsed, prev_close=None, face_value=None):
    """(factor or None, method). prev_close and face_value in rupees, needed for rights."""
    k = parsed["kind"]
    if "ratio" in parsed and k != "rights" or "fv" in parsed:
        f = Decimal(1)
        if "ratio" in parsed:  # bonus a:b
            a, b = parsed["ratio"]
            f *= b / (a + b)
        if "fv" in parsed:  # face value from old to new
            old, new = parsed["fv"]
            f *= new / old
        return f, k
    if k == "rights":
        if prev_close is None or face_value is None:
            return None, "rights: needs previous close and face value"
        a, b = parsed["ratio"]
        issue = face_value + parsed["premium"]
        if issue >= prev_close:
            return Decimal(1), "rights above market price: no adjustment"
        return (b * prev_close + a * issue) / ((a + b) * prev_close), "rights TERP"
    return None, None


# ---------------------------------------------------------------- fetch and store

def connect(path=bhavcopy.DEFAULT_DB):
    db = bhavcopy.connect(path)
    db.executescript(SCHEMA)
    return db


RAW_CACHE = os.path.join(bhavcopy.ROOT, "data", "corporate_actions_raw.json")


def save_raw(rows, path=RAW_CACHE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)


def load_raw(path=RAW_CACHE):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def rebuild(db, rows):
    """Re-parse every action from the raw rows: after a parser fix, no refetch needed."""
    db.execute("DELETE FROM corporate_actions")
    return store(db, rows)


def fetch(start, end, get=bhavcopy.http_get, pause=1.0):
    """Corporate action rows from NSE for [start, end], one calendar year per request."""
    rows, d = [], start
    while d <= end:
        stop = min(dt.date(d.year, 12, 31), end)
        blob = get(API % (d.strftime("%d-%m-%Y"), stop.strftime("%d-%m-%Y")))
        if blob:
            rows += json.loads(blob)
        d = stop + dt.timedelta(days=1)
        time.sleep(pause)
    return rows


LINK_GAP_DAYS = 10  # calendar days allowed between an old ISIN's last trade and the new one's first


def links(db, gap_days=LINK_GAP_DAYS):
    """{old ISIN: new ISIN} where a symbol stops trading under one ISIN and
    resumes under another within a few days, as on most face value splits
    and some schemes. Symbols reused by a different company after a long
    gap are not linked."""
    spans = {}
    for isin, sym, first, last in db.execute(
            "SELECT isin, symbol, MIN(date), MAX(date) FROM prices WHERE series IN ('EQ','BE','BZ') "
            "GROUP BY isin, symbol"):
        spans.setdefault(sym, []).append((first, last, isin))
    out = {}
    for sym, rows in spans.items():
        rows.sort()
        for (f1, l1, a), (f2, l2, b) in zip(rows, rows[1:]):
            if a != b and l1 < f2 and (dt.date.fromisoformat(f2) - dt.date.fromisoformat(l1)).days <= gap_days:
                out[a] = b
    return out


def chain(isin, link_map):
    """Every ISIN of one security, oldest first, ending at its latest ISIN."""
    back = {v: k for k, v in link_map.items()}
    last = isin
    seen = {isin}
    while last in link_map and link_map[last] not in seen:
        last = link_map[last]
        seen.add(last)
    members = [last]
    while members[0] in back and back[members[0]] not in members:
        members.insert(0, back[members[0]])
    return members


def _members(db, isin, link_map=None):
    if not isin:
        return []
    return chain(isin, link_map if link_map is not None else links(db))


def _in(members):
    return ",".join("?" * len(members))


def _price_before(db, members, ex_date):
    return db.execute("SELECT date, close FROM prices WHERE isin IN (%s) AND date<? AND series IN ('EQ','BE','BZ') "
                      "ORDER BY date DESC LIMIT 1" % _in(members), tuple(members) + (ex_date,)).fetchone()


def _price_on(db, members, ex_date):
    return db.execute("SELECT date, close FROM prices WHERE isin IN (%s) AND date>=? AND series IN ('EQ','BE','BZ') "
                      "ORDER BY date LIMIT 1" % _in(members), tuple(members) + (ex_date,)).fetchone()


def resolve_isin(db, symbol, reported, ex_date):
    """The ISIN trading as `symbol` around the ex-date. NSE's action list
    sometimes carries a stale ISIN, so the prices decide."""
    if reported and db.execute("SELECT 1 FROM prices WHERE isin=? LIMIT 1", (reported,)).fetchone():
        return reported
    row = db.execute("SELECT isin FROM prices WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 1",
                     (symbol, ex_date)).fetchone()
    return row[0] if row else None


def store(db, rows):
    """Parse and insert actions, then check every security-day's combined factor against prices."""
    n = 0
    link_map = links(db)
    for r in rows:
        try:
            ex = dt.datetime.strptime(r["exDate"], "%d-%b-%Y").date().isoformat()
        except (ValueError, TypeError):
            continue
        p = parse_subject(r.get("subject") or "")
        if p["kind"] == "other":
            continue  # AGMs, interest payments, buy-backs: nothing to adjust
        isin = resolve_isin(db, r["symbol"], r.get("isin"), ex)
        members = chain(isin, link_map) if isin else []
        isin = members[-1] if members else None  # stored against the security's latest ISIN
        f, method, note = None, None, None
        before, on = (_price_before(db, members, ex), _price_on(db, members, ex)) if members else (None, None)
        if "ratio" in p or "fv" in p:
            fv = Decimal(r["faceVal"]) if (r.get("faceVal") or "").replace(".", "", 1).isdigit() else None
            f, method = factor(p, Decimal(before[1]) / 100 if before else None, fv)
        elif p["kind"] == "restructure" and before and on and on[0] == ex:
            f, method = Decimal(on[1]) / Decimal(before[1]), "price-implied"
        if isin is None:
            note = "symbol not found in prices"
        db.execute("INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            ex, r["symbol"], r.get("isin"), isin, r.get("series"), r["subject"], p["kind"],
            str(f) if f is not None else None, method, str(p["dividend"]) if p.get("dividend") else None, note))
        n += 1
    verify(db, link_map)
    db.commit()
    return n


def verify(db, link_map):
    """Check each security-day: the product of its factors against the actual move.

    Several events on one day (a bonus and a split listed separately) are
    checked together. A price-implied restructure factor is dropped when
    the same day has a stated factor, so the move is not counted twice.
    """
    groups = {}
    for rowid, ex, isin, f, method in db.execute(
            "SELECT rowid, ex_date, isin, factor, method FROM corporate_actions "
            "WHERE isin IS NOT NULL AND factor IS NOT NULL"):
        groups.setdefault((isin, ex), []).append((rowid, Decimal(f), method))
    for (isin, ex), items in groups.items():
        stated = [x for x in items if x[2] != "price-implied"]
        if stated and len(stated) < len(items):
            for rowid, _, method in items:
                if method == "price-implied":
                    db.execute("UPDATE corporate_actions SET factor=NULL, check_note=? WHERE rowid=?",
                               ("price-implied factor dropped: a stated factor exists for the same day", rowid))
            items = stated
        combined = Decimal(1)
        for _, f, _ in items:
            combined *= f
        if combined == 1:
            continue
        members = chain(isin, link_map)
        before, on = _price_before(db, members, ex), _price_on(db, members, ex)
        note = None
        if not (before and on and on[0] == ex):
            note = "no trade on the ex-date: factor not checked against prices"
        elif all(m != "price-implied" for _, _, m in items):
            day = Decimal(on[1]) / Decimal(before[1]) / combined - 1
            if abs(day) > NORMAL_DAY:
                note = "adjusted ex-date move %+.1f%%: factor may be wrong" % (day * 100)
        for rowid, _, _ in items:
            db.execute("UPDATE corporate_actions SET check_note=? WHERE rowid=?", (note, rowid))


def failed_checks(db):
    """[(latest ISIN, ex_date)] whose stated factors did not match the prices."""
    return db.execute("SELECT DISTINCT isin, ex_date FROM corporate_actions WHERE check_note LIKE 'adjusted%' "
                      "AND isin IS NOT NULL").fetchall()


def factors(db, isin, members=None, link_map=None):
    """{ex_date: Decimal factor} for one security (all its ISINs): splits,
    bonuses, rights and restructures."""
    members = members or _members(db, isin, link_map)
    out = {}
    for d, f in db.execute("SELECT ex_date, factor FROM corporate_actions WHERE isin IN (%s) AND factor IS NOT NULL"
                           % _in(members), tuple(members)):
        if Decimal(f) != 1:
            out[d] = out.get(d, Decimal(1)) * Decimal(f)  # a bonus and a split on one day both apply
    return out


def adjusted(db, isin, series=("EQ", "BE", "BZ"), link_map=None):
    """[(date, close, adjusted close)] in rupees for the whole security (every
    ISIN in its chain), back-adjusted: the latest price is unchanged, each
    earlier price is multiplied by the factors of every ex-date after it."""
    members = _members(db, isin, link_map)
    q = "SELECT date, close FROM prices WHERE isin IN (%s) AND series IN (%s) ORDER BY date" % (
        _in(members), ",".join("?" * len(series)))
    rows = [(d, Decimal(c) / 100) for d, c in db.execute(q, tuple(members) + tuple(series)) if c is not None]
    later = sorted(factors(db, isin, members).items(), reverse=True)  # newest ex-date first
    out, mult, i = [], Decimal(1), 0
    for d, c in reversed(rows):
        while i < len(later) and later[i][0] > d:
            mult *= later[i][1]
            i += 1
        out.append((d, c, (c * mult).quantize(Decimal("0.0001"))))
    return list(reversed(out))


def unexplained_jumps(db, threshold=JUMP):
    """One-day moves beyond the threshold, after adjustment, with no event that day."""
    out = []
    events = {(i, d) for i, d in db.execute("SELECT isin, ex_date FROM corporate_actions WHERE isin IS NOT NULL")}
    link_map = links(db)
    latest = {i for (i,) in db.execute("SELECT DISTINCT isin FROM prices WHERE series IN ('EQ','BE','BZ')")} - set(link_map)
    for isin in sorted(latest):
        prev = None
        for d, c, adj in adjusted(db, isin, link_map=link_map):
            if prev and prev[1] and abs(adj / prev[1] - 1) > threshold and (isin, d) not in events:
                out.append((isin, d, (adj / prev[1] - 1)))
            prev = (d, adj)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE corporate actions -> adjustment factors, checked against prices.")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--start", required=True)
    f.add_argument("--end", default=dt.date.today().isoformat())
    sub.add_parser("rebuild", help="re-parse the cached raw actions (after a parser change)")
    sub.add_parser("check")
    a = ap.parse_args(argv)
    db = connect(a.db)
    if a.cmd == "fetch":
        rows = fetch(dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end))
        save_raw(rows)
        print("stored %d price-relevant actions from %d rows" % (rebuild(db, rows), len(rows)))
        return 0
    if a.cmd == "rebuild":
        rows = load_raw()
        print("stored %d price-relevant actions from %d cached rows" % (rebuild(db, rows), len(rows)))
        return 0
    for row in db.execute("SELECT ex_date, symbol, kind, factor, method, check_note FROM corporate_actions "
                          "WHERE check_note IS NOT NULL ORDER BY ex_date"):
        print("check  %s %-12s %-13s factor %-10s %s" % (row[0], row[1], row[2], row[3], row[5]))
    for isin, d, r in unexplained_jumps(db):
        print("jump   %s %s %+.1f%% with no corporate action" % (d, isin, r * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
