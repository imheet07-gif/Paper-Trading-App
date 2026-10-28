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

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, amount=50000, risk="medium", stock=None, broker="zerodha"):
        return advise.advise(amount, risk, stock, self.path, as_of=self.day, ticks={}, broker=broker,
                             sector_of={}, strategy=strategy())

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

    def test_never_below_the_tested_top_n(self):
        s = strategy()
        s["top"] = 2  # the strategy buys only its top 2 here
        a = advise.advise(50000, "medium", None, self.path, as_of=self.day, ticks={}, broker="zerodha",
                          sector_of={}, strategy=s)
        self.assertTrue(all(c["rank"] <= 2 for c in a["picks"]))
        self.assertNotIn("DOWN", [c["symbol"] for c in a["picks"]])  # rank 4: never offered
        self.assertIn("among the tested top 2", a["note"])

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

    def test_bad_inputs(self):
        with self.assertRaises(advise.AdviseError):
            self.ask(amount=0)
        with self.assertRaises(advise.AdviseError):
            self.ask(risk="yolo")
        with self.assertRaises(advise.AdviseError):
            self.ask(stock="NOSUCH")


if __name__ == "__main__":
    unittest.main()
