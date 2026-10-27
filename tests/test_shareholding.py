import json
from decimal import Decimal
import os
import tempfile
import unittest

from _load import FIX, load

sh = load("core", "shareholding")
DIR = os.path.join(FIX, "shareholding")


def rows(name):
    with open(os.path.join(DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


class Parse(unittest.TestCase):
    def test_an_ordinary_filing(self):
        r = sh.row(rows("master_2026-09.json")[0])
        self.assertEqual((r["symbol"], r["isin"], r["period_end"]), ("DONEAR", "INE668D01028", "2026-09-30"))
        self.assertEqual((r["promoter_pct"], r["public_pct"], r["employee_trusts_pct"]), ("67.55", "32.45", "0"))
        self.assertEqual((r["broadcast_at"], r["submitted_on"], r["revised"], r["revised_on"]),
                         ("2026-10-27T15:34:58", "2026-10-27", 0, None))
        self.assertTrue(r["xbrl"].startswith("https://nsearchives.nseindia.com/corporate/xbrl/SHP_"))

    def test_a_revision_keeps_the_original_submission_day(self):
        tcs = {x["period_end"]: x for x in (sh.row(r) for r in rows("history_TCS.json"))}
        mar = tcs["2026-03-31"]
        self.assertEqual((mar["revised"], mar["submitted_on"], mar["revised_on"], mar["broadcast_at"]),
                         (1, "2026-04-21", "2026-09-16", "2026-09-17T13:00:28"))
        epl = sh.row(rows("master_2026-09.json")[1])
        self.assertEqual((epl["revised"], epl["promoter_pct"], epl["public_pct"]), (1, "0", "100"))

    def test_tcs_history(self):
        tcs = [sh.row(r) for r in rows("history_TCS.json")]
        self.assertEqual(len(tcs), 21)
        self.assertEqual(min(x["period_end"] for x in tcs), "2021-09-30")
        self.assertEqual({x["isin"] for x in tcs}, {"INE467B01029"})
        dec23 = [x for x in tcs if x["period_end"] == "2023-12-31"][0]
        self.assertEqual((dec23["promoter_pct"], dec23["public_pct"]), ("72.41", "27.59"))

    def test_employee_trusts(self):
        self.assertEqual(sh.row(rows("master_2026-09.json")[2])["employee_trusts_pct"], "0.22")

    def test_bad_values(self):
        self.assertIsNone(sh.pct("n/a"))
        self.assertIsNone(sh.pct("101"))
        self.assertIsNone(sh.pct("-1"))
        self.assertIsNone(sh.row({"recordId": "1", "date": "30-SEP-2026", "broadcastDate": "soon"}))
        self.assertIsNone(sh.row(dict(rows("master_2026-09.json")[0], recordId=None)))


def xbrl(sym):
    with open(os.path.join(DIR, "shp_xbrl_%s_2026-09.xml" % sym), "rb") as fh:
        return fh.read()


class Xbrl(unittest.TestCase):
    def test_tcs_categories_match_the_list_and_add_the_detail(self):
        f = sh.xbrl_facts(xbrl("TCS"))
        self.assertEqual((f["symbol"], f["isin"], f["date"]), ("TCS", "INE467B01029", "2026-09-30"))
        d = sh.detail(f)
        self.assertEqual((d["promoter"], d["public"]), ("71.77", "28.23"))  # the same as NSE's list
        self.assertEqual((d["fii"], d["fpi_cat1"], d["fpi_cat2"]), ("9.01", "8.24", "0.77"))
        self.assertEqual((d["dii"], d["mutual_funds"], d["insurance"]), ("13.57", "5.65", "6.8"))
        self.assertEqual((d["retail_small"], d["retail_large"], d["employee_trusts"]), ("4.67", "0.23", "0"))
        self.assertEqual(d["total_shares"], 3618087518)
        self.assertEqual((d["pledged"], d["ndu"], d["other_encumbrance"]), (0, 0, 0))

    def test_a_small_company(self):
        d = sh.detail(sh.xbrl_facts(xbrl("DONEAR")))
        self.assertEqual((d["promoter"], d["public"], d["fii"], d["mutual_funds"]), ("67.55", "32.45", "0", "0"))
        self.assertEqual((d["retail_small"], d["retail_large"], d["total_shares"]), ("12.41", "15.25", 51999999))

    def test_only_category_facts_are_kept(self):
        f = sh.xbrl_facts(xbrl("TCS"))
        self.assertTrue(all(set(v) <= set(sh.KEEP) for v in f["categories"].values()))
        self.assertNotIn("Name", json.dumps(f))

    def test_round_percents_are_plain_numbers(self):
        cats = {"ShareholdingOfPromoterAndPromoterGroupMember": {"ShareholdingAsAPercentageOfTotalNumberOfShares": "0.5"},
                "PublicShareholdingMember": {"ShareholdingAsAPercentageOfTotalNumberOfShares": "0.50"}}
        d = sh.detail({"categories": cats, "flags": {}})
        self.assertEqual((d["promoter"], d["public"]), ("50", "50"))  # never "5E+1"

    def test_missing_values_and_bad_files(self):
        d = sh.detail({"categories": {}, "flags": {}})
        self.assertIsNone(d["promoter"])
        self.assertIsNone(d["pledged"])
        with self.assertRaises(ValueError):
            sh.xbrl_facts(b"<not xml")
        self.assertEqual(sh.xbrl_name("https://nsearchives.nseindia.com/corporate/xbrl/../../SHP_1.xml"), "SHP_1")
        for bad in ("https://nsearchives.nseindia.com/corporate/xbrl/SHP_1.pdf",
                    "https://nsearchives.nseindia.com/corporate/xbrl/SHP 1;.xml"):
            with self.assertRaises(ValueError):
                sh.xbrl_name(bad)


class FetchSurvivesBadFiles(unittest.TestCase):
    def test_a_bad_file_or_a_failed_download_is_skipped_and_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, "raw")
            db = sh.connect(os.path.join(tmp, "m.db"))
            sh._save(sh.raw_path("latest", "2026-10-27", raw), rows("master_2026-09.json"))
            sh.rebuild(db, raw=raw, log=None)

            def get(url):
                if "SHP_1746677" in url:
                    raise OSError("connection reset")
                return b"<html>error page</html"
            logs = []
            self.assertEqual(sh.fetch_xbrl(db, get=get, raw=raw, pause=0, log=logs.append), 0)
            self.assertTrue(any("download failed" in l for l in logs))
            self.assertTrue(any("unreadable" in l for l in logs))
            self.assertFalse(os.path.isdir(os.path.join(raw, "xbrl")))  # nothing cached, so the next run retries
            db.close()

            def bad_history(url):
                if "symbol=TCS" in url:
                    raise OSError("timed out")
                return b"[]"
            self.assertEqual(sh.fetch_history(["TCS", "INFY"], get=bad_history, raw=raw, pause=0, log=None), 1)
            self.assertFalse(os.path.exists(sh.raw_path("history", "TCS", raw)))


class Holders(unittest.TestCase):
    def test_tcs_promoters_and_named_public_holders(self):
        hs = sh.xbrl_facts(xbrl("TCS"))["holders"]
        by = {h["name"]: h for h in hs}
        tata = by["Tata Sons Private Limited"]
        self.assertEqual((tata["group"], tata["promoter"], tata["category"], tata["pct"]),
                         ("OthersIndianShareholders", True, "Bodies Corporate", "0.7174"))
        lic = by["LIC of India"]
        self.assertEqual((lic["group"], lic["promoter"], lic["pct"]), ("InsuranceCompanies", False, "0.0565"))
        promoter_pct = sum(Decimal(h["pct"]) for h in hs if h["promoter"])
        self.assertEqual(promoter_pct * 100, Decimal("71.77"))  # the named promoters add up to the filed stake

    def test_category_rows_and_beneficial_owners_are_not_holders(self):
        for sym in ("TCS", "DONEAR"):
            for h in sh.xbrl_facts(xbrl(sym))["holders"]:
                self.assertTrue(h["name"])
                self.assertNotIn("Beneficial", h["group"])
        groups = {h["group"] for h in sh.xbrl_facts(xbrl("DONEAR"))["holders"]}
        self.assertIn("ResidentIndividualShareholdersHoldingNominalShareCapitalInExcessOfRsTwoLakh", groups)

    def test_holder_key_groups_spellings(self):
        self.assertEqual(sh.holder_key("Mr. Rakesh R. Jhunjhunwala"), sh.holder_key("RAKESH R JHUNJHUNWALA"))
        self.assertEqual(sh.holder_key("ABC Pvt. Ltd."), "ABC PRIVATE LIMITED")
        self.assertEqual(sh.holder_key("A & B Co"), "A AND B COMPANY")
        self.assertNotEqual(sh.holder_key("Rakesh Jhunjhunwala"), sh.holder_key("Rekha Jhunjhunwala"))


class FetchAndStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = os.path.join(self.tmp.name, "raw")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, url):
        self.calls.append(url)
        if "symbol=TCS" in url:
            return json.dumps(rows("history_TCS.json")).encode()
        if "symbol=" in url:
            return b"[]"
        return json.dumps(rows("master_2026-09.json")).encode()

    def test_latest_history_cache_and_rebuild(self):
        import datetime as dt
        sh.fetch_latest(get=self.get, raw=self.raw, today=dt.date(2026, 10, 27), log=None)
        self.assertEqual(self.calls, ["https://www.nseindia.com/api/corporate-share-holdings-master?index=equities"])
        self.assertEqual(sh.latest_symbols(self.raw), ["DONEAR", "EPL", "SPENCERS"])
        n = sh.fetch_history(["TCS", "M&M"], get=self.get, raw=self.raw, pause=0, log=None)
        self.assertEqual(n, 2)
        self.assertTrue(self.calls[-1].endswith("&symbol=M%26M"))
        self.assertEqual(sh.fetch_history(["TCS"], get=self.get, raw=self.raw, pause=0, log=None), 0)  # cached
        self.assertEqual(sh.fetch_history(["TCS"], get=self.get, raw=self.raw, refresh=True, pause=0, log=None), 1)
        with self.assertRaises(ValueError):
            sh.fetch_history(["TCS; DROP"], get=self.get, raw=self.raw, pause=0, log=None)
        db = sh.connect(os.path.join(self.tmp.name, "m.db"))
        sh.rebuild(db, raw=self.raw, log=None)
        self.assertEqual(db.execute("SELECT COUNT(*), COUNT(DISTINCT isin), SUM(revised) FROM shareholding").fetchone(),
                         (24, 4, 2))
        self.assertEqual(db.execute("SELECT promoter_pct FROM shareholding WHERE isin='INE467B01029' "
                                    "AND period_end='2026-09-30'").fetchone(), ("71.77",))
        sh.rebuild(db, raw=self.raw, log=None)  # rebuilding is repeatable
        self.assertEqual(db.execute("SELECT COUNT(*) FROM shareholding").fetchone(), (24,))

        # stage 2: the XBRL of the September 2026 quarter, for the two companies with a sample
        files = {"SHP_1741905_19102026063741_WEB.xml": xbrl("TCS"), "SHP_1746677_27102026033452_WEB.xml": xbrl("DONEAR")}

        def get_xbrl(url):
            self.calls.append(url)
            return files.get(url.rsplit("/", 1)[-1])
        n = sh.fetch_xbrl(db, get=get_xbrl, raw=self.raw, since="2026-09-30", pause=0, log=None)
        self.assertEqual(n, 2)  # EPL and SPENCERS answer nothing here and are skipped
        self.assertEqual(sh.fetch_xbrl(db, get=get_xbrl, raw=self.raw, since="2026-09-30", pause=0, log=None), 0)
        # a cache from before holders were kept is refetched once
        path = sh.raw_path("xbrl", "SHP_1746677_27102026033452_WEB", self.raw)
        with open(path) as fh:
            old = json.load(fh)
        old.pop("version"), old.pop("holders")
        with open(path, "w") as fh:
            json.dump(old, fh)
        self.assertEqual(sh.fetch_xbrl(db, get=get_xbrl, raw=self.raw, since="2026-09-30", pause=0, log=None), 1)
        sh.rebuild(db, raw=self.raw, log=None)
        tata = db.execute("SELECT holder_group, person, promoter, pct, shares, broadcast_at FROM shareholding_holders "
                          "WHERE holder_key = 'TATA SONS PRIVATE LIMITED'").fetchone()
        self.assertEqual(tata, ("OthersIndianShareholders", 0, 1, "71.74", 2595499419, "2026-10-19T18:37:52"))
        people = db.execute("SELECT COUNT(*) FROM shareholding_holders WHERE person = 1").fetchone()[0]
        self.assertGreater(people, 0)
        zero = db.execute("SELECT COUNT(*) FROM shareholding_holders WHERE shares = 0").fetchone()[0]
        self.assertEqual(zero, 0)  # members holding nothing are not stored
        got = db.execute("SELECT isin, promoter, fii, mutual_funds, total_shares, pledged FROM shareholding_detail "
                         "ORDER BY isin").fetchall()
        self.assertEqual(got, [("INE467B01029", "71.77", "9.01", "5.65", 3618087518, 0),
                               ("INE668D01028", "67.55", "0", "0", 51999999, 0)])
        db.close()


if __name__ == "__main__":
    unittest.main()
