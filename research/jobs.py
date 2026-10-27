#!/usr/bin/env python3
"""The tasks the local app can run, as one command line. The app starts
these as subprocesses and never anything else.

  python3 research/jobs.py update
  python3 research/jobs.py scan --strategy momentum_quality_top20
  python3 research/jobs.py backtest --signal momentum_quality --period dev
  python3 research/jobs.py backtest --composite momentum_quality=1,low_volatility=1 --top 20
  python3 research/jobs.py save-strategy RESULT_ID --name my_blend

A backtest writes its full result (equity curve, benchmark, drawdown,
yearly returns, information coefficient, trial count, deflated Sharpe) to
data/app/backtests/ID.json and is logged in research/runs.jsonl like every
other run. The holdout period is not available here: it is opened once per
finished strategy, by hand, with research/backtest.py --open-holdout.
"""

import argparse
import datetime as dt
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
RESULTS = os.path.join(ROOT, "data", "app", "backtests")


def parse_composite(text):
    out = {}
    for part in text.split(","):
        name, _, w = part.partition("=")
        out[name.strip()] = float(w or 1)
    return out


def run_backtest(spec, top, regime, slippage_bps, period, note="", db_path=None, out_dir=RESULTS):
    import pandas as pd

    import backtest
    import evaluate
    import panel as panel_mod
    import regimes as regimes_mod
    import signals as signals_mod

    if period not in ("dev", "validation"):
        raise ValueError("the app tests on dev or validation only; the holdout is opened by hand")
    db_path = db_path or panel_mod.bhavcopy.DEFAULT_DB
    start, end = backtest.PERIODS[period]
    p = panel_mod.load(db_path, start=(pd.Timestamp(start) - pd.DateOffset(years=2)).date().isoformat(), end=end)
    scores = signals_mod.scores(p, spec)
    cfg = backtest.Config(top=top, slippage_bps=slippage_bps, regime=regime)
    res = backtest.run(p, scores, start, end, cfg, exposure=regimes_mod.REGIMES[regime](p, db_path))
    m, b = backtest.metrics(res["nav"]), backtest.metrics(res["bench"])
    elig = panel_mod.universe(p)
    lo, hi = p.dates.searchsorted(pd.Timestamp(start)), p.dates.searchsorted(pd.Timestamp(end), "right")
    ic = evaluate.ic_report(scores, evaluate.forward_returns(p), elig, panel_mod.month_ends(p.dates[lo:hi])[:-1])
    trials = backtest.past_sharpes(period=period) + [m["sharpe"]]
    dsr = backtest.deflated_sharpe(m["sharpe"], m["days"], m["skew"], m["kurtosis"], trials)
    rid = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    years = m["days"] / 252
    s = res["stats"]
    result = {
        "id": rid, "at": dt.datetime.now().isoformat(timespec="seconds"), "period": period, "start": start,
        "end": end, "spec": spec, "label": signals_mod.describe(spec), "config": cfg.as_dict(), "note": note,
        "metrics": {k: v for k, v in m.items() if k != "yearly"}, "bench": {k: v for k, v in b.items() if k != "yearly"},
        "yearly": m["yearly"], "bench_yearly": b["yearly"], "ic": ic, "trials": len(trials),
        "deflated_sharpe": None if dsr != dsr else dsr,
        "costs": {"turnover_per_year": s["turnover"] / years, "costs_pct_per_year": s["costs"] / years * 100,
                  "blocked_buys": s["blocked_buys"], "blocked_sells": s["blocked_sells"],
                  "delisting_exits": s["delist_exits"]},
        "nav": [[d.date().isoformat(), round(float(v), 5)] for d, v in res["nav"].items()],
        "bench_nav": [[d.date().isoformat(), round(float(v), 5)] for d, v in res["bench"].items()],
    }
    dd = res["nav"] / res["nav"].cummax() - 1
    result["drawdown"] = [[d.date().isoformat(), round(float(v), 5)] for d, v in dd.items()]
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, rid + ".json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    backtest.log_run({"at": result["at"], "signal": result["label"], "spec": spec, "period": period,
                      "config": cfg.as_dict(), "sharpe": m["sharpe"], "cagr": m["cagr"],
                      "max_drawdown": m["max_drawdown"], "bench_sharpe": b["sharpe"], "ic": ic,
                      "note": note or "strategy lab", "result_id": rid})
    return result


