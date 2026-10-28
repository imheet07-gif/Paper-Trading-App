#!/usr/bin/env python3
"""The trade finder: "I have Rs 50,000, medium risk: what is the best trade?"
and "what about this stock?", each answered with its evidence.

  python3 research/advise.py --amount 50000 --risk medium
  python3 research/advise.py --amount 50000 --risk low --json
  python3 research/advise.py --amount 50000 --stock TATAMOTORS

What "best" means, and only this: the highest ranked by the one signal that
passed the dev / validation protocol (research/strategies.json), among the
stocks that pass every hard filter. Never a forecast. Everything else on a
card (results, shareholding, SAST, deals) is context, labelled untested.

Candidates: only the top tenth of the tested ranking (about 43 of 430
eligible stocks); never further down, however many fail a filter. The
top tenth beat the average stock in both tested periods (FINDINGS.md,
2026-10-28); each card shows its tenth's record from the logged runs.

Hard filters (a pick failing one is dropped and the answer says why):
  the strategy's universe   company shares only, EQ series, no failed
                            corporate action check, price, liquidity, history
  locked                    not locked at a circuit on the last session
  liquidity                 the order is at most 1% of the 60-session median
                            traded value
  size                      at least one share fits the stock's slice and the
                            risk limit (research/exits.py)

Sizing per profile (research/FINDINGS.md, 2026-10-28): low 5 stocks with
0.5% of the amount lost at the stop, medium 3 with 1%, high 1 with 2%;
stop at 2 ATR, target at 2 R; whole shares at a limit price on the tick,
charges from core/charges.py. Stops and targets are risk limits, not a
tested edge, until the exit test passes.

Not yet available, and said on every answer until it is: the record of each
profile and rank bucket (backtests pending), the after-tax comparison with
a Nifty momentum index fund, and a results calendar. Nothing places an
order: the answer ends in an order sheet.
"""

import argparse
import datetime as dt
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
import backtest  # noqa: E402
import bhavcopy  # noqa: E402
import corporate_actions as ca  # noqa: E402
import exits  # noqa: E402
import nse_calendar as cal  # noqa: E402
import panel as panel_mod  # noqa: E402
import scan  # noqa: E402
import signals as signals_mod  # noqa: E402
from money import D, inr, paise, round_to_tick  # noqa: E402

STRATEGY = "momentum_quality_top20"
LIMIT_BAND = Decimal("0.01")      # buy limit 1% above the last close, as the scan's order sheet
MAX_SHARE_OF_VALUE = 0.01         # the order at most 1% of the 60-session median traded value
LOOKBACK_DAYS = 90                # context window for shareholding, SAST and deals
CANDIDATE_SHARE = 0.10            # the top tenth of the ranking (FINDINGS.md, 2026-10-28)
RUNS_LOG = backtest.RUNS_LOG
LEVEL_PENDING = "the record of this risk level on dev and validation (research/profiles.py, not yet run)"
SAFER = ["high", "medium", "low"]
PENDING = ["the after-tax comparison with a Nifty momentum index fund (step 1, pending)",
           "a results calendar: dates of upcoming results are not stored yet"]


