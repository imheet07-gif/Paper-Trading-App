#!/usr/bin/env python3
"""Daily scan: today's ranking for a validated strategy, evidence for each
candidate, and on rebalance days an order sheet. Paper trading only.

  python3 research/scan.py update        # after about 7 pm IST: prices, delivery, actions, indices, instruments
  python3 research/scan.py run           # the scan; writes private/scan/YYYY-MM-DD.html and .csv
  python3 research/scan.py run --capital 1000000   # first run: paper capital in rupees

What it refuses to do:
  * run on stale data: the store must hold the latest completed NSE session
    (files appear in the evening; before 7 pm IST the previous session counts)
  * trade off-schedule: orders appear only on the last NSE trading day of a
    month, exactly as the strategy was validated
  * use a parameter that is not in research/strategies.json
  * place anything: the order sheet is for entering by hand; the paper
    ledger (trader/paper.json) fills it at the next open

Each candidate gets an evidence card: rank and score, the inputs behind the
score, liquidity against the proposed position, delivery, circuit hits,
recent and upcoming corporate actions, trade-for-trade days, and red flags.
"""

import argparse
import csv
import datetime as dt
import html
import json
import os
import sqlite3
import sys
from decimal import Decimal

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))
import backtest  # noqa: E402
import bhavcopy  # noqa: E402
import corporate_actions as ca  # noqa: E402
import instruments as instruments_mod  # noqa: E402
import nse_calendar as cal  # noqa: E402
import panel as panel_mod  # noqa: E402
import paper  # noqa: E402
import signals as signals_mod  # noqa: E402
from money import D, inr, inr_short  # noqa: E402

STRATEGIES = os.path.join(HERE, "strategies.json")
OUT_DIR = os.path.join(ROOT, "private", "scan")
INSTRUMENTS = instruments_mod.DEFAULT_OUT
PUBLISHED_BY = dt.time(19, 0)  # NSE end-of-day files are normally up by this time, IST
NIFTY500_LIST = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
EQUITY_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
KITE_INSTRUMENTS = "https://api.kite.trade/instruments"
SECTORS = os.path.join(ROOT, "data", "nifty500_sectors.csv")


class Stale(RuntimeError):
    pass


def load_strategy(name):
    with open(STRATEGIES, encoding="utf-8") as fh:
        all_ = json.load(fh)
    if name not in all_:
        raise KeyError("no strategy %r in research/strategies.json" % name)
    return all_[name]


# ---------------------------------------------------------------- data freshness

def expected_session(now_ist):
    """The latest NSE session whose files should be published by now."""
    d = now_ist.date()
    if not (cal.is_trading_day(d) and now_ist.time() >= PUBLISHED_BY):
        d -= dt.timedelta(days=1)
        while not cal.is_trading_day(d):
            d -= dt.timedelta(days=1)
    return d


def last_stored(db):
    r = db.execute("SELECT MAX(date) FROM fetched WHERE kind='prices' AND status='ok'").fetchone()[0]
    return dt.date.fromisoformat(r) if r else None


def check_fresh(db, now_ist):
    want, have = expected_session(now_ist), last_stored(db)
    if have is None or have < want:
        raise Stale("market data ends %s but the %s session should be published; run: python3 research/scan.py update"
                    % (have, want))
    return have


def next_session(d, db=None):
    """The next trading day after d: from the stored sessions when the store
    already has one (the record of what happened, which the backtest also
    uses), else from NSE's holiday calendar (the future)."""
    if db is not None:
        r = db.execute("SELECT MIN(date) FROM fetched WHERE kind='prices' AND status='ok' AND date > ?",
                       (d.isoformat(),)).fetchone()[0]
        if r:
            return dt.date.fromisoformat(r)
    return cal.next_trading_day(d)


def is_rebalance_day(d, db=None):
    """Last NSE trading day of its month."""
    return next_session(d, db).month != d.month


def next_rebalance(d, db=None):
    """The first rebalance day after d."""
    x = next_session(d, db)
    while not is_rebalance_day(x, db):
        x = next_session(x, db)
    return x


