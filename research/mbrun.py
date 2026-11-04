#!/usr/bin/env python3
"""Phase 4 runner: the registered candidates through the registered folds (FINDINGS.md, 2026-11-04, "Phase 3").

  python3 research/mbrun.py --stage dev                       # M1 to M4, dev walk-forward (2017, 2018, 2019 starts)
  python3 research/mbrun.py --stage validation --models M2    # one look, only for models that passed dev

Dev reads prices to 2022-12-30 and no later. Validation reads to 2024-12-31 and no later; it refuses a model that
has not passed dev, and a model that has already had its validation look. The holdout is not reachable. Every
result is logged in runs.jsonl (kind "mb_model", batch "mb-phase4").
"""

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import backtest  # noqa: E402
import features as feat  # noqa: E402
import labels as labels_mod  # noqa: E402
import models  # noqa: E402
import panel as panel_mod  # noqa: E402

BATCH = "mb-phase4"
DEV_END = labels_mod.DEV_PRICES_END  # 2022-12-30
VALID_END = "2024-12-31"
OUT_DIR = os.path.join(ROOT, "data", "mb")


def logged(path=backtest.RUNS_LOG):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("kind") == "mb_model" and e.get("batch") == BATCH:
                    out.append(e)
    return out


def gate(requested, history):
    """Validation is allowed only for models that passed dev and have not had their look. Returns an error or None."""
    for m in requested:
        if m == "M1":
            continue
        if not any(e["stage"] == "dev" and e["model"] == m and e.get("dev_pass") for e in history):
            return "%s has not passed the dev walk-forward: no validation look" % m
        if any(e["stage"] == "validation" and e["model"] == m for e in history):
            return "%s has already had its one validation look" % m
    return None


def pct(x):
    return "n/a" if x is None or not np.isfinite(x) else "%.1f%%" % (x * 100)


def fmt(x):
    return "n/a" if x is None or not np.isfinite(x) else "%.2f" % x


def describe(r):
    p = r["pooled"]
    return "lift %s (top %s of %d, universe %s of %d), halved %s vs %s, delisted %s vs %s, median %s vs %s" % (
        fmt(p["lift"]), pct(p["top"]["hit"]), p["top"]["n"], pct(p["universe"]["hit"]), p["universe"]["n"],
        pct(p["top"]["halved"]), pct(p["universe"]["halved"]), pct(p["top"]["delisted"]),
        pct(p["universe"]["delisted"]), fmt(p["top"]["median_multiple"]), fmt(p["universe"]["median_multiple"]))


def run_dev(df, requested, log):
    folds = models.dev_folds(df)
    print("dev walk-forward: %s" % ", ".join("%d (train to %s, %d rows; test %d rows)" % (
        y, df.loc[tr, "start"].max().date(), tr.sum(), te.sum()) for y, tr, te in folds))
    results = {}
    for m in requested:
        by_year, tops = {}, []
        for y, tr, te in folds:
            test = df[te]
            score = models.fit_predict(m, df[tr], test)
            r = models.evaluate(test, score)
            by_year[y] = r["pooled"]
            tops.append(r["top"])
        ok, years = models.dev_pass(by_year)
        pooled_top = pd.concat(tops)
        pooled = models.lift_of(pooled_top, df[df["start"].dt.year.isin(by_year)])
        results[m] = {"by_year": by_year, "pass": ok, "years_above_one": years, "pooled": pooled}
        print("\n%s  dev %s   lifts by test year: %s" % (m, "PASS" if ok else "fail", "  ".join(
            "%d: %s" % (y, fmt(r["lift"])) for y, r in by_year.items())))
        print("   pooled " + describe({"pooled": pooled}))
        if log:
            backtest.log_run({"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": "mb_model", "batch": BATCH,
                              "stage": "dev", "model": m, "dev_pass": bool(ok),
                              "lift_by_year": {str(y): r["lift"] for y, r in by_year.items()},
                              "pooled": {"lift": pooled["lift"], "top": pooled["top"], "universe": pooled["universe"]},
                              "config": {"floor": feat.FLOOR, "top_share": models.TOP_SHARE,
                                         "features": list(feat.FEATURES), "m3": models.LOGISTIC, "m4": models.BOOSTING},
                              "note": "Phase 4 dev walk-forward, settings fixed in FINDINGS.md 2026-11-04 (Phase 3)"})
    passed = [m for m in requested if m != "M1" and results[m]["pass"]]
    print("\npassed dev: %s" % (", ".join(passed) or "none"))
    return results


