import datetime as dt
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

from _load import FIX, ROOT, load

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None
ev = load("core", "events")
bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
EVF = os.path.join(FIX, "events")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import event_signals  # noqa: E402
    import signals  # noqa: E402
    import panel  # noqa: E402
    from test_research import market, weekdays  # noqa: E402


def fixture(name):
    with open(os.path.join(EVF, name), encoding="utf-8") as fh:
        return json.load(fh)


class Parsing(unittest.TestCase):
    def test_stamps(self):
        self.assertEqual(ev.stamp("31-Jan-2024 21:54"), "2024-01-31T21:54:00")
        self.assertEqual(ev.stamp("31-Jan-2024 21:25:51"), "2024-01-31T21:25:51")
        self.assertIsNone(ev.stamp("-"))
        self.assertEqual(ev.day("30-Jan-2024"), "2024-01-30")

    def test_insider_row_keeps_disclosure_time_apart_from_trade_dates(self):
        r = ev.insider_row(fixture("insider_2024-01.json")[0])
        self.assertEqual((r["symbol"], r["category"], r["mode"], r["value"]), ("MAHICKRA", "Promoter Group", "Market Purchase", 411000))
        self.assertEqual((r["trade_to"], r["disclosed_at"]), ("2024-01-30", "2024-01-31T18:03:00"))

    def test_results_row(self):
        r = ev.results_row(fixture("results_2024-01.json")[0])
        self.assertEqual((r["isin"], r["period_to"], r["broadcast_at"]), ("INE482J01021", "2023-12-31", "2024-01-31T21:25:51"))

    def test_results_from_announcements_under_both_headings(self):
        rows = fixture("announcements_2024-2026.json")
        got = [ev.announcement_result(r) for r in rows]
        self.assertEqual([g and (g["symbol"], g["period_to"], g["broadcast_at"]) for g in got],
                         [("BHARTIARTL", "2024-06-30", "2024-08-05T16:58:46"),
                          ("RPOWER", "2026-06-30", "2026-08-06T19:42:43"),
                          None,  # a board meeting that was not results
                          ("GPIL", "2026-06-30", "2026-08-07T17:35:20"),
                          None])  # a press release
        self.assertTrue(got[0]["seq"].startswith("A"))  # cannot collide with the results feed's numbers

    def test_period_end_reads_the_formats_companies_use_and_only_quarter_ends(self):
        self.assertEqual(ev.period_end("the financial results for the period ended June 30, 2024."), "2024-06-30")
        self.assertEqual(ev.period_end("Results for the quarter ended 30th June 2026"), "2026-06-30")
        self.assertEqual(ev.period_end("for the quarter ended 30.09.2026"), "2026-09-30")
        self.assertEqual(ev.period_end("for the year ended 31st March, 2026"), "2026-03-31")
        self.assertIsNone(ev.period_end("period ended June 29, 2024"))
        self.assertIsNone(ev.period_end("Outcome of Board Meeting held on August 03, 2026"))

    def test_sast_row(self):
        r = [ev.sast_row(x) for x in fixture("sast_2026-09.json")]
        self.assertEqual((r[0]["symbol"], r[0]["promoter"], r[0]["mode"], r[0]["side"], r[0]["pct_acquired"],
                          r[0]["pct_after"], r[0]["shares_acquired"]),
                         ("UMIYA-MRO", 1, "Open market", "Acquisition", "0.05", "38.84", 8500))
        self.assertEqual((r[0]["trade_from"], r[0]["trade_to"], r[0]["disclosed_at"]),
                         ("2026-09-24", "2026-09-24", "2026-09-30T18:05:00"))
        self.assertEqual((r[1]["promoter"], r[1]["side"], r[1]["pct_sold"], r[1]["pct_acquired"]), (0, "Sale", "2.5", None))
        self.assertEqual(r[2]["mode"], "Inter-se transfer")
        self.assertEqual(ev.sast_row(dict(fixture("sast_2026-09.json")[0], acquisitionMode="Open market"))["mode"],
                         "Open market")  # one spelling whatever the year

    def test_months(self):
        self.assertEqual(list(ev.months("2023-11", "2024-02")), ["2023-11", "2023-12", "2024-01", "2024-02"])
        self.assertEqual(ev.month_range("2024-02"), (dt.date(2024, 2, 1), dt.date(2024, 2, 29)))


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = os.path.join(self.tmp.name, "raw")
        for src, name in (("insider", "insider_2024-01.json"), ("results", "results_2024-01.json")):
            os.makedirs(os.path.join(self.raw, src))
            shutil.copy(os.path.join(EVF, name), os.path.join(self.raw, src, "2024-01.json"))
        self.db = ev.connect(os.path.join(self.tmp.name, "m.db"))
        bc.store_day(self.db, [{"date": "2024-01-29", "isin": "INE000M01019", "symbol": "MAHICKRA", "series": "EQ",
                                "open": 1, "high": 1, "low": 1, "close": 1, "last": 1, "prev_close": 1, "volume": 1,
                                "value": 1, "trades": 1}])

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_rebuild_maps_symbols_to_isins_through_prices(self):
        counts = ev.rebuild(self.db, self.raw, log=None)
        self.assertEqual((counts["insider"], counts["results"]), (5, 3))
        self.assertEqual(self.db.execute("SELECT isin FROM insider WHERE symbol='MAHICKRA'").fetchone(), ("INE000M01019",))
        self.assertEqual(counts["unmapped"], 4)  # the other symbols have no prices in this test store

    def test_a_transaction_listed_twice_is_stored_once_at_its_first_listing(self):
        rows = fixture("insider_2024-01.json")
        again = dict(rows[0], date="31-Jan-2024 18:04")
        earlier = dict(rows[1], date="31-Jan-2024 09:00")
        with open(os.path.join(self.raw, "insider", "2024-02.json"), "w", encoding="utf-8") as fh:
            json.dump([again, earlier], fh)
        ev.rebuild(self.db, self.raw, log=None)
        got = dict(self.db.execute("SELECT did, disclosed_at FROM insider").fetchall())
        self.assertEqual(len(got), 5)
        self.assertEqual(got[rows[0]["did"]], "2024-01-31T18:03:00")
        self.assertEqual(got[rows[1]["did"]], "2024-01-31T09:00:00")

    def test_results_with_a_retired_isin_take_the_one_trading_then(self):
        bc.store_day(self.db, [{"date": "2024-01-29", "isin": "INE999Z01011", "symbol": "SHAH", "series": "EQ",
                                "open": 1, "high": 1, "low": 1, "close": 1, "last": 1, "prev_close": 1, "volume": 1,
                                "value": 1, "trades": 1}])
        ev.rebuild(self.db, self.raw, log=None)
        got = {r[0] for r in self.db.execute("SELECT isin FROM results WHERE symbol='SHAH'")}
        self.assertEqual(got, {"INE999Z01011"})  # the fixture's INE482J01021 has no prices here

    def test_isin_for_takes_the_isin_trading_at_the_time(self):
        index = {"ABC": [("2013-01-01", "2019-06-30", "INE0OLD"), ("2019-07-01", "2026-10-26", "INE0NEW")]}
        self.assertEqual(ev.isin_for(index, "ABC", "2018-02-01"), "INE0OLD")
        self.assertEqual(ev.isin_for(index, "ABC", "2019-07-03"), "INE0NEW")
        self.assertEqual(ev.isin_for(index, "ABC", "2012-05-01"), "INE0OLD")
        self.assertIsNone(ev.isin_for(index, "XYZ", "2020-01-01"))

    def test_rebuild_merges_both_results_sources_at_the_earliest_release(self):
        os.makedirs(os.path.join(self.raw, "announcements"))
        os.makedirs(os.path.join(self.raw, "sast"))
        shutil.copy(os.path.join(EVF, "announcements_2024-2026.json"), os.path.join(self.raw, "announcements", "2026-08.json"))
        shutil.copy(os.path.join(EVF, "sast_2026-09.json"), os.path.join(self.raw, "sast", "2026-09.json"))
        counts = ev.rebuild(self.db, self.raw, log=None)
        self.assertEqual((counts["announcements"], counts["sast"]), (3, 3))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM results").fetchone()[0], 6)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM sast WHERE promoter=1").fetchone()[0], 2)

    def test_announcements_are_fetched_only_from_2025_and_cached_filtered(self):
        def get(url):
            return json.dumps(fixture("announcements_2024-2026.json")).encode()
        n = ev.fetch("2024-11", "2025-01", get=get, pause=0, today=dt.date(2026, 10, 27), log=None,
                     sources={"announcements": ev.SOURCES["announcements"]}, raw=self.raw)
        self.assertEqual(n, 1)
        with open(os.path.join(self.raw, "announcements", "2025-01.json"), encoding="utf-8") as fh:
            kept = json.load(fh)
        self.assertEqual(len(kept), 4)  # the press release is not cached

    def test_integrated_filing_keeps_financials_and_refuses_a_partial_answer(self):
        rows = [{"seq_Id": "1", "type": "Integrated Filing- Financials"},
                {"seq_Id": "2", "type": "Integrated Filing- Governance"}]

        def get(url):
            self.assertIn("integrated-filing-results", url)
            return json.dumps({"data": rows, "totalCount": total}).encode()
        total = 2
        src = {"integrated": ev.SOURCES["integrated"]}
        self.assertEqual(ev.fetch("2024-12", "2025-01", get=get, pause=0, today=dt.date(2026, 10, 27), log=None,
                                  sources=src, raw=self.raw), 1)  # nothing asked for before 2025
        with open(os.path.join(self.raw, "integrated", "2025-01.json"), encoding="utf-8") as fh:
            self.assertEqual([r["seq_Id"] for r in json.load(fh)], ["1"])
        total = 3  # NSE pages its answer: a short page is not cached as the whole month
        with self.assertRaisesRegex(ValueError, "2 of 3 rows"):
            ev.fetch("2026-10", "2026-10", get=get, pause=0, today=dt.date(2026, 10, 27), log=None,
                     sources=src, raw=self.raw)
        self.assertFalse(os.path.exists(os.path.join(self.raw, "integrated", "2026-10.json")))

    def test_fetch_skips_cached_months_but_refreshes_recent_ones(self):
        calls = []

        def get(url):
            calls.append(url)
            return b'{"data": []}'
        ev.fetch("2024-01", "2024-01", get=get, pause=0, today=dt.date(2026, 10, 27), log=None,
                 sources={"insider": ev.SOURCES["insider"]}, raw=self.raw)
        self.assertEqual(calls, [])  # cached and settled
        ev.fetch("2026-09", "2026-10", get=get, pause=0, today=dt.date(2026, 10, 27), log=None,
                 sources={"insider": ev.SOURCES["insider"]}, raw=self.raw)
        self.assertEqual(len(calls), 2)
        ev.fetch("2026-09", "2026-10", get=get, pause=0, today=dt.date(2026, 10, 27), log=None,
                 sources={"insider": ev.SOURCES["insider"]}, raw=self.raw)
        self.assertEqual(len(calls), 4)  # this month and last are still filling up: always refetched
        self.assertTrue(os.path.exists(os.path.join(self.raw, "insider", "2026-10.json")))


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for event signals")
class Signals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "m.db")
        db = ev.connect(cls.path)
        ca.connect(cls.path).close()
        cls.days = weekdays(dt.date(2013, 1, 1), 520)
        market(db, cls.days)
        isin = "INE000T01010"  # UPUP in the made-up market
        d300, d301 = cls.days[300], cls.days[301]
        ins = [("1", "P1", "UPUP", isin, "A", "Promoters", "Market Purchase", "Buy", "Equity Shares", 10, 50_000_000,
                d300.isoformat(), d300.isoformat(), d300.isoformat() + "T10:00:00"),
               ("2", "P1", "UPUP", isin, "B", "Employees/Designated Employees", "Market Purchase", "Buy", "Equity Shares", 10,
                9_000_000_000, d300.isoformat(), d300.isoformat(), d300.isoformat() + "T10:00:00"),
               ("3", "P1", "UPUP", isin, "C", "Promoters", "ESOP", "Buy", "Equity Shares", 10, 9_000_000_000,
                d300.isoformat(), d300.isoformat(), d300.isoformat() + "T10:00:00")]
        db.executemany("INSERT INTO insider VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ins)
        # results released after the close on day 300: day 301 reacts, known at the close of day 302
        db.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?)",
                   ("9", "FLAT", "INE001T01010", "2013-10-01", "2013-12-31", "Third Quarter", "Consolidated",
                    "Un-Audited", d300.isoformat() + "T18:00:00"))
        # SAST: a promoter open-market buy of 0.5% disclosed after the close on day 300, and rows that must not count
        sast = [("S1", "UPUP", isin, "A", 1, "Open market", "Acquisition", "29(2)", 100, None, 1000, "0.5", None, "40",
                 d300.isoformat(), d300.isoformat(), d300.isoformat() + "T18:05:00"),
                ("S2", "UPUP", isin, "B", 0, "Open market", "Acquisition", "29(2)", 100, None, 1000, "3", None, "9",
                 d300.isoformat(), d300.isoformat(), d300.isoformat() + "T18:05:00"),
                ("S3", "UPUP", isin, "C", 1, "Inter-se transfer", "Acquisition", "29(2)", 100, None, 1000, "10", None, "50",
                 d300.isoformat(), d300.isoformat(), d300.isoformat() + "T18:05:00"),
                ("S4", "UPUP", isin, "D", 1, "Open market", "Sale", "29(2)", None, 100, 900, None, "1", "39",
                 d300.isoformat(), d300.isoformat(), d300.isoformat() + "T18:05:00")]
        db.executemany("INSERT INTO sast VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", sast)
        db.commit()
        db.close()
        cls.p = panel.load(cls.path)
        cls.d301 = d301

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_insider_buying_starts_the_day_after_disclosure(self):
        s = event_signals.insider_buying(self.p)["INE000T01010"]
        self.assertTrue(s.iloc[:301].isna().all())  # disclosed on day 300: not usable until day 301
        typical = self.p.value["INE000T01010"].rolling(60, min_periods=40).median().iloc[301]
        self.assertAlmostEqual(s.iloc[301], 50_000_000 / typical)  # employees and ESOP rows ignored
        self.assertFalse(s.iloc[301 + 62:301 + 63].isna().any())
        self.assertTrue(s.iloc[301 + 63:].isna().all())  # out of the 63-day window
        self.assertTrue(event_signals.insider_buying(self.p).drop(columns=["INE000T01010"]).isna().all().all())

    def test_promoter_buying_counts_only_promoter_open_market_buys_from_the_next_session(self):
        s = event_signals.promoter_buying(self.p)["INE000T01010"]
        self.assertTrue(s.iloc[:301].isna().all())  # disclosed the evening of day 300: usable from day 301
        self.assertAlmostEqual(s.iloc[301], 0.5)  # non-promoter, inter-se transfer and sale rows ignored
        self.assertFalse(s.iloc[301:301 + 63].isna().any())
        self.assertTrue(s.iloc[301 + 63:].isna().all())
        self.assertTrue(event_signals.promoter_buying(self.p).drop(columns=["INE000T01010"]).isna().all().all())
        self.assertIn("promoter_buying", signals.SIGNALS)

    def test_results_reaction_known_at_the_close_of_the_second_session(self):
        s = event_signals.results_reaction(self.p)["INE001T01010"]
        self.assertTrue(s.iloc[:302].isna().all())
        self.assertFalse(s.iloc[302:302 + 64].isna().any())  # the event day plus 63 days live
        self.assertTrue(s.iloc[302 + 64:].isna().all())
        self.assertLess(s.iloc[302], 0)  # FLAT does not move while the market rises: negative abnormal return


if __name__ == "__main__":
    unittest.main()
