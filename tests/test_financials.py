import json
import os
import tempfile
import unittest

from _load import FIX, load

fi = load("core", "financials")
DIR = os.path.join(FIX, "financials")


def xml(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


class Parse(unittest.TestCase):
    def test_older_format_with_undeclared_contexts(self):
        p = fi.parse(xml("INDAS_TCS_2020-03_consolidated.xml"))
        self.assertEqual((p["symbol"], p["isin"], p["nature"]), ("TCS", None, "Consolidated"))  # no ISIN filed then
        self.assertEqual((p["period_start"], p["period_end"]), ("2020-01-01", "2020-03-31"))  # the quarter, not the year
        self.assertEqual((p["revenue"], p["net_profit"], p["net_profit_owners"]),
                         ("399460000000", "80930000000", "80490000000"))
        self.assertEqual((p["pbt"], p["tax"], p["eps_basic"], p["eps_diluted"]),
                         ("105120000000", "24190000000", "21.45", "21.45"))
        self.assertEqual(p["rounding"], "Crores")
        self.assertIsNone(p["interest_earned"])

    def test_declared_contexts(self):
        p = fi.parse(xml("INDAS_INFY_2022-06_standalone.xml"))
        self.assertEqual((p["symbol"], p["nature"], p["period_start"], p["period_end"]),
                         ("INFY", "Standalone", "2022-04-01", "2022-06-30"))
        self.assertEqual((p["revenue"], p["other_income"], p["total_income"]),
                         ("295270000000", "6480000000", "301750000000"))
        self.assertEqual((p["net_profit"], p["eps_basic"], p["eps_diluted"]), ("49010000000", "11.65", "11.64"))
        self.assertIsNone(p["net_profit_owners"])  # standalone: no owners line

    def test_bank_format(self):
        p = fi.parse(xml("BANKING_HDFCBANK_2020-06_consolidated.xml"))
        self.assertEqual((p["symbol"], p["period_start"], p["period_end"]), ("HDFCBANK", None, "2020-06-30"))
        self.assertIsNone(p["revenue"])  # banks report interest, not revenue from operations
        self.assertEqual((p["interest_earned"], p["interest_expended"]), ("323516100000", "156084900000"))
        self.assertEqual((p["operating_profit"], p["provisions"]), ("136840200000", "43445100000"))
        self.assertEqual((p["pbt"], p["net_profit"], p["net_profit_owners"], p["eps_basic"]),
                         ("93395100000", "69408800000", "69272400000", "12.6"))

    def test_integrated_filing(self):
        p = fi.parse(xml("INTEGRATED_INDAS_SANOFICONR_2026-09_standalone.xml"))
        self.assertEqual((p["symbol"], p["isin"], p["nature"], p["period_start"], p["period_end"]),
                         ("SANOFICONR", "INE0UOS01011", "Standalone", "2026-07-01", "2026-09-30"))
        self.assertEqual((p["revenue"], p["other_income"], p["total_income"], p["pbt"], p["tax"]),
                         ("2631000000", "44000000", "2675000000", "1027000000", "261000000"))
        self.assertEqual((p["net_profit"], p["eps_basic"], p["eps_diluted"]), ("766000000", "33.26", "33.26"))

    def test_integrated_filing_bank(self):
        p = fi.parse(xml("INTEGRATED_BANKING_CUB_2026-09_standalone.xml"))
        self.assertEqual((p["symbol"], p["isin"], p["period_start"], p["period_end"]),
                         ("CUB", "INE491A01021", "2026-07-01", "2026-09-30"))
        self.assertIsNone(p["revenue"])
        self.assertEqual((p["interest_earned"], p["interest_expended"], p["operating_profit"], p["provisions"]),
                         ("20922522000", "12194900000", "6609121000", "1200000000"))
        self.assertEqual((p["net_profit"], p["eps_basic"]), ("4159121000", "4.2"))

    def test_half_yearly_filer_has_no_quarter(self):
        with self.assertRaisesRegex(ValueError, "no quarter"):  # SME: October to March, then the year
            fi.parse(xml("INTEGRATED_NONINDAS_SWARAJ_2025-03_consolidated.xml"))

    def test_no_clear_quarter_is_an_error(self):
        with self.assertRaises(ValueError):
            fi.parse(b"<not xml")
        blob = xml("INDAS_INFY_2022-06_standalone.xml").replace(b"2022-04-01</", b"2021-04-01</")
        with self.assertRaisesRegex(ValueError, "no quarter"):
            fi.parse(blob)  # a twelve-month period is not taken for a quarter


class FetchAndStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.results = os.path.join(self.tmp.name, "results")
        os.makedirs(self.results)
        base = "https://nsearchives.nseindia.com/corporate/xbrl/"
        rows = [{"seqNumber": "1", "symbol": "TCS", "isin": "INE467B01029", "broadCastDate": "16-Apr-2020 20:27:28",
                 "xbrl": base + "INDAS_54905_240070_16042020082728_WEB.xml"},
                {"seqNumber": "2", "symbol": "INFY", "isin": "INE009A01021", "broadCastDate": "24-Jul-2022 18:21:00",
                 "xbrl": base + "INDAS_600774_2315_24072022182100_WEB.xml"},
                {"seqNumber": "3", "symbol": "HDFCBANK", "isin": "INE040A01034", "broadCastDate": "19-Jul-2020 20:40:08",
                 "xbrl": base + "BANKING_57945_291264_19072020084008_WEB.xml"},
                {"seqNumber": "4", "symbol": "NOXBRL", "isin": "INE000X01010", "broadCastDate": "19-Jul-2020 20:40:08",
                 "xbrl": "-"},
                # INFY's feed row with its pre-split ISIN, stale by the filing day: updated, then it matches
                {"seqNumber": "5", "symbol": "INFY", "isin": "INE009A01013", "broadCastDate": "24-Jul-2022 18:21:00",
                 "xbrl": base + "INDAS_600774_2315_24072022182100_WEB.xml"},
                # a row for TCS pointing at Infosys's filing: another company's results, refused
                {"seqNumber": "6", "symbol": "TCS", "isin": "INE467B01029", "broadCastDate": "24-Jul-2022 18:21:00",
                 "xbrl": base + "INDAS_600774_2315_24072022182100_WEB.xml"}]
        with open(os.path.join(self.results, "2020-07.json"), "w") as fh:
            json.dump(rows, fh)
        self.files = {"INDAS_54905_240070_16042020082728_WEB.xml": xml("INDAS_TCS_2020-03_consolidated.xml"),
                      "INDAS_600774_2315_24072022182100_WEB.xml": xml("INDAS_INFY_2022-06_standalone.xml"),
                      "BANKING_57945_291264_19072020084008_WEB.xml": xml("BANKING_HDFCBANK_2020-06_consolidated.xml")}
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, url):
        self.calls.append(url)
        return self.files.get(url.rsplit("/", 1)[-1])

    def test_filter_fetch_cache_and_store(self):
        none = os.path.join(self.tmp.name, "integrated")
        rows = fi.filings(self.results, integrated_raw=none)
        self.assertEqual([r[1] for r in rows], ["TCS", "INFY", "HDFCBANK", "INFY", "TCS"])  # no link, no row
        self.assertEqual([r[1] for r in fi.filings(self.results, since="2021-01-01", integrated_raw=none)],
                         ["INFY", "INFY", "TCS"])
        self.assertEqual([r[1] for r in fi.filings(self.results, symbols=["tcs"], integrated_raw=none)], ["TCS", "TCS"])
        raw = os.path.join(self.tmp.name, "raw")
        self.assertEqual(fi.fetch(rows, get=self.get, raw=raw, pause=0, log=None), 3)
        self.assertEqual(fi.fetch(rows, get=self.get, raw=raw, pause=0, log=None), 0)  # cached

        def broken(url):
            raise OSError("connection reset")
        other = os.path.join(self.tmp.name, "raw2")
        self.assertEqual(fi.fetch(rows, get=broken, raw=other, pause=0, log=None), 0)  # survives, caches nothing
        self.assertFalse(os.path.isdir(other))
        db = fi.connect(os.path.join(self.tmp.name, "m.db"))
        bc = load("core", "bhavcopy")
        p = {"series": "EQ", "open": 1, "high": 1, "low": 1, "close": 1, "last": 1, "prev_close": 1, "volume": 1,
             "value": 1, "trades": 1}
        bc.store_day(db, [dict(p, date="2014-12-01", isin="INE009A01013", symbol="INFY"),   # before the split
                          dict(p, date="2022-07-22", isin="INE009A01021", symbol="INFY")])  # trading at the filing
        db.commit()
        counts = fi.rebuild(db, rows, raw=raw, log=None)
        # seq 5's stale ISIN is updated, then matches; seq 6 is Infosys's filing under TCS: refused
        self.assertEqual((counts["stored"], counts["isin_mismatch"], counts["isin_updated"]), (4, 1, 1))
        self.assertEqual(db.execute("SELECT isin FROM financials WHERE seq = '5'").fetchone(), ("INE009A01021",))
        got = db.execute("SELECT symbol, isin, period_end, net_profit, eps_basic, broadcast_at FROM financials "
                         "WHERE seq IN ('1', '3', '5') ORDER BY seq").fetchall()
        self.assertEqual(got, [("TCS", "INE467B01029", "2020-03-31", "80930000000", "21.45", "2020-04-16T20:27:28"),
                               ("HDFCBANK", "INE040A01034", "2020-06-30", "69408800000", "12.6", "2020-07-19T20:40:08"),
                               ("INFY", "INE009A01021", "2022-06-30", "49010000000", "11.65", "2022-07-24T18:21:00")])
        db.close()


