#!/usr/bin/env python3
"""One testing machine for every idea (Phase 2 of the engine).

An idea is a score frame (a signal in signals.SIGNALS, or any function of the
panel), its holding period and its number of positions. For each idea the
harness reports, at every capital level in research/retail.json (Rs 20,000
and Rs 10 lakh):

  ranking     IC and deciles on the idea's own horizon (evaluate.ic_report)
  portfolio   research/profiles.py's simulator: whole shares, Zerodha charges
              from core/charges.json, limit buys at the next open, the 1%
              liquidity limit, circuit locks, the top tenth as candidates
  after tax   the same run with each financial year's tax (core/tax.py) paid
              from the portfolio, and everything sold at the last close
  capital     the minimum capital for the idea's own picks (retail.min_capital),
              the maximum before the liquidity limit passes over too many of
              them (retail.idea_maximum), and the share of its own picks each
              level actually bought
  the bar     option B: IC t above 3, deciles monotonic above 0.5, Sharpe
              above the equal-weight universe's

On dev it also runs walk-forward windows: train on years 1..k, test on year
k+1, roll a year; each window's test year is reported, then all test years
pooled. An idea may pass a `fit` that sees only its training years; a plain
signal has nothing to fit, so its training columns are in-sample context.

Validation (2021 to 2023) is a single later look per idea, refused a second
time. The holdout is never offered: the CLI refuses it as profiles.py does.
Every run is logged to research/runs.jsonl with the idea's name and batch,
and the report says how many attempts the batch has used.

  python3 research/harness.py --signal momentum_quality --hold monthly --positions 20 --period dev
  python3 research/harness.py --signal low_volatility --hold 63 --positions 10 --period dev --batch B1

Numbers are float estimates of a simulation, never accounting.
"""

import argparse
import datetime as dt
import json
import math
import os
import secrets
import sys
from decimal import Decimal

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import aftertax  # noqa: E402
import backtest  # noqa: E402
import evaluate  # noqa: E402
import lots  # noqa: E402
import panel as panel_mod  # noqa: E402
import profiles  # noqa: E402
import retail  # noqa: E402
import signals as signals_mod  # noqa: E402
import tax as tax_mod  # noqa: E402
from money import D  # noqa: E402

PERIODS = {k: backtest.PERIODS[k] for k in ("dev", "validation")}  # never the holdout
ENGINE = "harness.py"
MONTH_SESSIONS = 21  # evaluate.HORIZON: a monthly hold's forward return


class Idea:
    """name; scores: a function of the panel giving a score frame (higher is better), or a
    signal name in signals.SIGNALS; hold: "monthly" (each month's last session) or a number
    of sessions; positions: how many to hold; fit(p, train_start, train_end), optional, gives
    the score frame for a walk-forward window from its training years only."""

    def __init__(self, name, scores, hold="monthly", positions=20, fit=None):
        if isinstance(scores, str):
            if scores not in signals_mod.SIGNALS:
                raise KeyError("unknown signal %r" % scores)
            self.signal, scores = scores, signals_mod.SIGNALS[scores]
        else:
            self.signal = getattr(scores, "__name__", "custom")
        if hold != "monthly" and (not isinstance(hold, int) or hold < 1):
            raise ValueError("hold is 'monthly' or a whole number of sessions")
        if not isinstance(positions, int) or positions < 1:
            raise ValueError("positions must be a whole number of at least 1")
        self.name, self.score_fn, self.hold, self.positions, self.fit = name, scores, hold, positions, fit

    def horizon(self):
        return MONTH_SESSIONS if self.hold == "monthly" else self.hold

    def as_dict(self):
        return {"name": self.name, "signal": self.signal, "hold": self.hold, "positions": self.positions,
                "fit": self.fit is not None}


# ---------------------------------------------------------------- rules

