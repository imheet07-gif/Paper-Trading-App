#!/usr/bin/env python3
"""Backtest a cross-sectional signal on the NSE history, honestly.

The rules that keep a backtest from lying:

  * survivorship: every security that traded is in the data; a holding that
    stops trading is closed at its last price less a haircut (default 50%)
  * look-ahead: scores and the universe use data to the rebalance date's
    close; orders fill at the next day's open
  * untradeable days: no buying a stock locked at its upper circuit, no
    selling one locked at its lower circuit; no fill when it did not trade
  * costs: STT, stamp duty, exchange and SEBI fees and GST from
    core/charges.json, slippage per side, and the DP charge per scrip sold
  * a point-in-time universe (panel.universe)

And the protocol that keeps the researcher from lying:

  dev        2013-01-01 to 2020-12-31   build and tune here
  validation 2021-01-01 to 2023-12-31   check what dev chose, rarely
  holdout    2024-01-01 onward          only with --open-holdout; every
                                        opening is appended to
                                        research/holdout_log.jsonl

Every run is appended to data/research_runs.jsonl, so the number of
variants tried is known when judging the best one (deflated Sharpe).

  python3 research/backtest.py --signal momentum_12_1 --period dev
  python3 research/backtest.py --signal high_52w --period validation --top 30
"""

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "core"))
import charges as charges_mod  # noqa: E402
import panel as panel_mod  # noqa: E402
import signals as signals_mod  # noqa: E402

PERIODS = {
    "dev": ("2013-01-01", "2020-12-31"),
    "validation": ("2021-01-01", "2023-12-31"),
    "holdout": ("2024-01-01", None),
}
RUNS_LOG = os.path.join(ROOT, "data", "research_runs.jsonl")
HOLDOUT_LOG = os.path.join(HERE, "holdout_log.jsonl")
RISK_FREE = 0.065  # annual, roughly the Indian 91-day T-bill over the period


def cost_rates(rates=None):
    """(buy, sell) cost as a fraction of trade value from charges.json, delivery, NSE."""
    r = rates or charges_mod.load_rates()
    st, ex = r["statutory"], r["exchange_txn_pct"]
    pct = lambda x: float(x) / 100
    fees = pct(ex["NSE"]) + float(st["sebi_fee_per_crore"]) / 1e7
    gst = fees * pct(st["gst_pct"])
    buy = pct(st["stt_pct"]["delivery"]["buy"]) + pct(st["stamp_pct"]["delivery"]["buy"]) + fees + gst
    sell = pct(st["stt_pct"]["delivery"]["sell"]) + fees + gst
    return buy, sell


class Config:
    def __init__(self, top=20, slippage_bps=15.0, capital=1_000_000.0, dp_charge=15.34, delist_haircut=0.5,
                 delist_after=20, locked_move=0.02):
        self.top, self.slippage, self.capital = top, slippage_bps / 1e4, capital
        self.dp_charge, self.delist_haircut, self.delist_after = dp_charge, delist_haircut, delist_after
        self.locked_move = locked_move

    def as_dict(self):
        return dict(self.__dict__)


def locked(H, L, C, i, k, direction, min_move):
    """True if column k traded at a single price all day (high == low) after a
    move of at least min_move in `direction`: a stock locked at its circuit."""
    h, l, c = H[i, k], L[i, k], C[i, k]
    prev = C[i - 1, k] if i else np.nan
    if np.isnan(h) or np.isnan(l) or np.isnan(prev) or h != l:
        return False
    move = c / prev - 1
    return move >= min_move if direction > 0 else move <= -min_move