# ---------------------------------------------------------------- update

def update(db_path=bhavcopy.DEFAULT_DB, today=None, log=print):
    today = today or dt.date.today()
    db = ca.connect(db_path)
    last = last_stored(db) or dt.date(2012, 1, 1)
    n = bhavcopy.backfill(db, last + dt.timedelta(days=1), today, pause=0.3, workers=4, log=log, today=today)
    log("prices: %d new day(s), store now ends %s" % (n, last_stored(db)))
    # corporate actions: a window around today, merged into the raw cache, then every factor re-checked
    rows = ca.fetch(today - dt.timedelta(days=120), today + dt.timedelta(days=90))
    try:
        cached = ca.load_raw()
    except FileNotFoundError:
        cached = []
    key = lambda r: (r.get("exDate"), r.get("symbol"), r.get("subject"))
    merged = {key(r): r for r in cached}
    merged.update({key(r): r for r in rows})
    ca.save_raw(list(merged.values()))
    stored = ca.rebuild(db, list(merged.values()))
    log("corporate actions: %d fetched, %d price-relevant stored, %d failed checks" % (
        len(rows), stored, len(ca.failed_checks(db))))
    bhavcopy.backfill_indices(db, last - dt.timedelta(days=10), today, today=today)
    # instruments (tick sizes) and sectors
    nse = bhavcopy.http_get(EQUITY_LIST)
    kite = bhavcopy.http_get(KITE_INSTRUMENTS)
    if nse and kite:
        reg = instruments_mod.build(nse.decode("utf-8-sig", "replace"), kite.decode("utf-8", "replace"))
        os.makedirs(os.path.dirname(INSTRUMENTS), exist_ok=True)
        with open(INSTRUMENTS, "w", encoding="utf-8") as fh:
            json.dump(reg, fh)
        log("instruments: %d, %d to check" % (reg["count"], len(reg["problems"])))
    sec = bhavcopy.http_get(NIFTY500_LIST)
    if sec:
        with open(SECTORS, "wb") as fh:
            fh.write(sec)
    db.close()


# ---------------------------------------------------------------- evidence

