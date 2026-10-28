import datetime as dt
import io
import json
import importlib.util
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT, load  # noqa: E402

bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import pandas as pd  # noqa: E402
    import panel  # noqa: E402
    import profiles  # noqa: E402
    import signals  # noqa: E402
    from test_research import weekdays  # noqa: E402


def store(db, days, paths):
    """paths: symbol -> list of (open, high, low, close) in rupees, one per day."""
    rows = []
    for k, (sym, bars) in enumerate(paths.items()):
        prev = bars[0][3]
        for d, (o, h, l, c) in zip(days, bars):
            p = lambda x: int(round(x * 100))
            rows.append({"date": d.isoformat(), "isin": "INE%03dP01010" % k, "symbol": sym, "series": "EQ",
                         "open": p(o), "high": p(h), "low": p(l), "close": p(c), "last": p(c), "prev_close": p(prev),
                         "volume": 1_000_000, "value": 1_000_000 * p(c), "trades": 100})
            prev = c
    bc.store_day(db, rows)
    db.commit()


def path(n, growth, events=None, wiggle=0.0):
    """A steady stock; events: day -> ("gap", factor) moves the open, low, high and close by factor."""
    out, c = [], 100.0
    for i in range(n):
        prev = c
        c = prev * (1 + growth + (wiggle if i % 2 else -wiggle))
        o = prev
        if events and i in events:
            o = c = prev * events[i]
            out.append((o, o * 1.005, o * 0.995, c))
            continue
        out.append((o, max(o, c) * 1.01, min(o, c) * 0.99, c))
    return out


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Profiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "m.db")
        cls.days = weekdays(dt.date(2013, 1, 1), 340)
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in cls.days])
        ends = list(panel.month_ends(idx))
        cls.r = next(int(idx.get_loc(e)) for e in ends if idx.get_loc(e) >= 290)  # the rebalance tested
        r, n = cls.r, len(cls.days)
        db = ca.connect(cls.path)
        store(db, cls.days, {
            "RISE": path(n, 0.003, wiggle=0.002),
            "CRASH": path(n, 0.004, {r + 3: 0.88}, wiggle=0.002),    # gaps 12% down two sessions after entry
            "JUMP": path(n, 0.0035, {r + 5: 1.15}, wiggle=0.002),    # gaps 15% up: through its target
            "GAP": path(n, 0.0045, {r + 1: 1.05}, wiggle=0.002),     # opens 5% up at entry: above the limit
            "FLAT": path(n, 0.0, wiggle=0.002),
        })
        db.close()
        cls.p = panel.load(cls.path)
        cls.elig = panel.universe(cls.p, min_value=1)
        cls.scores = signals.momentum_quality(cls.p)
        cls.sym = {s: n for s, n in cls.p.names.items()}
        profiles.CANDIDATE_SHARE, cls.share = 1.0, profiles.CANDIDATE_SHARE  # a handful of stocks: all are candidates

    @classmethod
    def tearDownClass(cls):
        profiles.CANDIDATE_SHARE = cls.share
        cls.tmp.cleanup()

    def run_(self, stocks, risk=None):
        return profiles.simulate(self.p, self.scores, self.elig, self.days[self.r - 5].isoformat(),
                                 self.days[self.r + 15].isoformat(), stocks, risk)

    def trades(self, res, sym, side=None):
        return [t for t in res["trades"] if self.sym[t[1]] == sym and (side is None or t[2] == side)]

    def test_risk_level_stops_targets_and_the_limit(self):
        res = self.run_(5, "medium")
        crash = self.trades(res, "CRASH", "sell")
        self.assertEqual([t[5] for t in crash], ["stop"])  # sold at the gapped open, below its stop
        self.assertEqual(crash[0][0], self.days[self.r + 3].isoformat())
        jump = self.trades(res, "JUMP", "sell")
        self.assertEqual([t[5] for t in jump], ["target"])
        self.assertEqual(jump[0][0], self.days[self.r + 5].isoformat())
        self.assertEqual(self.trades(res, "GAP", "buy"), [])  # the open was above the buy limit
        self.assertGreaterEqual(res["stats"]["unfilled"], 1)
        self.assertEqual(res["stats"]["stops"], 1)
        self.assertEqual(res["stats"]["targets"], 1)
        for t in self.trades(res, "RISE", "buy"):
            self.assertEqual(t[0], self.days[self.r + 1].isoformat())  # bought at the next open

    def test_loss_at_the_stop_stays_within_the_risk_level(self):
        res = self.run_(5, "medium")
        value = res["nav"].loc[pd.Timestamp(self.days[self.r])] * profiles.CAPITAL  # sized on this
        buy = self.trades(res, "CRASH", "buy")[0]
        sold = self.trades(res, "CRASH", "sell")[0]
        loss = buy[3] * (buy[4] - sold[4]) / value
        # a 12% gap goes through the stop, so the loss can pass 1%; it stays far below the gap itself
        self.assertGreater(loss, 0)
        self.assertLess(loss, 0.12 / 5)

    def test_concentration_holds_equal_money_without_stops(self):
        res = self.run_(3)
        self.assertEqual(res["stats"]["stops"], 0)
        self.assertEqual(res["stats"]["targets"], 0)
        buys = [t for t in res["trades"] if t[2] == "buy"]
        self.assertLessEqual(len({t[1] for t in buys}), 3)
        values = [t[3] * t[4] for t in buys]
        self.assertLess(max(values) - min(values), 0.02 * profiles.CAPITAL)  # equal slices, whole shares
        self.assertGreater(res["stats"]["invested_avg"], 0)
        self.assertEqual(res["stats"]["rebalances"], 1)

    def test_never_more_cash_than_it_has(self):
        for risk in (None, "low", "high"):
            res = self.run_(1 if risk == "high" else 5, risk)
            self.assertTrue((res["nav"] > 0).all())

    def test_charges_take_adjusted_fractional_units(self):
        fee = profiles.charges_fn("zerodha")
        whole = fee("sell", 100, 250.0)
        self.assertGreater(whole, 0)
        self.assertAlmostEqual(fee("sell", 66.6667, 375.0), whole, places=1)  # same turnover after a 3:2 bonus
        self.assertEqual(fee("buy", 1e-12, 100.0), 0.0)

    def test_basket_volatility(self):
        import numpy as np
        C = np.array([[100.0, 50.0]] + [[100.0 * (1.01 if k % 2 else 0.99) ** 0, 50.0] for k in range(10)])
        self.assertIsNone(profiles.basket_vol(C, 10, [0, 1], days=63))  # too few sessions
        C = np.cumprod(np.where(np.arange(100) % 2, 1.01, 0.99))[:, None] * np.ones((1, 2)) * 100
        v = profiles.basket_vol(C, 99, [0, 1])
        self.assertAlmostEqual(v, 0.01 * np.sqrt(252), delta=0.02)

    def test_volatility_target_puts_the_rest_in_the_index(self):
        import numpy as np
        n = len(self.p.dates)
        index = 1000.0 * 1.001 ** np.arange(n)
        vol = profiles.basket_vol(self.p.close.values, self.r, [self.p.close.columns.get_loc(c) for c in self.p.close.columns])
        res = profiles.simulate(self.p, self.scores, self.elig, self.days[self.r - 5].isoformat(),
                                self.days[self.r + 15].isoformat(), 3, None, target_vol=vol / 2, index=index)
        st = res["stats"]
        self.assertLess(st["exposure_avg"], 0.8)
        self.assertGreater(st["index_avg"], 0.1)  # the rest went into the index, not cash
        self.assertTrue((res["nav"] > 0).all())
        full = profiles.simulate(self.p, self.scores, self.elig, self.days[self.r - 5].isoformat(),
                                 self.days[self.r + 15].isoformat(), 3, None, target_vol=10.0, index=index)
        self.assertEqual(full["stats"]["exposure_avg"], 1.0)  # never above 100%
        self.assertLess(full["stats"]["index_avg"], 0.01)

    def test_v2_levels_fixed_in_findings(self):
        self.assertEqual(profiles.VOL_LEVELS, {"low": (20, 0.10), "medium": (5, 0.20), "high": (3, 0.30)})
        bench = {"cagr": 0.10, "sharpe": 0.5}
        self.assertFalse(profiles.passes_period("vol", "low", {"cagr": 0.12, "sharpe": 0.3}, bench))
        self.assertTrue(profiles.passes_period("vol", "high", {"cagr": 0.12, "sharpe": 0.3}, bench))

    def test_month_stats(self):
        idx = pd.to_datetime(["2020-01-31", "2020-02-28", "2020-03-31"])
        m = profiles.month_stats(pd.Series([1.1, 0.99, 1.0], index=idx))
        self.assertAlmostEqual(m["worst_month"], -0.1)
        self.assertAlmostEqual(m["months_losing"], 1 / 3)
        self.assertEqual(m["months"], 3)

    def test_the_holdout_is_never_offered(self):
        self.assertEqual(sorted(profiles.PERIODS), ["dev", "validation"])
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            profiles.main(["--period", "holdout", "--all", "--db", self.path])

    def test_offer_rule_and_fallback(self):
        bench = {"cagr": 0.10, "sharpe": 0.5}
        self.assertTrue(profiles.passes_period("risk", "high", {"cagr": 0.12, "sharpe": 0.3}, bench))
        self.assertFalse(profiles.passes_period("risk", "high", {"cagr": 0.09, "sharpe": 0.9}, bench))
        self.assertFalse(profiles.passes_period("risk", "low", {"cagr": 0.12, "sharpe": 0.3}, bench))
        self.assertTrue(profiles.passes_period("risk", "low", {"cagr": 0.12, "sharpe": 0.6}, bench))
        self.assertEqual(profiles.safest_offered("high", {"high": False, "medium": True, "low": True}), "medium")
        self.assertEqual(profiles.safest_offered("high", {"high": False, "medium": False, "low": True}), "low")
        self.assertEqual(profiles.safest_offered("medium", {"high": True, "medium": True, "low": True}), "medium")
        self.assertIsNone(profiles.safest_offered("low", {"low": False}))

    def test_exit_test_holds_the_same_stocks_without_stops(self):
        held = profiles.simulate(self.p, self.scores, self.elig, self.days[self.r - 5].isoformat(),
                                 self.days[self.r + 15].isoformat(), 5, "medium", use_exits=False)
        self.assertEqual(held["stats"]["stops"], 0)
        self.assertEqual(held["stats"]["targets"], 0)
        with_exits = self.run_(5, "medium")
        buys = lambda r: sorted((t[1], round(t[3], 6)) for t in r["trades"] if t[2] == "buy")
        self.assertEqual(buys(held), buys(with_exits))  # same picks and quantities: only the exits differ
        self.assertEqual([t[5] for t in self.trades(held, "CRASH", "sell")], [])  # held through the gap

    def test_exits_pass_rule(self):
        self.assertTrue(profiles.exits_pass({"sharpe": 0.8, "max_drawdown": -0.2}, {"sharpe": 0.7, "max_drawdown": -0.3}))
        self.assertFalse(profiles.exits_pass({"sharpe": 0.8, "max_drawdown": -0.4}, {"sharpe": 0.7, "max_drawdown": -0.3}))
        self.assertFalse(profiles.exits_pass({"sharpe": 0.6, "max_drawdown": -0.2}, {"sharpe": 0.7, "max_drawdown": -0.3}))

    def test_exit_test_needs_the_logged_level_runs(self):
        log = os.path.join(self.tmp.name, "runs.jsonl")
        with open(log, "w") as fh:
            fh.write(json.dumps({"at": "2026-10-29T00:00:00", "period": "dev", "profile": "risk:low", "sharpe": 1}) + "\n")
        with redirect_stderr(io.StringIO()) as err:
            code = profiles.main(["--period", "dev", "--exit-test", "--db", self.path], runs_log=log)
        self.assertEqual(code, 2)
        self.assertIn("medium, high", err.getvalue())
        self.assertEqual(profiles.logged("dev", "risk:low", log)["sharpe"], 1)
        self.assertIsNone(profiles.logged("validation", "risk:low", log))

    def test_runs_fixed_in_findings(self):
        self.assertEqual(profiles.RUNS, [("top", 1), ("top", 3), ("top", 5), ("top", 20),
                                         ("risk", "low"), ("risk", "medium"), ("risk", "high")])


if __name__ == "__main__":
    unittest.main()
