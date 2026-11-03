#!/usr/bin/env python3
"""The multibagger miss audit (FINDINGS.md, 2026-11-04, step 1). Dev only.

A description, not a strategy test: it ranks nothing for trading, adopts
nothing and is not a variant in the attempt count. It asks, of the stocks
that tripled in 24 months, how many `momentum_quality_top20` would have
held, and what blocked the rest.

Definitions, all from the settings fixed in FINDINGS.md before the run:

  * dates: month-ends from 2013-01 to 2018-12, so a 24-month window ends
    inside dev (2020-12). The store is read to 2020-12-31 and no further;
    the script refuses to run on anything later.
  * multibagger: a company share (ISIN starting INE) whose split-adjusted
    close 24 month-ends after t is at least 3 times its close at t. Each
    stock counts once, at its earliest such t (its window). A stock with a
    failed corporate action check at any month-end of its window is listed
    apart and not counted.
  * caught: in the top 20 of momentum_quality within the strategy's
    universe (min price, median traded value, history, EQ series, as in
    research/strategies.json) at any of the 24 month-ends t .. t+23 (a pick
    at t+24 would earn nothing inside the window). Also: in the top tenth
    of the eligible ranking (research/advise.py's candidates).
  * share of the window's log return held: the log returns of the months
    (month-end to next month-end) from a month-end where it was in the top
    20, over its log return for the whole window.
  * blocking filter, for each month-end t .. t+23 where it was not held, the
    first of: not EQ, price under the minimum, history under the minimum,
    failed corporate action check, median value under the floor, no score
    (not enough history for the signal), ranked below the top 20.
  * look-alikes: at each month-end, stocks that pass every universe filter
    except the value floor, have a median value of at least the look-alike
    floor (Rs 50 lakh), and score at least the 20th best score of the
    actual universe (so they would make the top 20 if let in). Their
    forward returns are set against the actual top 20's and the whole
    universe's, pooled over stock-months.

Forward prices: the split-adjusted close, carried forward at most 20
sessions; a stock with no close for longer is treated as delisted and
valued at its last close less the backtest's 50% haircut, so dead stocks
count against the group they were in rather than vanishing.

  python3 research/misses.py                 # prints, logs once as kind "audit"
  python3 research/misses.py --no-log        # print only
"""

import argparse
import datetime as dt
import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest  # noqa: E402
import panel as panel_mod  # noqa: E402
import signals as signals_mod  # noqa: E402

DEV_END = "2020-12-31"
LOAD_FROM = "2012-01-01"
FIRST, LAST = "2013-01-01", "2018-12-31"
FILTERS = ("not EQ", "price", "history", "corporate action check", "traded value", "no score", "ranked below top 20")
PRICE_BUCKETS = ("under Rs 20", "Rs 20 to 100", "over Rs 100", "unknown")
VALUE_BUCKETS = ("under Rs 50 lakh", "Rs 50 lakh to 2 crore", "over Rs 2 crore", "unknown")
LOOKALIKE_FLOOR = 5e6  # Rs 50 lakh, fixed in FINDINGS.md before the run
FFILL = 20  # sessions a missing close is carried forward (the backtest's delist_after)


def settings(path=None):
    """Universe and top-N of momentum_quality_top20, read from strategies.json."""
    path = path or os.path.join(HERE, "strategies.json")
    with open(path, encoding="utf-8") as fh:
        s = json.load(fh)["momentum_quality_top20"]
    return {"top": s["top"], "universe": s["universe"], "signal": s["signal"]}


def components(p, u):
    """Each universe filter as its own frame (the universe is their AND)."""
    med = p.value.rolling(u["value_days"], min_periods=int(u["value_days"] * 0.8)).median()
    history = p.close.notna().cumsum()
    company = pd.Series([str(c).startswith("INE") for c in p.close.columns], index=p.close.columns)
    return {
        "eq": p.eq,
        "price": p.raw_close >= u["min_price"],
        "history": history >= u["min_history"],
        "issue": ~p.data_issue,
        "value": med >= u["min_value"],
        "company": company,
        "med_value": med,
    }


def price_bucket(x):
    # under 20 | 20 to 100 (100 itself counts as over) | over 100
    if x is None or not np.isfinite(x):
        return "unknown"
    return "under Rs 20" if x < 20 else ("Rs 20 to 100" if x <= 100 else "over Rs 100")