def sectors():
    out = {}
    if os.path.exists(SECTORS):
        with open(SECTORS, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                out[(r.get("ISIN Code") or "").strip()] = (r.get("Industry") or "").strip()
    return out


def ticks():
    if not os.path.exists(INSTRUMENTS):
        raise RuntimeError("no instrument registry (tick sizes); run: python3 research/scan.py update")
    with open(INSTRUMENTS, encoding="utf-8") as fh:
        reg = json.load(fh)
    return {isin: D(r["tick_size"]) for isin, r in reg["instruments"].items() if r.get("tick_size")}


def evidence(p, i, sid, scores, elig, db, link_map, position_value, sector_of):
    """One candidate's evidence card as a dict of plain values."""
    c, v = p.close[sid], p.value[sid]
    close = c.iloc[i]
    r = lambda k: c.iloc[i - k] if i - k >= 0 else np.nan
    vol = p.returns()[sid].iloc[max(0, i - 251):i + 1].std() * np.sqrt(252)
    med_value = v.iloc[max(0, i - 59):i + 1].median()
    hi52 = c.iloc[max(0, i - 251):i + 1].max()
    d20 = p.deliv[sid].iloc[max(0, i - 19):i + 1].mean()
    d250 = p.deliv[sid].iloc[max(0, i - 249):i + 1].mean()
    hh, ll = p.high[sid].iloc[max(0, i - 19):i + 1], p.low[sid].iloc[max(0, i - 19):i + 1]
    cc = c.iloc[max(0, i - 20):i + 1]
    moves = (cc / cc.shift(1) - 1).iloc[1:]
    locked = int(((hh.values == ll.values) & (np.abs(moves.values) >= 0.02)).sum())
    t2t = int((~p.eq[sid].iloc[max(0, i - 249):i + 1] & c.iloc[max(0, i - 249):i + 1].notna()).sum())
    members = ca.chain(sid, link_map)
    day = p.dates[i].date().isoformat()
    year_ago = (p.dates[i] - pd.Timedelta(days=365)).date().isoformat()
    q = "SELECT ex_date, subject, kind, check_note FROM corporate_actions WHERE isin IN (%s) AND ex_date > ? " \
        "AND kind != 'dividend' ORDER BY ex_date" % ",".join("?" * len(members))
    actions = db.execute(q, tuple(members) + (year_ago,)).fetchall()
    ranked = scores.iloc[i].where(elig.iloc[i]).dropna().rank(ascending=False)
    vol_rank = p.returns().iloc[max(0, i - 251):i + 1].std().where(elig.iloc[i]).rank(pct=True).get(sid, np.nan)
    flags = []
    today_locked = p.high[sid].iloc[i] == p.low[sid].iloc[i] and close / c.iloc[i - 1] - 1 >= 0.02
    if today_locked:
        flags.append("locked at its upper circuit today: a buy at the open may not fill")
    if med_value and position_value / med_value > 0.02:
        flags.append("position would be %.1f%% of its median daily traded value" % (position_value / med_value * 100))
    if t2t:
        flags.append("traded in a trade-for-trade or surveillance series on %d day(s) in the past year" % t2t)
    for ex, subject, kind, note in actions:
        if ex > day:
            flags.append("corporate action coming: %s on %s" % (subject, ex))
        if note:
            flags.append("corporate action check: %s (%s)" % (note, ex))
    if hi52 and close / hi52 < 0.75:
        flags.append("%.0f%% below its 52-week high" % ((1 - close / hi52) * 100))
    if vol_rank and vol_rank > 0.9:
        flags.append("among the most volatile tenth of the universe")
    return {
        "isin": sid, "symbol": p.names.get(sid, sid), "sector": sector_of.get(sid, ""),
        "rank": int(ranked.get(sid, 0)), "eligible": int(len(ranked)), "score": float(scores[sid].iloc[i]),
        "close": float(close), "ret_12_1": float(r(21) / r(252) - 1), "ret_6m": float(close / r(126) - 1),
        "ret_1m": float(close / r(21) - 1), "vol": float(vol), "from_high": float(close / hi52 - 1),
        "median_value": float(med_value), "deliv_20": float(d20), "deliv_250": float(d250), "locked_days": locked,
        "actions": [(ex, s) for ex, s, _, _ in actions], "flags": flags,
    }


# ---------------------------------------------------------------- the scan

def run(name="momentum_quality_top20", capital=None, db_path=bhavcopy.DEFAULT_DB, ledger_path=paper.DEFAULT_LEDGER,
        out_dir=OUT_DIR, now_ist=None, as_of=None):
    """as_of: replay the scan on a past session using only data up to it (no freshness check)."""
    strat = load_strategy(name)
    now_ist = now_ist or dt.datetime.now(cal.IST)
    db = sqlite3.connect(db_path)
    day = as_of or check_fresh(db, now_ist)
    start = (pd.Timestamp(day) - pd.DateOffset(years=3)).date().isoformat()
    p = panel_mod.load(db_path, start=start, end=day.isoformat())
    i = len(p.dates) - 1
    if p.dates[i].date() != day:
        raise RuntimeError("panel ends %s, store ends %s" % (p.dates[i].date(), day))
    u = strat["universe"]
    elig = panel_mod.universe(p, u["min_price"], u["min_value"], u["value_days"], u["min_history"])
    scores = signals_mod.SIGNALS[strat["signal"]](p)
    closes = {s: D(str(round(float(x), 2))) for s, x in p.close.iloc[i].dropna().items()}

    # paper ledger: fill what was ordered at the last rebalance, then value it
    ledger = paper.load(ledger_path)
    if ledger is None:
        if capital is None:
            raise RuntimeError("no paper ledger yet; start one with --capital (rupees)")
        ledger = paper.new(name, D(capital), day.isoformat())
    fills = []
    pend = ledger.get("pending")
    if pend and pend["date"] < day.isoformat():
        fill_day = next(d for d in p.dates if d.date().isoformat() > pend["date"])
        k = p.dates.get_loc(fill_day)
        bars = {}
        for o in pend["orders"]:
            s = o["isin"]
            if s in p.open.columns:
                vals = [p.open[s].iloc[k], p.high[s].iloc[k], p.low[s].iloc[k]]
                bars[s] = tuple(None if np.isnan(x) else str(round(float(x), 2)) for x in vals)
        fills = paper.fill_pending(ledger, fill_day.date().isoformat(), bars, D(strat["slippage_bps"]) / 10000)
    nav = paper.value(ledger, closes)
    if not ledger["values"] or ledger["values"][-1]["date"] != day.isoformat():
        ledger["values"].append({"date": day.isoformat(), "value": str(nav)})

    # today's ranking and evidence
    top = strat["top"]
    ranked = scores.iloc[i].where(elig.iloc[i]).dropna().sort_values(ascending=False)
    targets = backtest.select_top(scores, elig, i, top)  # the backtest's own selection
    link_map = ca.links(db)
    sec = sectors()
    cards = [evidence(p, i, s, scores, elig, db, link_map, float(nav) / top, sec) for s in targets]
    held = {}
    for s, pos in ledger["positions"].items():
        rk = int(ranked.rank(ascending=False).get(s, 0)) if s in ranked.index else None
        held[s] = {"symbol": pos["symbol"], "qty": pos["qty"], "rank": rk, "eligible": bool(elig.iloc[i].get(s, False))}

    orders = []
    rebalance = is_rebalance_day(day, db)
    if rebalance:
        tk = ticks()
        missing = [s for s in targets + list(ledger["positions"]) if s not in tk]
        if missing:
            raise RuntimeError("no tick size for %s; run update" % ", ".join(missing))
        orders = paper.orders_for(ledger, targets, nav, closes, tk, top, {s: p.names.get(s, s) for s in targets},
                                  D(str(strat["trim_band"])))
        ledger["pending"] = {"date": day.isoformat(), "orders": orders}
    paper.save(ledger, ledger_path)
    upcoming = next_rebalance(day, db)
    db.close()

    result = {"strategy": name, "strat": strat, "date": day.isoformat(), "rebalance": rebalance,
              "next_rebalance": upcoming.isoformat(), "nav": nav, "ledger": ledger, "fills": fills,
              "orders": orders, "cards": cards, "held": held, "universe": int(elig.iloc[i].sum())}
    write_outputs(result, out_dir)
    return result


# ---------------------------------------------------------------- output

def esc(s):
    return html.escape("" if s is None else str(s))


def pct(x):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else "%+.1f%%" % (x * 100)


def write_outputs(res, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, res["date"])
    if res["orders"]:
        with open(base + "-orders.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["side", "symbol", "isin", "qty", "limit_price", "reference_close", "reason"])
            for o in res["orders"]:
                w.writerow([o["side"].upper(), o["symbol"], o["isin"], o["qty"], o["limit"], o["ref_price"], o["reason"]])
    with open(base + ".html", "w", encoding="utf-8") as fh:
        fh.write(render(res))


def render(res):
    import build as dash  # dashboard/build.py: the same look and the same no-network rule
    s, led = res["strat"], res["ledger"]
    start = D(led["capital"])
    nav = res["nav"]
    head = ("<div class='kpis'>"
            "<div class='kpi'><div class='k'>Paper portfolio</div><div class='v'>%s</div><div class='s'>%s since %s</div></div>"
            "<div class='kpi'><div class='k'>Positions</div><div class='v'>%d</div><div class='s'>cash %s</div></div>"
            "<div class='kpi'><div class='k'>Universe today</div><div class='v'>%d</div><div class='s'>eligible stocks</div></div>"
            "<div class='kpi'><div class='k'>%s</div><div class='v'>%s</div><div class='s'>%s</div></div></div>") % (
        esc(inr(nav, 0)), esc(pct(float(nav / start - 1))), esc(led["started"]), len(led["positions"]),
        esc(inr_short(D(led["cash"]))), res["universe"],
        "Rebalance" if res["rebalance"] else "Next rebalance",
        "today" if res["rebalance"] else esc(res["next_rebalance"]),
        "orders below, for the next open" if res["rebalance"] else "no orders until then")

    order_html = ""
    if res["orders"]:
        rows = "".join("<tr><td>%s</td><td>%s</td><td class='n'>%d</td><td class='n'>%s</td><td class='n'>%s</td>"
                       "<td>%s</td></tr>" % (esc(o["side"].upper()), esc(o["symbol"]), o["qty"], esc(inr(D(o["limit"]))),
                                             esc(inr(D(o["limit"]) * o["qty"], 0)), esc(o["reason"]))
                       for o in sorted(res["orders"], key=lambda o: o["side"] != "sell"))
        order_html = ("<section class='card'><h2>Order sheet for the next open</h2><p class='muted'>Limit orders, day "
                      "validity. Sells first. Enter them by hand in your broker if you choose to follow the paper "
                      "strategy; the paper ledger fills them at the next open.</p><div class='tablewrap'><table><thead>"
                      "<tr><th>Side</th><th>Stock</th><th class='n'>Qty</th><th class='n'>Limit</th>"
                      "<th class='n'>Value at limit</th><th>Why</th></tr></thead><tbody>%s</tbody></table></div>"
                      "</section>") % rows

    fills_html = ""
    if res["fills"]:
        fills_html = "<section class='card'><h2>Paper fills</h2><ul>%s</ul></section>" % "".join(
            "<li>%s %s %d at %s (charges %s)</li>" % (esc(f["side"]), esc(f["symbol"]), f["qty"],
                                                      esc(inr(D(f["price"]))), esc(inr(D(f["charges"]))))
            for f in res["fills"])

    cards = []
    for c in res["cards"]:
        flags = "".join("<li>%s</li>" % esc(f) for f in c["flags"]) or "<li class='muted'>none</li>"
        acts = ", ".join("%s %s" % (esc(a), esc(b)) for a, b in c["actions"]) or "none in the past year"
        cards.append(
            "<div class='card'><h2>#%d %s <span class='muted'>%s</span></h2>"
            "<p class='muted'>%s. Ranked %d of %d eligible stocks with a score today.</p>"
            "<div class='tablewrap'><table><tbody>"
            "<tr><td>12-month return, skipping the last month</td><td class='n'>%s</td></tr>"
            "<tr><td>One-year volatility</td><td class='n'>%.0f%%</td></tr>"
            "<tr><td>Score (return per unit of volatility)</td><td class='n'>%.2f</td></tr>"
            "<tr><td>6 months / 1 month</td><td class='n'>%s / %s</td></tr>"
            "<tr><td>From 52-week high</td><td class='n'>%s</td></tr>"
            "<tr><td>Median daily traded value (60 days)</td><td class='n'>%s</td></tr>"
            "<tr><td>Delivery, last 20 days vs one year</td><td class='n'>%.0f%% vs %.0f%%</td></tr>"
            "<tr><td>Days locked at a circuit, last 20</td><td class='n'>%d</td></tr>"
            "<tr><td>Corporate actions</td><td>%s</td></tr>"
            "</tbody></table></div><p><b>Red flags</b></p><ul>%s</ul></div>" % (
                c["rank"], esc(c["symbol"]), esc(c["sector"]),
                "Close %s" % esc(inr(D(str(round(c["close"], 2))))), c["rank"], c["eligible"],
                pct(c["ret_12_1"]), c["vol"] * 100, c["score"], pct(c["ret_6m"]), pct(c["ret_1m"]), pct(c["from_high"]),
                esc(inr_short(D(str(round(c["median_value"]))))), c["deliv_20"] * 100, c["deliv_250"] * 100,
                c["locked_days"], acts, flags))

    held = "".join("<tr><td>%s</td><td class='n'>%d</td><td class='n'>%s</td><td>%s</td></tr>" % (
        esc(h["symbol"]), h["qty"], "-" if h["rank"] is None else h["rank"],
        "" if h["eligible"] else "<span class='flag'>no longer eligible</span>")
        for _, h in sorted(res["held"].items(), key=lambda kv: kv[1]["rank"] or 10 ** 6))
    held_html = ("<section class='card'><h2>Paper holdings and today's rank</h2><div class='tablewrap'><table><thead>"
                 "<tr><th>Stock</th><th class='n'>Qty</th><th class='n'>Rank</th><th></th></tr></thead><tbody>%s"
                 "</tbody></table></div><p class='muted'>Holdings outside the top %d are sold at the next "
                 "rebalance, not before.</p></section>" % (held, s["top"])) if held else ""

    ev = "".join("<tr><td>%s</td><td>%s</td></tr>" % (esc(k), esc(v)) for k, v in s["evidence"].items())
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Signal scan</title>
<style>%(css)s</style></head><body><main>
<header><div><h1>Signal scan, %(date)s</h1><div class="muted">%(desc)s Status: %(status)s.</div></div>
<button id="theme" type="button">Light / dark</button></header>
%(head)s%(orders)s%(fills)s%(held)s
<section class="card"><h2>Today's top %(top)d, with the evidence</h2><p class="muted">The ranking is recomputed
every trading day; it only becomes orders on the rebalance day.</p></section>
%(cards)s
<section class="card"><h2>Track record of this strategy</h2><div class="tablewrap"><table><tbody>%(ev)s</tbody></table>
</div><p class="muted">Every experiment, including the ones that failed: research/FINDINGS.md.</p></section>
<footer>Not financial advice. Generated from public NSE data on this device; this file makes no network requests.
Nothing here places an order.</footer>
</main><div id="tip" role="tooltip"></div><script>%(js)s</script></body></html>
""" % {"css": dash.CSS, "js": dash.JS, "date": esc(res["date"]), "desc": esc(s["description"]),
       "status": esc(s["status"]), "head": head, "orders": order_html, "fills": fills_html, "held": held_html,
       "top": s["top"], "cards": "".join(cards), "ev": ev}
    return page


def main(argv=None):
    ap = argparse.ArgumentParser(description="Daily signal scan (paper trading only).")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update", help="fetch the latest NSE data")
    r = sub.add_parser("run", help="run the scan")
    r.add_argument("--strategy", default="momentum_quality_top20")
    r.add_argument("--capital", help="paper capital in rupees, for the first run")
    r.add_argument("--ledger", default=paper.DEFAULT_LEDGER)
    r.add_argument("--out", default=OUT_DIR)
    r.add_argument("--as-of", help="replay on a past session (YYYY-MM-DD), data up to that day only; use a separate --ledger")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "update":
            update(a.db)
            return 0
        as_of = dt.date.fromisoformat(a.as_of) if a.as_of else None
        if as_of and os.path.abspath(a.ledger) == os.path.abspath(paper.DEFAULT_LEDGER):
            raise RuntimeError("a replay must use its own --ledger, not the live paper ledger")
        res = run(a.strategy, a.capital, a.db, a.ledger, a.out, as_of=as_of)
    except (Stale, RuntimeError, KeyError, cal.UnknownYear) as e:
        print("error:", e, file=sys.stderr)
        return 2
    print("scan %s: universe %d, paper value %s, %s" % (
        res["date"], res["universe"], inr(res["nav"], 0),
        "%d order(s) for the next open" % len(res["orders"]) if res["rebalance"]
        else "next rebalance %s" % res["next_rebalance"]))
    for c in res["cards"][:5]:
        print("  #%d %-12s 12-1 %s, vol %.0f%%%s" % (c["rank"], c["symbol"], pct(c["ret_12_1"]), c["vol"] * 100,
                                                    ", %d flag(s)" % len(c["flags"]) if c["flags"] else ""))
    print("wrote", os.path.join(a.out, res["date"] + ".html"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
