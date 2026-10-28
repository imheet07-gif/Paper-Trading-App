"""Concentration and risk-level backtests for the trade finder.

Settings fixed in FINDINGS.md (2026-10-28) before any run:

  candidates  the top tenth of the tested ranking (momentum_quality_top20's
              signal, universe and month-end rebalance); the first k that pass
              the trade finder's filters (circuit lock, 1% of median traded
              value, a whole share fits) are bought, the rest stays in cash
  orders      buy limit at the rebalance close + 1%, filled at the next open
              plus 15 bps (never above the limit, not at all above it); sells
              at the open less 15 bps, retried after a lower-circuit lock
  sizing      concentration: equal money per stock; risk levels: exits.plan
              (loss at the stop within 0.5 / 1 / 2% of the portfolio)
  exits       risk levels only: stop at entry - 2 ATR, target 2R, checked each
              session from the entry day; both touched in a session = the stop
  money       Rs 1,00,000 compounding, whole shares on unadjusted prices,
              Zerodha delivery charges from core/charges.json, cash at 6%

  python3 research/profiles.py --period dev --stocks 3
  python3 research/profiles.py --period dev --risk medium
  python3 research/profiles.py --period dev --all

Every run is logged to runs.jsonl. Dev and validation only: the holdout is
never opened here. Numbers are float estimates and never feed accounting.
"""

import argparse
import datetime as dt
import json
import math
import os
import sys
from decimal import ROUND_DOWN

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import backtest  # noqa: E402
import charges as charges_mod  # noqa: E402
import exits  # noqa: E402
import panel as panel_mod  # noqa: E402
import scan  # noqa: E402
import signals as signals_mod  # noqa: E402
from money import D, paise  # noqa: E402

STRATEGY = "momentum_quality_top20"
CANDIDATE_SHARE = 0.10
LIMIT_BAND = 0.01
MAX_SHARE_OF_VALUE = 0.01
LOCKED_MOVE = 0.02
CAPITAL = 100_000.0
SLIPPAGE_BPS = 15.0
CASH_YIELD = 0.06
DELIST_AFTER, DELIST_HAIRCUT = 20, 0.5
VALUE_DAYS = 60
PERIODS = {k: backtest.PERIODS[k] for k in ("dev", "validation")}  # never the holdout
RUNS = [("top", 1), ("top", 3), ("top", 5), ("top", 20), ("risk", "low"), ("risk", "medium"), ("risk", "high")]
VOL_LEVELS = {"low": (20, 0.10), "medium": (5, 0.20), "high": (3, 0.30)}  # stocks, target volatility (FINDINGS 2026-10-29)
VOL_DAYS = 63
INDEX = "Nifty 500"
EXIT_RUNS = [("hold", "low"), ("hold", "medium"), ("hold", "high")]  # the exit test (FINDINGS.md 2026-10-28)


def charges_fn(broker):
    """Charges on a fill of `qty` units at `price`. Units are adjusted for corporate actions
    and need not be whole, so the charges are worked out on the fill's turnover as one
    line: every delivery charge in core/charges.json is a share of turnover or a flat fee."""
    def fn(side, qty, price):
        turnover = paise(D(str(round(qty * price, 4))))
        if turnover <= 0:
            return 0.0
        return float(charges_mod.trade_charges(broker, side, 1, turnover, "delivery", "NSE")["total"])
    return fn


def _locked(H, L, C, i, k, direction):
    return backtest.locked(H, L, C, i, k, direction, LOCKED_MOVE)