def check_rules(rules):
    """The simulator's constants must be the values research/retail.json states; a drift is an error."""
    want = {
        "LIMIT_BAND": rules["orders"]["limit_band"], "SLIPPAGE_BPS": rules["orders"]["slippage_bps"],
        "MAX_SHARE_OF_VALUE": rules["liquidity"]["max_share_of_median_value"],
        "VALUE_DAYS": rules["liquidity"]["value_days"], "LOCKED_MOVE": rules["circuits"]["locked_move"],
        "CASH_YIELD": rules["cash"]["yield"], "DELIST_AFTER": rules["cash"]["delist_after_sessions"],
        "DELIST_HAIRCUT": rules["cash"]["delist_haircut"],
    }
    drift = ["%s is %s in profiles.py, %s in retail.json" % (k, getattr(profiles, k), v)
             for k, v in want.items() if float(getattr(profiles, k)) != float(v)]
    if drift:
        raise ValueError("retail.json and the simulator disagree: " + "; ".join(drift))
    return rules


# ---------------------------------------------------------------- dates

def rebalance_dates(dates, start, end, hold):
    """Decision sessions: the last session before `start` (so the first holding starts on the
    window's first session), then each month's last session (monthly) or every `hold` sessions.
    Returns (sessions, the first one) inside the loaded panel; never past `end`."""
    lo = dates.searchsorted(pd.Timestamp(start))
    hi = dates.searchsorted(pd.Timestamp(end), "right")
    first = max(lo - 1, 0)
    if hold == "monthly":
        inside = list(panel_mod.month_ends(dates[lo:hi]))
        out = [dates[first]] + [d for d in inside if d != dates[first]]
    else:
        out = list(dates[first:hi:hold])
    return pd.DatetimeIndex(out), dates[first]


def ic_dates(dates, rebal, start, end, horizon, data_end=None):
    """Rebalance sessions inside [start, end] whose forward return (next open to `horizon`
    sessions later) ends inside the loaded data and, when given, by `data_end`."""
    limit = dates.searchsorted(pd.Timestamp(data_end), "right") if data_end else len(dates)
    out = []
    for d in rebal:
        if d < pd.Timestamp(start) or d > pd.Timestamp(end):
            continue
        k = dates.get_loc(d)
        if k + 1 + horizon < limit:
            out.append(d)
    return out


# ---------------------------------------------------------------- tax

class TaxHook:
    """profiles.simulate's on_day: turns its fills into broker-neutral trades and pays each
    financial year's tax (core/tax.py) from cash at that year's last session."""

    def __init__(self, p, end, haircut, fmv=None, rates=None, slab_pct=None):
        self.p, self.end, self.haircut = p, pd.Timestamp(end), haircut
        self.fmv, self.rates, self.slab = fmv or {}, rates or tax_mod.load_rates(), slab_pct
        self.trades, self.open, self.seen, self.paid = [], {}, 0, {}

    def record(self, day, s, side, qty, price):
        if qty <= 0:
            return
        self.trades.append({"date": day, "isin": s, "symbol": self.p.names.get(s, s), "side": side,
                            "qty": qty, "price": D("%.4f" % price)})
        self.open[s] = self.open.get(s, D(0)) + (qty if side == "buy" else -qty)

    def take(self, trades):
        for day, s, side, q, px, why in trades[self.seen:]:
            day = dt.date.fromisoformat(day)
            have = self.open.get(s, D(0))
            if side == "buy":
                self.record(day, s, "buy", D("%.6f" % q), px)
            elif why == "delisted":
                self.record(day, s, "sell", have, px * (1 - self.haircut))
            else:
                self.record(day, s, "sell", min(D("%.6f" % q), have), px)
        self.seen = len(trades)

    def due(self, fy):
        for y in tax_mod.assess(self.trades, (), self.slab, self.fmv, self.rates):
            if y["fy"] == fy:
                return float(y["tax"])
        return 0.0

    def __call__(self, i, d, units, trades):
        self.take(trades)
        dates = self.p.dates
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        fy = lots.financial_year(d.date())
        if nxt is not None and nxt <= self.end and lots.financial_year(nxt.date()) != fy:
            self.paid[fy] = self.due(fy)
            return -self.paid[fy]
        return 0.0