def save_strategy(result_id, name, out_dir=RESULTS, strategies=None):
    """Add a tested configuration to research/strategies.json as a candidate.
    Its status says which periods it was tested on; only a candidate that
    passed validation should ever be followed."""
    import scan
    strategies = strategies or scan.STRATEGIES
    if not name.replace("_", "").isalnum():
        raise ValueError("strategy names are letters, digits and underscores")
    with open(os.path.join(out_dir, result_id + ".json"), encoding="utf-8") as fh:
        r = json.load(fh)
    with open(strategies, encoding="utf-8") as fh:
        all_ = json.load(fh)
    if name in all_:
        raise ValueError("a strategy called %r already exists" % name)
    m, b = r["metrics"], r["bench"]
    entry = dict(r["spec"])
    entry.update({
        "status": "candidate: tested on %s only, not validated; do not follow" % r["period"],
        "description": "Top %d by %s, equal weight, monthly." % (r["config"]["top"], r["label"]),
        "top": r["config"]["top"], "rebalance": "last NSE trading day of each month, orders at the next open",
        "regime": r["config"]["regime"],
        "universe": {"min_price": 20.0, "min_value": 20000000.0, "value_days": 60, "min_history": 250},
        "slippage_bps": r["config"]["slippage"] * 1e4, "trim_band": 0.02,
        "evidence": {"%s %s to %s" % (r["period"], r["start"], r["end"]):
                     "CAGR %.1f%% against %.1f%%; Sharpe %.2f against %.2f; worst drawdown %.1f%%; IC %.3f; "
                     "%d %s runs tried (result %s)" % (m["cagr"] * 100, b["cagr"] * 100, m["sharpe"], b["sharpe"],
                                                         m["max_drawdown"] * 100, (r["ic"] or {}).get("ic_mean", 0),
                                                         r["trials"], r["period"], result_id)},
    })
    all_[name] = entry
    with open(strategies, "w", encoding="utf-8") as fh:
        json.dump(all_, fh, indent=2)
        fh.write("\n")
    return entry


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tasks the local app runs.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update")
    s = sub.add_parser("scan")
    s.add_argument("--strategy", default="momentum_quality_top20")
    s.add_argument("--capital")
    b = sub.add_parser("backtest")
    g = b.add_mutually_exclusive_group(required=True)
    g.add_argument("--signal")
    g.add_argument("--composite", help="name=weight,name=weight")
    b.add_argument("--top", type=int, default=20)
    b.add_argument("--regime", default="none")
    b.add_argument("--slippage-bps", type=float, default=15.0)
    b.add_argument("--period", default="dev", choices=["dev", "validation"])
    b.add_argument("--note", default="")
    v = sub.add_parser("save-strategy")
    v.add_argument("result_id")
    v.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "update":
        import scan
        scan.update()
    elif a.cmd == "scan":
        import scan
        res = scan.run(a.strategy, a.capital)
        print("scan %s: %d candidates, %d orders" % (res["date"], len(res["cards"]), len(res["orders"])))
    elif a.cmd == "backtest":
        spec = {"composite": parse_composite(a.composite)} if a.composite else {"signal": a.signal}
        r = run_backtest(spec, a.top, a.regime, a.slippage_bps, a.period, a.note)
        m = r["metrics"]
        print("result %s: %s on %s, CAGR %.1f%%, Sharpe %.2f, max drawdown %.1f%%, %d runs tried" % (
            r["id"], r["label"], r["period"], m["cagr"] * 100, m["sharpe"], m["max_drawdown"] * 100, r["trials"]))
        print("RESULT_ID=" + r["id"])
    elif a.cmd == "save-strategy":
        save_strategy(a.result_id, a.name)
        print("saved %s as a candidate" % a.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