def run_validation(df, requested, log):
    tr, te = models.validation_fold(df)
    train, test = df[tr], df[te]
    print("validation: train %s to %s (%d rows), test %s to %s (%d rows, %d starts)" % (
        train["start"].min().date(), train["start"].max().date(), len(train), test["start"].min().date(),
        test["start"].max().date(), len(test), test["start"].nunique()))
    res = {m: models.evaluate(test, models.fit_predict(m, train, test)) for m in sorted(set(requested) | {"M1"})}
    out = {}
    for m, r in res.items():
        print("\n%s" % m)
        print("   pooled " + describe(r))
        print("   by year: " + "  ".join("%d: %s" % (y, fmt(x["lift"])) for y, x in r["by_year"].items()))
        print("   by series: " + "  ".join("%s: %s (n=%d)" % (k, fmt(x["lift"]), x["top"]["n"]) for k, x in r["by_group"].items()))
        lo, hi = models.bootstrap_lift(test, r["top"])
        print("   90%% interval for the pooled lift, resampling stocks: %s to %s" % (fmt(lo), fmt(hi)))
        if m != "M1":
            print("   overlap of its top picks with M1's: %s" % pct(models.overlap(r["top"], res["M1"]["top"])))
        if m in ("M3", "M4"):
            prob = models.fit_predict(m, train, test)
            cal, rho = models.calibration(test, prob)
            print("   calibration, realised 3x rate by decile of predicted probability (rank correlation %.2f):" % rho)
            print("   " + "  ".join("%s/%s" % (pct(a), pct(b)) for a, b in zip(cal["predicted"], cal["realised"])))
        verdict, checks = (None, None)
        if m != "M1":
            verdict, checks = models.validation_pass(r, res["M1"], True)
            print("   bar: %s" % ("PASS" if verdict else "fail"))
            for k, v in checks.items():
                print("      [%s] %s" % ("x" if v else " ", k))
        out[m] = {"pass": verdict, "checks": checks, "lift": r["pooled"]["lift"], "interval": (lo, hi)}
        if log:
            backtest.log_run({"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": "mb_model", "batch": BATCH,
                              "stage": "validation", "model": m, "validation_pass": verdict, "checks": checks,
                              "lift_by_year": {str(y): x["lift"] for y, x in r["by_year"].items()},
                              "pooled": {"lift": r["pooled"]["lift"], "top": r["pooled"]["top"],
                                         "universe": r["pooled"]["universe"]},
                              "interval90": [lo, hi], "note": "Phase 4 validation look, one per model, FINDINGS.md 2026-11-04"})
    passed = {m for m, v in out.items() if v["pass"]}
    print("\npassed validation: %s; kept (the simplest that passes): %s" % (
        ", ".join(sorted(passed)) or "none", models.simplest(passed) or "none"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Phase 4: the registered candidates through the registered folds.")
    ap.add_argument("--stage", required=True, choices=["dev", "validation"])
    ap.add_argument("--models", nargs="*", default=list(models.MODELS), choices=list(models.MODELS))
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--no-log", action="store_true", help="dev only: print, log nothing")
    a = ap.parse_args(argv)
    log = not a.no_log
    if a.stage == "validation":
        if not log:
            raise SystemExit("a validation look is always logged")
        err = gate(a.models, logged())
        if err:
            raise SystemExit("refusing: " + err)
        end, first, last = VALID_END, models.VALID_TRAIN[0], models.VALID_TEST[1]
    else:
        end, first, last = DEV_END, "2013-01-01", "2019-12-31"
    p = panel_mod.load(a.db, start=labels_mod.LOAD_FROM, end=end, series=panel_mod.EQUITY_SERIES)
    if p.dates[-1] > pd.Timestamp(end):
        raise SystemExit("refusing to run: the panel holds data after %s" % end)
    df = feat.build(p, first, last)
    print("%s table: %d rows, %d starts, median universe %d stocks; features read to %s" % (
        a.stage, len(df), df["start"].nunique(), int(df.groupby("start").size().median()), p.dates[-1].date()))
    os.makedirs(OUT_DIR, exist_ok=True)
    if a.stage == "dev":
        run_dev(df, a.models, log)
    else:
        run_validation(df, a.models, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