def liquidate(res, hook, capital, slippage_bps, broker):
    """Sell what is left at the last close less slippage and charges, and pay the last year's tax.
    Returns (value before that tax, value after it)."""
    nav = res["nav"] * capital
    day = nav.index[-1].date()
    fee = profiles.charges_fn(broker)
    value = float(nav.iloc[-1])
    slip = slippage_bps / 1e4
    for s, q in res["units"].items():
        px = res["last_px"][s]
        fill = px * (1 - slip)
        value -= q * px - q * fill + fee("sell", q, fill)
        if hook is not None:
            hook.record(day, s, "sell", hook.open.get(s, D(0)), fill)
    if hook is None:
        return value, value
    fy = lots.financial_year(day)
    hook.paid[fy] = hook.due(fy)
    return value, value - hook.paid[fy]


# ---------------------------------------------------------------- one run

def simulate(p, scores, elig, idea, start, end, capital, rules, tax=False, dividends=None, fmv=None):
    """One portfolio run of the idea over [start, end] at `capital` rupees. Returns
    {"nav" (rupees), "metrics", "stats", "value_sold", "value_after_tax", "tax_paid", "cagr_sold",
    "cagr_after_tax"}."""
    rebal, first = rebalance_dates(p.dates, start, end, idea.hold)
    hook = TaxHook(p, end, rules["cash"]["delist_haircut"], fmv, slab_pct=rules["tax"]["dividend_slab_pct"]) \
        if tax else None
    res = profiles.simulate(p, scores, elig, first.date().isoformat(), end, idea.positions, None, capital=capital,
                            broker=rules["charges"]["broker"], slippage_bps=rules["orders"]["slippage_bps"],
                            cash_yield=rules["cash"]["yield"], dividends=dividends, rebalance_dates=rebal,
                            candidate_share=rules["candidates"]["share_of_ranking"], on_day=hook)
    sold, after = liquidate(res, hook, capital, rules["orders"]["slippage_bps"], rules["charges"]["broker"])
    nav = res["nav"] * capital
    a, b = nav.index[0], nav.index[-1]
    return {"nav": nav, "metrics": backtest.metrics(res["nav"]), "stats": res["stats"], "value_sold": sold,
            "value_after_tax": after, "tax_paid": dict(hook.paid) if hook else {},
            "cagr_sold": aftertax.cagr(capital, sold, a, b), "cagr_after_tax": aftertax.cagr(capital, after, a, b),
            "trades": len(res["trades"])}


def minimum_capital(p, scores, elig, idea, start, end, rules):
    """The minimum capital on each decision session inside [start, end], for the idea's own top
    picks at their buy limits (raw close plus the limit band), then the idea's minimum."""
    rebal, _ = rebalance_dates(p.dates, start, end, idea.hold)
    R = p.raw_close
    band = D(str(rules["orders"]["limit_band"]))
    dp = retail.dp_charge(rules)
    per = []
    for d in rebal:
        i = p.dates.get_loc(d)
        picks = backtest.select_top(scores, elig, i, idea.positions)
        prices = [D("%.2f" % R.iat[i, R.columns.get_loc(s)]) * (1 + band) for s in picks
                  if not np.isnan(R.iat[i, R.columns.get_loc(s)])]
        per.append(retail.min_capital(prices, rules, idea.positions, dp) if prices else None)
    return retail.idea_minimum(per, rules)


