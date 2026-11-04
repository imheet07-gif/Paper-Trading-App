#!/usr/bin/env python3
"""Multibagger labels and base rates (FINDINGS.md, 2026-11-04, Phase 1). Dev only.

For every company share and month-end starting point t, the multiple of its
adjusted close 36 months later over its adjusted close at t, and whether it
reached 3x. Descriptive: it ranks nothing, adopts nothing and is not a
variant in the attempt count. Every definition below was fixed in
FINDINGS.md before the first run.

  * population at t: ISIN starting INE with security code 01 (characters 8
    and 9: equity shares; rights entitlements, warrants and fund units drop
    out), a close in the 20 sessions up to t, last printed in EQ, BE, BZ, SM,
    ST or SZ. No liquidity or price floor.
  * listed apart: a failed corporate action check at any session from t to
    the window's end.
  * window end: the month-end 36 months after t. A stock with no close in
    the 20 sessions up to it is delisted: valued at its last close less 50%.
  * touched: the adjusted close at some month-end in the window reached 3x.
  * buckets at t: series group, unadjusted close, traded value (the 60
    sessions' total over 60, no-trade days as zero), listing age, start year.

  python3 research/labels.py                  # dev: prints, writes data/labels/dev.csv.gz, logs once
  python3 research/labels.py --no-log         # print only, nothing logged
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
import panel as panel_mod  # noqa: E402

# Splits by starting point (CLAUDE.md, "The multibagger protocol"). Only dev is open here.
SPLITS = {"dev": ("2013-01-01", "2019-12-31")}
DEV_PRICES_END = "2022-12-31"  # the last dev window ends here; nothing later is read
LOAD_FROM = "2012-01-01"
HORIZON = 36  # months
MULTIPLE = 3.0
CARRY = 20  # sessions a close is carried (the backtest's delist_after)
HAIRCUT = 0.5  # backtest.Config().delist_haircut, checked in tests
VALUE_DAYS = 60
STORE_START = pd.Timestamp("2012-01-31")  # a first print before this means "listed before the store began"

SERIES_GROUP = {"EQ": "main board EQ", "BE": "main board BE/BZ", "BZ": "main board BE/BZ",
                "SM": "SME", "ST": "SME", "SZ": "SME"}
PRICE_EDGES = [(20, "under Rs 20"), (100, "Rs 20 to 100"), (500, "Rs 100 to 500"), (np.inf, "Rs 500 and over")]
VALUE_EDGES = [(1e6, "under Rs 10 lakh"), (5e6, "Rs 10 to 50 lakh"), (2e7, "Rs 50 lakh to 2 crore"),
               (1e8, "Rs 2 to 10 crore"), (np.inf, "Rs 10 crore and over")]
OUT_DIR = os.path.join(ROOT, "data", "labels")


def is_equity_share(isin):
    s = str(isin)
    return s.startswith("INE") and len(s) == 12 and s[7:9] == "01"


def bucket(x, edges):
    if x is None or not np.isfinite(x):
        return "unknown"
    for edge, name in edges:
        if x < edge:
            return name
    return edges[-1][1]


def label_rows(p, first, last, horizon=HORIZON, multiple=MULTIPLE, carry=CARRY, haircut=HAIRCUT):
    """One row per (start, security) in the population. Columns: start, sid, symbol,
    series, series_group, raw_close, traded_value, listing, price_bucket, value_bucket,
    start_year, multiple, delisted, touched, issue (failed corporate action check in the window)."""
    dates = p.dates
    me = panel_mod.month_ends(dates)
    starts = me[(me >= pd.Timestamp(first)) & (me <= pd.Timestamp(last))]
    if not len(starts):
        return pd.DataFrame()
    me_pos = {d: k for k, d in enumerate(me)}
    k_last = me_pos[starts[-1]] + horizon
    if k_last >= len(me):
        raise SystemExit("the panel ends %s: the window from %s needs prices to a month-end %d months later"
                         % (dates[-1].date(), starts[-1].date(), horizon))
    sess = dates.get_indexer(me)

    ids = list(p.close.columns)
    equity = np.array([is_equity_share(c) for c in ids])
    code = getattr(p, "series_code", None)
    if code is None:  # older panels: EQ where p.eq, else BE
        code = pd.DataFrame(np.where(p.eq.values, 0.0, 1.0), index=dates, columns=p.close.columns).where(
            p.close.notna())
    code_last = code.ffill(limit=carry).values
    C = p.close.values
    Cc = p.close.ffill(limit=carry).values  # carried at most `carry` sessions
    Cl = p.close.ffill().values  # last close ever seen
    raw = p.raw_close.ffill(limit=carry).values
    tv = (p.value.fillna(0.0).rolling(VALUE_DAYS, min_periods=1).sum() / VALUE_DAYS).values
    traded = p.close.notna()
    first_print = traded.idxmax().where(traded.any())  # first session with a close, per security
    # the session a failed check starts (panel.data_issue marks it and the year after it)
    issue = p.data_issue.values
    fail = issue & ~np.vstack([np.zeros((1, len(ids)), dtype=bool), issue[:-1]])
    fail_cum = np.vstack([np.zeros((1, len(ids)), dtype=np.int64), np.cumsum(fail, axis=0)])
    names_codes = {v: k for k, v in panel_mod.SERIES_CODE.items()}

    out = []
    for t in starts:
        k = me_pos[t]
        i0, i1 = sess[k], sess[k + horizon]
        c0 = Cc[i0]
        live = equity & np.isfinite(c0) & np.isfinite(code_last[i0])
        end = Cc[i1]
        dead = ~np.isfinite(end) & np.isfinite(Cl[i1])
        end_val = np.where(dead, Cl[i1] * (1 - haircut), end)
        path = Cc[sess[k + 1:k + horizon + 1]]
        with np.errstate(invalid="ignore", divide="ignore"):
            mult = end_val / c0
            touched = np.nanmax(np.where(np.isfinite(path), path, -np.inf), axis=0) >= multiple * c0
        bad = (fail_cum[i1 + 1] - fail_cum[i0]) > 0  # a failure at any session from t to the window's end
        for j in np.flatnonzero(live):
            sid = ids[j]
            fp = first_print[sid]
            young = bool(pd.notna(fp) and fp > STORE_START and (t - fp).days <= 365)
            ser = names_codes[int(code_last[i0, j])]
            out.append({
                "start": t.date().isoformat(), "sid": sid, "symbol": p.names.get(sid, sid), "series": ser,
                "series_group": SERIES_GROUP[ser], "raw_close": float(raw[i0, j]), "traded_value": float(tv[i0, j]),
                "listing": "listed under a year" if young else "listed a year or more",
                "price_bucket": bucket(raw[i0, j], PRICE_EDGES), "value_bucket": bucket(tv[i0, j], VALUE_EDGES),
                "start_year": t.year, "multiple": float(mult[j]), "delisted": bool(dead[j]),
                "touched": bool(touched[j]), "issue": bool(bad[j]),
            })
    return pd.DataFrame(out)


def summarise(rows, multiple=MULTIPLE):
    """Base rates over the counted rows (no failed check in the window)."""
    r = rows[~rows["issue"]]

    def block(g):
        m = g["multiple"]
        return {"stock_months": int(len(g)), "stocks": int(g["sid"].nunique()),
                "hit": float((m >= multiple).mean()), "hit5": float((m >= 5).mean()),
                "hit10": float((m >= 10).mean()), "halved": float((m <= 0.5).mean()),
                "median_multiple": float(m.median()), "delisted": float(g["delisted"].mean()),
                "touched": float(g["touched"].mean())}

    out = {"all": block(r), "listed_apart": {"stock_months": int(rows["issue"].sum()),
                                             "stocks": int(rows.loc[rows["issue"], "sid"].nunique())}}
    hits = r[r["multiple"] >= multiple]
    out["distinct_multibaggers"] = int(hits["sid"].nunique())
    for col in ("start_year", "series_group", "price_bucket", "value_bucket", "listing"):
        out[col] = {str(k): block(g) for k, g in r.groupby(col)}
    return out


def report(s):
    lines = []
    a = s["all"]
    lines.append("counted: %d stock-months, %d stocks; %d distinct stocks tripled at least once; "
                 "listed apart (failed corporate action check): %d stock-months, %d stocks"
                 % (a["stock_months"], a["stocks"], s["distinct_multibaggers"],
                    s["listed_apart"]["stock_months"], s["listed_apart"]["stocks"]))
    head = "%-26s %9s %7s %7s %6s %6s %7s %7s %8s %8s" % (
        "", "stock-mo", "stocks", "3x", "5x", "10x", "halved", "median", "delisted", "touched")
    fmt = "%-26s %9d %7d %6.1f%% %5.1f%% %5.1f%% %6.1f%% %7.2f %7.1f%% %7.1f%%"

    def row(name, b):
        return fmt % (name[:26], b["stock_months"], b["stocks"], b["hit"] * 100, b["hit5"] * 100, b["hit10"] * 100,
                      b["halved"] * 100, b["median_multiple"], b["delisted"] * 100, b["touched"] * 100)

    lines += ["", head, row("all", a)]
    for col, title in (("start_year", "by start year"), ("series_group", "by series"),
                       ("price_bucket", "by price at start"), ("value_bucket", "by traded value at start"),
                       ("listing", "by listing age")):
        lines += ["", title]
        keys = sorted(s[col])
        if col == "price_bucket":
            keys = [n for _, n in PRICE_EDGES if n in s[col]] + [k for k in keys if k == "unknown"]
        if col == "value_bucket":
            keys = [n for _, n in VALUE_EDGES if n in s[col]] + [k for k in keys if k == "unknown"]
        lines += [row(k, s[col][k]) for k in keys]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Multibagger labels and base rates, dev starting points only.")
    ap.add_argument("--split", default="dev", choices=sorted(SPLITS))
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--note", default="Phase 1 labels and base rates, settings fixed in FINDINGS.md 2026-11-04")
    a = ap.parse_args(argv)
    first, last = SPLITS[a.split]
    p = panel_mod.load(a.db, start=LOAD_FROM, end=DEV_PRICES_END, series=panel_mod.EQUITY_SERIES)
    if p.dates[-1] > pd.Timestamp(DEV_PRICES_END):
        raise SystemExit("refusing to run: the panel holds data after %s" % DEV_PRICES_END)
    rows = label_rows(p, first, last)
    s = summarise(rows)
    print("labels %s: starts %s to %s, %d-month windows, %gx; prices read to %s" % (
        a.split, first, last, HORIZON, MULTIPLE, p.dates[-1].date()))
    print(report(s))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.csv.gz" % a.split)
    rows.to_csv(path, index=False, compression="gzip")
    print("\nrows written to %s" % os.path.relpath(path, ROOT))
    if not a.no_log:
        backtest.log_run({"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": "audit",
                          "signal": "labels", "period": a.split,
                          "config": {"engine": "labels.py", "horizon_months": HORIZON, "multiple": MULTIPLE,
                                     "starts": [first, last], "prices_to": DEV_PRICES_END,
                                     "series": list(panel_mod.EQUITY_SERIES)},
                          "summary": {k: s[k] for k in ("all", "listed_apart", "distinct_multibaggers")},
                          "note": a.note})
    return 0


if __name__ == "__main__":
    sys.exit(main())
