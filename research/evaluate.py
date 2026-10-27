#!/usr/bin/env python3
"""Evaluate candidate signals two ways on one period, loading the data once.

1. Information coefficient: at each month end, the rank correlation
   between the signal and every eligible stock's next-month return (from
   the next day's open). Reported as the mean, a t-statistic, the share of
   months it was positive, and mean returns by decile (is the top tenth
   better than the bottom tenth, in order?). This judges the whole ranking,
   not just 20 picks, so it is far less noisy than a backtest.
2. The tradeable backtest from backtest.py: top N, real costs and frictions.

A signal is only a candidate when both agree. Every run is logged as a
trial so the deflated Sharpe knows how many were tried.

  python3 research/evaluate.py --period dev
  python3 research/evaluate.py --period dev --signals momentum_12_1 high_52w --top 30
"""

import argparse
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest  # noqa: E402
import panel as panel_mod  # noqa: E402
import regimes as regimes_mod  # noqa: E402
import signals as signals_mod  # noqa: E402

HORIZON = 21  # trading days, about a month


def forward_returns(p, horizon=HORIZON):
    """Return from the next day's open to the open `horizon` days later, per date."""
    o = p.open
    return o.shift(-(1 + horizon)) / o.shift(-1) - 1


def ic_report(scores, fwd, elig, dates):
    """Per-date Spearman IC and decile returns on the eligible universe."""
    ics, deciles = [], []
    for d in dates:
        if d not in scores.index:
            continue
        ok = elig.loc[d]
        s, r = scores.loc[d].where(ok), fwd.loc[d].where(ok)
        both = pd.concat([s, r], axis=1).dropna()
        if len(both) < 50:
            continue
        ics.append(both.iloc[:, 0].rank().corr(both.iloc[:, 1].rank()))
        q = pd.qcut(both.iloc[:, 0].rank(method="first"), 10, labels=False)
        deciles.append(both.iloc[:, 1].groupby(q).mean().values - both.iloc[:, 1].mean())
    if not ics:
        return None
    ic = np.array(ics)
    dec = np.nanmean(np.array(deciles), axis=0)
    return {"months": len(ic), "ic_mean": float(ic.mean()), "ic_t": float(ic.mean() / ic.std(ddof=1) * math.sqrt(len(ic))),
            "ic_positive": float((ic > 0).mean()), "decile_excess": [float(x) for x in dec],
            "top_minus_bottom": float(dec[-1] - dec[0]),
            "monotonic": float(pd.Series(dec).rank().corr(pd.Series(range(10), dtype=float)))}  # Spearman, no scipy


def main(argv=None):
    ap = argparse.ArgumentParser(description="IC and backtest for candidate signals.")
    ap.add_argument("--period", default="dev", choices=["dev", "dev_2016", "validation"])
    ap.add_argument("--signals", nargs="*", default=sorted(signals_mod.SIGNALS))
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--regime", nargs="*", default=["none"], choices=sorted(regimes_mod.REGIMES),
                    help="one or more; each signal is backtested under each")
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    start, end = backtest.PERIODS[a.period]
    load_from = (pd.Timestamp(start) - pd.DateOffset(years=2)).date().isoformat()
    p = panel_mod.load(a.db, start=load_from, end=end)
    elig = panel_mod.universe(p)
    fwd = forward_returns(p)
    lo, hi = p.dates.searchsorted(pd.Timestamp(start)), p.dates.searchsorted(pd.Timestamp(end), "right")
    rebal = panel_mod.month_ends(p.dates[lo:hi])[:-1]  # the last month has no full forward return
    exposures = {r: regimes_mod.REGIMES[r](p, a.db) for r in a.regime}
    print("%s %s to %s: %d month ends, median universe %d stocks" % (
        a.period, start, end, len(rebal), int(elig.loc[rebal].sum(axis=1).median())))
    print("%-22s %-15s %7s %6s %6s %9s %6s | %7s %6s %7s %7s" % (
        "signal", "regime", "IC", "t", "IC>0", "top-bot", "mono", "CAGR", "Sharpe", "maxDD", "excess"))
    bench = None
    for name in a.signals:
        scores = signals_mod.SIGNALS[name](p)
        ic = ic_report(scores, fwd, elig, rebal)
        for regime in a.regime:
            cfg = backtest.Config(top=a.top, regime=regime)
            res = backtest.run(p, scores, start, end, cfg, exposure=exposures[regime])
            m = backtest.metrics(res["nav"])
            bench = bench or backtest.metrics(res["bench"])
            backtest.log_run({"at": dt.datetime.now().isoformat(timespec="seconds"), "signal": name,
                              "period": a.period, "config": cfg.as_dict(), "sharpe": m.get("sharpe"),
                              "cagr": m.get("cagr"), "max_drawdown": m.get("max_drawdown"),
                              "bench_sharpe": bench.get("sharpe"), "ic": ic, "note": a.note or "evaluate.py"})
            print("%-22s %-15s %7.3f %6.2f %5.0f%% %8.2f%% %6.2f | %6.1f%% %6.2f %6.1f%% %6.1f%%" % (
                name, regime, ic["ic_mean"], ic["ic_t"], ic["ic_positive"] * 100, ic["top_minus_bottom"] * 100,
                ic["monotonic"], m["cagr"] * 100, m["sharpe"], m["max_drawdown"] * 100,
                (m["cagr"] - bench["cagr"]) * 100))
    print("%-22s %-15s %7s %6s %6s %9s %6s | %6.1f%% %6.2f %6.1f%%" % (
        "equal-weight universe", "", "", "", "", "", "", bench["cagr"] * 100, bench["sharpe"],
        bench["max_drawdown"] * 100))
    print("IC: monthly rank correlation with next-month return; t > 2 is the usual bar. top-bot: monthly return of the")
    print("top decile minus the bottom. mono: how orderly the deciles are (1 = perfectly). Right side: top %d backtest" % a.top)
    print("with costs; excess is CAGR over the equal-weight universe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
