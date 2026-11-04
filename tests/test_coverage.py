import datetime as dt
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT, load  # noqa: E402

bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
fin = load("core", "financials")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import numpy as np  # noqa: E402
    import pandas as pd  # noqa: E402
    import coverage  # noqa: E402


def build_store(path):
    """Four made-up stocks over 70 weekdays from 2020-01-01 (prices in paise):
    MAINCO (EQ) has a recent filing with a share count; BARECO (EQ) has none;
    SMECO (SM) has a filing without a face value; STALECO (EQ) filed only
    long ago. Filings are broadcast on 2020-02-03 except STALECO's (2018)."""
    db = fin.connect(path)
    days = [d for d in (dt.date(2020, 1, 1) + dt.timedelta(days=x) for x in range(120)) if d.weekday() < 5][:70]
    spec = [("MAINCO", "INE001A01018", "EQ"), ("BARECO", "INE002A01018", "EQ"),
            ("SMECO", "INE003A01018", "SM"), ("STALECO", "INE004A01018", "EQ"), ("ETFCO", "INF005A01018", "EQ")]
    rows = []
    for sym, isin, ser in spec:
        for d in days:
            rows.append({"date": d.isoformat(), "isin": isin, "symbol": sym, "series": ser, "open": 10000,
                         "high": 10000, "low": 10000, "close": 10000, "last": 10000, "prev_close": 10000,
                         "volume": 100, "value": 100_000_000, "trades": 5})  # Rs 10 lakh a day (paise)
    bc.store_day(db, rows)
    cols = [r[1] for r in db.execute("PRAGMA table_info(financials)")]

    def add(seq, isin, period_end, broadcast, paid_up, face):
        rec = {c: None for c in cols}
        rec.update({"seq": seq, "isin": isin, "period_end": period_end, "broadcast_at": broadcast,
                    "paid_up": paid_up, "face_value": face, "symbol": seq})
        db.execute("INSERT INTO financials (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                   [rec[c] for c in cols])

    add("a", "INE001A01018", "2019-12-31", "2020-02-03T10:00:00", "5000000", "10")
    add("c", "INE003A01018", "2019-09-30", "2020-02-03T10:00:00", "2000000", None)
    add("d", "INE004A01018", "2017-12-31", "2018-02-03T10:00:00", "1000000", "10")
    db.commit()
    return db


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Coverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "m.db")
        cls.db = build_store(cls.path)
        cls.links = ca.links(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def pop(self, date):
        return coverage.coverage_at(self.db, date, self.links).set_index("isin")

    def test_population_is_company_shares_by_group(self):
        p = self.pop("2020-03-31")
        self.assertEqual(sorted(p.index), ["INE001A01018", "INE002A01018", "INE003A01018", "INE004A01018"])
        self.assertEqual(p.loc["INE003A01018", "group"], "SME")
        self.assertEqual(p.loc["INE001A01018", "group"], "main board EQ")
        self.assertEqual(p.loc["INE001A01018", "value_bucket"], "Rs 10 to 50 lakh")

    def test_flags(self):
        p = self.pop("2020-03-31")
        self.assertTrue(p.loc["INE001A01018", "filing"] and p.loc["INE001A01018", "share_count"])
        self.assertFalse(p.loc["INE002A01018", "filing"])
        self.assertTrue(p.loc["INE003A01018", "filing"])
        self.assertFalse(p.loc["INE003A01018", "share_count"])  # no face value
        self.assertFalse(p.loc["INE004A01018", "filing"])  # the quarter ended too long ago

    def test_only_filings_broadcast_before_the_day_count(self):
        p = self.pop("2020-02-03")  # broadcast that day: not yet known at its close
        self.assertFalse(p.loc["INE001A01018", "filing"])
        p = self.pop("2020-02-04")
        self.assertTrue(p.loc["INE001A01018", "filing"])

    def test_summary_and_sme_table(self):
        s = coverage.summarise_snapshot(self.pop("2020-03-31").reset_index())
        self.assertEqual(s["all"]["stocks"], 4)
        self.assertAlmostEqual(s["all"]["filing"], 0.5)
        self.assertAlmostEqual(s["SME"]["filing"], 1.0)
        sme = coverage.sme_by_year(self.db)
        self.assertEqual(int(sme.loc[sme["year"] == "2020", "stocks"].iloc[0]), 1)
        self.assertEqual(int(sme.loc[sme["year"] == "2020", "with_filings"].iloc[0]), 1)

    def test_sme_filings_are_counted_in_the_year_they_were_broadcast(self):
        sme = coverage.sme_by_year(self.db)
        self.assertEqual(int(sme.loc[sme["year"] == "2020", "with_filings"].iloc[0]), 1)
        try:
            self.db.execute("DELETE FROM financials WHERE seq='c'")
            self.db.execute("INSERT INTO financials (seq, isin, period_end, broadcast_at) VALUES "
                            "('late', 'INE003A01018', '2019-09-30', '2023-01-05T10:00:00')")
            sme = coverage.sme_by_year(self.db)
            self.assertEqual(int(sme.loc[sme["year"] == "2020", "with_filings"].iloc[0]), 0)
        finally:
            self.db.rollback()  # the store is shared by the other tests

    def test_year_end_sessions(self):
        self.assertEqual(coverage.year_end_sessions(self.db, 2020)[0][:4], "2020")


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Jumps(unittest.TestCase):
    def test_a_single_session_move_is_flagged(self):
        days = pd.bdate_range("2014-01-01", periods=10)
        close = pd.DataFrame({"A": [100.0] * 5 + [400.0] * 5, "B": [100.0, 101, 102, 101, 100, 99, 98, 99, 100, 101],
                              "C": [100.0] * 5 + [20.0] * 5}, index=days)
        hi, lo, ratio = coverage.jump_flags(close)
        self.assertAlmostEqual(hi["A"], 4.0)
        self.assertTrue(coverage.window_has_jump(ratio, "A", days[0], days[9]))
        self.assertTrue(coverage.window_has_jump(ratio, "C", days[0], days[9]))
        self.assertFalse(coverage.window_has_jump(ratio, "B", days[0], days[9]))
        self.assertFalse(coverage.window_has_jump(ratio, "A", days[5], days[9]))  # the move is on days[5], excluded

    def test_a_switch_after_a_thirty_session_gap_is_flagged(self):
        days = pd.bdate_range("2014-01-01", periods=120)
        a = np.full(120, np.nan)
        a[:40] = 0.25  # an old ISIN at Rs 0.25 ...
        a[70:] = 15.0  # ... then a different security's prices after 30 sessions of silence
        _, _, ratio = coverage.jump_flags(pd.DataFrame({"A": a}, index=days))
        self.assertTrue(coverage.window_has_jump(ratio, "A", days[0], days[119]))
        self.assertEqual(coverage.gap_segments(pd.DataFrame({"A": a}, index=days)), {})  # under 60 sessions

    def test_a_long_gap_inside_a_window_is_found(self):
        days = pd.bdate_range("2014-01-01", periods=200)
        a = np.full(200, 100.0)
        a[10:150] = np.nan  # 140 sessions with no close, then trading resumes
        close = pd.DataFrame({"A": a, "B": np.where(np.arange(200) < 100, 50.0, np.nan),
                              "C": np.full(200, 10.0)}, index=days)
        segs = coverage.gap_segments(close)
        self.assertEqual(list(segs), ["A"])  # B stops for good: no later close, no gap; C trades every day
        self.assertTrue(coverage.window_has_gap(segs, "A", days[0], days[199]))
        self.assertFalse(coverage.window_has_gap(segs, "A", days[20], days[199]))  # the gap began before the window
        self.assertFalse(coverage.window_has_gap(segs, "A", days[0], days[100]))  # and ends after it
        self.assertFalse(coverage.window_has_gap(segs, "C", days[0], days[199]))

    def test_gap_in_trading_does_not_hide_or_invent_a_move(self):
        days = pd.bdate_range("2014-01-01", periods=8)
        close = pd.DataFrame({"A": [100, np.nan, np.nan, 100, 100, np.nan, np.nan, 400.0]}, index=days)
        _, _, ratio = coverage.jump_flags(close)
        self.assertTrue(coverage.window_has_jump(ratio, "A", days[0], days[7]))


if __name__ == "__main__":
    unittest.main()
