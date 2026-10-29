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

    def test_f3_lines_and_net_worth_from_the_year(self):
        p = fi.parse(xml("INDAS_TCS_2020-03_consolidated.xml"))
        self.assertEqual((p["employee_cost"], p["finance_costs"], p["depreciation"], p["other_expenses"], p["expenses"]),
                         ("220510000000", "2510000000", "9510000000", "69190000000", "301720000000"))
        self.assertEqual((p["exceptional"], p["face_value"], p["paid_up"]), ("0", "1", "3750000000"))
        self.assertEqual(p["reserves"], "843740000000")  # the year's column: paid-up plus this is Rs 84,749 crore
        self.assertEqual(p["facts"]["quarter"]["FinanceCosts"], "2510000000")
        self.assertEqual(p["facts"]["year"]["FinanceCosts"], "9240000000")  # every figure kept, for later lines
        self.assertEqual(p["version"], fi.FACTS_VERSION)
        q = fi.parse(xml("INDAS_INFY_2022-06_standalone.xml"))
        self.assertIsNone(q["reserves"])  # June: no year-end net worth
        self.assertIsNotNone(q["finance_costs"])
        self.assertIsNone(q["facts"]["year"])
        b = fi.parse(xml("BANKING_HDFCBANK_2020-06_consolidated.xml"))
        self.assertIsNone(b["finance_costs"])  # banks file interest expended instead

    def test_quarters_cached_before_f3_are_read_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, err = (os.path.join(tmp, n) for n in ("old.json", "new.json", "err.json"))
            for path, doc in ((old, {"span": "quarter", "revenue": "1"}),
                              (new, {"span": "quarter", "version": fi.FACTS_VERSION}),
                              (err, {"error": "no DateOfEndOfReportingPeriod in the filing"})):
                with open(path, "w") as fh:
                    json.dump(doc, fh)
            self.assertTrue(fi._stale(old))
            self.assertFalse(fi._stale(new))
            self.assertFalse(fi._stale(err))  # a file that can never be read is not asked for again

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

    def test_half_yearly_filer_gives_the_half_year(self):
        p = fi.parse(xml("INTEGRATED_NONINDAS_SWARAJ_2025-03_consolidated.xml"))  # October to March, then the year
        self.assertEqual((p["span"], p["isin"], p["period_start"], p["period_end"]),
                         ("half", "INE0GMR01016", "2024-10-01", "2025-03-31"))
        self.assertEqual((p["revenue"], p["other_income"], p["net_profit"]), ("2550609000", "-5583000", "235751000"))
        self.assertEqual((p["year"]["total_income"], p["year"]["net_profit"]), ("4183639000", "334789000"))
        self.assertEqual(fi.parse(xml("INDAS_INFY_2022-06_standalone.xml"))["span"], "quarter")

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
        self.assertEqual(db.execute("SELECT finance_costs, paid_up, reserves FROM financials WHERE seq = '1'").fetchone(),
                         ("2510000000", "3750000000", "843740000000"))
        cov = {r["year"]: r for r in fi.coverage(db)}
        self.assertEqual((cov["2020"]["quarters"], cov["2020"]["march"]), (2, 1))
        self.assertEqual(cov["2020"]["reserves"], 1.0)  # of March quarters only
        self.assertEqual(cov["2020"]["finance_costs"], 0.5)  # the bank files none
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
        self.assertEqual((counts["stored"], counts["half"], counts["derived"], counts["no_quarter"],
                          counts["isin_mismatch"]), (3, 1, 0, 0, 0))  # SWARAJ: no quarter to subtract
        got = db.execute("SELECT seq, isin, period_end, net_profit, broadcast_at FROM financials ORDER BY seq").fetchall()
        self.assertEqual(got, [("I205676", "INE491A01021", "2026-09-30", "4159121000", "2026-10-28T18:11:07"),
                               ("I205791", "INE0UOS01011", "2026-09-30", "766000000", "2026-10-28T19:17:28"),
                               ("I205833", "INE0UOS01011", "2026-09-30", "766000000", "2026-10-28T19:49:25")])
        db.close()


