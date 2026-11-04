"""research/harness.py: one testing machine for every idea, on a made-up store (offline)."""

import datetime as dt
import importlib.util
import io
import json
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT, load  # noqa: E402

bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import pandas as pd  # noqa: E402
    import backtest  # noqa: E402
    import harness  # noqa: E402
    import jobs  # noqa: E402
    import panel  # noqa: E402
    import profiles  # noqa: E402
    import retail  # noqa: E402
    from test_research import weekdays  # noqa: E402

STOCKS = 56  # the IC needs 50 scored stocks a date


def make_store(path, days, volume=lambda k: 1_000_000):
    """STOCKS made-up stocks with steady drifts from -0.1% to +0.2% a session plus noise, so momentum
    ranks them; a few start at Rs 4,000 to Rs 6,000 so whole shares matter at Rs 20,000."""
    rng = random.Random(7)
    rows = []
    for k in range(STOCKS):
        drift = -0.001 + 0.003 * k / (STOCKS - 1)
        c = 4000.0 + 50 * k if k % 9 == 0 else 50.0 + 10 * k
        prev = c
        for d in days:
            o = c
            c = max(5.0, c * (1 + drift + rng.gauss(0, 0.012)))
            p = lambda x: int(round(x * 100))
            rows.append({"date": d.isoformat(), "isin": "INE%03dQ01010" % k, "symbol": "S%02d" % k, "series": "EQ",
                         "open": p(o), "high": p(max(o, c) * 1.004), "low": p(min(o, c) * 0.996), "close": p(c),
                         "last": p(c), "prev_close": p(prev), "volume": volume(k), "value": volume(k) * p(c),
                         "trades": 100})
            prev = c
    db = ca.connect(path)
    bc.store_day(db, rows)
    db.commit()
    db.close()


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "m.db")
        cls.days = weekdays(dt.date(2012, 1, 2), 3 * 261)  # 2012 to 2014: a year of history, then two "dev" years
        make_store(cls.db, cls.days)
        cls.p = panel.load(cls.db)
        cls.rules = harness.check_rules(retail.load())
        cls.idea = harness.Idea("test_mq", "momentum_quality", "monthly", 5)
        cls.res = harness.run(cls.p, cls.idea, "dev", cls.rules, walk=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_reports_both_capital_levels(self):
        self.assertEqual(sorted(self.res["levels"]), ["1000000", "20000"])
        for lv in self.res["levels"].values():
            for k in ("cagr", "sharpe", "max_drawdown", "cagr_sold", "cagr_after_tax", "bar", "stats"):
                self.assertIn(k, lv)
            self.assertEqual(set(lv["bar"]), {"ic_t", "monotonic", "sharpe", "passes"})
        self.assertIsNotNone(self.res["ic"])
        self.assertGreater(self.res["ic"]["ic_mean"], 0)  # the drifts are what momentum ranks

    def test_whole_shares_and_never_more_than_the_cash(self):
        rebal, first = harness.rebalance_dates(self.p.dates, "2013-01-01", "2014-12-31", "monthly")
        elig = panel.universe(self.p)
        scores = self.idea.score_fn(self.p)
        res = profiles.simulate(self.p, scores, elig, first.date().isoformat(), "2014-12-31", 5, None,
                                capital=20000.0, rebalance_dates=rebal)
        buys = [t for t in res["trades"] if t[2] == "buy"]
        self.assertTrue(buys)
        for t in buys:
            self.assertAlmostEqual(t[3], round(t[3]), places=6)  # no corporate actions: units are shares
            self.assertGreaterEqual(round(t[3]), 1)
        self.assertTrue((res["nav"] > 0).all())

    def test_tax_is_paid_each_financial_year_and_after_tax_is_lower(self):
        for lv in self.res["levels"].values():
            self.assertLessEqual(lv["cagr_after_tax"], lv["cagr_sold"] + 1e-12)
        big = self.res["levels"]["1000000"]
        self.assertTrue(any(fy.startswith("FY2013-14") for fy in big["tax_paid"]))
        self.assertGreater(sum(big["tax_paid"].values()), 0)

    def test_minimum_capital_flags_the_small_account(self):
        mc = self.res["min_capital"]
        # five positions, each at least Rs 15.34 / 0.5% = Rs 3,068, and some picks cost Rs 4,000 or more a share
        self.assertGreater(mc["minimum"], 20000)
        self.assertIn(retail.capital_levels(self.rules)[0], mc["fails"])
        self.assertNotIn(retail.capital_levels(self.rules)[1], mc["fails"])

    def test_maximum_capital_and_the_share_of_own_picks_bought(self):
        xc = self.res["max_capital"]
        self.assertEqual(xc["picks"], 5 * 25)  # five own picks on each of 25 decision dates
        self.assertGreater(xc["maximum"], 1000000)  # every made-up stock trades about Rs 1 crore or more a day
        self.assertEqual(xc["fails"], [])
        for lv in self.res["levels"].values():
            self.assertTrue(0 <= lv["own_picks_bought"] <= 1)
        self.assertLess(self.res["levels"]["20000"]["own_picks_bought"], 1)  # Rs 4,000 a slot misses the dear ones
        text = harness.report(self.res, 1, 1, "B")
        self.assertIn("capital   maximum", text)
        self.assertIn("at the flagged Rs 20,000, following the skip rule got CAGR", text)

    def test_rebalance_dates(self):
        d = self.p.dates
        rebal, first = harness.rebalance_dates(d, "2013-01-01", "2013-12-31", "monthly")
        self.assertEqual(first, d[d.searchsorted(pd.Timestamp("2013-01-01")) - 1])  # the session before
        self.assertEqual(len(rebal), 13)
        rebal, first = harness.rebalance_dates(d, "2013-01-01", "2013-12-31", 10)
        self.assertEqual(rebal[0], first)
        self.assertTrue(all(d.get_loc(b) - d.get_loc(a) == 10 for a, b in zip(rebal, rebal[1:])))
        self.assertLessEqual(rebal[-1], pd.Timestamp("2013-12-31"))

    def test_ic_dates_never_read_past_the_data_end(self):
        d = self.p.dates
        rebal, _ = harness.rebalance_dates(d, "2013-01-01", "2014-12-31", "monthly")
        got = harness.ic_dates(d, rebal, "2013-01-01", "2014-12-31", 21, data_end="2013-12-31")
        self.assertTrue(got)
        cut = d.searchsorted(pd.Timestamp("2013-12-31"), "right")
        self.assertTrue(all(d.get_loc(x) + 22 < cut for x in got))

    def test_walk_forward_trains_on_the_past_and_tests_the_next_year(self):
        rules = json.loads(json.dumps(self.rules))
        rules["walk_forward"]["first_train_years"] = 1
        seen = []

        def fit(p, a, b):
            seen.append((a, b))
            return self.idea.score_fn(p)

        idea = harness.Idea("fitted", "momentum_quality", "monthly", 5, fit=fit)
        elig = panel.universe(self.p)
        scores = idea.score_fn(self.p)
        fwd = harness.evaluate.forward_returns(self.p, 21)
        ref = backtest.run(self.p, scores, "2012-12-31", "2014-12-31", backtest.Config(top=5))
        wf = harness.walk_forward(self.p, scores, elig, fwd, ref["bench"], idea, "2013-01-01", "2020-12-31", rules)
        self.assertEqual([w["test"][0] for w in wf["windows"]], ["2014-01-01"])  # stops at the stored data
        self.assertEqual(seen, [("2013-01-01", "2013-12-31")])
        w = wf["windows"][0]
        self.assertEqual(sorted(w["levels"]), ["1000000", "20000"])
        self.assertIsNotNone(wf["pooled"]["levels"]["1000000"]["cagr"])

    def test_rules_and_the_simulator_must_agree(self):
        self.assertEqual(self.rules["liquidity"]["max_share_of_median_value"], profiles.MAX_SHARE_OF_VALUE)
        self.assertEqual(self.rules["orders"]["limit_band"], profiles.LIMIT_BAND)
        self.assertEqual(self.rules["candidates"]["share_of_ranking"], profiles.CANDIDATE_SHARE)
        self.assertEqual(float(retail.dp_charge(self.rules)), backtest.Config().dp_charge)
        bad = json.loads(json.dumps(self.rules))
        bad["liquidity"]["max_share_of_median_value"] = 0.05
        with self.assertRaises(ValueError):
            harness.check_rules(bad)

    def test_the_holdout_is_never_offered(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                harness.main(["--period", "holdout", "--signal", "momentum_quality", "--db", self.db])
        with self.assertRaises(ValueError):
            harness.run(self.p, self.idea, "holdout", self.rules)
        self.assertNotIn("holdout", harness.PERIODS)
        self.assertNotIn("holdout", jobs.HARNESS_PERIODS)

    def test_validation_needs_a_note_and_is_one_look(self):
        log = os.path.join(self.tmp.name, "val.jsonl")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(harness.main(["--period", "validation", "--signal", "momentum_quality", "--db", self.db],
                                          runs_log=log), 2)
        with open(log, "w") as fh:
            fh.write(json.dumps({"period": "validation", "idea": "momentum_quality_top20_monthly",
                                 "config": {"engine": "harness.py", "hold": "monthly", "positions": 20}}) + "\n")
        with redirect_stderr(err):
            self.assertEqual(harness.main(["--period", "validation", "--signal", "momentum_quality", "--db", self.db,
                                           "--note", "one look"], runs_log=log), 2)
        self.assertIn("already had its validation look", err.getvalue())

    def test_every_run_is_logged_and_attempts_are_counted(self):
        log = os.path.join(self.tmp.name, "runs.jsonl")
        out = os.path.join(self.tmp.name, "results")
        with open(log, "w") as fh:  # an earlier attempt in the batch and a run from another engine
            fh.write(json.dumps({"period": "dev", "batch": "B1", "sharpe": 0.4, "config": {"engine": "harness.py"}}) + "\n")
            fh.write(json.dumps({"period": "dev", "sharpe": 0.3, "config": {"top": 20}}) + "\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = harness.main(["--period", "dev", "--signal", "momentum_quality", "--positions", "5", "--batch", "B1",
                               "--db", self.db, "--out", out, "--note", "test"], runs_log=log)
        self.assertEqual(rc, 0)
        self.assertIn("2 in batch 'B1', 2 harness runs logged in all", buf.getvalue())
        with open(log) as fh:
            last = json.loads(fh.readlines()[-1])
        self.assertEqual((last["idea"], last["batch"], last["config"]["engine"]), ("momentum_quality_top5_monthly", "B1",
                                                                                    "harness.py"))
        self.assertIn("ic_report", last)
        self.assertNotIn("ic", last)  # the trade finder's decile record reads "ic": the harness never feeds it
        self.assertTrue(os.path.exists(os.path.join(out, last["result_id"] + ".json")))
        self.assertEqual(harness.attempts("B1", log), (2, 2))
        self.assertEqual(harness.attempts("B2", log), (0, 2))

    def test_idea_arguments(self):
        with self.assertRaises(KeyError):
            harness.Idea("x", "no_such_signal")
        with self.assertRaises(ValueError):
            harness.Idea("x", "momentum_quality", hold=0)
        with self.assertRaises(ValueError):
            harness.Idea("x", "momentum_quality", positions=0)
        self.assertEqual(harness.Idea("x", "momentum_quality", hold=5).horizon(), 5)
        self.assertEqual(harness.Idea("x", "momentum_quality").horizon(), 21)


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class WideNet(unittest.TestCase):
    """momentum_quality_wide_top20: the current strategy with the value floor at Rs 50 lakh, nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "m.db")
        cls.days = weekdays(dt.date(2012, 1, 2), 3 * 261)
        # three groups by turnover: thin (under Rs 10 lakh a day even at Rs 4,000 a share), middle (Rs 50
        # lakh to several crore), thick (Rs 5 crore and more); made-up prices run from Rs 50 to Rs 600 or so
        make_store(cls.db, cls.days, volume=lambda k: (20, 100_000, 1_000_000)[k % 3])
        cls.p = panel.load(cls.db)
        cls.rules = harness.check_rules(retail.load())
        cls.base = harness.strategy_idea("momentum_quality_top20")
        cls.wide = harness.strategy_idea("momentum_quality_wide_top20")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_one_change_only(self):
        with open(os.path.join(ROOT, "research", "strategies.json"), encoding="utf-8") as fh:
            strat = json.load(fh)
        u = strat["momentum_quality_top20"]["universe"]
        self.assertEqual(self.base.universe, {"min_price": 20.0, "min_value": 20000000.0, "value_days": 60,
                                              "min_history": 250})
        self.assertEqual(self.wide.universe["min_value"], 5000000.0)  # Rs 50 lakh
        for k in ("min_price", "value_days", "min_history"):
            self.assertEqual(self.wide.universe[k], u[k])
        for k in ("signal", "hold", "positions", "fit"):
            self.assertEqual(getattr(self.wide, k), getattr(self.base, k))
        self.assertEqual((self.wide.signal, self.wide.hold, self.wide.positions), ("momentum_quality", "monthly", 20))
        self.assertNotIn("wide", json.dumps(strat))  # strategies.json is untouched
        self.assertEqual(self.base.universe["min_value"], u["min_value"])

    def test_the_base_universe_is_the_default_universe(self):
        self.assertTrue(self.base.eligible(self.p).equals(panel.universe(self.p)))

    def test_wide_admits_only_the_middle_band_and_keeps_every_other_filter(self):
        narrow, wide = self.base.eligible(self.p), self.wide.eligible(self.p)
        self.assertTrue((wide | ~narrow).all().all())  # a superset: nothing the base admits is dropped
        extra = wide & ~narrow
        self.assertTrue(extra.values.any())
        med = self.p.value.rolling(60, min_periods=48).median()
        self.assertTrue((med[extra] >= 5e6).where(extra, True).all().all())  # at least Rs 50 lakh ...
        self.assertTrue((med[extra] < 2e7).where(extra, True).all().all())  # ... and under the old floor
        self.assertTrue((self.p.raw_close[extra] >= 20).where(extra, True).all().all())  # price floor kept
        self.assertTrue(self.p.eq.where(extra, True).all().all())  # EQ series kept
        thin = [c for c in self.p.close.columns if int(c[3:6]) % 3 == 0]  # under Rs 10 lakh: still out
        self.assertFalse(wide[thin].values.any())

    def test_runs_with_its_own_universe_and_after_tax_numbers(self):
        res = harness.run(self.p, self.wide, "dev", self.rules, walk=False)
        self.assertEqual(res["idea"]["universe"]["min_value"], 5000000.0)
        self.assertEqual(res["idea"]["name"], "momentum_quality_wide_top20")
        for lv in res["levels"].values():
            self.assertIsNotNone(lv["sharpe_after_tax"])
            self.assertLessEqual(lv["max_drawdown_after_tax"], 0)
            self.assertLessEqual(lv["cagr_after_tax"], lv["cagr_sold"] + 1e-12)
        entry = harness.log_entry(res, "wide", "test", self.rules)
        self.assertEqual(entry["config"]["universe"]["min_value"], 5000000.0)
        self.assertEqual(entry["idea"], "momentum_quality_wide_top20")
        self.assertIn("after tax: Sharpe", harness.report(res, 1, 1, "wide"))

    def test_the_reference_and_universe_follow_the_ideas_universe(self):
        a = harness.run(self.p, self.base, "dev", self.rules, walk=False)
        b = harness.run(self.p, self.wide, "dev", self.rules, walk=False)
        self.assertNotEqual(a["bench"]["cagr"], b["bench"]["cagr"])  # a different set of stocks

    def test_walk_forward_reports_after_tax_pooled_and_per_year(self):
        rules = json.loads(json.dumps(self.rules))
        rules["walk_forward"]["first_train_years"] = 1
        res = harness.run(self.p, self.wide, "dev", rules, walk=True)
        wf = res["walk_forward"]
        self.assertTrue(wf["windows"])
        for lv, v in wf["pooled"]["levels"].items():
            self.assertIn("sharpe_after_tax", v)
            self.assertLessEqual(v["max_drawdown_after_tax"], 0)
            self.assertLessEqual(v["cagr_after_tax"], v["cagr"] + 1e-9)
            for w in wf["windows"]:
                self.assertIn("sharpe_after_tax", w["levels"][lv])

    def test_cli_runs_the_named_strategy_on_dev_only(self):
        log = os.path.join(self.tmp.name, "runs.jsonl")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = harness.main(["--period", "dev", "--strategy", "momentum_quality_wide_top20", "--batch", "wide",
                               "--db", self.db, "--note", "test"], runs_log=log)
        self.assertEqual(rc, 0)
        with open(log) as fh:
            last = json.loads(fh.readlines()[-1])
        self.assertEqual((last["idea"], last["period"]), ("momentum_quality_wide_top20", "dev"))
        self.assertEqual(last["config"]["universe"]["min_value"], 5000000.0)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            harness.main(["--period", "holdout", "--strategy", "momentum_quality_wide_top20", "--db", self.db],
                         runs_log=log)
        with self.assertRaises(KeyError):
            harness.strategy_idea("no_such_strategy")


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Jobs(unittest.TestCase):
    def test_arguments_are_checked_against_fixed_lists(self):
        import argparse
        self.assertEqual(jobs.hold_arg("monthly"), "monthly")
        self.assertEqual(jobs.hold_arg("63"), "63")
        for bad in ("0", "253", "weekly", "-5", "5; rm"):
            with self.assertRaises(argparse.ArgumentTypeError):
                jobs.hold_arg(bad)
        self.assertEqual(jobs.positions_arg("20"), 20)
        for bad in ("0", "51", "x"):
            with self.assertRaises(argparse.ArgumentTypeError):
                jobs.positions_arg(bad)
        self.assertEqual(jobs.name_arg("batch-B1_2"), "batch-B1_2")
        for bad in ("", "a b", "../x", "x" * 65):
            with self.assertRaises(argparse.ArgumentTypeError):
                jobs.name_arg(bad)
        self.assertEqual(jobs.price_arg("812.5"), "812.5")
        for bad in ("0", "-1", "abc", "nan"):
            with self.assertRaises(argparse.ArgumentTypeError):
                jobs.price_arg(bad)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                jobs.main(["harness", "--signal", "momentum_quality", "--period", "holdout"])


if __name__ == "__main__":
    unittest.main()
