#!/usr/bin/env python3
"""Phase 2 data checks (FINDINGS.md, 2026-11-04). Descriptive: adopts nothing, ranks nothing.

  1. Coverage at each year-end snapshot (2013 to the latest session): of the
     company shares (ISIN INE, code 01) with a close in the 20 sessions to the
     snapshot, by series group (main board EQ, main board BE/BZ, SME) and by
     traded value, how many had by then
       * a published results filing whose quarter ended within 400 days,
       * a share count (paid-up capital over face value) in that filing,
       * a shareholding detail row (total shares) for a quarter ended within 400 days.
     Only filings broadcast before the snapshot day count. No returns are read.
  2. SME history: stocks trading per year and how many of them have any filing.
  3. The largest multiples of Phase 1, checked for a single-session adjusted move
     beyond +50% or -33% inside the window (NSE price bands are at most 20% for
     almost every stock, so such a move in an adjusted series points to a missed
     consolidation or split). Dev only; the base rate is re-stated without those
     rows as a sensitivity, never as a replacement for the fixed definition.

  python3 research/coverage.py                # prints, logs once as kind "audit"
  python3 research/coverage.py --no-log
"""

import argparse
import datetime as dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "core"))
import backtest  # noqa: E402
import corporate_actions as ca  # noqa: E402
import labels as labels_mod  # noqa: E402
import panel as panel_mod  # noqa: E402

FRESH_DAYS = 400  # a quarter ended this long ago still counts as current (a year plus filing delay)
UP_JUMP, DOWN_JUMP = 1.5, 1.0 / 3.0  # adjusted one-session close ratio beyond which a move is suspect
VALUE_DAYS = 60
RECENT = 20
LABELS = os.path.join(ROOT, "data", "labels", "dev.csv.gz")


def year_end_sessions(db, first_year=2013):
    """The last stored session of each year from first_year, and the latest session."""
    rows = db.execute("SELECT MAX(date) FROM prices WHERE date >= ? GROUP BY substr(date,1,4) ORDER BY 1",
                      ("%d-01-01" % first_year,)).fetchall()
    return [r[0] for r in rows]


def population(db, date, link_map):
    """One row per security (latest ISIN) trading in the 20 sessions to `date`: series group and the mean
    traded value of the 60 sessions to `date` (days without a trade count as zero), in rupees."""
    sessions = [r[0] for r in db.execute("SELECT DISTINCT date FROM prices WHERE date <= ? ORDER BY date DESC LIMIT ?",
                                         (date, VALUE_DAYS))]
    if len(sessions) < RECENT:
        return pd.DataFrame(columns=["sid", "group", "value", "isin"])
    marks = ",".join("?" * len(sessions))
    q = ("SELECT date, isin, series, value FROM prices WHERE date IN (%s) AND series IN (%s)"
         % (marks, ",".join("?" * len(panel_mod.EQUITY_SERIES))))
    df = pd.read_sql_query(q, db, params=list(sessions) + list(panel_mod.EQUITY_SERIES))
    df = df[df["isin"].map(labels_mod.is_equity_share)]
    df["sid"] = df["isin"].map(lambda i: ca.chain(i, link_map)[-1])
    recent = set(sessions[:RECENT])
    alive = set(df.loc[df["date"].isin(recent), "sid"])
    df = df[df["sid"].isin(alive)]
    total = (df.groupby(["sid", "date"])["value"].sum().groupby("sid").sum() / 100.0 / VALUE_DAYS)  # paise to rupees
    last = df.sort_values("date").groupby("sid").tail(1).set_index("sid")
    out = pd.DataFrame({"group": last["series"].map(labels_mod.SERIES_GROUP), "value": total, "isin": last["isin"]})
    out["value_bucket"] = out["value"].map(lambda v: labels_mod.bucket(v, labels_mod.VALUE_EDGES))
    return out.reset_index()


def coverage_at(db, date, link_map):
    """Population at `date` with the three coverage flags."""
    pop = population(db, date, link_map)
    if pop.empty:
        return pop
    cutoff = (dt.date.fromisoformat(date) - dt.timedelta(days=FRESH_DAYS)).isoformat()
    f = pd.read_sql_query("SELECT isin, period_end, paid_up, face_value FROM financials "
                          "WHERE broadcast_at < ? AND period_end >= ?", db, params=[date, cutoff])
    f["sid"] = f["isin"].map(lambda i: ca.chain(i, link_map)[-1] if i else None)
    pu, fv = pd.to_numeric(f["paid_up"], errors="coerce"), pd.to_numeric(f["face_value"], errors="coerce")
    f["shares_ok"] = (pu > 0) & (fv > 0)
    has_f = set(f["sid"].dropna())
    has_sh = set(f.loc[f["shares_ok"], "sid"].dropna())
    try:
        d = pd.read_sql_query("SELECT isin FROM shareholding_detail WHERE period_end <= ? AND period_end >= ? "
                              "AND total_shares IS NOT NULL AND total_shares > 0", db, params=[date, cutoff])
        has_d = set(d["isin"].map(lambda i: ca.chain(i, link_map)[-1]))
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        has_d = set()
    try:
        h = pd.read_sql_query("SELECT isin FROM shareholding WHERE broadcast_at < ? AND period_end >= ? "
                              "AND promoter_pct IS NOT NULL", db, params=[date, cutoff])
        has_h = set(h["isin"].map(lambda i: ca.chain(i, link_map)[-1]))
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        has_h = set()
    pop["stake"] = pop["sid"].isin(has_h)
    pop["filing"] = pop["sid"].isin(has_f)
    pop["share_count"] = pop["sid"].isin(has_sh)
    pop["holding_detail"] = pop["sid"].isin(has_d)
    return pop