def maximum_capital(p, scores, elig, idea, start, end, rules):
    """The idea's maximum capital: every own pick on every decision session inside [start, end]
    gives the capital above which the liquidity limit passes it over (retail.liquidity_threshold)."""
    rebal, _ = rebalance_dates(p.dates, start, end, idea.hold)
    days = int(rules["liquidity"]["value_days"])
    med = p.value.rolling(days, min_periods=int(days * 0.8)).median()  # as profiles.simulate
    out = []
    for d in rebal:
        i = p.dates.get_loc(d)
        for s in backtest.select_top(scores, elig, i, idea.positions):
            v = med.iat[i, med.columns.get_loc(s)]
            out.append(retail.liquidity_threshold(None if np.isnan(v) else D("%.2f" % v), idea.positions, rules))
    return retail.idea_maximum(out, rules)


def bar(ic, m, bench, rules):
    """Option B (FINDINGS.md 2026-10-29) on one capital level: each test, then all three."""
    b = rules["bar"]
    out = {"ic_t": bool(ic and ic["ic_t"] > b["ic_t_above"]),
           "monotonic": bool(ic and ic["monotonic"] > b["monotonic_above"]),
           "sharpe": bool(m and bench and m.get("sharpe", -math.inf) > bench.get("sharpe", math.inf))}
    out["passes"] = all(out.values())
    return out


def _slice_metrics(nav, a, b):
    s = nav.loc[a:b]
    return backtest.metrics(s / s.iloc[0]) if len(s) > 1 else {}


def walk_forward(p, scores, elig, fwd, bench_nav, idea, start, end, rules):
    """Train on years first..k, test on year k+1, roll. Each window: IC on the training years
    (forward returns that end inside them) and on the test year; the test year simulated at each
    capital level from its own fresh capital. Then every test month and day pooled."""
    wf = rules["walk_forward"]
    y0, y1 = pd.Timestamp(start).year, min(pd.Timestamp(end).year, p.dates[-1].year)  # never past the stored data
    if p.dates[-1] < pd.Timestamp("%d-12-01" % y1):
        y1 -= 1  # a test year needs (nearly) all its sessions
    k, step = int(wf["first_train_years"]), int(wf["test_years"])
    levels = [float(x) for x in retail.capital_levels(rules)]
    windows, pooled_dates, pooled_r = [], [], {lv: [] for lv in levels}
    pooled_bench = []
    ty = y0 + k
    while ty + step - 1 <= y1:
        tr_a, tr_b = "%d-01-01" % y0, "%d-12-31" % (ty - 1)
        te_a, te_b = "%d-01-01" % ty, min("%d-12-31" % (ty + step - 1), end)
        sc = idea.fit(p, tr_a, tr_b) if idea.fit else scores
        rebal, _ = rebalance_dates(p.dates, tr_a, te_b, idea.hold)
        tr_dates = ic_dates(p.dates, rebal, tr_a, tr_b, idea.horizon(), data_end=tr_b)
        te_dates = ic_dates(p.dates, rebal, te_a, te_b, idea.horizon(), data_end=end)
        w = {"train": [tr_a, tr_b], "test": [te_a, te_b],
             "ic_train": evaluate.ic_report(sc, fwd, elig, tr_dates),
             "ic_test": evaluate.ic_report(sc, fwd, elig, te_dates), "levels": {}}
        pooled_dates += te_dates
        first = rebalance_dates(p.dates, te_a, te_b, idea.hold)[1]
        bm = _slice_metrics(bench_nav, first, te_b)
        w["bench"] = {"cagr": bm.get("cagr"), "sharpe": bm.get("sharpe")}
        bs = bench_nav.loc[first:te_b]
        pooled_bench.append(bs.pct_change().dropna())
        for lv in levels:
            r = simulate(p, sc, elig, idea, te_a, te_b, lv, rules)
            m = r["metrics"]
            w["levels"][str(int(lv))] = {"cagr": m.get("cagr"), "sharpe": m.get("sharpe"),
                                         "max_drawdown": m.get("max_drawdown"), "unfilled": r["stats"]["unfilled"],
                                         "short_months": r["stats"]["short_months"]}
            pooled_r[lv].append(r["nav"].pct_change().dropna())
        windows.append(w)
        ty += step
    if not windows:
        return {"windows": [], "pooled": None}
    if idea.fit:
        ics = [w["ic_test"] for w in windows if w["ic_test"]]
        pooled_ic = {"months": sum(x["months"] for x in ics),
                     "ic_mean": float(np.average([x["ic_mean"] for x in ics], weights=[x["months"] for x in ics]))
                     if ics else None, "note": "fitted per window: months-weighted mean of the test ICs"}
    else:
        pooled_ic = evaluate.ic_report(scores, fwd, elig, pooled_dates)
    pooled = {"ic": pooled_ic, "levels": {}}
    br = pd.concat(pooled_bench)
    bm = backtest.metrics((1 + br).cumprod())
    pooled["bench"] = {"cagr": bm.get("cagr"), "sharpe": bm.get("sharpe")}
    for lv in levels:
        r = pd.concat(pooled_r[lv])
        m = backtest.metrics((1 + r).cumprod())
        pooled["levels"][str(int(lv))] = {"cagr": m.get("cagr"), "sharpe": m.get("sharpe"),
                                          "max_drawdown": m.get("max_drawdown"),
                                          "windows_beating_universe": sum(
                                              1 for w in windows if (w["levels"][str(int(lv))]["cagr"] or -1) >
                                              (w["bench"]["cagr"] if w["bench"]["cagr"] is not None else math.inf))}
    return {"windows": windows, "pooled": pooled}


