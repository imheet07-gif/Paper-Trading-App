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
    import numpy as np  # noqa: E402
    import pandas as pd  # noqa: E402
    import backtest  # noqa: E402
    import labels  # noqa: E402
    import panel  # noqa: E402

N = 400  # weekdays from 2014-01-01: about 19 months
H = 6  # a short horizon keeps the made-up panel small


def made_up():
    """A made-up panel (no store, offline). Prices per session:

    TRIPLE is flat to March 2014, then rises to 3.5x by June and stays. TOUCH reaches 3.5x
    then falls back to 1x. FLAT stays at 100. DEAD stops trading after session
    150. SME triples in the SM series. RIGHTS (security code 20) and ETF (INF)
    triple but are not equity shares. BADIN has a failed corporate action
    check inside the first window; BADOUT one in January 2014 (before the
    first start). YOUNG lists in March 2014.
    """
    days = pd.bdate_range("2014-01-01", periods=N)
    i = np.arange(N)
    up = 100 * np.minimum(3.5, np.exp(np.maximum(0, i - 45) * np.log(3.5) / 60.0))  # flat to March, 3.5x by June
    spec = {  # name: (isin, prices)
        "TRIPLE": ("INE001A01018", up),
        "TOUCH": ("INE002A01018", np.where(i < 130, up, np.maximum(100, 350 - (i - 130) * 5.0))),
        "FLAT": ("INE003A01018", np.full(N, 100.0)),
        "DEAD": ("INE004A01018", np.where(i <= 150, 100.0, np.nan)),
        "SME": ("INE005A01018", up),
        "RIGHTS": ("INE001A20018", up),
        "ETF": ("INF006A01018", up),
        "BADIN": ("INE007A01018", up),
        "BADOUT": ("INE008A01018", np.full(N, 100.0)),
        "YOUNG": ("INE009A01018", np.where(i >= 45, 50.0, np.nan)),
    }
    close = pd.DataFrame({isin: px for isin, px in spec.values()}, index=days)
    names = {v[0]: k for k, v in spec.items()}
    code = pd.DataFrame(0.0, index=days, columns=close.columns).where(close.notna())
    code["INE005A01018"] = np.where(close["INE005A01018"].notna(), panel.SERIES_CODE["SM"], np.nan)
    eq = code.eq(0.0)
    issue = pd.DataFrame(False, index=days, columns=close.columns)
    issue.iloc[60:60 + 252, list(close.columns).index("INE007A01018")] = True  # fails in late March 2014
    issue.iloc[5:5 + 252, list(close.columns).index("INE008A01018")] = True  # fails in January 2014
    frames = {"raw_close": close.copy(), "open": close, "high": close, "low": close, "close": close,
              "volume": close * 0 + 1000, "value": close * 1000, "deliv": close * 0 + 0.5, "series_code": code}
    return panel.Panel(frames, names, eq, issue)


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Labels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = made_up()
        cls.rows = labels.label_rows(cls.p, "2014-02-01", "2014-02-28", horizon=H)
        cls.by = {r["symbol"]: r for r in cls.rows.to_dict("records")}

    def test_population_is_equity_shares_only(self):
        self.assertNotIn("RIGHTS", self.by)
        self.assertNotIn("ETF", self.by)
        self.assertNotIn("YOUNG", self.by)  # not trading yet at the February start
        self.assertEqual(set(self.by), {"TRIPLE", "TOUCH", "FLAT", "DEAD", "SME", "BADIN", "BADOUT"})

    def test_multiples_and_hits(self):
        self.assertGreaterEqual(self.by["TRIPLE"]["multiple"], 3.0)
        self.assertAlmostEqual(self.by["FLAT"]["multiple"], 1.0)
        self.assertLess(self.by["TOUCH"]["multiple"], 3.0)
        self.assertTrue(self.by["TOUCH"]["touched"])
        self.assertFalse(self.by["FLAT"]["touched"])

    def test_delisted_stocks_count_with_the_haircut(self):
        self.assertEqual(labels.HAIRCUT, backtest.Config().delist_haircut)
        d = self.by["DEAD"]
        self.assertTrue(d["delisted"])
        self.assertAlmostEqual(d["multiple"], 0.5)

    def test_sme_is_in_and_grouped(self):
        self.assertEqual(self.by["SME"]["series"], "SM")
        self.assertEqual(self.by["SME"]["series_group"], "SME")
        self.assertEqual(self.by["TRIPLE"]["series_group"], "main board EQ")

    def test_failed_check_inside_the_window_only(self):
        self.assertTrue(self.by["BADIN"]["issue"])
        self.assertFalse(self.by["BADOUT"]["issue"])
        s = labels.summarise(self.rows)
        self.assertEqual(s["listed_apart"]["stock_months"], 1)
        self.assertEqual(s["all"]["stock_months"], 6)
        # TRIPLE and SME hit; TOUCH, FLAT, DEAD, BADOUT do not
        self.assertAlmostEqual(s["all"]["hit"], 2 / 6)
        self.assertEqual(s["distinct_multibaggers"], 2)

    def test_listing_age(self):
        rows = labels.label_rows(self.p, "2014-04-01", "2014-04-30", horizon=H)
        young = rows[rows["symbol"] == "YOUNG"].iloc[0]
        self.assertEqual(young["listing"], "listed under a year")
        flat = rows[rows["symbol"] == "FLAT"].iloc[0]
        self.assertEqual(flat["listing"], "listed under a year")  # the made-up store starts in 2014

    def test_refuses_a_window_past_the_panel(self):
        with self.assertRaises(SystemExit):
            labels.label_rows(self.p, "2015-03-01", "2015-03-31", horizon=H)

    def test_buckets(self):
        self.assertEqual(labels.bucket(19.99, labels.PRICE_EDGES), "under Rs 20")
        self.assertEqual(labels.bucket(500, labels.PRICE_EDGES), "Rs 500 and over")
        self.assertEqual(labels.bucket(5e6, labels.VALUE_EDGES), "Rs 50 lakh to 2 crore")
        self.assertEqual(labels.bucket(float("nan"), labels.VALUE_EDGES), "unknown")
        self.assertTrue(labels.is_equity_share("INE00C501018"))
        self.assertFalse(labels.is_equity_share("INE06ZX20015"))  # a rights entitlement

    def test_report_prints(self):
        text = labels.report(labels.summarise(self.rows))
        self.assertIn("by start year", text)
        self.assertIn("SME", text)

    def test_dev_split_is_the_only_one_open(self):
        self.assertEqual(sorted(labels.SPLITS), ["dev"])
        self.assertEqual(labels.SPLITS["dev"], ("2013-01-01", "2019-12-31"))


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class PanelSeries(unittest.TestCase):
    def test_sme_is_read_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.db")
            db = ca.connect(path)
            rows = []
            for k, d in enumerate([dt.date(2014, 1, 1) + dt.timedelta(days=x) for x in range(5)]):
                for isin, sym, ser in (("INE001A01018", "MAIN", "EQ"), ("INE002A01018", "SMALL", "SM"),
                                       ("IN1020230604", "SDL", "SG")):
                    rows.append({"date": d.isoformat(), "isin": isin, "symbol": sym, "series": ser,
                                 "open": 10000, "high": 10000, "low": 10000, "close": 10000, "last": 10000,
                                 "prev_close": 10000, "volume": 10, "value": 100000, "trades": 1})
            bc.store_day(db, rows)
            db.commit()
            db.close()
            default = panel.load(path)
            self.assertEqual(list(default.close.columns), ["INE001A01018"])
            both = panel.load(path, series=panel.EQUITY_SERIES)
            self.assertEqual(sorted(both.close.columns), ["INE001A01018", "INE002A01018"])
            self.assertEqual(both.series_code["INE002A01018"].iloc[0], panel.SERIES_CODE["SM"])
            self.assertFalse(both.eq["INE002A01018"].any())
            with self.assertRaises(ValueError):
                panel.load(path, series=("SG",))


if __name__ == "__main__":
    unittest.main()