class IntegratedFiling(unittest.TestCase):
    """Integrated Filing rows as cached by core/events.py: an original and its revision
    (no broadcast_Date), a governance filing, a bank, and a half-yearly filer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.integrated = os.path.join(self.tmp.name, "integrated")
        os.makedirs(self.integrated)
        with open(os.path.join(DIR, "integrated_feed.json")) as fh:
            rows = json.load(fh)
        with open(os.path.join(self.integrated, "2026-10.json"), "w") as fh:
            json.dump(rows, fh)
        self.files = {"INTEGRATED_FILING_INDAS_1748152_28102026071728_WEB.xml":
                      xml("INTEGRATED_INDAS_SANOFICONR_2026-09_standalone.xml"),
                      # the revision files the same figures again
                      "INTEGRATED_FILING_INDAS_1748193_28102026074925_WEB.xml":
                      xml("INTEGRATED_INDAS_SANOFICONR_2026-09_standalone.xml"),
                      "INTEGRATED_FILING_BANKING_1748004_28102026061141_WEB.xml":
                      xml("INTEGRATED_BANKING_CUB_2026-09_standalone.xml"),
                      "INTEGRATED_FILING_NONINDAS_1460174_30052025074441_WEB.xml":
                      xml("INTEGRATED_NONINDAS_SWARAJ_2025-03_consolidated.xml")}

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, url):
        return self.files.get(url.rsplit("/", 1)[-1])

    def test_rows_fetch_and_store(self):
        none = os.path.join(self.tmp.name, "results")  # never the real data/ folder
        rows = fi.filings(none, integrated_raw=self.integrated)
        self.assertEqual([(r[0], r[1], r[2], r[3]) for r in rows],
                         [("I205791", "SANOFICONR", None, "2026-10-28T19:17:28"),
                          ("I205833", "SANOFICONR", None, "2026-10-28T19:49:25"),  # revision: creation time
                          ("I205676", "CUB", None, "2026-10-28T18:11:07"),
                          ("I97018", "SWARAJ", None, "2025-05-30T19:44:41")])  # no governance filing
        self.assertEqual([r[1] for r in fi.filings(none, since="2026-01-01", integrated_raw=self.integrated)],
                         ["SANOFICONR", "SANOFICONR", "CUB"])
        raw = os.path.join(self.tmp.name, "raw")
        self.assertEqual(fi.fetch(rows, get=self.get, raw=raw, pause=0, log=None), 4)
        db = fi.connect(os.path.join(self.tmp.name, "m.db"))
        bc = load("core", "bhavcopy")
        p = {"series": "EQ", "open": 1, "high": 1, "low": 1, "close": 1, "last": 1, "prev_close": 1, "volume": 1,
             "value": 1, "trades": 1}
        bc.store_day(db, [dict(p, date="2026-10-27", isin="INE0UOS01011", symbol="SANOFICONR"),
                          dict(p, date="2026-10-27", isin="INE491A01021", symbol="CUB")])
        db.commit()
        counts = fi.rebuild(db, rows, raw=raw, log=None)
        self.assertEqual((counts["stored"], counts["no_quarter"], counts["isin_mismatch"]), (3, 1, 0))
        got = db.execute("SELECT seq, isin, period_end, net_profit, broadcast_at FROM financials ORDER BY seq").fetchall()
        self.assertEqual(got, [("I205676", "INE491A01021", "2026-09-30", "4159121000", "2026-10-28T18:11:07"),
                               ("I205791", "INE0UOS01011", "2026-09-30", "766000000", "2026-10-28T19:17:28"),
                               ("I205833", "INE0UOS01011", "2026-09-30", "766000000", "2026-10-28T19:49:25")])
        db.close()


if __name__ == "__main__":
    unittest.main()