def run(p, idea, period, rules, walk=True, dividends=None):
    """Everything the harness reports for one idea on one period (dev or validation)."""
    if period not in PERIODS:
        raise ValueError("the harness runs dev and validation only")
    start, end = PERIODS[period]
    elig = panel_mod.universe(p)
    scores = idea.score_fn(p)
    rebal, first = rebalance_dates(p.dates, start, end, idea.hold)
    fwd = evaluate.forward_returns(p, idea.horizon())
    ic = evaluate.ic_report(scores, fwd, elig, ic_dates(p.dates, rebal, start, end, idea.horizon(), data_end=end))
    ref = backtest.run(p, scores, first.date().isoformat(), end, backtest.Config(top=idea.positions),
                       rebalance_dates=rebal)
    bench = backtest.metrics(ref["bench"])
    fractional = backtest.metrics(ref["nav"])
    fmv = aftertax.fmv_2018(p)
    out = {"idea": idea.as_dict(), "period": period, "start": start, "end": end, "ic": ic,
           "bench": {k: bench.get(k) for k in ("cagr", "sharpe", "max_drawdown")},
           "fractional": {k: fractional.get(k) for k in ("cagr", "sharpe", "max_drawdown")},
           "min_capital": minimum_capital(p, scores, elig, idea, start, end, rules),
           "max_capital": maximum_capital(p, scores, elig, idea, start, end, rules), "levels": {}}
    for lv in retail.capital_levels(rules):
        lv = float(lv)
        pre = simulate(p, scores, elig, idea, start, end, lv, rules, tax=False, dividends=dividends)
        post = simulate(p, scores, elig, idea, start, end, lv, rules, tax=True, dividends=dividends, fmv=fmv)
        m = pre["metrics"]
        ms = profiles.month_stats(pre["nav"] / lv)
        out["levels"][str(int(lv))] = {
            "cagr": m.get("cagr"), "sharpe": m.get("sharpe"), "max_drawdown": m.get("max_drawdown"),
            "worst_month": ms["worst_month"], "months_losing": ms["months_losing"], "yearly": m.get("yearly"),
            "days": m.get("days"), "skew": m.get("skew"), "kurtosis": m.get("kurtosis"),
            "cagr_sold": pre["cagr_sold"], "cagr_after_tax": post["cagr_after_tax"],
            "value_after_tax": post["value_after_tax"], "tax_paid": post["tax_paid"],
            "stats": {k: v for k, v in pre["stats"].items() if k not in ("exposure_avg", "index_avg")},
            "own_picks_bought": pre["stats"]["own_picks"] / pre["stats"]["picks_wanted"]
            if pre["stats"]["picks_wanted"] else None,
            "bar": bar(ic, m, bench, rules)}
    if walk and period in rules["walk_forward"]["periods"]:
        out["walk_forward"] = walk_forward(p, scores, elig, fwd, ref["bench"], idea, start, end, rules)
    return out


