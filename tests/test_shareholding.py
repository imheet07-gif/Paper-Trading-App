import json
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
        db.close()


if __name__ == "__main__":
    unittest.main()
