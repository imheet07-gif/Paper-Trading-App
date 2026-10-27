import os
import shutil
import tempfile
import unittest

from _load import FIX, load

deals = load("core", "deals")
bc = load("core", "bhavcopy")
DIR = os.path.join(FIX, "deals")


def text(name):
    with open(os.path.join(DIR, name), encoding="utf-8") as fh:
        return fh.read()


class Parse(unittest.TestCase):
    def test_bulk_file(self):
        day, rows = deals.parse(text("bulk_2026-10-27.csv"), "bulk")
        self.assertEqual((day, len(rows)), ("2026-10-27", 9))
        r = rows[0]
        self.assertEqual((r["kind"], r["date"], r["symbol"], r["client"], r["side"], r["qty"], r["price"]),
                         ("bulk", "2026-10-27", "AGIIL", "ALPHAGREP SECURITIES PRIVATE LIMITED", "buy", 837618, "227.2"))
        self.assertEqual(r["security"], "Agi Infra Limited")
        self.assertIsNone(r["remarks"])  # "-" means none
        self.assertEqual(sum(1 for r in rows if r["side"] == "sell"), 3)

    def test_no_records(self):
        self.assertEqual(deals.parse(text("block_2026-10-27.csv"), "block"), (None, []))

    def test_bad_rows_are_dropped(self):
        head = "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price\n"
        bad = ("27-OCT-2026,X,X Ltd,A,HOLD,10,1.00\n27-OCT-2026,X,X Ltd,A,BUY,ten,1.00\n"
               "27-OCT-2026,X,X Ltd,A,BUY,10,0\nsoon,X,X Ltd,A,BUY,10,1.00\n27-OCT-2026,Y,Y Ltd,B,SELL,\"1,000\",2.5\n")
        day, rows = deals.parse(head + bad, "block")
        self.assertEqual([(r["symbol"], r["qty"], r["price"]) for r in rows], [("Y", 1000, "2.5")])
        with self.assertRaises(ValueError):
            deals.parse(head, "odd")


class FetchAndStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_fetch_cache_and_rebuild_with_isins(self):
        files = {"bulk.csv": text("bulk_2026-10-27.csv").encode(), "block.csv": text("block_2026-10-27.csv").encode()}
        raw = os.path.join(self.tmp, "raw")
        found = deals.fetch(get=lambda url: files[url.rsplit("/", 1)[-1]], raw=raw, log=None)
        self.assertEqual(found, {"bulk": 9, "block": 0})
        self.assertTrue(os.path.exists(os.path.join(raw, "bulk", "2026-10-27.csv")))
        self.assertFalse(os.path.exists(os.path.join(raw, "block")))  # nothing published, nothing cached
        db = deals.connect(os.path.join(self.tmp, "m.db"))
        p = {"open": 1, "high": 1, "low": 1, "close": 1, "last": 1, "prev_close": 1, "volume": 1, "value": 1, "trades": 1}
        bc.store_day(db, [dict(p, date="2026-10-27", isin="INE000A01011", symbol="AGIIL", series="EQ")])
        self.assertEqual(deals.rebuild(db, raw=raw, log=None), 9)
        got = db.execute("SELECT COUNT(*), COUNT(isin) FROM deals").fetchone()
        self.assertEqual(got, (9, 4))  # the four AGIIL deals find their ISIN
        self.assertEqual(deals.rebuild(db, raw=raw, log=None), 9)  # repeatable
        db.close()


if __name__ == "__main__":
    unittest.main()