def choose(p, scores, elig, i, amount, stocks, risk, arrays, broker):
    """The rebalance at row i: [(sid, raw qty, raw limit, raw stop or None, raw target or None)],
    the first `stocks` of the top tenth that pass the filters."""
    C, H, L, R, ATR, MED, col = arrays
    row = scores.iloc[i].where(elig.iloc[i]).dropna().sort_values(ascending=False)
    n = max(1, int(math.ceil(CANDIDATE_SHARE * len(row))))
    amount_d = D(str(round(amount, 2)))
    picks = []
    for sid in row.index[:n]:
        k = col[sid]
        raw = R[i, k]
        if np.isnan(raw) or raw <= 0 or _locked(H, L, C, i, k, +1) or _locked(H, L, C, i, k, -1):
            continue
        limit = paise(D(str(raw)) * D(str(1 + LIMIT_BAND)))
        stop = target = None
        if risk:
            mult = C[i, k] / raw
            a = ATR[i, k] / mult if mult else np.nan
            if np.isnan(a) or a <= 0:
                continue
            try:
                pl = exits.plan(limit, D(str(round(float(a), 4))), amount_d, risk, stocks, None,
                                lambda s, q, px: charges_mod.trade_charges(broker, s, q, px, "delivery", "NSE")["total"])
            except ValueError:
                continue
            qty, stop, target = pl["qty"], float(pl["stop"]), float(pl["target"])
        else:
            qty = int((amount_d / stocks / limit).to_integral_value(rounding=ROUND_DOWN))
            if qty < 1:
                continue
        med = MED[i, k]
        if np.isnan(med) or qty * float(limit) > MAX_SHARE_OF_VALUE * med:
            continue
        picks.append((sid, qty, float(limit), stop, target))
        if len(picks) == stocks:
            break
    return picks


def basket_vol(C, i, cols, days=VOL_DAYS):
    """Yearly volatility of an equal-money basket of these columns over the last `days` sessions to row i,
    or None with fewer than two thirds of the days."""
    if not cols or i < 1:
        return None
    block = C[max(0, i - days):i + 1, cols]
    with np.errstate(invalid="ignore", divide="ignore"):
        r = block[1:] / block[:-1] - 1
    ok = ~np.isnan(r).all(axis=1)
    if ok.sum() < days * 2 // 3:
        return None
    daily = np.nanmean(r[ok], axis=1)
    return float(np.std(daily, ddof=1) * np.sqrt(252))


