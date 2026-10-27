import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import FIX, load

cc = load("trader-copycat", "copycat")
sh = load("core", "shareholding")
bc = load("core", "bhavcopy")
size = load("trader-budget", "size")
DIR = os.path.join(FIX, "shareholding")
TCS, DONEAR = "INE467B01029", "INE668D01028"


def rows(name):
    with open(os.path.join(DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def build(tmp):
    """A store from the real fixtures (September 2026, TCS and DONEAR), plus a made-up June 2026
    for TCS: LIC with fewer shares, and a fund named then and gone now."""
    raw = os.path.join(tmp, "raw")
    files = {"SHP_1741905_19102026063741_WEB.xml": "shp_xbrl_TCS_2026-09.xml",
             "SHP_1746677_27102026033452_WEB.xml": "shp_xbrl_DONEAR_2026-09.xml"}

    def get(url):
        name = url.rsplit("/", 1)[-1]
        if name in files:
            with open(os.path.join(DIR, files[name]), "rb") as fh:
                return fh.read()
        if name.endswith(".xml"):
            return b"<html>not found</html"  # what a failed download can look like: skipped, not fatal
        if "symbol=TCS" in url:
            return json.dumps(rows("history_TCS.json")).encode()
        return json.dumps(rows("master_2026-09.json")).encode()
    import datetime as dt
    sh.fetch_latest(get=get, raw=raw, today=dt.date(2026, 10, 27), log=None)
    sh.fetch_history(["TCS"], get=get, raw=raw, pause=0, log=None)
    path = os.path.join(tmp, "market.db")
    db = sh.connect(path)
    sh.rebuild(db, raw=raw, log=None)
    sh.fetch_xbrl(db, get=get, raw=raw, since="2026-09-30", pause=0, log=None)
    sh.rebuild(db, raw=raw, log=None)
    june = db.execute("SELECT record_id, broadcast_at FROM shareholding WHERE isin = ? AND period_end = '2026-06-30'",
                      (TCS,)).fetchone()
    lic_now = db.execute("SELECT shares FROM shareholding_holders WHERE holder_key = 'LIC OF INDIA'").fetchone()[0]
    for holder, group, pct, shares in (("LIC of India", "InsuranceCompanies", "5", lic_now - 1000000),
                                        ("Made Up Fund", "MutualFundsOrUTI", "1.2", 43400000)):
        db.execute("INSERT INTO shareholding_holders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (june[0], TCS, "2026-06-30", june[1], holder, sh.holder_key(holder), group, 0, 0, None, pct, shares))
    p = {"open": 1, "high": 1, "low": 1, "last": 1, "prev_close": 1, "volume": 1, "value": 1, "trades": 1}
    for day in ("2026-06-30", "2026-09-30"):
        bc.store_day(db, [dict(p, date=day, isin=TCS, symbol="TCS", series="EQ", close=300000),   # Rs 3,000, made up
                          dict(p, date=day, isin=DONEAR, symbol="DONEAR", series="EQ", close=10000)])  # Rs 100
    db.execute("CREATE TABLE IF NOT EXISTS deals (kind TEXT, date TEXT, symbol TEXT, isin TEXT, security TEXT, client TEXT,"
               " side TEXT, qty INTEGER, price TEXT, remarks TEXT)")
    db.execute("INSERT INTO deals VALUES ('bulk','2026-10-20','TCS',?,'Tata Consultancy Services','LIC OF INDIA','buy',"
               "1000000,'3010.5',NULL)", (TCS,))
    db.commit()
    db.close()
    return path


class Copycat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = build(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def setUp(self):
        self.follows = os.path.join(self.tmp, "copycat.json")
        if os.path.exists(self.follows):
            os.remove(self.follows)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cc.main(["--db", self.path, "--follows", self.follows] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def db(self):
        return cc.connect(self.path)

    def test_discover_ranks_public_holders_by_value(self):
        quarter, top = cc.discover(self.db())
        self.assertEqual(quarter, "2026-09-30")
        self.assertEqual(top[0]["name"], "LIC of India")
        self.assertEqual(top[0]["kind"], "institution")
        self.assertNotIn("TATA SONS PRIVATE LIMITED", [r["key"] for r in top])  # promoters only on request
        _, withp = cc.discover(self.db(), promoters=True)
        self.assertEqual(withp[0]["key"], "TATA SONS PRIVATE LIMITED")
        _, people = cc.discover(self.db(), who="individuals")
        self.assertTrue(people)
        self.assertTrue(all(r["kind"] == "individual" for r in people))

    def test_search_and_follow(self):
        self.assertTrue(cc.search(self.db(), "lic"))
        self.assertEqual(self.run_cli("follow", "add", "LIC OF INDIA", "--as", "LIC")[0], 0)
        self.assertIn("already following", self.run_cli("follow", "add", "lic of india")[2])
        self.assertIn("nobody is named", self.run_cli("follow", "add", "Nobody At All")[2])
        self.assertEqual(self.run_cli("follow", "alias", "LIC", "Made Up Fund")[0], 0)
        self.assertIn("MADE UP FUND", self.run_cli("follow", "list")[1])
        self.assertEqual(self.run_cli("follow", "remove", "LIC")[0], 0)
        self.assertIn("Following nobody", self.run_cli("follow", "list")[1])

    def test_changes_say_gone_means_sold_or_below_one_percent(self):
        lic = cc.changes(self.db(), ["LIC OF INDIA"], "2026-09-30")
        self.assertEqual([(r["symbol"], r["change"]) for r in lic], [("TCS", "added")])
        self.assertEqual(lic[0]["pct_now"], Decimal("5.65"))
        fund = cc.changes(self.db(), ["MADE UP FUND"], "2026-09-30")
        self.assertEqual(fund[0]["change"], "gone: sold, or now below 1%")
        code, text, _ = self.run_cli("show", "LIC OF INDIA")
        self.assertEqual(code, 0)
        self.assertIn("TCS", text)
        self.assertIn("2026-10-20 bulk  BUY  1000000 TCS at ₹3,010.50", text)
        self.assertIn("sold or fell below 1%", text)

    def test_a_holder_without_an_earlier_filing_is_said_so(self):
        key = self.db().execute("SELECT holder_key FROM shareholding_holders WHERE isin = ? AND promoter = 0 "
                                "AND person = 1 LIMIT 1", (DONEAR,)).fetchone()[0]
        r = cc.changes(self.db(), [key], "2026-09-30")
        self.assertEqual(r[0]["change"], "held (no earlier filing stored)")

    def test_copy_writes_weights_trader_budget_reads(self):
        out = os.path.join(self.tmp, "targets.json")
        code, text, _ = self.run_cli("copy", "LIC OF INDIA", "--write", out)
        self.assertEqual(code, 0)
        with open(out) as fh:
            t = json.load(fh)
        self.assertEqual([(w["symbol"], w["isin"], w["weight"]) for w in t["weights"]], [("TCS", TCS, "100.00")])
        self.assertIn("quarter ended 2026-09-30", t["source"])
        self.assertEqual(size.parse_weights(t)[0]["symbol"], "TCS")

    def test_missing_store(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cc.main(["--db", os.path.join(self.tmp, "none.db"), "discover"])
        self.assertEqual(code, 1)
        self.assertIn("no market store", err.getvalue())


if __name__ == "__main__":
    unittest.main()
