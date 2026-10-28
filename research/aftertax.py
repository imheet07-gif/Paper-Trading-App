"""After-tax check: a tested strategy against an index fund, year by year.

Settings fixed in FINDINGS.md (2026-10-29) before any run:

  strategy   momentum_quality_top20 exactly as research/strategies.json and
             research/backtest.py run it (top 20, month-end rebalance, next
             open fills, 15 bps slippage, charges from core/charges.json)
  tax        core/tax.py on the backtest's own fills: sells matched oldest
             first, each financial year's tax paid from cash on its last
             session's close (31 March or the last trading day before),
             dividends credited on the ex-date from the store's corporate
             actions and taxed by tax_rates.json; at the end of the window
             everything is sold at the last close less slippage and charges,
             and that year's tax is paid
  fund       an index fund on the stored price index plus its daily
             dividend yield (a stand-in for the total return index, which
             the store lacks), less the expense ratio typed with
             --fund-expense-pct; bought at the first close of the window,
             held, sold at the last; a growth plan, so no dividends reach
             the holder
  windows    dev_2016 (2016 to 2020, Nifty 50: the momentum index is
             stored only from August 2020) and validation (2021 to 2023,
             Nifty200 Momentum 30); never the holdout
  money      Rs 10,00,000 by default (--capital); the exemption limit is in
             rupees, so the result depends on the amount

The slab rate for dividends is typed in (--slab-pct or a prompt) and never
written anywhere: the run log keeps the result with dividends untaxed.
Numbers are float estimates of a simulation, never accounting.

  python3 research/aftertax.py --period validation --fund-expense-pct 0.3
"""

import argparse
import datetime as dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import backtest  # noqa: E402
import lots  # noqa: E402
import panel as panel_mod  # noqa: E402
import signals as signals_mod  # noqa: E402
import tax as tax_mod  # noqa: E402
from money import D  # noqa: E402

STRATEGY = "momentum_quality_top20"
SIGNAL, TOP = "momentum_quality", 20
PERIODS = {"dev_2016": backtest.PERIODS["dev_2016"], "validation": backtest.PERIODS["validation"]}  # never the holdout
FUNDS = {"dev_2016": "Nifty 50", "validation": "Nifty200 Momentum 30"}
CAPITAL = 1_000_000.0
FUND_ID = "FUND"


def _px(x):
    return D("%.4f" % x)


def _qty(x):
    return D("%.6f" % x)


class TaxHook:
    """backtest.run's on_day: turns its fills into broker-neutral trades,
    credits dividends, and pays each financial year's tax at its last close."""

    def __init__(self, p, cfg, dividends, end, slab_pct=None, fmv=None, pay_tax=True, rates=None):
        self.p, self.cfg, self.end = p, cfg, pd.Timestamp(end)
        self.dividends = dividends  # {Timestamp: [(sid, rupees per share)]}
        self.slab, self.fmv, self.pay_tax = slab_pct, fmv or {}, pay_tax
        self.rates = rates or tax_mod.load_rates()
        self.trades, self.seen, self.open = [], 0, {}
        self.div_paid, self.tax_paid, self.prev_units = [], {}, {}
        self.ratio = (p.close / p.raw_close).values  # adjusted units -> shares actually held
        self.col = {s: k for k, s in enumerate(p.close.columns)}

    def _record(self, day, s, side, qty, price):
        self.trades.append({"date": day, "isin": s, "symbol": self.p.names.get(s, s), "side": side,
                            "qty": qty, "price": price})
        self.open[s] = self.open.get(s, D(0)) + (qty if side == "buy" else -qty)

    def _take_fills(self, units):
        for day, s, side, q, px in self.trades_ref[self.seen:]:
            day = dt.date.fromisoformat(day)
            have = self.open.get(s, D(0))
            if side == "buy":
                self._record(day, s, "buy", _qty(q), _px(px))
            elif side == "sell":
                qty = have if s not in units else min(_qty(q), have)  # a full exit sells exactly what is open
                if qty > 0:
                    self._record(day, s, "sell", qty, _px(px))
            elif side == "delist" and have > 0:
                self._record(day, s, "sell", have, _px(px * (1 - self.cfg.delist_haircut)))
        self.seen = len(self.trades_ref)

    def tax_for(self, fy):
        for y in tax_mod.assess(self.trades, self.div_paid, self.slab, self.fmv, self.rates):
            if y["fy"] == fy:
                return y
        return None

    def __call__(self, i, d, units, trades):
        self.trades_ref = trades
        self._take_fills(units)
        cash = 0.0
        for s, per_share in self.dividends.get(d, ()):
            u = self.prev_units.get(s)
            if u and i:
                r = self.ratio[i - 1, self.col[s]]
                if not np.isnan(r):
                    amount = u * r * per_share
                    self.div_paid.append((d.date(), D("%.2f" % amount)))
                    cash += amount
        self.prev_units = dict(units)
        nxt = self.p.dates[i + 1] if i + 1 < len(self.p.dates) else None
        fy = lots.financial_year(d.date())
        if self.pay_tax and nxt is not None and nxt <= self.end and lots.financial_year(nxt.date()) != fy:
            y = self.tax_for(fy)  # the year's last session: its tax leaves the portfolio at this close
            due = float(y["tax"]) if y else 0.0
            self.tax_paid[fy] = due
            cash -= due
        return cash