def simulate(p, scores, elig, start, end, stocks, risk=None, capital=CAPITAL, broker="zerodha",
             slippage_bps=SLIPPAGE_BPS, cash_yield=CASH_YIELD, use_exits=True, target_vol=None, index=None):
    """Run one concentration (risk=None) or risk-level portfolio over [start, end].
    use_exits=False holds a risk level's stocks to the next rebalance (the exit test).
    target_vol scales the stock part to that yearly volatility (risk levels v2); index, an
    array of index closes aligned to p.dates, then holds the rest, traded at the close of
    the day the stock orders fill.
    Positions are held in adjusted units (raw shares / the adjustment multiplier at
    the rebalance), so splits and bonuses inside a month change nothing."""
    slip = slippage_bps / 1e4
    fee = charges_fn(broker)
    dates = p.dates
    lo = dates.searchsorted(pd.Timestamp(start))
    hi = dates.searchsorted(pd.Timestamp(end), "right") if end else len(dates)
    rebal = set(panel_mod.month_ends(dates[lo:hi]))
    col = {s: k for k, s in enumerate(p.close.columns)}
    C, O, H, L, R = p.close.values, p.open.values, p.high.values, p.low.values, p.raw_close.values
    ATR = exits.atr(p).values
    MED = p.value.rolling(VALUE_DAYS, min_periods=int(VALUE_DAYS * 0.8)).median().values
    arrays = (C, H, L, R, ATR, MED, col)
    daily_cash = (1 + cash_yield) ** (1 / 252) - 1

    cash, units, last_px, missing, exit_at = capital, {}, {}, {}, {}
    pending, navs, invested, trades = None, [], [], []
    idx_units, idx_share, idx_day, exposures, idx_held = 0.0, 0.0, None, [], []
    IX = np.asarray(index, dtype=float) if index is not None else None
    stats = {"rebalances": 0, "short_months": 0, "unfilled": 0, "blocked_sells": 0, "stops": 0, "targets": 0,
             "delist_exits": 0, "costs": 0.0}

    def sell(s, q, px, why):
        nonlocal cash
        trades.append((dates[i].date().isoformat(), s, "sell", q, px, why))
        value = q * px
        cost = fee("sell", q, px)
        cash += value - cost
        stats["costs"] += cost
        units[s] -= q
        if units[s] <= 1e-9:
            del units[s]
            exit_at.pop(s, None)

    for i in range(lo, hi):
        d = dates[i]
        entered = set()
        # 1. yesterday's rebalance orders at today's open: sells first, then buys
        if pending is not None:
            for s in list(units):
                if s not in pending:
                    continue  # a retry day: only the sells still owed
                k = col[s]
                want = pending[s][0]
                if units[s] - want > 1e-9:
                    if np.isnan(O[i, k]):
                        continue  # no price today: tried again tomorrow
                    if _locked(H, L, C, i, k, -1):
                        stats["blocked_sells"] += 1
                        continue
                    sell(s, units[s] - want, O[i, k] * (1 - slip), "rebalance")
                    if s in pending:
                        pending[s] = (min(want, units.get(s, 0.0)),) + pending[s][1:]
            for s, (want, limit, stop, target) in pending.items():
                if limit is None:
                    continue  # a sell retried from an earlier day: no buy, the stop stays
                k = col[s]
                have = units.get(s, 0.0)
                if s in units:
                    exit_at[s] = (stop, target)  # kept stocks take the new plan's stop and target
                if want - have <= 1e-9:
                    continue
                px = O[i, k]
                if np.isnan(px) or px > limit:
                    stats["unfilled"] += 1
                    continue
                fill = min(px * (1 + slip), limit)
                q = want - have
                c = fee("buy", q, fill)
                if q * fill + c > cash:  # never borrow: fewer shares
                    q = max(0.0, (cash - c) / fill)
                    c = fee("buy", q, fill) if q > 0 else 0.0
                if q <= 1e-9:
                    stats["unfilled"] += 1
                    continue
                cash -= q * fill + c
                stats["costs"] += c
                trades.append((d.date().isoformat(), s, "buy", q, fill, "rebalance"))
                units[s] = have + q
                exit_at[s] = (stop, target)
                last_px[s] = px
                entered.add(s)
            # sells not done today are retried at the next open
            pending = {s: v for s, v in pending.items() if s in units and units[s] - v[0] > 1e-9} or None
            if pending:
                pending = {s: (v[0], None, None, None) for s, v in pending.items()}
        # 2. stops and targets, from the entry day on
        if risk and use_exits:
            for s in list(units):
                stop, target = exit_at.get(s, (None, None))
                if stop is None:
                    continue
                k = col[s]
                o, h, l = O[i, k], H[i, k], L[i, k]
                if np.isnan(l) or np.isnan(h):
                    continue
                first = s in entered
                if not first and not np.isnan(o) and o <= stop:
                    if _locked(H, L, C, i, k, -1):
                        stats["blocked_sells"] += 1
                        continue
                    sell(s, units[s], o * (1 - slip), "stop")
                    stats["stops"] += 1
                elif l <= stop:
                    if _locked(H, L, C, i, k, -1):
                        stats["blocked_sells"] += 1
                        continue
                    sell(s, units[s], stop * (1 - slip), "stop")
                    stats["stops"] += 1
                elif h >= target:
                    sell(s, units[s], target if first or np.isnan(o) else max(o, target), "target")
                    stats["targets"] += 1
        # 3. mark to the close; exit anything that stopped trading
        for s in list(units):
            c = C[i, col[s]]
            if np.isnan(c):
                missing[s] = missing.get(s, 0) + 1
                if missing[s] >= DELIST_AFTER:
                    cash += units.pop(s) * last_px[s] * (1 - DELIST_HAIRCUT)
                    trades.append((d.date().isoformat(), s, "sell", 0, last_px[s], "delisted"))
                    exit_at.pop(s, None)
                    stats["delist_exits"] += 1
            else:
                missing[s] = 0
                last_px[s] = c
        cash *= 1 + daily_cash
        held = sum(u * last_px[s] for s, u in units.items())
        ix = IX[i] if IX is not None else np.nan
        if idx_day == i and not np.isnan(ix):
            # the index part, at the close of the day the stock orders filled, never borrowing
            nav_now = cash + held + idx_units * ix
            diff = nav_now * idx_share - idx_units * ix
            if diff < 0 or cash > 0:
                diff = diff if diff < 0 else min(diff, cash)
                idx_units += diff / ix
                cash -= diff
            idx_day = None
        idx_value = idx_units * ix if idx_units and not np.isnan(ix) else 0.0
        navs.append(cash + held + idx_value)
        invested.append(held / navs[-1] if navs[-1] > 0 else 0.0)
        idx_held.append(idx_value / navs[-1] if navs[-1] > 0 else 0.0)
        # 4. rebalance at the close: tomorrow's orders, sized on today's value
        if d in rebal and i + 1 < hi:
            amount = navs[-1]
            if target_vol:
                first = choose(p, scores, elig, i, navs[-1], stocks, risk, arrays, broker)
                vol = basket_vol(C, i, [col[x[0]] for x in first])
                w = min(1.0, target_vol / vol) if vol else 1.0
                exposures.append(w)
                amount = navs[-1] * w
                if IX is not None:
                    idx_share, idx_day = 1.0 - w, i + 1
            picks = choose(p, scores, elig, i, amount, stocks, risk, arrays, broker)
            stats["rebalances"] += 1
            if len(picks) < stocks:
                stats["short_months"] += 1
            pending = {}
            for sid, qty, limit, stop, target in picks:
                k = col[sid]
                mult = C[i, k] / R[i, k]  # adjusted = raw x mult at this rebalance
                pending[sid] = (qty * (1 / mult), limit * mult,
                                stop * mult if stop is not None else None,
                                target * mult if target is not None else None)
            for s in units:
                pending.setdefault(s, (0.0, np.inf, None, None))  # no longer picked: sold

    idx = dates[lo:hi]
    nav = pd.Series(navs, index=idx) / capital
    stats["invested_avg"] = float(np.mean(invested)) if invested else 0.0
    stats["index_avg"] = float(np.mean(idx_held)) if idx_held else 0.0
    stats["exposure_avg"] = float(np.mean(exposures)) if exposures else None
    return {"nav": nav, "stats": stats, "trades": trades}