class HalfYearsAndTypos(unittest.TestCase):
    """rebuild on hand-made cached files: the March quarter from a half-year file, checked against the
    year, and mistyped file ISINs. SWARAJ's year has total income 4,183,639,000 and its October to March
    context 2,545,026,000; the December quarter below has 1,001,000,000."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = os.path.join(self.tmp.name, "raw")
        os.makedirs(self.raw)
        self.db = fi.connect(os.path.join(self.tmp.name, "m.db"))
        bc = load("core", "bhavcopy")
        p = {"date": "2024-12-02", "series": "EQ", "open": 1, "high": 1, "low": 1, "close": 1, "last": 1,
             "prev_close": 1, "volume": 1, "value": 1, "trades": 1}
        bc.store_day(self.db, [dict(p, isin="INE0GMR01016", symbol="SWARAJ"),
                               dict(p, isin="INE903I01023", symbol="TYPO"),
                               dict(p, isin="INE001A01011", symbol="NEWNAME")])
        self.db.commit()
        half = fi.parse(xml("INTEGRATED_NONINDAS_SWARAJ_2025-03_consolidated.xml"))
        december = dict(half, span="quarter", audited="Unaudited", period_start="2024-10-01",
                        period_end="2024-12-31", revenue="1000000000", other_income="1000000",
                        total_income="1001000000", pbt="100000000", tax="25000000", net_profit="75000000",
                        eps_basic="3.4", eps_diluted="3.4")
        typo = dict(december, symbol="TYPO", isin="INE903I01010")  # digits swapped in the company's own file
        renamed = dict(december, symbol="OLDNAME", isin="INE009Z01019")
        # June and September sum to 1,638,613,000: the year less the three quarters is the context less December
        june = dict(december, period_start="2024-04-01", period_end="2024-06-30", total_income="800000000")
        september = dict(december, period_start="2024-07-01", period_end="2024-09-30", total_income="838613000")
        self.rows = []
        for seq, sym, at, name, p in (("I5", "SWARAJ", "2024-08-10T18:00:00", "Q1", june),
                                      ("I6", "SWARAJ", "2024-11-10T18:00:00", "Q2", september),
                                      ("I1", "SWARAJ", "2025-02-10T18:00:00", "Q3", december),
                                      ("I2", "SWARAJ", "2025-05-30T19:44:41", "H2", half),
                                      ("I3", "TYPO", "2025-02-11T18:00:00", "T", typo),
                                      ("I4", "NEWNAME", "2025-02-11T18:00:00", "R", renamed)):
            with open(os.path.join(self.raw, name + ".json"), "w") as fh:
                json.dump(p, fh)
            self.rows.append((seq, sym, None, at, "https://x/%s.xml" % name))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_quarter_derived_from_the_half_year(self):
        counts = fi.rebuild(self.db, self.rows, raw=self.raw, log=None)
        self.assertEqual((counts["stored"], counts["half"], counts["derived"]), (4, 1, 1))
        got = self.db.execute("SELECT seq, period_start, period_end, revenue, other_income, net_profit, eps_basic, "
                              "broadcast_at, derived FROM financials WHERE seq = 'DI2'").fetchone()
        self.assertEqual(got, ("DI2", "2025-01-01", "2025-03-31", "1550609000", "-6583000", "160751000", None,
                               "2025-05-30T19:44:41", "I1"))

    def test_a_quarter_dated_as_the_half_year_is_kept_as_the_quarter(self):
        with open(os.path.join(self.raw, "Q2.json")) as fh:
            september = json.load(fh)
        with open(os.path.join(self.raw, "Q2.json"), "w") as fh:  # June and September now sum to 637,613,000
            json.dump(dict(september, total_income="-162387000"), fh)
        self.assertEqual(fi.rebuild(self.db, self.rows, raw=self.raw, log=None)["derived"], 1)
        got = self.db.execute("SELECT period_start, revenue, eps_basic, derived FROM financials WHERE seq = 'DI2'")
        self.assertEqual(got.fetchone(), ("2025-01-01", "2550609000", "10.71", "period"))

    def test_nothing_when_the_year_matches_neither(self):
        with open(os.path.join(self.raw, "Q1.json")) as fh:
            june = json.load(fh)
        with open(os.path.join(self.raw, "Q1.json"), "w") as fh:
            json.dump(dict(june, total_income="900000000"), fh)
        self.assertEqual(fi.rebuild(self.db, self.rows, raw=self.raw, log=None)["derived"], 0)

    def test_no_derivation_from_a_quarter_published_later(self):
        late = [(s, sym, i, "2025-06-01T10:00:00" if s == "I1" else at, u) for s, sym, i, at, u in self.rows]
        self.assertEqual(fi.rebuild(self.db, late, raw=self.raw, log=None)["derived"], 0)

    def test_mistyped_isin_kept_by_symbol_and_a_different_symbol_refused(self):
        counts = fi.rebuild(self.db, self.rows, raw=self.raw, log=None)
        self.assertEqual((counts["isin_typo"], counts["isin_mismatch"]), (1, 1))
        self.assertEqual(counts["stored"], 4)
        self.assertEqual(self.db.execute("SELECT isin FROM financials WHERE seq = 'I3'").fetchone(), ("INE903I01023",))
        self.assertIsNone(self.db.execute("SELECT 1 FROM financials WHERE seq = 'I4'").fetchone())

    def test_old_no_quarter_answers_are_read_again(self):
        with open(os.path.join(self.raw, "H2.json"), "w") as fh:
            json.dump({"error": "no quarter context ending 2025-03-31"}, fh)
        with open(os.path.join(self.raw, "Q1.json"), "w") as fh:  # a half-year read before the year was kept
            json.dump({"span": "half", "period_end": "2025-03-31"}, fh)
        swaraj = lambda url: xml("INTEGRATED_NONINDAS_SWARAJ_2025-03_consolidated.xml")  # noqa: E731
        self.assertEqual(fi.fetch(self.rows[:4], get=swaraj, raw=self.raw, pause=0, log=None), 2)
        self.assertEqual(fi.fetch(self.rows[:4], get=None, raw=self.raw, pause=0, log=None), 0)


if __name__ == "__main__":
    unittest.main()