def run(p, scores, start, end, cfg=None, rebalance_dates=None):
    """Simulate. Returns {"nav": Series, "bench": Series, "trades": [...], "stats": {...}}."""
    cfg = cfg or Config()
    buy_cost, sell_cost = cost_rates()
    dates = p.dates
    lo, hi = dates.searchsorted(pd.Timestamp(start)), (dates.searchsorted(pd.Timestamp(end), "right") if end else len(dates))
    rebal = set(rebalance_dates if rebalance_dates is not None else panel_mod.month_ends(dates[lo:hi]))
    elig = panel_mod.universe(p)
    close, open_ = p.close, p.open
    col = {s: k for k, s in enumerate(close.columns)}
    C, O, H, L = close.values, open_.values, p.high.values, p.low.values

    cash, units, last_px, missing = cfg.capital, {}, {}, {}
    pending, navs, bench_navs, trades = None, [], [], []
    bench_members, bench_nav = [], 1.0
    stats = {"blocked_buys": 0, "blocked_sells": 0, "no_fill": 0, "delist_exits": 0, "costs": 0.0, "turnover": 0.0}

    for i in range(lo, hi):
        d = dates[i]
        # 1. fill yesterday's orders at today's open
        if pending is not None:
            nav_open = cash + sum(u * (O[i, col[s]] if not np.isnan(O[i, col[s]]) else last_px[s]) for s, u in units.items())
            target = {s: nav_open / len(pending) for s in pending} if pending else {}
            for s in list(units):
                px = O[i, col[s]]
                want = target.get(s, 0.0)
                have = units[s] * (px if not np.isnan(px) else last_px[s])
                if want == 0 or want < have * 0.98:  # sell exits and trims of more than 2%
                    if np.isnan(px):
                        stats["no_fill"] += 1
                        continue
                    if locked(H, L, C, i, col[s], -1, cfg.locked_move):
                        stats["blocked_sells"] += 1
                        continue
                    q = units[s] if want == 0 else (have - want) / px
                    fill = px * (1 - cfg.slippage)
                    value = q * fill
                    cost = value * sell_cost + cfg.dp_charge
                    cash += value - cost
                    stats["costs"] += cost + q * px * cfg.slippage
                    stats["turnover"] += q * px
                    units[s] -= q
                    if units[s] <= 1e-9:
                        del units[s]
                    trades.append((d.date().isoformat(), s, "sell", q, fill))
            for s, want in target.items():
                px = O[i, col[s]]
                have = units.get(s, 0.0) * (px if not np.isnan(px) else last_px.get(s, 0.0))
                if want <= have * 1.02:  # top-ups of 2% or less are not worth the costs
                    continue
                if np.isnan(px):
                    stats["no_fill"] += 1
                    continue
                if locked(H, L, C, i, col[s], +1, cfg.locked_move):
                    stats["blocked_buys"] += 1
                    continue
                spend = min(want - have, cash)
                if spend <= 0:
                    continue
                fill = px * (1 + cfg.slippage)
                q = spend / (fill * (1 + buy_cost))
                cost = q * fill * buy_cost
                cash -= q * fill + cost
                stats["costs"] += cost + q * px * cfg.slippage
                stats["turnover"] += q * px
                units[s] = units.get(s, 0.0) + q
                trades.append((d.date().isoformat(), s, "buy", q, fill))
            pending = None
        # 2. mark to the close; exit anything that stopped trading
        for s in list(units):
            c = C[i, col[s]]
            if np.isnan(c):
                missing[s] = missing.get(s, 0) + 1
                if missing[s] >= cfg.delist_after:
                    cash += units.pop(s) * last_px[s] * (1 - cfg.delist_haircut)
                    stats["delist_exits"] += 1
                    trades.append((d.date().isoformat(), s, "delist", 0, last_px[s]))
            else:
                missing[s] = 0
                last_px[s] = c
        navs.append(cash + sum(u * last_px[s] for s, u in units.items()))
        # benchmark: equal weight of the eligible universe, rebalanced with the strategy, no costs
        if bench_members and i > lo:
            r = C[i, bench_members] / C[i - 1, bench_members] - 1
            r = r[~np.isnan(r)]
            bench_nav *= 1 + (r.mean() if len(r) else 0.0)
        bench_navs.append(bench_nav)
        # 3. rebalance at the close: tomorrow's orders
        if d in rebal:
            ok = elig.iloc[i]
            row = scores.iloc[i].where(ok)
            row = row.dropna()
            pending = list(row.sort_values(ascending=False).index[:cfg.top])
            bench_members = [col[s] for s in ok[ok].index]

    idx = dates[lo:hi]
    nav = pd.Series(navs, index=idx) / cfg.capital
    stats["turnover"] = stats["turnover"] / cfg.capital
    return {"nav": nav, "bench": pd.Series(bench_navs, index=idx), "trades": trades, "stats": stats}


# ---------------------------------------------------------------- metrics