def summarise_snapshot(pop):
    def block(g):
        n = len(g)
        return {"stocks": int(n), "filing": float(g["filing"].mean()), "share_count": float(g["share_count"].mean()),
                "stake": float(g["stake"].mean()), "holding_detail": float(g["holding_detail"].mean())}
    out = {"all": block(pop)}
    for k, g in pop.groupby("group"):
        out[k] = block(g)
    return out


def jump_flags(close, up=UP_JUMP, down=DOWN_JUMP):
    """Per security: the largest and smallest ratio of an adjusted close to the previous close it printed,
    however long ago that was (a limit here once hid a chain switch after a 30-session gap, see FINDINGS.md).
    Frames: date x security."""
    prev = close.ffill().shift(1)
    ratio = (close / prev).replace([np.inf, -np.inf], np.nan)
    return ratio.max(), ratio.min(), ratio


def window_has_jump(ratio, sid, start, end, up=UP_JUMP, down=DOWN_JUMP):
    r = ratio[sid].loc[(ratio.index > pd.Timestamp(start)) & (ratio.index <= pd.Timestamp(end))]
    return bool(((r > up) | (r < down)).any())


GAP_SESSIONS = 60  # a stretch with no close this long inside a window is suspect (see FINDINGS.md 2026-11-04)


def gap_segments(close, gap=GAP_SESSIONS):
    """{security: [(last close before the gap, first close after it), ...]} for every stretch of more than `gap`
    sessions without a close between two closes. A stock that stops for good has no later close: no segment."""
    out = {}
    idx = close.index
    for sid in close.columns:
        pos = np.flatnonzero(close[sid].notna().values)
        if len(pos) < 2:
            continue
        d = np.diff(pos)
        for k in np.flatnonzero(d > gap):
            out.setdefault(sid, []).append((idx[pos[k]], idx[pos[k + 1]]))
    return out


def window_has_gap(segments, sid, start, end):
    s0, e0 = pd.Timestamp(start), pd.Timestamp(end)
    return any(a >= s0 and b <= e0 for a, b in segments.get(sid, ()))


def multiples_check(p, rows, top_n=25, horizon=labels_mod.HORIZON):
    """Largest distinct-stock multiples, whether each holds a suspect move in its window, and the base rate
    without every row whose window holds one."""
    me = panel_mod.month_ends(p.dates)
    ends = {d.date().isoformat(): me[k + horizon] for k, d in enumerate(me) if k + horizon < len(me)}
    _, _, ratio = jump_flags(p.close)
    r = rows[~rows["issue"]].copy()
    # one flag per stock, over the widest span of its starts' windows
    stock_span = r.groupby("sid")["start"].agg(["min", "max"])
    stock_jump = {}
    for sid, row in stock_span.iterrows():
        stock_jump[sid] = window_has_jump(ratio, sid, row["min"], ends[row["max"]])
    suspect_stocks = {s for s, v in stock_jump.items() if v}
    clean = r[~r["sid"].isin(suspect_stocks)]
    segs = gap_segments(p.close)
    gapped = np.fromiter((window_has_gap(segs, sid, st, ends[st]) for sid, st in zip(r["sid"], r["start"])),
                         dtype=bool, count=len(r))
    nogap = r[~gapped]
    top = r.sort_values("multiple", ascending=False).drop_duplicates("sid").head(top_n).copy()
    top["jump"] = top["sid"].map(stock_jump)
    top["gap"] = [window_has_gap(segs, sid, st, ends[st]) for sid, st in zip(top["sid"], top["start"])]
    return {
        "stocks_with_suspect_move": len(suspect_stocks),
        "stocks": int(r["sid"].nunique()),
        "hit_all": float((r["multiple"] >= labels_mod.MULTIPLE).mean()),
        "hit_without_suspect_stocks": float((clean["multiple"] >= labels_mod.MULTIPLE).mean()),
        "stock_months_without": int(len(clean)),
        "multibaggers_all": int(r.loc[r["multiple"] >= labels_mod.MULTIPLE, "sid"].nunique()),
        "multibaggers_without": int(clean.loc[clean["multiple"] >= labels_mod.MULTIPLE, "sid"].nunique()),
        "gap_stock_months": int(gapped.sum()),
        "gap_stocks": int(r.loc[gapped, "sid"].nunique()),
        "hit_without_gap_rows": float((nogap["multiple"] >= labels_mod.MULTIPLE).mean()),
        "multibaggers_without_gap_rows": int(nogap.loc[nogap["multiple"] >= labels_mod.MULTIPLE, "sid"].nunique()),
        "hit_in_gap_rows": float((r.loc[gapped, "multiple"] >= labels_mod.MULTIPLE).mean()) if gapped.any() else None,
        "top": top[["start", "symbol", "series", "raw_close", "traded_value", "multiple", "jump", "gap"]],
    }