def value_bucket(x):
    if x is None or not np.isfinite(x):
        return "unknown"
    return "under Rs 50 lakh" if x < 5e6 else ("Rs 50 lakh to 2 crore" if x < 2e7 else "over Rs 2 crore")


def stats(vals):
    v = np.array([x for x in vals if x is not None and np.isfinite(x)], dtype=float)
    if not len(v):
        return {"n": 0, "mean": None, "median": None}
    return {"n": int(len(v)), "mean": float(v.mean()), "median": float(np.median(v))}


def share(flags):
    f = [x for x in flags if x is not None]
    return {"n": len(f), "share": (sum(1 for x in f if x) / len(f)) if f else None}


def audit(p, cfg, first=FIRST, last=LAST, horizon=24, multiple=3.0, lookalike_floor=LOOKALIKE_FLOOR, fwd_short=1, fwd_long=12):
    u, top = cfg["universe"], cfg["top"]
    if p.dates[-1] > pd.Timestamp(DEV_END):
        raise SystemExit("refusing to run: the panel holds data after %s" % DEV_END)
    dates = p.dates
    me = panel_mod.month_ends(dates)
    pos = dates.get_indexer(me)  # session index of every month-end
    ids = list(p.close.columns)
    col = {s: k for k, s in enumerate(ids)}

    comp = components(p, u)
    elig = panel_mod.universe(p, **u)
    scores = signals_mod.SIGNALS[cfg["signal"]](p)
    S = scores.values
    E = elig.values
    # consistency: the filters taken apart must AND back to the universe
    parts = comp["eq"] & comp["price"] & comp["history"] & comp["issue"] & comp["value"] & comp["company"]
    assert (parts.values[pos] == E[pos]).all(), "filter decomposition disagrees with panel.universe"

    Pf = p.close.ffill(limit=FFILL).values[pos]          # carried-forward adjusted close at each month-end
    Pl = p.close.ffill().values[pos]                      # last close ever seen by then
    Pdead = np.where(np.isnan(Pf) & ~np.isnan(Pl), Pl * (1 - backtest.Config().delist_haircut), Pf)
    raw = p.raw_close.values[pos]
    medv = comp["med_value"].values[pos]
    issue = p.data_issue.values[pos]
    close = p.close.values
    lock_or_be = ((p.high.values == p.low.values) & (np.abs(close / np.vstack([np.full((1, close.shape[1]), np.nan), close[:-1]]) - 1) >= 0.02)
                  & ~np.isnan(close)) | (~p.eq.values & ~np.isnan(close))
    cum_lock = np.vstack([np.zeros((1, close.shape[1])), np.cumsum(lock_or_be, axis=0)])

    lo = int(np.searchsorted(me.values, np.datetime64(pd.Timestamp(first))))
    hi = int(np.searchsorted(me.values, np.datetime64(pd.Timestamp(last)), side="right")) - 1
    nme = len(me)

    # ranking at every month-end in range
    top_set, tenth_set, thr20 = {}, {}, {}
    for a in range(lo, min(nme, hi + horizon + 1)):
        i = pos[a]
        row = pd.Series(S[i], index=ids).where(pd.Series(E[i], index=ids)).dropna().sort_values(ascending=False)
        n = len(row)
        order = list(row.index)
        top_set[a] = set(order[:top])
        tenth_set[a] = {s for r, s in enumerate(order, 1) if (r - 1) * 10 < n}
        thr20[a] = float(row.iloc[top - 1]) if n >= top else -np.inf

    # ---- multibaggers: earliest qualifying t for each company share
    found, excluded = {}, []
    company = comp["company"].values
    for a in range(lo, hi + 1):
        if a + horizon >= nme:
            break
        ratio = Pf[a + horizon] / Pf[a]
        for k in np.nonzero(company & np.isfinite(ratio) & (ratio >= multiple))[0]:
            if ids[k] not in found:
                found[ids[k]] = a
    mbs = []
    for sid, a in sorted(found.items(), key=lambda kv: (kv[1], kv[0])):
        k = col[sid]
        if issue[a:a + horizon + 1, k].any():
            excluded.append({"id": sid, "symbol": p.names.get(sid), "start": me[a].date().isoformat()})
            continue
        held = [m for m in range(horizon) if sid in top_set.get(a + m, ())]
        tenth = [m for m in range(horizon) if sid in tenth_set.get(a + m, ())]
        total = math.log(Pf[a + horizon, k] / Pf[a, k])
        earned = 0.0
        for m in held:
            x, y = Pf[a + m, k], Pf[a + m + 1, k]
            if np.isfinite(x) and np.isfinite(y) and x > 0 and y > 0:
                earned += math.log(y / x)
        blocked = {f: 0 for f in FILTERS}
        for m in range(horizon):
            if m in held:
                continue
            i, e = pos[a + m], None
            if not comp["eq"].values[i, k]:
                e = "not EQ"
            elif not comp["price"].values[i, k]:
                e = "price"
            elif not comp["history"].values[i, k]:
                e = "history"
            elif not comp["issue"].values[i, k]:
                e = "corporate action check"
            elif not comp["value"].values[i, k]:
                e = "traded value"
            elif not np.isfinite(S[i, k]):
                e = "no score"
            else:
                e = "ranked below top 20"
            blocked[e] += 1
        mbs.append({"id": sid, "symbol": p.names.get(sid), "start": me[a].date().isoformat(), "multiple": float(Pf[a + horizon, k] / Pf[a, k]),
                    "caught": bool(held), "caught_top_tenth": bool(tenth), "months_to_first_catch": held[0] if held else None,
                    "months_held": len(held), "return_share_held": (earned / total) if held and total > 0 else (0.0 if not held else None),
                    "blocked_months": blocked, "start_price": nan_none(raw[a, k]), "start_value": nan_none(medv[a, k])})

    # ---- totals
    def tally(rows):
        c = sum(1 for r in rows if r["caught"])
        return {"multibaggers": len(rows), "caught": c, "missed": len(rows) - c,
                "caught_top_tenth": sum(1 for r in rows if r["caught_top_tenth"])}

    missed = [r for r in mbs if not r["caught"]]
    by_filter = {f: {"stocks_blocked_ever": 0, "stocks_mostly": 0, "months": 0} for f in FILTERS}
    for r in missed:
        for f in FILTERS:
            n = r["blocked_months"][f]
            by_filter[f]["months"] += n
            by_filter[f]["stocks_blocked_ever"] += n > 0
        main = max(FILTERS, key=lambda f: (r["blocked_months"][f], -FILTERS.index(f)))
        by_filter[main]["stocks_mostly"] += 1
    by_price = {b: tally([r for r in mbs if price_bucket(r["start_price"]) == b]) for b in PRICE_BUCKETS}
    by_value = {b: tally([r for r in mbs if value_bucket(r["start_value"]) == b]) for b in VALUE_BUCKETS}
    caught = [r for r in mbs if r["caught"]]
    first_catch = [r["months_to_first_catch"] for r in caught]
    shares = [r["return_share_held"] for r in caught if r["return_share_held"] is not None]

    # ---- look-alikes
    wide = panel_mod.universe(p, **dict(u, min_value=lookalike_floor)).values
    groups = {"look-alikes": [], "actual top 20": [], "whole universe": []}
    for a in range(lo, hi + 1):
        i = pos[a]
        la = np.nonzero(wide[i] & ~E[i] & np.isfinite(S[i]) & (S[i] >= thr20[a]))[0]
        tp = [col[s] for s in top_set[a]]
        un = np.nonzero(E[i])[0]
        for name, ks in (("look-alikes", la), ("actual top 20", tp), ("whole universe", un)):
            for k in ks:
                groups[name].append((a, k))

    def row_stats(obs):
        short, long_, tri, half, hit = [], [], [], [], []
        for a, k in obs:
            p0 = Pf[a, k]
            if not np.isfinite(p0) or p0 <= 0:
                continue
            if a + fwd_short < nme:
                short.append(Pdead[a + fwd_short, k] / p0 - 1)
            if a + fwd_long < nme:
                long_.append(Pdead[a + fwd_long, k] / p0 - 1)
                s0, s1 = pos[a] + 1, pos[a + fwd_long] + 1
                hit.append(bool(cum_lock[s1, k] - cum_lock[s0, k] > 0))
            if a + horizon < nme:
                r = Pdead[a + horizon, k] / p0
                tri.append(bool(r >= multiple))
                half.append(bool(r <= 0.5))
        return {"stock_months": len(obs), "next_1m": stats(short), "next_12m": stats(long_),
                "tripled_in_24m": share(tri), "halved_in_24m": share(half), "lock_or_BE_within_12m": share(hit)}

    lookalikes = {name: row_stats(obs) for name, obs in groups.items()}
    per_month = len(groups["look-alikes"]) / max(1, hi - lo + 1)

    return {
        "windows": {"first": me[lo].date().isoformat(), "last": me[hi].date().isoformat(), "horizon_months": horizon, "multiple": multiple},
        "totals": {**tally(mbs), "excluded_failed_check": len(excluded), "median_months_to_first_catch": float(np.median(first_catch)) if first_catch else None,
                   "median_return_share_held_of_caught": float(np.median(shares)) if shares else None},
        "misses_by_blocking_filter": by_filter,
        "by_start_price": by_price,
        "by_start_median_value": by_value,
        "look_alikes": lookalikes,
        "look_alikes_per_month": per_month,
        "excluded": excluded,
        "multibaggers": mbs,
    }