def metrics(nav, rf=RISK_FREE):
    r = nav.pct_change().dropna()
    years = len(r) / 252
    if years <= 0 or len(r) < 20:
        return {}
    cagr = nav.iloc[-1] ** (1 / years) - 1
    vol = r.std() * math.sqrt(252)
    ex = r - rf / 252
    sharpe = ex.mean() / r.std() * math.sqrt(252) if r.std() else float("nan")
    down = r[r < 0].std() * math.sqrt(252)
    dd = (nav / nav.cummax() - 1).min()
    yearly = nav.groupby(nav.index.year).last() / nav.groupby(nav.index.year).first() - 1
    return {"cagr": cagr, "vol": vol, "sharpe": sharpe, "sortino": (r.mean() * 252 - rf) / down if down else float("nan"),
            "max_drawdown": dd, "calmar": cagr / abs(dd) if dd else float("nan"), "days": len(r),
            "skew": float(r.skew()), "kurtosis": float(r.kurt()) + 3, "yearly": {int(k): float(v) for k, v in yearly.items()}}


def deflated_sharpe(sr_annual, n_days, skew, kurt, trial_srs):
    """Probability that the true Sharpe is above what the best of len(trial_srs)
    random trials would show (Bailey and Lopez de Prado). Sharpes annualised."""
    n = len(trial_srs)
    if n < 2 or n_days < 30:
        return float("nan")
    nd = statistics.NormalDist()
    sr = sr_annual / math.sqrt(252)
    var_trials = statistics.pvariance([s / math.sqrt(252) for s in trial_srs])
    g = 0.5772156649
    sr0 = math.sqrt(var_trials) * ((1 - g) * nd.inv_cdf(1 - 1 / n) + g * nd.inv_cdf(1 - 1 / (n * math.e)))
    den = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-12))
    return nd.cdf((sr - sr0) * math.sqrt(n_days - 1) / den)


def log_run(entry, path=RUNS_LOG):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def past_sharpes(path=RUNS_LOG, period="dev"):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("period") == period and e.get("sharpe") is not None:
                out.append(e["sharpe"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Backtest a signal on the NSE history.")
    ap.add_argument("--signal", required=True, choices=sorted(signals_mod.SIGNALS))
    ap.add_argument("--period", default="dev", choices=sorted(PERIODS))
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--slippage-bps", type=float, default=15.0)
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--open-holdout", action="store_true", help="required for the holdout period; logged")
    ap.add_argument("--note", default="", help="why this run (kept in the run log)")
    a = ap.parse_args(argv)
    if a.period == "holdout" and not a.open_holdout:
        print("error: the holdout is opened once per finished strategy; pass --open-holdout to do it", file=sys.stderr)
        return 2
    start, end = PERIODS[a.period]
    load_from = (pd.Timestamp(start) - pd.DateOffset(years=2)).date().isoformat()  # lookback for signals
    p = panel_mod.load(a.db, start=load_from, end=end)
    scores = signals_mod.SIGNALS[a.signal](p)
    cfg = Config(top=a.top, slippage_bps=a.slippage_bps)
    res = run(p, scores, start, end, cfg)
    m, b = metrics(res["nav"]), metrics(res["bench"])
    trials = past_sharpes(period=a.period) + [m.get("sharpe", float("nan"))]
    dsr = deflated_sharpe(m["sharpe"], m["days"], m["skew"], m["kurtosis"], trials) if m else float("nan")
    entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), "signal": a.signal, "period": a.period,
             "config": cfg.as_dict(), "sharpe": m.get("sharpe"), "cagr": m.get("cagr"), "bench_sharpe": b.get("sharpe"),
             "note": a.note}
    log_run(entry)
    if a.period == "holdout":
        log_run(entry, HOLDOUT_LOG)
    print("%s, %s %s to %s, top %d, slippage %.0f bps" % (a.signal, a.period, start, end or "now", a.top, a.slippage_bps))
    for name, x in (("strategy", m), ("equal-weight universe", b)):
        print("  %-22s CAGR %6.1f%%  vol %5.1f%%  Sharpe %5.2f  max drawdown %6.1f%%" % (
            name, x["cagr"] * 100, x["vol"] * 100, x["sharpe"], x["max_drawdown"] * 100))
    print("  by year: " + "  ".join("%d %+.0f%%/%+.0f%%" % (y, m["yearly"][y] * 100, b["yearly"].get(y, float("nan")) * 100)
                                   for y in sorted(m["yearly"])))
    s = res["stats"]
    print("  turnover %.1fx/yr, costs %.2f%% of capital/yr, blocked buys %d, blocked sells %d, delisting exits %d" % (
        s["turnover"] / (m["days"] / 252), s["costs"] / cfg.capital / (m["days"] / 252) * 100,
        s["blocked_buys"], s["blocked_sells"], s["delist_exits"]))
    print("  deflated Sharpe probability %.2f over %d %s run(s) logged" % (dsr, len(trials), a.period))
    return 0


if __name__ == "__main__":
    sys.exit(main())