def load_dividends(p, db_path, start, end):
    """{Timestamp ex-date: [(security id, rupees per share)]} from the store."""
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT ex_date, isin, dividend FROM corporate_actions WHERE kind = 'dividend' "
                      "AND dividend IS NOT NULL AND isin IS NOT NULL AND ex_date BETWEEN ? AND ?", (start, end)).fetchall()
    db.close()
    out = {}
    cols = set(p.close.columns)
    for ex, isin, amount in rows:
        sid = panel_mod.ca.chain(isin, p.link_map)[-1]
        if sid in cols:
            out.setdefault(pd.Timestamp(ex), []).append((sid, float(amount)))
    return out


def fmv_2018(p):
    """{security id: highest adjusted price on 31 January 2018, or the last earlier day it traded}."""
    day = pd.Timestamp("2018-01-31")
    if p.dates[0] > day:
        return {}
    upto = p.high.loc[:day]
    last = upto.ffill().iloc[-1]
    return {s: _px(v) for s, v in last.items() if not np.isnan(v)}


def strategy(p, start, end, capital, slab_pct, pay_tax, rates=None):
    """Run the strategy with the tax hook; sell everything at the last close.
    Returns {"nav": Series of portfolio value in rupees, "final": value after
    the last sale and its year's tax, "years": core/tax.py's years, "hook"}."""
    cfg = backtest.Config(top=TOP, capital=capital)
    scores = signals_mod.SIGNALS[SIGNAL](p)
    divs = load_dividends(p, p.db_path, start, end)
    hook = TaxHook(p, cfg, divs, end, slab_pct, fmv_2018(p), pay_tax, rates)
    res = backtest.run(p, scores, start, end, cfg, on_day=hook)
    nav = res["nav"] * capital
    # liquidate at the last close
    i = p.dates.get_loc(nav.index[-1])
    last = p.close.iloc[: i + 1].ffill().iloc[-1]
    _, sell_cost = backtest.cost_rates()
    held = {s: float(q) for s, q in hook.open.items() if q > 0}
    value = nav.iloc[-1]
    day = nav.index[-1].date()
    for s, q in held.items():
        fill = last[s] * (1 - cfg.slippage)
        hook._record(day, s, "sell", hook.open[s], _px(fill))
        value -= q * last[s] - q * fill + q * fill * sell_cost + cfg.dp_charge
    years, final_tax = [], 0.0
    if pay_tax:
        years = tax_mod.assess(hook.trades, hook.div_paid, hook.slab, hook.fmv, hook.rates)
        fy = lots.financial_year(day)
        final_tax = next((float(y["tax"]) for y in years if y["fy"] == fy), 0.0)
        hook.tax_paid[fy] = final_tax
    return {"nav": nav, "final": value - final_tax, "years": years, "hook": hook}