def month_stats(nav):
    """Worst month and the share of months that lost money, from month-end values."""
    m = nav.groupby([nav.index.year, nav.index.month]).last()
    r = pd.concat([pd.Series([1.0]), m.reset_index(drop=True)]).pct_change().dropna()
    if not len(r):
        return {"worst_month": None, "months_losing": None, "months": 0}
    return {"worst_month": float(r.min()), "months_losing": float((r < 0).mean()), "months": int(len(r))}


def passes_period(kind, value, m, bench):
    """This period's half of the rule fixed in FINDINGS.md: every run must earn more than the
    equal-weight universe; the low risk level must also beat its Sharpe. A level is offered
    only if it passes on dev and on validation; one that fails hands over to the next safer
    level that passed."""
    if not m or not bench:
        return False
    if m["cagr"] <= bench["cagr"]:
        return False
    return m["sharpe"] > bench["sharpe"] if kind in ("risk", "vol") and value == "low" else True


def safest_offered(asked, passed):
    """The level to offer for a request: the asked level if it passed, else the next safer one
    that did; None when even low failed (the answer is then no trade)."""
    order = ["high", "medium", "low"]
    for level in order[order.index(asked):]:
        if passed.get(level):
            return level
    return None


def label(kind, value):
    if kind == "top":
        return "top %d, equal money" % value
    if kind == "vol":
        n, tv = VOL_LEVELS[value]
        return "%s v2 (%d stocks, %d%% vol)" % (value, n, round(tv * 100))
    n = exits.PROFILE_STOCKS[value]
    return "%s risk (%d stock%s)%s" % (value, n, "" if n == 1 else "s", ", no exits" if kind == "hold" else "")


