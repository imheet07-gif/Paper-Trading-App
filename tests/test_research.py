import datetime as dt
import importlib.util
import os
import sys
import tempfile
import unittest

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT, load  # noqa: E402

bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import backtest  # noqa: E402
    import evaluate  # noqa: E402
    import panel  # noqa: E402
    import regimes  # noqa: E402
    import signals  # noqa: E402
    import numpy as np  # noqa: E402


def weekdays(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def market(db, days):
    """Five made-up stocks over `days` weekdays, prices in paise.

    UPUP rises 0.2% a day, FLAT stays at 100, DOWN falls 0.1% a day, GONE
    rises fastest but stops trading 60 days before the end (delisted), and
    LOCK rises fast but is locked at its upper circuit on every day after
    day 300 (high == low, +5%), so it can never be bought then.
    """
    spec = {"UPUP": 0.002, "FLAT": 0.0, "DOWN": -0.001, "GONE": 0.004, "LOCK": 0.003}
    rows = []
    for k, (sym, g) in enumerate(spec.items()):
        isin = "INE%03dT01010" % k
        px = 100.0
        for i, d in enumerate(days):
            if sym == "GONE" and i >= len(days) - 60:
                break
            prev = px
            px = px * (1.05 if sym == "LOCK" and i > 300 else 1 + g)
            p = int(round(px * 100))
            hi = lo = p if (sym == "LOCK" and i > 300) else None
            rows.append({"date": d.isoformat(), "isin": isin, "symbol": sym, "series": "EQ",
                         "open": int(round(prev * 100)) if hi is None else p, "high": hi or int(p * 1.01),
                         "low": lo or int(p * 0.99), "close": p, "last": p, "prev_close": int(round(prev * 100)),
                         "volume": 1_000_000, "value": 1_000_000 * p, "trades": 100})
    bc.store_day(db, rows)
    db.commit()


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Research(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "m.db")
        db = ca.connect(cls.path)
        cls.days = weekdays(dt.date(2013, 1, 1), 520)
        market(db, cls.days)
        # an index renamed halfway, as CNX 500 became Nifty 500: rises, then falls below its average
        level = 100.0
        for i, d in enumerate(cls.days):
            level *= 1.002 if i < 400 else 0.99
            name = "CNX 500" if i < 250 else "Nifty 500"
            db.execute("INSERT INTO index_prices VALUES (?,?,?,?,?,?,?,?,?)",
                       (d.isoformat(), name, level, level, level, level, None, None, None))
        db.commit()
        db.close()
        cls.p = panel.load(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def sym(self, sid):
        return self.p.names[sid]

    def test_panel_shape_and_universe_needs_history(self):
        self.assertEqual(len(self.p.dates), 520)
        u = panel.universe(self.p, min_value=1)
        self.assertFalse(u.iloc[100].any())  # under 250 days of history
        self.assertTrue(u.iloc[300].any())

    def test_funds_and_etfs_are_never_eligible(self):
        import pandas as pd
        p = self.p
        etf = p.close.columns[0]
        renamed = {etf: "INF" + etf[3:]}
        for name in ("close", "raw_close", "value"):
            setattr(p, name, getattr(p, name).rename(columns=renamed))
        p.eq, p.data_issue = p.eq.rename(columns=renamed), p.data_issue.rename(columns=renamed)
        try:
            u = panel.universe(p, min_value=1)
            self.assertFalse(u["INF" + etf[3:]].any())
            self.assertTrue(u.drop(columns=["INF" + etf[3:]]).iloc[300].any())
        finally:
            back = {v: k for k, v in renamed.items()}
            for name in ("close", "raw_close", "value"):
                setattr(p, name, getattr(p, name).rename(columns=back))
            p.eq, p.data_issue = p.eq.rename(columns=back), p.data_issue.rename(columns=back)

    def test_momentum_ranks_the_trend_first(self):
        s = signals.momentum_12_1(self.p).iloc[400].dropna().sort_values(ascending=False)
        self.assertEqual([self.sym(x) for x in s.index[:2]], ["LOCK", "GONE"])
        self.assertEqual(self.sym(s.index[-1]), "DOWN")

    def test_costs_are_charged(self):
        buy, sell = backtest.cost_rates()
        self.assertAlmostEqual(buy, 0.001 + 0.00015 + 0.0000307 + 0.000001 + 0.18 * (0.0000307 + 0.000001), places=9)
        self.assertGreater(sell, 0.001)

    def test_backtest_holds_winners_exits_delisting_and_respects_circuits(self):
        scores = signals.momentum_12_1(self.p)
        start = self.days[310].isoformat()  # after LOCK locks at its circuit on every day
        cfg = backtest.Config(top=2, capital=1_000_000)
        res = backtest.run(self.p, scores, start, None, cfg, rebalance_dates=None)
        bought = {self.sym(t[1]) for t in res["trades"] if t[2] == "buy"}
        self.assertIn("GONE", bought)
        self.assertNotIn("DOWN", bought)
        self.assertGreaterEqual(res["stats"]["delist_exits"], 1)  # GONE stopped trading while held
        self.assertGreater(res["stats"]["blocked_buys"], 0)  # LOCK was wanted but locked at its circuit
        lock_buys = [t for t in res["trades"] if t[2] == "buy" and self.sym(t[1]) == "LOCK"]
        self.assertEqual(lock_buys, [])
        self.assertGreater(res["stats"]["costs"], 0)
        self.assertTrue((res["nav"] > 0).all())

    def test_metrics_and_deflated_sharpe(self):
        import pandas as pd
        nav = pd.Series([1.0 * (1.0005 ** i) for i in range(504)], index=pd.bdate_range("2014-01-01", periods=504))
        m = backtest.metrics(nav, rf=0.0)
        self.assertAlmostEqual(m["cagr"], 1.0005 ** 252 - 1, places=3)
        self.assertEqual(m["max_drawdown"], 0)
        low = backtest.deflated_sharpe(1.0, 2000, 0.0, 3.0, [1.0, 0.9, 1.1, 0.95, 1.05] * 20)
        high = backtest.deflated_sharpe(3.0, 2000, 0.0, 3.0, [1.0, 0.9, 1.1, 0.95, 1.05] * 20)
        self.assertLess(low, high)

    def test_forward_returns_start_at_the_next_open(self):
        f = evaluate.forward_returns(self.p, horizon=5)
        o = self.p.open
        sid = [s for s in o.columns if self.sym(s) == "UPUP"][0]
        self.assertAlmostEqual(f[sid].iloc[100], o[sid].iloc[106] / o[sid].iloc[101] - 1)

    def test_ic_sees_a_perfect_signal(self):
        import pandas as pd
        fwd = evaluate.forward_returns(self.p)
        elig = panel.universe(self.p, min_value=1)
        dates = self.p.dates[260:400:21]
        rep = evaluate.ic_report(fwd, fwd, elig, dates)  # the future itself as the signal
        self.assertIsNone(rep)  # five stocks are too few to judge: at least 50 are required
        wide = pd.concat([fwd] * 12, axis=1)
        wide.columns = range(wide.shape[1])
        noise = pd.DataFrame(np.random.default_rng(1).normal(0, 1e-6, wide.shape), index=wide.index, columns=wide.columns)
        many = wide + noise
        rep = evaluate.ic_report(many, many, pd.DataFrame(True, index=wide.index, columns=wide.columns), dates)
        self.assertGreater(rep["ic_mean"], 0.99)

    def test_index_joined_across_rename_and_trend_filter(self):
        s = regimes.index_close(self.path, "Nifty 500")
        self.assertEqual(len(s), 520)  # both names, one series
        t = regimes.trend(self.p, self.path, below=0.0)
        self.assertTrue(t.iloc[:150].isna().all())  # no 200-day average yet
        self.assertEqual(t.iloc[399], 1.0)  # rising, above its average
        self.assertEqual(t.iloc[-1], 0.0)  # fallen below: go to cash
        half = regimes.trend(self.p, self.path, below=0.5)
        self.assertEqual(half.iloc[-1], 0.5)

    def test_exposure_keeps_the_rest_in_cash(self):
        import pandas as pd
        scores = signals.momentum_12_1(self.p)
        start = self.days[310].isoformat()
        zero = pd.Series(0.0, index=self.p.dates)
        res = backtest.run(self.p, scores, start, None, backtest.Config(top=2, cash_yield=0.06), exposure=zero)
        self.assertEqual([t for t in res["trades"] if t[2] == "buy"], [])
        years = len(res["nav"]) / 252
        self.assertAlmostEqual(res["nav"].iloc[-1], 1.06 ** years, places=3)  # all cash, at the cash yield

    def test_target_weights_equal_inverse_vol_cap_and_buffer(self):
        eq = backtest.target_weights(["A", "B", "C", "D"], {}, backtest.Config())
        self.assertEqual(eq, {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25})
        vol = {"A": 0.10, "B": 0.20, "C": 0.40, "D": float("nan")}
        iv = backtest.target_weights(["A", "B", "C", "D"], vol, backtest.Config(sizing="inverse_vol"))
        # 1/0.1 : 1/0.2 : 1/0.4 : 1/median(0.2) = 10 : 5 : 2.5 : 5
        self.assertAlmostEqual(iv["A"], 10 / 22.5)
        self.assertAlmostEqual(iv["C"], 2.5 / 22.5)
        self.assertAlmostEqual(iv["D"], iv["B"])
        self.assertAlmostEqual(sum(iv.values()), 1.0)
        capped = backtest.target_weights(["A", "B", "C", "D"], vol,
                                         backtest.Config(sizing="inverse_vol", max_weight=0.3, cash_buffer=0.1))
        self.assertAlmostEqual(capped["A"], 0.27)  # 30% of the 90% invested
        self.assertAlmostEqual(sum(capped.values()), 0.9)
        self.assertTrue(all(x <= 0.27 + 1e-12 for x in capped.values()))
        all_capped = backtest.target_weights(["A", "B"], {}, backtest.Config(max_weight=0.2))
        self.assertAlmostEqual(sum(all_capped.values()), 0.4)  # the rest stays in cash
        with self.assertRaises(ValueError):
            backtest.Config(sizing="kelly")
        with self.assertRaises(ValueError):
            backtest.Config(cash_buffer=1.0)

    def test_cash_buffer_and_cap_leave_money_in_cash(self):
        scores = signals.momentum_12_1(self.p)
        start = self.days[310].isoformat()
        full = backtest.run(self.p, scores, start, None, backtest.Config(top=2))
        held = backtest.run(self.p, scores, start, None, backtest.Config(top=2, cash_buffer=0.5))
        self.assertTrue(held["trades"])
        first_full = sum(t[3] * t[4] for t in full["trades"] if t[2] == "buy" and t[0] == full["trades"][0][0])
        first_held = sum(t[3] * t[4] for t in held["trades"] if t[2] == "buy" and t[0] == held["trades"][0][0])
        self.assertAlmostEqual(first_held / first_full, 0.5, places=2)
        self.assertEqual(backtest.Config(sizing="inverse_vol", max_weight=0.1).as_dict()["max_weight"], 0.1)

    def test_evaluate_reports_a_signal_too_sparse_to_rank(self):
        import io
        import pandas as pd
        from contextlib import redirect_stdout
        logged = []
        sparse = pd.DataFrame(float("nan"), index=self.p.dates, columns=self.p.close.columns)
        sparse.iloc[300:, 0] = 1.0  # one stock scored: never 50, so no IC
        signals.SIGNALS["_sparse"] = lambda p: sparse
        real_log, real_load = backtest.log_run, panel.load
        backtest.log_run = logged.append  # never write the real run log from a test
        panel.load = lambda *a, **k: self.p
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                code = evaluate.main(["--period", "dev", "--signals", "_sparse", "--db", self.path])
        finally:
            backtest.log_run, panel.load = real_log, real_load
            del signals.SIGNALS["_sparse"]
        self.assertEqual(code, 0)
        self.assertIn("n/a", out.getvalue())
        self.assertIsNone(logged[0]["ic"])

    def test_composite_is_weighted_average_of_ranks(self):
        eq = signals.composite(self.p, {"momentum_quality": 1, "low_volatility": 1})
        blend = signals.momentum_lowvol(self.p)
        a, b = eq.iloc[400].dropna(), blend.iloc[400].dropna()
        self.assertEqual(list(a.index), list(b.index))
        self.assertTrue(((a - b).abs() < 1e-12).all())
        only = signals.composite(self.p, {"momentum_12_1": 3})
        self.assertTrue((only.iloc[400].dropna() == signals.momentum_12_1(self.p).rank(axis=1, pct=True).iloc[400].dropna()).all())
        with self.assertRaises(ValueError):
            signals.composite(self.p, {"momentum_12_1": -1})
        with self.assertRaises(KeyError):
            signals.composite(self.p, {"astrology": 1})
        self.assertEqual(signals.describe({"composite": {"a": 1, "b": 3}}), "25% a + 75% b")

    def test_holdout_needs_the_flag(self):
        with open(os.devnull, "w") as null:
            old, sys.stderr = sys.stderr, null
            try:
                self.assertEqual(backtest.main(["--signal", "momentum_12_1", "--period", "holdout",
                                                "--db", self.path]), 2)
            finally:
                sys.stderr = old


if __name__ == "__main__":
    unittest.main()