def fund_nav(db_path, name, start, end, expense_pct):
    """Daily value of 1 rupee in an index fund: price index plus the stored
    dividend yield, less the expense ratio, both accrued by calendar day."""
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT date, close, div_yield FROM index_prices WHERE name = ? AND date <= ? ORDER BY date",
                      (name, end)).fetchall()
    db.close()
    if not rows or rows[0][0] > start:
        raise ValueError("%s is stored from %s, after the window's start %s" % (name, rows[0][0] if rows else "never", start))
    df = pd.DataFrame(rows, columns=["date", "close", "div_yield"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    gaps = df.index.to_series().diff().dt.days
    if (gaps[df.index >= pd.Timestamp(start)] > 10).any():
        raise ValueError("%s has a gap of more than 10 days inside the window" % name)
    df = df[df.index >= df.index[df.index.searchsorted(pd.Timestamp(start)) - 1]] if df.index[0] < pd.Timestamp(start) else df
    days = df.index.to_series().diff().dt.days.fillna(0).values
    price = df["close"].values
    dy = df["div_yield"].ffill().fillna(0).values / 100
    nav = [1.0]
    for k in range(1, len(df)):
        nav.append(nav[-1] * price[k] / price[k - 1] * (1 + (dy[k - 1] - expense_pct / 100) * days[k] / 365))
    s = pd.Series(nav, index=df.index)
    return s[s.index >= pd.Timestamp(start)] / s[s.index >= pd.Timestamp(start)].iloc[0]


def fund(db_path, name, start, end, capital, expense_pct, rates=None):
    """Buy at the first close, sell at the last; tax on the one sale."""
    nav = fund_nav(db_path, name, start, end, expense_pct) * 1000  # units start at Rs 1,000, so prices keep their decimals
    units = _qty(capital / nav.iloc[0])
    fmv = {}
    if nav.index[0] <= pd.Timestamp("2018-01-31") <= nav.index[-1]:
        fmv[FUND_ID] = _px(nav.loc[:pd.Timestamp("2018-01-31")].iloc[-1])  # section 55(2)(ac): the unit's NAV that day
    trades = [{"date": nav.index[0].date(), "isin": FUND_ID, "symbol": name, "side": "buy", "qty": units,
               "price": _px(nav.iloc[0])},
              {"date": nav.index[-1].date(), "isin": FUND_ID, "symbol": name, "side": "sell", "qty": units,
               "price": _px(nav.iloc[-1])}]
    years = tax_mod.assess(trades, (), None, fmv, rates)
    tax = sum(float(y["tax"]) for y in years)
    value = nav / nav.iloc[0] * capital
    return {"nav": value, "final": value.iloc[-1] - tax, "years": years}


def cagr(start_value, end_value, first, last):
    years = (last - first).days / 365.25
    return (end_value / start_value) ** (1 / years) - 1 if years > 0 else float("nan")


def fy_table(nav, years, tax_paid, final=None):
    """Per financial year: return before tax, tax, return after tax. nav is
    the after-tax run's value series, which already has each year's tax taken
    off at its last close; the last year's tax is in `final`."""
    by = {y["fy"]: y for y in years}
    out, prev = [], None
    fys = list(nav.groupby([lots.financial_year(d.date()) for d in nav.index]))
    for n, (fy, s) in enumerate(fys):
        start = prev if prev is not None else s.iloc[0]
        t = tax_paid.get(fy, 0.0)
        in_nav = 0.0 if n == len(fys) - 1 and final is not None else t  # the last year's tax is paid after the sale
        end = s.iloc[-1] + in_nav - t  # value after this year's tax
        y = by.get(fy, {})
        out.append({"fy": fy, "start": start, "end": end, "tax": t,
                    "short": float(y.get("short", 0)), "long": float(y.get("long", 0)),
                    "dividends": float(y.get("dividends", 0)),
                    "carried": float(sum(c["amount"] for c in y.get("carried_forward", []))),
                    "before": (end + t) / start - 1 if start else float("nan"),
                    "after": end / start - 1 if start else float("nan")})
        prev = end
    if final is not None and out:
        out[-1]["after"] = final / out[-1]["start"] - 1
    return out


def main(argv=None):
    from money import inr
    ap = argparse.ArgumentParser(description="After-tax check: momentum_quality_top20 against an index fund.")
    ap.add_argument("--period", required=True, choices=sorted(PERIODS))
    ap.add_argument("--fund-expense-pct", type=float, required=True,
                    help="the fund's expense ratio in percent a year, from the fund's own factsheet")
    ap.add_argument("--index", help="price index the fund tracks (default: %s)" % ", ".join("%s %s" % kv for kv in FUNDS.items()))
    ap.add_argument("--capital", type=float, default=CAPITAL)
    ap.add_argument("--slab-pct", type=float, help="your slab rate for dividends from April 2020; asked if left out; never stored")
    ap.add_argument("--db", default=panel_mod.bhavcopy.DEFAULT_DB)
    ap.add_argument("--note", default="", help="why this run (kept in the run log)")
    a = ap.parse_args(argv)
    start, end = PERIODS[a.period]
    name = a.index or FUNDS[a.period]
    slab = a.slab_pct
    if slab is None and end >= "2020-04-01":
        slab = float(input("Your income tax slab rate in percent (for dividends; not saved): ").strip())
    load_from = (pd.Timestamp(start) - pd.DateOffset(years=2)).date().isoformat()
    p = panel_mod.load(a.db, start=load_from, end=end)
    rates = tax_mod.load_rates()
    pre = strategy(p, start, end, a.capital, None, pay_tax=False, rates=rates)
    gains = strategy(p, start, end, a.capital, 0.0, pay_tax=True, rates=rates)
    mine = strategy(p, start, end, a.capital, slab, pay_tax=True, rates=rates) if slab else gains
    f = fund(a.db, name, start, end, a.capital, a.fund_expense_pct, rates)
    first, last = gains["nav"].index[0], gains["nav"].index[-1]
    out = {
        "strategy_cagr_pre_tax": cagr(a.capital, pre["final"], first, last),
        "strategy_cagr_after_gains_tax": cagr(a.capital, gains["final"], first, last),
        "fund_cagr_pre_tax": cagr(a.capital, f["nav"].iloc[-1], f["nav"].index[0], f["nav"].index[-1]),
        "fund_cagr_after_tax": cagr(a.capital, f["final"], f["nav"].index[0], f["nav"].index[-1]),
    }
    backtest.log_run({
        "at": dt.datetime.now().isoformat(timespec="seconds"), "signal": SIGNAL, "period": a.period,
        "profile": "after_tax", "config": {"engine": "aftertax.py", "strategy": STRATEGY, "capital": a.capital,
                                           "fund_index": name, "fund_expense_pct": a.fund_expense_pct},
        **out,
        "gains_tax_by_fy": {k: round(v, 2) for k, v in gains["hook"].tax_paid.items()},
        "dividends_received": round(sum(float(x) for _, x in gains["hook"].div_paid), 2),
        "dividend_tax": "not recorded: it depends on the slab rate, which is never stored",
        "note": a.note})
    print("After-tax check, %s %s to %s, Rs %s, %s against an index fund on %s (expense ratio %.2f%%)" % (
        a.period, start, end, inr(a.capital, 0, ""), STRATEGY, name, a.fund_expense_pct))
    print("  strategy: dividends credited; each year's tax paid at its end; all sold %s" % last.date())
    print("  %-10s %9s %9s %12s %12s %12s %12s %9s" % ("year", "before", "after", "short gain", "long gain",
                                                     "dividends", "tax paid", "carried"))
    for r in fy_table(mine["nav"], mine["years"], mine["hook"].tax_paid, mine["final"]):
        print("  %-10s %8.1f%% %8.1f%% %12s %12s %12s %12s %9s" % (
            r["fy"], r["before"] * 100, r["after"] * 100, inr(r["short"], 0), inr(r["long"], 0),
            inr(r["dividends"], 0), inr(r["tax"], 0), inr(r["carried"], 0)))
    print("  fund: bought %s, sold %s, one sale taxed" % (f["nav"].index[0].date(), f["nav"].index[-1].date()))
    for y in f["years"]:
        print("    %s gain %s, tax %s" % (y["fy"], inr(y["short"] + y["long"], 0), inr(y["tax"], 0)))
    print("  %-34s %10s %10s" % ("", "strategy", "fund"))
    print("  %-34s %9.1f%% %9.1f%%" % ("CAGR before tax", out["strategy_cagr_pre_tax"] * 100, out["fund_cagr_pre_tax"] * 100))
    print("  %-34s %9.1f%% %9s" % ("CAGR after gains tax", out["strategy_cagr_after_gains_tax"] * 100, ""))
    print("  %-34s %9.1f%% %9.1f%%" % ("CAGR after all tax (your slab)", cagr(a.capital, mine["final"], first, last) * 100,
                                      out["fund_cagr_after_tax"] * 100))
    print("  %-34s %10s %10s" % ("value at the end, after tax", inr(mine["final"], 0), inr(f["final"], 0)))
    lead = "the fund" if f["final"] > mine["final"] else "the strategy"
    print("  After tax, %s ended ahead over this window." % lead)
    print("  Before surcharge; tax rules and what is left out: core/tax_rates.json. Estimates, not tax advice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
