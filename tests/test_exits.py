import importlib.util
import os
import sys
import unittest
from decimal import Decimal
from types import SimpleNamespace

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT  # noqa: E402

if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import exits  # noqa: E402
    import pandas as pd  # noqa: E402


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class TrueRange(unittest.TestCase):
    def test_the_three_cases(self):
        high = pd.Series([102.0, 110.0, 101.0, 104.0])
        low = pd.Series([98.0, 104.0, 95.0, 100.0])
        close = pd.Series([100.0, 108.0, 96.0, 103.0])
        tr = exits.true_range(high, low, close)
        # day 1: no previous close, high - low; day 2: gap up, high - prev close;
        # day 3: gap down, prev close - low; day 4: inside, high - low
        self.assertEqual(list(tr), [4.0, 10.0, 13.0, 8.0])

    def test_atr_needs_fourteen_sessions(self):
        n = 20
        frame = lambda v: pd.DataFrame({"A": [v] * n})
        p = SimpleNamespace(high=frame(105.0), low=frame(95.0), close=frame(100.0))
        a = exits.atr(p)["A"]
        self.assertTrue(a.iloc[:13].isna().all())
        self.assertEqual(a.iloc[13], 10.0)


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Plan(unittest.TestCase):
    def test_the_worked_example(self):
        p = exits.plan(1000, 20, 50000, "medium")
        self.assertEqual((p["stop"], p["target"], p["qty"]), (Decimal("960.00"), Decimal("1080.00"), 12))
        self.assertEqual((p["loss_at_stop"], p["gain_at_target"], p["risk_limit"]),
                         (Decimal("480.00"), Decimal("960.00"), Decimal("500.00")))
        self.assertEqual(p["stocks"], 3)

    def test_profiles(self):
        self.assertEqual(exits.plan(1000, 20, 50000, "low")["qty"], 6)    # Rs 250 at risk
        self.assertEqual(exits.plan(1000, 20, 50000, "high")["qty"], 25)  # Rs 1,000 at risk, one stock
        with self.assertRaises(ValueError):
            exits.plan(1000, 20, 50000, "reckless")

    def test_the_slice_caps_the_quantity(self):
        p = exits.plan(1000, 1, 50000, "high")  # a calm stock: Rs 2 a share at risk, the slice binds first
        self.assertEqual(p["qty"], 50)
        self.assertLessEqual(p["cost"], Decimal("50000"))

    def test_charges_count_against_the_limit(self):
        flat = lambda side, q, price: Decimal("15")  # Rs 30 a round trip, made up
        p = exits.plan(1000, 20, 50000, "medium", charges=flat)
        self.assertEqual(p["qty"], 11)  # 12 x 40 + 30 = 510 > 500
        self.assertEqual(p["loss_at_stop"], Decimal("470.00"))
        self.assertTrue(p["charges_counted"])

    def test_tick_rounding_keeps_the_stop_below_and_the_target_above(self):
        p = exits.plan("1000.30", "20.33", 50000, "medium", tick="0.05")
        self.assertEqual((p["stop"], p["target"]), (Decimal("959.60"), Decimal("1081.65")))
        self.assertTrue(p["tick_checked"])

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "not even one share"):
            exits.plan(1000, 20, 1000, "low")  # Rs 5 at risk, one share risks Rs 40
        with self.assertRaisesRegex(ValueError, "at or below zero"):
            exits.plan(10, 6, 50000, "medium")
        with self.assertRaises(ValueError):
            exits.plan(1000, 0, 50000)


if __name__ == "__main__":
    unittest.main()