def decile_record(signal, runs_log=RUNS_LOG):
    """Each tenth of the ranking's mean monthly return above the average stock, from the
    latest logged evaluation of this signal on dev and on validation: {period: (at, [10 values])}.
    Logged runs only; never the holdout."""
    out = {}
    if not os.path.exists(runs_log):
        return out
    with open(runs_log, encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ic = e.get("ic") or {}
            if e.get("signal") == signal and e.get("period") in ("dev", "validation") and \
                    len(ic.get("decile_excess") or []) == 10:
                if e["period"] not in out or e["at"] > out[e["period"]][0]:
                    out[e["period"]] = (e["at"], ic["decile_excess"])
    return out


def tenth_of(rank, of):
    """1 for the top tenth of the ranking, 10 for the bottom."""
    return min(10, int((rank - 1) * 10 // of) + 1)


def bucket(rank, of, record):
    """What this stock's tenth of the ranking returned above the average stock, per month."""
    if not rank or not of:
        return None
    t = tenth_of(rank, of)
    out = {"tenth": t, "label": "top 10%" if t == 1 else "%d0%% to %d0%% band" % (t - 1, t)}
    for period, (at, dec) in record.items():
        out[period] = round(100 * dec[10 - t], 2)  # deciles are stored lowest score first
        out[period + "_logged"] = at
    return out


def level_record(runs_log=RUNS_LOG):
    """Each risk level's latest logged run of research/profiles.py on dev and on validation:
    {level: {"dev": entry, "validation": entry, "passed": True | False | None}}. passed is
    None until both periods are logged; the rule is the one fixed in FINDINGS.md (2026-10-28)
    and stored with each run as offer_test."""
    out = {lvl: {"passed": None} for lvl in SAFER}
    if not os.path.exists(runs_log):
        return out
    with open(runs_log, encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind, _, lvl = (e.get("profile") or "").partition(":")
            if kind != "risk" or lvl not in out or e.get("period") not in ("dev", "validation"):
                continue
            prev = out[lvl].get(e["period"])
            if prev is None or e.get("at", "") > prev.get("at", ""):
                out[lvl][e["period"]] = e
    for lvl, r in out.items():
        if "dev" in r and "validation" in r:
            r["passed"] = bool(r["dev"].get("offer_test") and r["validation"].get("offer_test"))
    return out


def level_to_offer(asked, record):
    """(level, why): the asked level if it passed or is not yet tested, else the next safer one
    that passed or is not yet tested; (None, why) when every level from it down failed."""
    failed = []
    for lvl in SAFER[SAFER.index(asked):]:
        if record[lvl]["passed"] is False:
            failed.append(lvl)
            continue
        if not failed:
            return lvl, None
        return lvl, "%s risk did not pass its backtest (%s), so this is the same ranking at %s risk" % (
            asked, "; ".join(_failed_why(record[f]) for f in failed[:1]), lvl)
    return None, "no risk level from %s down passed its backtest (%s)" % (
        asked, "; ".join("%s: %s" % (f, _failed_why(record[f])) for f in failed))


def _failed_why(r):
    for period in ("dev", "validation"):
        e = r.get(period) or {}
        if not e.get("offer_test"):
            beat = (e.get("cagr") or 0) > (e.get("bench_cagr") or 0)
            return "%s: %s" % ({"dev": "2013 to 2020", "validation": "2021 to 2023"}[period],
                               "a return for its risk below the average stock's" if beat else
                               "it grew less than the average stock")
    return "did not pass"


def level_summary(r, amount):
    """The record a card shows for a level: per period CAGR, Sharpe, worst drawdown, worst month,
    share of months losing, and the worst month in rupees on this amount."""
    out = {"passed": r["passed"]}
    for period in ("dev", "validation"):
        e = r.get(period)
        if not e:
            continue
        wm = e.get("worst_month")
        out[period] = {"cagr": e.get("cagr"), "sharpe": e.get("sharpe"), "max_drawdown": e.get("max_drawdown"),
                       "worst_month": wm, "months_losing": e.get("months_losing"),
                       "universe_cagr": e.get("bench_cagr"), "universe_sharpe": e.get("bench_sharpe"),
                       "worst_month_rupees": str(paise(D(amount) * D(str(wm)))) if wm is not None else None,
                       "logged": e.get("at")}
    return out


class AdviseError(Exception):
    pass


# ---------------------------------------------------------------- context from the store

def _q(db, sql, args=()):
    """A query on a table that may not exist yet in this store: [] when it does not."""
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def results_context(db, isin, day):
    """The latest quarter published by `day` against the same quarter a year earlier; of a
    quarter filed again (a revision), the latest version published by `day`."""
    rows = _q(db, "SELECT period_end, nature, revenue, net_profit, eps_basic, broadcast_at FROM financials "
                  "WHERE isin = ? AND broadcast_at <= ? ORDER BY period_end DESC, nature, broadcast_at DESC", (isin, day + "T23:59:59"))
    if not rows:
        return None
    latest = rows[0][0]
    pick = lambda pe: next((r for r in rows if r[0] == pe and r[1] == "Consolidated"), None) or \
        next((r for r in rows if r[0] == pe), None)
    now = pick(latest)
    year_ago = (dt.date.fromisoformat(latest) - dt.timedelta(days=365)).isoformat()
    before = next((pick(r[0]) for r in rows if abs((dt.date.fromisoformat(r[0]) - dt.date.fromisoformat(year_ago)).days) <= 10),
                  None)

    def growth(a, b):
        try:
            a, b = D(a), D(b)
            return str(((a / b - 1) * 100).quantize(Decimal("0.1"))) if b > 0 else None
        except Exception:
            return None
    out = {"quarter": now[0], "nature": now[1], "revenue": now[2], "net_profit": now[3], "eps": now[4],
           "published": now[5][:10], "year_earlier": before[0] if before else None}
    if before:
        out["revenue_growth_pct"] = growth(now[2], before[2])
        out["profit_growth_pct"] = growth(now[3], before[3])
    return out


def ownership_context(db, isin, day):
    since = (dt.date.fromisoformat(day) - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    out = {"label": "untested context"}
    q = _q(db, "SELECT d.period_end, d.promoter, d.fii, d.dii, d.mutual_funds, d.pledged FROM shareholding_detail d "
               "JOIN shareholding s ON s.record_id = d.record_id WHERE d.isin = ? AND s.broadcast_at <= ? "
               "ORDER BY d.period_end DESC LIMIT 2", (isin, day + "T23:59:59"))
    if q:
        keys = ("quarter", "promoter", "fii", "dii", "mutual_funds", "pledged")
        out["shareholding"] = dict(zip(keys, q[0]))
        if len(q) > 1:
            out["shareholding_before"] = dict(zip(keys, q[1]))
    out["sast"] = [dict(zip(("disclosed", "holder_is_promoter", "mode", "side", "pct"), r)) for r in
                   _q(db, "SELECT substr(disclosed_at, 1, 10), promoter, mode, side, COALESCE(pct_acquired, pct_sold) "
                          "FROM sast WHERE isin = ? AND disclosed_at > ? AND disclosed_at <= ? ORDER BY disclosed_at DESC",
                      (isin, since, day + "T23:59:59"))]
    out["deals"] = [dict(zip(("date", "kind", "side", "qty", "price", "client"), r)) for r in
                    _q(db, "SELECT date, kind, side, qty, price, client FROM deals WHERE isin = ? AND date > ? AND date <= ? "
                           "ORDER BY date DESC", (isin, since, day))]
    return out


# ---------------------------------------------------------------- the answer

def charges_fn(broker):
    if not broker:
        return None
    import charges

    def fn(side, qty, price):
        return charges.trade_charges(broker, side, qty, price, "delivery", "NSE")["total"]
    return fn


def card(p, i, sid, scores, elig, db, link_map, amount, risk, n_stocks, ticks, broker, sector_of, atr_now, ranked):
    """One stock's evidence card, with its filters and plan. Never raises for a filter: it says."""
    day = p.dates[i].date().isoformat()
    close = D(str(round(float(p.close[sid].iloc[i]), 2)))
    base = scan.evidence(p, i, sid, scores, elig, db, link_map, float(amount) / n_stocks, sector_of)
    rank = int(ranked.get(sid, 0)) if sid in ranked.index else None
    c = {"isin": sid, "symbol": base["symbol"], "sector": base["sector"], "close": str(close),
         "rank": rank, "of": int(len(ranked)), "percentile": round(100.0 * (1 - (rank - 1) / len(ranked)), 1) if rank else None,
         "score": base["score"], "parts": {"return_12_1": base["ret_12_1"], "volatility_1y": base["vol"]},
         "recent": {"return_1m": base["ret_1m"], "return_6m": base["ret_6m"], "from_52w_high": base["from_high"]},
         "flags": base["flags"], "filters": [], "plan": None}
    if not bool(elig.iloc[i].get(sid, False)):
        c["filters"].append("outside the strategy's universe today (series, liquidity, price, history or a failed "
                            "corporate action check)")
    hi, lo, prev = p.high[sid].iloc[i], p.low[sid].iloc[i], p.close[sid].iloc[i - 1] if i else np.nan
    if hi == lo and not np.isnan(prev) and abs(float(p.close[sid].iloc[i]) / prev - 1) >= 0.02:
        c["filters"].append("locked at a circuit on the last session: a buy at the open may not fill")
    tick = ticks.get(sid)
    limit = round_to_tick(close * (1 + LIMIT_BAND), tick, "down") if tick else paise(close * (1 + LIMIT_BAND))
    a = atr_now.get(sid, np.nan)
    if np.isnan(a) or a <= 0:
        c["filters"].append("no 14-session ATR yet, so no stop can be set")
    else:
        try:
            plan = exits.plan(limit, D(str(round(float(a), 4))), amount, risk, n_stocks, tick, charges_fn(broker))
        except ValueError as e:
            c["filters"].append("size: %s" % e)
            plan = None
        if plan:
            med = base["median_value"]
            if med and float(plan["cost"]) > MAX_SHARE_OF_VALUE * med:
                c["filters"].append("liquidity: the order is %.2f%% of its median daily traded value (limit 1%%)"
                                    % (100 * float(plan["cost"]) / med))
            rt = None
            fn = charges_fn(broker)
            if fn:
                rt = fn("buy", plan["qty"], limit) + fn("sell", plan["qty"], limit)
            c["plan"] = {"order": "BUY %d %s on NSE, delivery, limit %s" % (plan["qty"], base["symbol"], limit),
                         "limit": str(limit), "qty": plan["qty"], "cost": str(plan["cost"]),
                         "stop": str(plan["stop"]), "target": str(plan["target"]),
                         "loss_at_stop": str(plan["loss_at_stop"]), "gain_at_target": str(plan["gain_at_target"]),
                         "round_trip_charges": str(paise(rt)) if rt is not None else None,
                         "round_trip_pct": str((rt / plan["cost"] * 100).quantize(Decimal("0.01"))) if rt else None,
                         "tick_checked": plan["tick_checked"],
                         "exits_label": "risk limits, not a tested edge (exit test pending)"}
    c["results"] = results_context(db, sid, day)
    c["ownership"] = ownership_context(db, sid, day)
    c["coming"] = [{"ex_date": ex, "action": s} for ex, s in base["actions"] if ex > day]
    c["passes"] = not c["filters"]
    return c


def advise(amount, risk="medium", stock=None, db_path=bhavcopy.DEFAULT_DB, as_of=None, now_ist=None, ticks=None,
           broker="zerodha", sector_of=None, strategy=STRATEGY, candidate_share=CANDIDATE_SHARE, runs_log=RUNS_LOG):
    """The full answer as a dict. as_of replays a past session with data to it only."""
    amount = D(amount)
    if amount <= 0:
        raise AdviseError("the amount must be positive")
    if risk not in exits.RISK_SHARE:
        raise AdviseError("risk must be low, medium or high")
    levels = level_record(runs_log)
    asked = risk
    risk, fallback = level_to_offer(asked, levels)
    strat = strategy if isinstance(strategy, dict) else scan.load_strategy(strategy)
    db = sqlite3.connect(db_path)
    day = as_of or scan.check_fresh(db, now_ist or dt.datetime.now(cal.IST))
    start = (pd.Timestamp(day) - pd.DateOffset(years=3)).date().isoformat()
    p = panel_mod.load(db_path, start=start, end=day.isoformat())
    i = len(p.dates) - 1
    u = strat["universe"]
    elig = panel_mod.universe(p, u["min_price"], u["min_value"], u["value_days"], u["min_history"])
    scores = signals_mod.scores(p, strat)
    ranked = scores.iloc[i].where(elig.iloc[i]).dropna().rank(ascending=False, method="first")
    # only the top tenth of the tested ranking can ever be offered
    n_candidates = max(1, int(np.ceil(candidate_share * len(ranked))))
    order = list(ranked.sort_values().index[:n_candidates])
    record = decile_record(strat.get("signal"), runs_log)
    atr_now = exits.atr(p).iloc[i]
    link_map = ca.links(db)
    ticks = ticks if ticks is not None else scan.ticks()
    sector_of = sector_of if sector_of is not None else scan.sectors()
    args = (scores, elig, db, link_map, amount, risk or asked)
    n = exits.PROFILE_STOCKS[risk or asked]

    def pick(profile):
        k = exits.PROFILE_STOCKS[profile]
        picks, dropped = [], []
        for sid in order:
            cd = card(p, i, sid, scores, elig, db, link_map, amount, profile, k, ticks, broker, sector_of, atr_now, ranked)
            if cd["passes"]:
                picks.append(cd)
            else:
                dropped.append({"symbol": cd["symbol"], "rank": cd["rank"], "why": cd["filters"]})
            if len(picks) == k:
                break
        return picks, dropped

    picks, dropped = pick(risk) if risk else ([], [])
    for c in picks:
        c["bucket"] = bucket(c["rank"], c["of"], record)
    try:
        review = scan.next_rebalance(day, db).isoformat()
    except Exception:  # the holiday calendar does not cover the year
        review = None
    pending = PENDING + ([] if record else ["each tenth's record: no logged evaluation of this signal yet"])
    if risk is None or levels[risk]["passed"] is None:
        pending = [LEVEL_PENDING] + pending
    out = {"date": day.isoformat(), "amount": str(amount), "risk": risk or asked, "risk_asked": asked,
           "level_record": level_summary(levels[risk or asked], amount), "fallback": fallback,
           "strategy": strategy if isinstance(strategy, str) else strat.get("name", "custom"),
           "evidence": strat.get("description", ""), "validated": strat.get("status", ""),
           "candidates": n_candidates, "picks": picks, "dropped": dropped,
           "pending": pending,
           "review_date": review,
           "rules": ["best means highest ranked by the tested signal, never a forecast",
                     "a one-month hold, entered at the next open from end-of-day data",
                     "a single stock swings far more than the tested basket of 20",
                     "candidates are the top tenth of the tested ranking, never below it",
                     "order sheet only: nothing places an order"]}
    if risk is None:
        out["answer"] = "no trade: " + fallback
    elif len(picks) < n:
        out["note"] = ("only %d of the %d stocks this profile holds pass every filter among the top %d of the ranking"
                       % (len(picks), n, n_candidates))
    if not picks and risk is not None:
        out["answer"] = "no trade: nothing in the top %d of the tested ranking passes the filters for this amount" % n_candidates
    if risk not in ("low", None) and levels["low"]["passed"] is not False:
        low, _ = pick("low")
        out["low_profile"] = [{"symbol": c["symbol"], "rank": c["rank"], "order": c["plan"]["order"],
                               "loss_at_stop": c["plan"]["loss_at_stop"]} for c in low]
    if stock:
        sym = stock.strip().upper()
        hits = [s for s in p.close.columns if p.names.get(s) == sym]
        if not hits:
            raise AdviseError("no stored prices for %s" % sym)
        sid = hits[-1]
        cd = card(p, i, sid, *args, n, ticks, broker, sector_of, atr_now, ranked)
        cd["bucket"] = bucket(cd["rank"], cd["of"], record)
        if cd["rank"] is None:
            cd["verdict"] = "not ranked by the tested signal today (outside its universe): no tested reason to buy it"
        elif cd["rank"] > n_candidates:
            cd["verdict"] = ("ranked %d of %d by the tested signal, outside its top tenth: no tested reason to prefer it "
                             "to the picks above" % (cd["rank"], cd["of"]))
        else:
            cd["verdict"] = "inside the top tenth of the tested ranking (rank %d of %d)%s" % (
                cd["rank"], cd["of"], "" if cd["passes"] else ", but it fails a filter: " + "; ".join(cd["filters"]))
        out["stock"] = cd
    db.close()
    return out


# ---------------------------------------------------------------- report

def _p(x):
    return "%.1f%%" % (100 * x) if x is not None and not np.isnan(x) else "-"


def _bucket_line(b):
    if not b:
        return "its tenth of the ranking: no record"
    parts = ["%s %+.2f%% a month" % (k, b[k]) for k in ("dev", "validation") if k in b]
    return "%s of the ranking: beat the average stock by %s (logged evaluations)" % (
        b["label"], " and ".join(parts) if parts else "no logged record")


def report(a):
    L = ["Rs %s, %s risk, %s. Evidence: %s" % (inr(a["amount"], 0)[1:], a["risk"], a["date"], a["evidence"]), ""]
    if a.get("fallback") and a["risk"] != a["risk_asked"]:
        L.append("Asked for %s risk: %s." % (a["risk_asked"], a["fallback"]))
    lr = a.get("level_record") or {}
    for period, name in (("dev", "2013 to 2020"), ("validation", "2021 to 2023")):
        x = lr.get(period)
        if x:
            L.append("%s risk, %s: %s a year (average stock %s), worst drop %s, worst month %s (%s on this amount), "
                     "%s of months lost money" % (a["risk"], name, _p(x["cagr"]), _p(x["universe_cagr"]),
                                                  _p(x["max_drawdown"]), _p(x["worst_month"]),
                                                  inr(x["worst_month_rupees"]) if x["worst_month_rupees"] else "-",
                                                  _p(x["months_losing"])))
    if a.get("answer"):
        L.append(a["answer"].upper())
    for k, c in enumerate(a["picks"], 1):
        pl = c["plan"]
        L.append("%d. %s (%s), rank %d of %d (top %.1f%%)" % (k, c["symbol"], c["sector"] or "sector unknown",
                                                           c["rank"], c["of"], 100.0 * c["rank"] / c["of"]))
        L.append("   %s, about %s; stop %s (lose %s), target %s (gain %s)%s" % (
            pl["order"], inr(pl["cost"]), inr(pl["stop"]), inr(pl["loss_at_stop"]), inr(pl["target"]),
            inr(pl["gain_at_target"]), (", round trip %s%%" % pl["round_trip_pct"]) if pl["round_trip_pct"] else ""))
        L.append("   " + _bucket_line(c.get("bucket")))
        L.append("   12-1 return %s, 1-year volatility %s, 1 month %s, %s from its 52-week high" % (
            _p(c["parts"]["return_12_1"]), _p(c["parts"]["volatility_1y"]), _p(c["recent"]["return_1m"]),
            _p(c["recent"]["from_52w_high"])))
        r = c["results"]
        if r:
            L.append("   results %s: revenue %s, profit %s against a year earlier" % (
                r["quarter"], (r.get("revenue_growth_pct") or "?") + "%", (r.get("profit_growth_pct") or "?") + "%"))
        o = c["ownership"]
        bits = []
        if o.get("shareholding"):
            sh = o["shareholding"]
            bits.append("promoter %s%%, FII %s%%, MF %s%%%s" % (sh["promoter"], sh["fii"], sh["mutual_funds"],
                                                               ", PLEDGED" if sh["pledged"] else ""))
        if o["sast"]:
            bits.append("%d SAST filing(s)" % len(o["sast"]))
        if o["deals"]:
            bits.append("%d bulk or block deal(s)" % len(o["deals"]))
        if bits:
            L.append("   untested context, last 90 days: " + "; ".join(bits))
        for f in c["flags"]:
            L.append("   flag: " + f)
    if a.get("note"):
        L.append("Note: " + a["note"])
    for d in a["dropped"][:8]:
        L.append("Passed over %s (rank %s): %s" % (d["symbol"], d["rank"], "; ".join(d["why"])))
    if a.get("low_profile") is not None:
        L.append("")
        L.append("The low-risk alternative: " + ("; ".join("%s (%s)" % (x["order"], "lose " + inr(x["loss_at_stop"]) + " at the stop")
                                                         for x in a["low_profile"]) or "nothing passes the filters"))
    if a.get("stock"):
        s = a["stock"]
        L.append("")
        L.append("%s: %s" % (s["symbol"], s["verdict"]))
        L.append("   " + _bucket_line(s.get("bucket")))
        if s.get("plan"):
            L.append("   if bought anyway: %s, stop %s, target %s" % (s["plan"]["order"], inr(s["plan"]["stop"]),
                                                                    inr(s["plan"]["target"])))
    L.append("")
    L.append("Review on %s (the next rebalance). Stops and targets are risk limits, not a tested edge."
             % (a["review_date"] or "the last trading day of the month"))
    L.append("Not available yet: " + "; ".join(a["pending"]) + ".")
    L.append("Order sheet only: nothing here places an order. Not financial advice.")
    return "\n".join(L)


def _jsonable(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def main(argv=None):
    ap = argparse.ArgumentParser(description="The trade finder: the best tested trade for an amount and a risk level.")
    ap.add_argument("--amount", required=True, help="rupees to invest")
    ap.add_argument("--risk", choices=sorted(exits.RISK_SHARE), default="medium")
    ap.add_argument("--stock", help="also show this NSE symbol's card")
    ap.add_argument("--broker", default="zerodha", help="charge rates from core/charges.json ('' for none)")
    ap.add_argument("--db", default=bhavcopy.DEFAULT_DB)
    ap.add_argument("--as-of", help="replay a past session (YYYY-MM-DD), data to it only")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        as_of = dt.date.fromisoformat(a.as_of) if a.as_of else None
        res = advise(a.amount, a.risk, a.stock, a.db, as_of, broker=a.broker or None)
    except (AdviseError, scan.Stale, RuntimeError, KeyError) as e:
        print("advise.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(_jsonable(res), indent=2, ensure_ascii=False) if a.json else report(res))
    return 0 if res["picks"] else 1


if __name__ == "__main__":
    sys.exit(main())