def sme_by_year(db):
    """Distinct SME securities that traded in each year, and how many of them have a results filing broadcast
    in that same year."""
    df = pd.read_sql_query("SELECT substr(date,1,4) AS year, isin FROM prices WHERE series IN ('SM','ST','SZ') "
                           "AND isin LIKE 'INE%' GROUP BY 1, 2", db)
    f = pd.read_sql_query("SELECT DISTINCT substr(broadcast_at,1,4) AS year, isin FROM financials "
                          "WHERE isin IS NOT NULL", db)
    f["filed"] = True
    df = df.merge(f, on=["year", "isin"], how="left")
    df["filed"] = df["filed"].fillna(False).astype(bool)
    return df.groupby("year").agg(stocks=("isin", "nunique"), with_filings=("filed", "sum")).reset_index()


def report_coverage(snaps):
    lines = ["%-11s %-18s %7s %9s %12s %11s %15s" % (
        "snapshot", "group", "stocks", "filing", "share count", "promoter %", "holding detail")]
    for date, s in snaps:
        for k in ("all", "main board EQ", "main board BE/BZ", "SME"):
            if k in s:
                b = s[k]
                lines.append("%-11s %-18s %7d %8.0f%% %11.0f%% %10.0f%% %14.0f%%" % (
                    date, k, b["stocks"], b["filing"] * 100, b["share_count"] * 100, b["stake"] * 100,
                    b["holding_detail"] * 100))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Phase 2 data checks.")
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--note", default="Phase 2 data checks, settings fixed in FINDINGS.md 2026-11-04")
    a = ap.parse_args(argv)

    db = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(a.db), uri=True)
    link_map = ca.links(db)
    snaps, by_value = [], {}
    for date in year_end_sessions(db):
        pop = coverage_at(db, date, link_map)
        snaps.append((date, summarise_snapshot(pop)))
        by_value[date] = {k: {"stocks": int(len(g)), "filing": float(g["filing"].mean())}
                          for k, g in pop.groupby("value_bucket")}
    print("1. coverage at each year-end session (filings broadcast before that day; quarter ended within %d days)"
          % FRESH_DAYS)
    print(report_coverage(snaps))
    last = snaps[-1][0]
    print("\n   filing coverage by traded value at the latest snapshot (%s)" % last)
    for edge, name in labels_mod.VALUE_EDGES:
        if name in by_value[last]:
            b = by_value[last][name]
            print("   %-24s %5d stocks, %3.0f%% with a recent filing" % (name, b["stocks"], b["filing"] * 100))
    sme = sme_by_year(db)
    print("\n2. SME securities (INE) trading, and with any results filing in the store")
    print(sme.to_string(index=False))
    db.close()

    print("\n3. the largest Phase 1 multiples, checked for a one-session adjusted move beyond +50%% or -33%% (dev)")
    p = panel_mod.load(a.db, start=labels_mod.LOAD_FROM, end=labels_mod.DEV_PRICES_END, series=panel_mod.EQUITY_SERIES)
    if p.dates[-1] > pd.Timestamp(labels_mod.DEV_PRICES_END):
        raise SystemExit("refusing to run: the panel holds data after %s" % labels_mod.DEV_PRICES_END)
    rows = pd.read_csv(LABELS)
    m = multiples_check(p, rows)
    print("   %d of %d stocks have such a move somewhere in their starts' windows" % (
        m["stocks_with_suspect_move"], m["stocks"]))
    print("   base rate %.1f%% as fixed; %.1f%% without those stocks (%d multibaggers, from %d)" % (
        m["hit_all"] * 100, m["hit_without_suspect_stocks"] * 100, m["multibaggers_without"], m["multibaggers_all"]))
    print("   a trading gap of more than %d sessions inside the window (a possible ISIN chain across unrelated "
          "securities, or a suspension): %d stock-months, %d stocks" % (GAP_SESSIONS, m["gap_stock_months"], m["gap_stocks"]))
    print("   3x rate in those rows %s; without them %.1f%% (%d multibaggers)" % (
        "n/a" if m["hit_in_gap_rows"] is None else "%.1f%%" % (m["hit_in_gap_rows"] * 100),
        m["hit_without_gap_rows"] * 100, m["multibaggers_without_gap_rows"]))
    print(m["top"].to_string(index=False))
    if not a.no_log:
        backtest.log_run({"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": "audit", "signal": "coverage",
                          "period": "dev", "config": {"engine": "coverage.py", "fresh_days": FRESH_DAYS,
                                                      "jump": [UP_JUMP, DOWN_JUMP], "gap_sessions": GAP_SESSIONS},
                          "summary": {"snapshots": snaps[-1:], "sme_by_year": sme.to_dict("records"), "multiples": {k: v for k, v in m.items() if k != "top"}},
                          "note": a.note})
    return 0


if __name__ == "__main__":
    sys.exit(main())