def exits_pass(with_exits, held):
    """The exit test for one level and period (FINDINGS.md 2026-10-28): with the stop and
    target, a higher Sharpe and a shallower worst drawdown than holding to the rebalance."""
    try:
        return bool(with_exits["sharpe"] > held["sharpe"] and with_exits["max_drawdown"] > held["max_drawdown"])
    except (KeyError, TypeError):
        return False


def logged(period, profile, runs_log=backtest.RUNS_LOG):
    """The latest logged run of this profile ('risk:low') in this period, or None."""
    best = None
    if not os.path.exists(runs_log):
        return None
    with open(runs_log, encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("profile") == profile and e.get("period") == period and (best is None or e["at"] > best["at"]):
                best = e
    return best


def run_one(p, scores, elig, period, kind, value, bench, note="", log=True, runs_log=backtest.RUNS_LOG, index=None,
            index_metrics=None):
    start, end = PERIODS[period]
    if kind == "vol":
        stocks, tv = VOL_LEVELS[value]
        res = simulate(p, scores, elig, start, end, stocks, None, target_vol=tv, index=index)
    else:
        stocks = value if kind == "top" else exits.PROFILE_STOCKS[value]
        res = simulate(p, scores, elig, start, end, stocks, None if kind == "top" else value, use_exits=kind != "hold")
    m = backtest.metrics(res["nav"])
    ms = month_stats(res["nav"])
    entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), "signal": "momentum_quality", "period": period,
             "profile": "%s:%s" % (kind, value),
             "config": {"engine": "profiles.py", "candidate_share": CANDIDATE_SHARE, "capital": CAPITAL,
                        "slippage_bps": SLIPPAGE_BPS, "stocks": stocks, "risk": value if kind == "risk" else None,
                        "target_vol": VOL_LEVELS[value][1] if kind == "vol" else None,
                        "index": INDEX if kind == "vol" and index is not None else None},
             "sharpe": m.get("sharpe"), "cagr": m.get("cagr"), "max_drawdown": m.get("max_drawdown"),
             "worst_month": ms["worst_month"], "months_losing": ms["months_losing"],
             "bench_sharpe": bench.get("sharpe"), "bench_cagr": bench.get("cagr"), "stats": res["stats"],
             "offer_test": passes_period(kind, value, m, bench) if kind != "hold" else None,
             "index_cagr": (index_metrics or {}).get("cagr"), "index_sharpe": (index_metrics or {}).get("sharpe"),
             "yearly": m.get("yearly"),
             "note": note or "concentration and risk-level test, settings fixed in FINDINGS.md 2026-10-28"}
    if log:
        backtest.log_run(entry, runs_log)
    return entry, m


def report(period, entry, m):
    s = entry["stats"]
    pct = lambda x: "n/a" if x is None else "%.1f%%" % (x * 100)
    kind, value = entry["profile"].split(":")
    return ("%-26s CAGR %7s  Sharpe %5.2f  worst drawdown %7s  worst month %7s  months losing %5s  "
            "invested %4s  stops %d  targets %d  unfilled %d  short months %d of %d  passes this period: %s" % (
                label(kind, int(value) if kind == "top" else value), pct(m.get("cagr")), m.get("sharpe", float("nan")),
                pct(m.get("max_drawdown")), pct(entry["worst_month"]), pct(entry["months_losing"]),
                pct(s["invested_avg"]), s["stops"], s["targets"], s["unfilled"], s["short_months"], s["rebalances"],
                "n/a" if entry["offer_test"] is None else "yes" if entry["offer_test"] else "no")) + (
            "  in the index %s  stock share set %s" % (pct(s.get("index_avg")), pct(s.get("exposure_avg")))
            if kind == "vol" else "")