def nan_none(x):
    return None if x is None or not np.isfinite(x) else float(x)


def pct(x):
    return "n/a" if x is None else "%+.1f%%" % (x * 100)


def report(res):
    t, w = res["totals"], res["windows"]
    out = ["Miss audit, momentum_quality_top20, month-ends %s to %s, %d-month windows, %gx or more" % (w["first"], w["last"], w["horizon_months"], w["multiple"]),
           "multibaggers %d: caught %d, missed %d (top-tenth candidates %d); %d listed apart for a failed corporate action check" % (
               t["multibaggers"], t["caught"], t["missed"], t["caught_top_tenth"], t["excluded_failed_check"]),
           "caught ones: median months to first catch %s; median share of window return held %s" % (
               t["median_months_to_first_catch"], pct(t["median_return_share_held_of_caught"])),
           "", "Misses by blocking filter (stocks blocked by it at least once | stocks it blocked most | blocked stock-months):"]
    for f in FILTERS:
        b = res["misses_by_blocking_filter"][f]
        out.append("  %-24s %4d | %4d | %6d" % (f, b["stocks_blocked_ever"], b["stocks_mostly"], b["months"]))
    for title, key in (("By price at window start", "by_start_price"), ("By median traded value at window start", "by_start_median_value")):
        out += ["", title + ":"]
        for b, x in res[key].items():
            if x["multibaggers"]:
                out.append("  %-24s multibaggers %4d  caught %4d  missed %4d  top tenth %4d" % (b, x["multibaggers"], x["caught"], x["missed"], x["caught_top_tenth"]))
    out += ["", "Look-alikes (blocked only by the value floor, median value at least Rs 50 lakh, score in the actual top 20 range), %.1f a month:" % res["look_alikes_per_month"]]
    for name, g in res["look_alikes"].items():
        out.append("  %-15s stock-months %6d | next 1m mean %s median %s | next 12m mean %s median %s | tripled in 24m %s | halved in 24m %s | lock or BE in 12m %s" % (
            name, g["stock_months"], pct(g["next_1m"]["mean"]), pct(g["next_1m"]["median"]), pct(g["next_12m"]["mean"]), pct(g["next_12m"]["median"]),
            pct(g["tripled_in_24m"]["share"]), pct(g["halved_in_24m"]["share"]), pct(g["lock_or_BE_within_12m"]["share"])))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Dev-only multibagger miss audit (FINDINGS.md 2026-11-04, step 1).")
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--no-log", action="store_true", help="print only; do not append to runs.jsonl")
    ap.add_argument("--json", help="also write the full result (every multibagger) to this file")
    a = ap.parse_args(argv)
    cfg = settings()
    p = panel_mod.load(a.db, start=LOAD_FROM, end=DEV_END)
    res = audit(p, cfg)
    print(report(res))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1, default=str)
    if not a.no_log:
        small = {k: v for k, v in res.items() if k not in ("multibaggers", "excluded")}
        backtest.log_run({"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": "audit", "signal": cfg["signal"], "period": "dev",
                          "script": "research/misses.py", "config": {"top": cfg["top"], "universe": cfg["universe"], "lookalike_floor": LOOKALIKE_FLOOR},
                          "results": small, "note": "multibagger miss audit, step 1 (FINDINGS.md 2026-11-04); descriptive, adopts nothing, not a variant"})
        print("\nlogged once to research/runs.jsonl as kind audit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
