import importlib.util
import os
import sys
import unittest

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT  # noqa: E402

if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import numpy as np  # noqa: E402
    import pandas as pd  # noqa: E402
    import misses  # noqa: E402
    import panel  # noqa: E402

N = 700
CFG = {"top": 2, "signal": "momentum_quality",
       "universe": {"min_price": 20.0, "min_value": 2e7, "value_days": 60, "min_history": 250}}


def made_up():
    """A made-up panel of 700 weekdays (no store, offline). Drifts are per session.

    UP1, UP2 triple in every 6 months and are liquid: held. LATE is flat, then
    surges: ranked below the top 2. PENNY and PENNY2 triple but trade little
    (PENNY2 enough for the look-alike floor). BEE triples in the BE series. ETF
    (INF) triples but is not a company share. BADCA triples with a failed
    corporate action check. FLAT* only wiggle. DEAD stops trading; BELATE
    moves to BE late.
    """
    rng = np.random.default_rng(7)
    days = pd.bdate_range("2014-01-01", periods=N)
    spec = {  # name: (isin, drift, value in rupees a day)
        "UP1": ("INE001A01010", 0.015, 5e8), "UP2": ("INE002A01010", 0.014, 5e8),
        "LATE": ("INE003A01010", None, 5e8), "PENNY": ("INE004A01010", 0.009, 1e5),
        "PENNY2": ("INE005A01010", 0.016, 1e7), "BEE": ("INE006A01010", 0.009, 5e8),
        "ETF": ("INF007A01010", 0.009, 5e8), "BADCA": ("INE008A01010", 0.009, 5e8),
        "FLAT1": ("INE009A01010", 0.0, 5e8), "FLAT2": ("INE010A01010", 0.0, 5e8),
        "DEAD": ("INE011A01010", 0.0, 5e8), "BELATE": ("INE012A01010", 0.0, 5e8),
    }
    close, value = {}, {}
    for name, (isin, drift, val) in spec.items():
        noise = rng.normal(0, 0.006, N)
        if name == "LATE":
            d = np.where(np.arange(N) < 400, 0.0, 0.012)
        else:
            d = np.full(N, drift)
        px = 100 * np.exp(np.cumsum(d + noise))
        s = pd.Series(px, index=days)
        if name == "DEAD":
            s.iloc[450:] = np.nan
        close[isin] = s
        value[isin] = pd.Series(val, index=days)
    close = pd.DataFrame(close)
    names = {v[0]: k for k, v in spec.items()}
    eq = pd.DataFrame(True, index=days, columns=close.columns)
    eq["INE006A01010"] = False
    eq.iloc[500:, list(close.columns).index("INE012A01010")] = False
    eq = eq & close.notna()
    issue = pd.DataFrame(False, index=days, columns=close.columns)
    issue["INE008A01010"] = True
    frames = {"raw_close": close.copy(), "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
              "volume": close * 0 + 1e6, "value": pd.DataFrame(value).where(close.notna()), "deliv": close * 0 + 0.5}
    return panel.Panel(frames, names, eq, issue)


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Misses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = made_up()
        cls.res = misses.audit(cls.p, CFG, first="2015-02-01", last="2016-02-29", horizon=6)
        cls.by = {r["symbol"]: r for r in cls.res["multibaggers"]}

    def test_counts_and_who_is_caught(self):
        r = self.by
        self.assertIn("UP1", r)
        self.assertTrue(r["UP1"]["caught"] and r["UP2"]["caught"])
        self.assertEqual(r["UP1"]["months_to_first_catch"], 0)
        self.assertGreater(r["UP1"]["return_share_held"], 0.9)
        self.assertNotIn("ETF", r)  # a fund unit is not a company share
        self.assertEqual([e["symbol"] for e in self.res["excluded"]], ["BADCA"])
        self.assertNotIn("BADCA", r)
        t = self.res["totals"]
        self.assertEqual(t["multibaggers"], t["caught"] + t["missed"])
        self.assertEqual(t["multibaggers"], len(self.by))

    def test_each_miss_names_its_first_blocking_filter(self):
        h = 6
        self.assertEqual(self.by["PENNY"]["blocked_months"]["traded value"], h)
        self.assertEqual(self.by["BEE"]["blocked_months"]["not EQ"], h)
        late = self.by["LATE"]
        self.assertFalse(late["caught"])
        self.assertEqual(sum(late["blocked_months"].values()), h)
        self.assertGreater(late["blocked_months"]["ranked below top 20"], 0)
        by = self.res["misses_by_blocking_filter"]
        self.assertEqual(by["traded value"]["stocks_mostly"], 2)  # PENNY and PENNY2
        self.assertEqual(by["not EQ"]["stocks_mostly"], 1)

    def test_price_and_value_splits(self):
        v = self.res["by_start_median_value"]
        self.assertEqual(v["under Rs 50 lakh"]["missed"], 1)           # PENNY
        self.assertEqual(v["Rs 50 lakh to 2 crore"]["missed"], 1)      # PENNY2
        self.assertEqual(v["over Rs 2 crore"]["caught"], 2)
        self.assertEqual(sum(x["multibaggers"] for x in self.res["by_start_price"].values()), len(self.by))
        self.assertEqual(misses.price_bucket(19.99), "under Rs 20")
        self.assertEqual(misses.price_bucket(100), "Rs 20 to 100")
        self.assertEqual(misses.price_bucket(100.01), "over Rs 100")
        self.assertEqual(misses.value_bucket(4.99e6), "under Rs 50 lakh")
        self.assertEqual(misses.value_bucket(2e7), "over Rs 2 crore")
        self.assertEqual(misses.value_bucket(float("nan")), "unknown")

    def test_lookalikes_are_only_those_blocked_by_the_value_floor(self):
        la = self.res["look_alikes"]
        self.assertGreater(la["look-alikes"]["stock_months"], 0)
        self.assertGreater(la["look-alikes"]["next_12m"]["mean"], la["whole universe"]["next_12m"]["mean"])
        # PENNY (Rs 1 lakh a day) is under the Rs 50 lakh floor: never a look-alike; PENNY2 can be
        wide = panel.universe(self.p, **dict(CFG["universe"], min_value=5e6))
        strict = panel.universe(self.p, **CFG["universe"])
        only = wide & ~strict
        self.assertFalse(only["INE004A01010"].any())
        self.assertTrue(only["INE005A01010"].any())

    def test_dead_and_be_stocks_count_against_their_group(self):
        u = self.res["look_alikes"]["whole universe"]
        self.assertGreater(u["lock_or_BE_within_12m"]["share"], 0)  # BELATE moved to BE
        self.assertGreater(u["halved_in_24m"]["n"], 0)

    def test_filters_taken_apart_match_the_universe(self):
        c = misses.components(self.p, CFG["universe"])
        parts = c["eq"] & c["price"] & c["history"] & c["issue"] & c["value"] & c["company"]
        self.assertTrue((parts == panel.universe(self.p, **CFG["universe"])).all().all())

    def test_refuses_data_after_dev(self):
        p = made_up()
        p.dates = pd.DatetimeIndex(list(p.dates[:-1]) + [pd.Timestamp("2021-01-04")])
        with self.assertRaises(SystemExit):
            misses.audit(p, CFG, first="2015-02-01", last="2016-02-29", horizon=6)

    def test_settings_come_from_strategies_json(self):
        s = misses.settings()
        self.assertEqual(s["top"], 20)
        self.assertEqual(s["universe"]["min_value"], 20000000.0)
        self.assertEqual(s["signal"], "momentum_quality")
        self.assertEqual(misses.LOOKALIKE_FLOOR, 5e6)
        self.assertEqual((misses.FIRST, misses.LAST, misses.DEV_END), ("2013-01-01", "2018-12-31", "2020-12-31"))


if __name__ == "__main__":
    unittest.main()