def main(argv=None, runs_log=backtest.RUNS_LOG):
    ap = argparse.ArgumentParser(description="Concentration and risk-level backtests (dev and validation only).")
    ap.add_argument("--period", required=True, choices=sorted(PERIODS))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stocks", type=int, choices=[1, 3, 5, 20], help="concentration: top k, equal money, no stops")
    g.add_argument("--risk", choices=sorted(exits.RISK_SHARE), help="a risk level: its stocks, sizing, stop and target")
    g.add_argument("--all", action="store_true", help="the seven runs fixed in FINDINGS.md")
    g.add_argument("--vol-levels", action="store_true",
                   help="risk levels v2: stock count and a volatility target, the rest in the Nifty 500")
    g.add_argument("--exit-test", action="store_true",
                   help="each risk level held without its stop and target, against its logged --all run")
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.exit_test:
        missing = [lvl for _, lvl in EXIT_RUNS if not logged(a.period, "risk:" + lvl, runs_log)]
        if missing:
            print("error: run --period %s --all first (no logged run for: %s)" % (a.period, ", ".join(missing)),
                  file=sys.stderr)
            return 2
    start, end = PERIODS[a.period]
    strat = scan.load_strategy(STRATEGY)
    u = strat["universe"]
    load_from = (pd.Timestamp(start) - pd.DateOffset(years=2)).date().isoformat()
    p = panel_mod.load(a.db, start=load_from, end=end)
    elig = panel_mod.universe(p, u["min_price"], u["min_value"], u["value_days"], u["min_history"])
    scores = signals_mod.scores(p, strat)
    ref = backtest.run(p, scores, start, end, backtest.Config(top=int(strat["top"])))
    bench = backtest.metrics(ref["bench"])
    print("momentum_quality, %s %s to %s, Rs %s start, top tenth candidates" % (a.period, start, end, "{:,.0f}".format(CAPITAL)))
    print("%-26s CAGR %6.1f%%  Sharpe %5.2f  worst drawdown %6.1f%%" % (
        "equal-weight universe", bench["cagr"] * 100, bench["sharpe"], bench["max_drawdown"] * 100))
    index = index_metrics = None
    if a.vol_levels:
        import regimes as regimes_mod
        ix = regimes_mod.index_close(a.db, INDEX).reindex(p.dates).ffill()
        if ix.loc[start:end].isna().all():
            print("error: no %s closes stored for this period (research/scan.py update stores index closes)" % INDEX,
                  file=sys.stderr)
            return 2
        index = ix.values
        index_metrics = backtest.metrics(ix.loc[start:end].dropna() / ix.loc[start:end].dropna().iloc[0])
        print("%-26s CAGR %6.1f%%  Sharpe %5.2f  worst drawdown %6.1f%%  (price index, no dividends)" % (
            INDEX, index_metrics["cagr"] * 100, index_metrics["sharpe"], index_metrics["max_drawdown"] * 100))
    runs = [("vol", lvl) for lvl in ("low", "medium", "high")] if a.vol_levels else EXIT_RUNS if a.exit_test else RUNS if a.all else [("top", a.stocks)] if a.stocks else [("risk", a.risk)]
    for kind, value in runs:
        entry, m = run_one(p, scores, elig, a.period, kind, value, bench, a.note, runs_log=runs_log, index=index,
                           index_metrics=index_metrics)
        print(report(a.period, entry, m))
        if kind == "hold":
            w = logged(a.period, "risk:" + value, runs_log)
            yr = lambda e, y: "%+.1f%%" % (100 * e["yearly"][str(y)]) if e.get("yearly") and str(y) in e["yearly"] \
                else "%+.1f%%" % (100 * e["yearly"][y]) if e.get("yearly") and y in e["yearly"] else "n/a"
            print("  with the stop and target: Sharpe %.2f against %.2f, worst drawdown %.1f%% against %.1f%%, "
                  "2016 %s against %s, 2022 %s against %s: exits %s" % (
                      w["sharpe"], entry["sharpe"], 100 * w["max_drawdown"], 100 * entry["max_drawdown"],
                      yr(w, 2016), yr(entry, 2016), yr(w, 2022), yr(entry, 2022),
                      "pass this period" if exits_pass(w, entry) else "do not pass this period"))
    print("logged to research/runs.jsonl; record the results in research/FINDINGS.md, whatever they show")
    return 0


if __name__ == "__main__":
    sys.exit(main())
