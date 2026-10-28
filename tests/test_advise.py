import datetime as dt
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT, load  # noqa: E402

bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import advise  # noqa: E402
    import scan  # noqa: E402
    from test_research import market, weekdays  # noqa: E402

# the validated strategy's settings, with a liquidity floor the small made-up market can meet
def strategy():
    s = dict(scan.load_strategy(advise.STRATEGY))
    s["universe"] = dict(s["universe"], min_value=1.0)
    return s


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Advise(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "m.db")
        db = ca.connect(cls.path)
        cls.days = weekdays(dt.date(2013, 1, 1), 520)
        market(db, cls.days)  # UPUP, FLAT, DOWN, GONE (stops trading), LOCK (locked at its circuit from day 300)
        db.close()
        cls.day = cls.days[400]  # GONE still trades; LOCK is locked
        cls.ticks = {}
        cls.runs = os.path.join(cls.tmp.name, "runs.jsonl")
        dec = [-0.006, -0.003, -0.002, -0.001, -0.004, 0.0, 0.003, 0.003, 0.003, 0.008]
        with open(cls.runs, "w") as fh:
            for at, period in (("2026-01-01T00:00:00", "dev"), ("2026-01-02T00:00:00", "validation"),
                               ("2026-01-03T00:00:00", "holdout")):
                fh.write(json.dumps({"at": at, "signal": "momentum_quality", "period": period,
                                     "ic": {"decile_excess": dec}}) + "\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # the made-up market ranks only a handful of stocks: offer all of them unless a test says otherwise
    def ask(self, amount=50000, risk="medium", stock=None, broker="zerodha", share=1.0):
        return advise.advise(amount, risk, stock, self.path, as_of=self.day, ticks={}, broker=broker,
                             sector_of={}, strategy=strategy(), candidate_share=share, runs_log=self.runs)

    def test_picks_are_ranked_filtered_and_sized(self):
        a = self.ask()
        syms = [c["symbol"] for c in a["picks"]]
        self.assertNotIn("LOCK", syms)  # locked at a circuit: dropped, and said
        self.assertTrue(any(d["symbol"] == "LOCK" and "circuit" in " ".join(d["why"]) for d in a["dropped"]))
        self.assertNotIn("DOWN", syms[:1])
        for c in a["picks"]:
            pl = c["plan"]
            self.assertLess(Decimal(pl["stop"]), Decimal(pl["limit"]))
            self.assertGreater(Decimal(pl["target"]), Decimal(pl["limit"]))
            self.assertLessEqual(Decimal(pl["loss_at_stop"]), Decimal("500"))  # 1% of Rs 50,000
            self.assertEqual(pl["exits_label"], "risk limits, not a tested edge (exit test pending)")
        self.assertEqual(a["risk"], "medium")
        self.assertTrue(a["pending"])

    def test_never_below_the_top_tenth(self):
        a = self.ask(share=advise.CANDIDATE_SHARE)  # a tenth of a handful of stocks: the top one only
        self.assertEqual(a["candidates"], 1)
        self.assertTrue(all(c["rank"] == 1 for c in a["picks"]))
        self.assertNotIn("DOWN", [c["symbol"] for c in a["picks"]])
        self.assertIn("among the top 1 of the ranking", a["note"])
        self.assertIn("outside its top tenth", self.ask(stock="DOWN", share=advise.CANDIDATE_SHARE)["stock"]["verdict"])

    def test_each_pick_shows_its_tenths_record(self):
        b = self.ask()["picks"][0]["bucket"]
        self.assertEqual(b["tenth"], 1)
        self.assertEqual(b["dev"], 0.8)
        self.assertEqual(b["validation"], 0.8)
        self.assertNotIn("holdout", b)  # the holdout is never read
        self.assertEqual(advise.tenth_of(1, 430), 1)
        self.assertEqual(advise.tenth_of(43, 430), 1)
        self.assertEqual(advise.tenth_of(44, 430), 2)
        self.assertEqual(advise.tenth_of(430, 430), 10)
        self.assertEqual(advise.bucket(430, 430, advise.decile_record("momentum_quality", self.runs))["dev"], -0.6)
        self.assertEqual(advise.decile_record("momentum_quality", os.path.join(self.tmp.name, "none")), {})

    def test_ranks_follow_the_tested_signal(self):
        a = self.ask()
        ranks = [c["rank"] for c in a["picks"]]
        self.assertEqual(ranks, sorted(ranks))

    def test_low_profile_is_shown_beside_a_riskier_request(self):
        a = self.ask(risk="high")
        self.assertLessEqual(len(a["picks"]), 1)
        self.assertIn("low_profile", a)
        self.assertNotIn("low_profile", self.ask(risk="low"))

    def test_stock_mode_says_when_there_is_no_tested_reason(self):
        a = self.ask(stock="DOWN")
        self.assertIn("DOWN", a["stock"]["symbol"])
        self.assertTrue(a["stock"]["verdict"])
        locked = self.ask(stock="LOCK")["stock"]
        self.assertIn("circuit", " ".join(locked["filters"]))

    def test_too_little_money_is_no_trade_not_a_guess(self):
        a = self.ask(amount=100, risk="low")
        self.assertEqual(a["picks"], [])
        self.assertIn("no trade", a["answer"])

    def test_report_and_json(self):
        a = self.ask()
        text = advise.report(a)
        self.assertIn("risk limits, not a tested edge", text)
        self.assertIn("Order sheet only", text)
        self.assertIn("Not available yet", text)
        json.dumps(advise._jsonable(a))

    def levels_log(self, passed):
        """A runs log with the decile record and one dev and one validation run per risk level."""
        path = os.path.join(self.tmp.name, "levels.jsonl")
        with open(self.runs) as src, open(path, "w") as fh:
            fh.write(src.read())
            for lvl, ok in passed.items():
                for period in ("dev", "validation"):
                    fh.write(json.dumps({"at": "2026-10-29T00:00:00", "signal": "momentum_quality", "period": period,
                                         "profile": "risk:" + lvl, "cagr": 0.2 if ok else 0.05, "bench_cagr": 0.1,
                                         "sharpe": 0.9, "bench_sharpe": 0.5, "max_drawdown": -0.3,
                                         "worst_month": -0.12, "months_losing": 0.35, "offer_test": ok}) + "\n")
        return path

    def test_untested_levels_are_offered_and_said_to_be_pending(self):
        a = self.ask(risk="high")
        self.assertEqual(a["risk"], "high")
        self.assertIsNone(a["fallback"])
        self.assertIn(advise.LEVEL_PENDING, a["pending"])

    def test_a_failed_level_hands_over_to_the_next_safer_one(self):
        log = self.levels_log({"high": False, "medium": True, "low": True})
        a = advise.advise(50000, "high", None, self.path, as_of=self.day, ticks={}, broker="zerodha",
                          sector_of={}, strategy=strategy(), candidate_share=1.0, runs_log=log)
        self.assertEqual(a["risk_asked"], "high")
        self.assertEqual(a["risk"], "medium")
        self.assertIn("high risk did not pass", a["fallback"])
        self.assertIn("grew less than the average stock", a["fallback"])
        self.assertNotIn(advise.LEVEL_PENDING, a["pending"])
        self.assertEqual(a["level_record"]["dev"]["worst_month_rupees"], "-6000.00")  # 12% of Rs 50,000
        self.assertTrue(a["picks"])
        text = advise.report(a)
        self.assertIn("Asked for high risk", text)
        self.assertIn("medium risk, 2013 to 2020: 20.0% a year (average stock 10.0%)", text)

    def test_every_level_failed_is_no_trade(self):
        log = self.levels_log({"high": False, "medium": False, "low": False})
        a = advise.advise(50000, "medium", None, self.path, as_of=self.day, ticks={}, broker="zerodha",
                          sector_of={}, strategy=strategy(), candidate_share=1.0, runs_log=log)
        self.assertEqual(a["picks"], [])
        self.assertTrue(a["answer"].startswith("no trade: no risk level from medium down passed"))
        self.assertNotIn("low_profile", a)

    def test_level_to_offer(self):
        rec = {"high": {"passed": None}, "medium": {"passed": False, "dev": {"offer_test": False}}, "low": {"passed": True}}
        self.assertEqual(advise.level_to_offer("high", rec), ("high", None))
        self.assertEqual(advise.level_to_offer("medium", rec)[0], "low")
        self.assertEqual(advise.level_to_offer("low", rec), ("low", None))

    def test_bad_inputs(self):
        with self.assertRaises(advise.AdviseError):
            self.ask(amount=0)
        with self.assertRaises(advise.AdviseError):
            self.ask(risk="yolo")
        with self.assertRaises(advise.AdviseError):
            self.ask(stock="NOSUCH")


if __name__ == "__main__":
    unittest.main()