# ---------------------------------------------------------------- the run log

def attempts(batch, runs_log=backtest.RUNS_LOG):
    """(runs in this batch, harness runs in all) logged so far."""
    n = total = 0
    if not os.path.exists(runs_log):
        return 0, 0
    with open(runs_log, encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if (e.get("config") or {}).get("engine") == ENGINE:
                total += 1
                n += e.get("batch") == batch
    return n, total


def looked(idea, period, runs_log=backtest.RUNS_LOG):
    """True if this idea (same name, hold and positions) already has a harness run on `period`."""
    if not os.path.exists(runs_log):
        return False
    with open(runs_log, encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            c = e.get("config") or {}
            if c.get("engine") == ENGINE and e.get("period") == period and e.get("idea") == idea.name and \
                    c.get("hold") == idea.hold and c.get("positions") == idea.positions:
                return True
    return False


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, (np.floating, float)):
        return None if np.isnan(x) else float(x)
    if isinstance(x, np.integer):
        return int(x)
    return x


def log_entry(res, batch, note, rules):
    top = res["levels"][str(int(float(retail.capital_levels(rules)[-1])))]
    return _clean({
        "at": dt.datetime.now().isoformat(timespec="seconds"), "signal": res["idea"]["signal"],
        "idea": res["idea"]["name"], "batch": batch, "period": res["period"], "profile": "harness",
        "config": {"engine": ENGINE, "hold": res["idea"]["hold"], "positions": res["idea"]["positions"],
                   "fit": res["idea"]["fit"], "retail_version": rules.get("version"),
                   "capital_levels": rules["capital_levels"]["value"]},
        # the top level's pre-tax Sharpe is the one the deflated Sharpe counts
        "sharpe": top["sharpe"], "cagr": top["cagr"], "max_drawdown": top["max_drawdown"],
        "bench_sharpe": res["bench"]["sharpe"], "bench_cagr": res["bench"]["cagr"],
        "ic_report": res["ic"], "levels": res["levels"], "fractional": res["fractional"],
        "min_capital": res["min_capital"], "max_capital": res["max_capital"], "walk_forward": res.get("walk_forward"), "note": note})


# ---------------------------------------------------------------- report

def _pct(x):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else "%.1f%%" % (x * 100)


def _f(x, fmt="%.2f"):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else fmt % x


def report(res, batch_n, total_n, batch, dsr=None):
    from money import inr
    i, ic = res["idea"], res["ic"]
    out = ["%s: %s, hold %s, %d positions, %s %s to %s" % (
        i["name"], i["signal"], i["hold"], i["positions"], res["period"], res["start"], res["end"])]
    if ic:
        out.append("  ranking   IC %.3f  t %.2f  positive %.0f%%  top minus bottom %.2f%%  monotonic %.2f  (%d dates)" % (
            ic["ic_mean"], ic["ic_t"], ic["ic_positive"] * 100, ic["top_minus_bottom"] * 100, ic["monotonic"],
            ic["months"]))
    else:
        out.append("  ranking   n/a: no date with 50 scored eligible stocks")
    b, fr = res["bench"], res["fractional"]
    out.append("  universe  CAGR %s  Sharpe %s  worst drawdown %s  (equal weight, no costs)" % (
        _pct(b["cagr"]), _f(b["sharpe"]), _pct(b["max_drawdown"])))
    out.append("  reference CAGR %s  Sharpe %s  worst drawdown %s  (backtest.py: fractional shares, market fills)" % (
        _pct(fr["cagr"]), _f(fr["sharpe"]), _pct(fr["max_drawdown"])))
    for lv, x in res["levels"].items():
        s = x["stats"]
        out.append("  Rs %-9s CAGR %s  Sharpe %s  worst drawdown %s  worst month %s  months losing %s  invested %s" % (
            inr(float(lv), 0, ""), _pct(x["cagr"]), _f(x["sharpe"]), _pct(x["max_drawdown"]), _pct(x["worst_month"]),
            _pct(x["months_losing"]), _pct(s["invested_avg"])))
        out.append("  %12s sold at the end %s a year, after gains tax %s (tax paid %s)  unfilled %d  short months %d of %d"
                   "  costs %s" % ("", _pct(x["cagr_sold"]), _pct(x["cagr_after_tax"]),
                                   inr(sum(x["tax_paid"].values()), 0), s["unfilled"], s["short_months"],
                                   s["rebalances"], inr(s["costs"], 0)))
        out.append("  %12s bought %s of the idea's own picks" % ("", _pct(x.get("own_picks_bought"))))
        bb = x["bar"]
        out.append("  %12s bar: IC t>3 %s, monotonic>0.5 %s, Sharpe>universe %s: %s" % (
            "", "yes" if bb["ic_t"] else "no", "yes" if bb["monotonic"] else "no", "yes" if bb["sharpe"] else "no",
            "PASSES" if bb["passes"] else "fails"))
    mc = res["min_capital"]
    if mc["minimum"] is not None:
        cov = ", ".join("Rs %s on %.0f%% of dates" % (inr(float(k), 0, ""), v * 100) for k, v in mc["covered"].items())
        out.append("  capital   minimum %s (covers %s%% of %d decision dates; median %s, max %s); enough: %s" % (
            inr(mc["minimum"], 0), D(mc["quantile"]) * 100, mc["dates"], inr(mc["median"], 0), inr(mc["max"], 0), cov))
        if mc["fails"]:
            out.append("  %12s FLAG: fails at %s; never shown to a smaller account than %s" % (
                "", ", ".join("Rs " + inr(x, 0, "") for x in mc["fails"]), inr(mc["minimum"], 0)))
    xc = res.get("max_capital") or {}
    if xc.get("picks"):
        sk = ", ".join("Rs %s passes over %.0f%%" % (inr(float(k), 0, ""), v * 100) for k, v in xc["skipped_at"].items())
        out.append("  capital   maximum %s (the liquidity limit passes over at most %s%% of %d own picks); %s" % (
            "none reached" if xc["maximum"] is None else inr(xc["maximum"], 0), D(str(xc["share"])) * 100, xc["picks"], sk))
        if xc["fails"]:
            out.append("  %12s FLAG: above the maximum at %s: too many of its own picks are too small to trade there" % (
                "", ", ".join("Rs " + inr(x, 0, "") for x in xc["fails"])))
    for lv in res["min_capital"]["fails"]:
        x = res["levels"].get(str(int(lv)))
        if x:
            out.append("  %12s at the flagged Rs %s, following the skip rule got CAGR %s (%s after gains tax), "
                       "buying %s of its own picks" % ("", inr(float(lv), 0, ""), _pct(x["cagr"]),
                                                       _pct(x["cagr_after_tax"]), _pct(x.get("own_picks_bought"))))
    wf = res.get("walk_forward")
    if wf and wf["windows"]:
        out.append("  walk-forward (train years 1..k, test year k+1; fresh capital each test year)")
        for w in wf["windows"]:
            t, tt = w["ic_train"], w["ic_test"]
            lv = "  ".join("Rs %s %s/%s" % (inr(float(k), 0, ""), _pct(v["cagr"]), _f(v["sharpe"]))
                           for k, v in w["levels"].items())
            out.append("    test %s  IC train %s (t %s)  IC test %s (t %s)  universe %s/%s  %s" % (
                w["test"][0][:4], _f(t and t["ic_mean"], "%.3f"), _f(t and t["ic_t"]), _f(tt and tt["ic_mean"], "%.3f"),
                _f(tt and tt["ic_t"]), _pct(w["bench"]["cagr"]), _f(w["bench"]["sharpe"]), lv))
        po = wf["pooled"]
        pic = po["ic"] or {}
        out.append("    pooled    IC %s (t %s, %s months)  universe %s/%s  %s" % (
            _f(pic.get("ic_mean"), "%.3f"), _f(pic.get("ic_t")), pic.get("months", "n/a"), _pct(po["bench"]["cagr"]),
            _f(po["bench"]["sharpe"]), "  ".join("Rs %s %s/%s, beat the universe in %d of %d years" % (
                inr(float(k), 0, ""), _pct(v["cagr"]), _f(v["sharpe"]), v["windows_beating_universe"], len(wf["windows"]))
                for k, v in po["levels"].items())))
    out.append("  attempts  %d in batch %r, %d harness runs logged in all%s" % (
        batch_n, batch, total_n, "" if dsr is None or np.isnan(dsr) else
        "; deflated Sharpe probability %.2f over every logged %s run" % (dsr, res["period"])))
    return "\n".join(out)


def main(argv=None, runs_log=backtest.RUNS_LOG):
    ap = argparse.ArgumentParser(description="Test one idea under the retail rules (dev and validation only).")
    ap.add_argument("--period", required=True, choices=sorted(PERIODS))
    ap.add_argument("--signal", required=True, choices=sorted(signals_mod.SIGNALS))
    ap.add_argument("--hold", default="monthly", help="'monthly' or a number of sessions")
    ap.add_argument("--positions", type=int, default=20)
    ap.add_argument("--idea", help="the idea's name in the log (default: signal, positions and hold)")
    ap.add_argument("--batch", default="unbatched", help="the pre-registered batch this attempt counts against")
    ap.add_argument("--no-dividends", action="store_true", help="leave cash dividends out")
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--note", default="")
    ap.add_argument("--out", help="a folder to write the full result to, as ID.json (the app's task does)")
    a = ap.parse_args(argv)
    hold = a.hold if a.hold == "monthly" else int(a.hold) if a.hold.isdigit() else None
    if hold is None:
        ap.error("--hold is 'monthly' or a whole number of sessions")
    idea = Idea(a.idea or "%s_top%d_%s" % (a.signal, a.positions, hold), a.signal, hold, a.positions)
    rules = check_rules(retail.load())
    if a.period == "validation":
        if not a.note.strip():
            print("error: a validation look needs --note saying why (one look per finished idea)", file=sys.stderr)
            return 2
        if looked(idea, "validation", runs_log):
            print("error: %s already had its validation look; a changed idea needs a new name and dev first" % idea.name,
                  file=sys.stderr)
            return 2
    start, end = PERIODS[a.period]
    load_from = (pd.Timestamp(start) - pd.DateOffset(years=2)).date().isoformat()
    p = panel_mod.load(a.db, start=load_from, end=end)  # never past the period's end
    dividends = None if a.no_dividends else panel_mod.dividends(p, a.db).values
    res = run(p, idea, a.period, rules, dividends=dividends)
    entry = log_entry(res, a.batch, a.note or "harness.py", rules)
    rid = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    entry["result_id"] = rid
    backtest.log_run(entry, runs_log)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, rid + ".json"), "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
    n, total = attempts(a.batch, runs_log)
    top = res["levels"][str(int(float(retail.capital_levels(rules)[-1])))]
    trials = backtest.past_sharpes(runs_log, a.period)  # every logged run on this period, this one included
    dsr = backtest.deflated_sharpe(top["sharpe"], top["days"], top["skew"], top["kurtosis"], trials) \
        if top["sharpe"] is not None and top["days"] else float("nan")
    print(report(res, n, total, a.batch, dsr))
    print("logged to research/runs.jsonl as %s; record the results in research/FINDINGS.md, whatever they show" % rid)
    print("RESULT_ID=" + rid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
