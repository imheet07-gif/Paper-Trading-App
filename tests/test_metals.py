import datetime as dt
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal

from _load import load

metals = load("core", "metals")
bc = load("core", "bhavcopy")


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = metals.main(args)
    return code, out.getvalue(), err.getvalue()


class Listed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "m.db")
        db = bc.connect(self.db_path)
        rows = []
        d = dt.date(2025, 1, 1)
        for i in range(260):  # made-up prices: an SGB, a gold ETF and an ordinary stock
            day = (d + dt.timedelta(days=i)).isoformat()
            for isin, sym, series, px in (("IN0020200104", "SGBTEST28", "GB", 800000 + 100 * i),
                                          ("INF000T01011", "GOLDTEST", "EQ", 6000 + i),
                                          ("INE000T01010", "PLAIN", "EQ", 10000)):
                rows.append({"date": day, "isin": isin, "symbol": sym, "series": series, "open": px, "high": px,
                             "low": px, "close": px, "last": px, "prev_close": px, "volume": 1, "value": px, "trades": 1})
        bc.store_day(db, rows)
        db.commit()
        db.close()
        self.etf = os.path.join(self.tmp.name, "etf.csv")
        with open(self.etf, "w") as fh:
            fh.write("Symbol,Underlying,SecurityName\nGOLDTEST,Gold,Test Gold ETF\nNIFTYTEST,Nifty 50,Test Index ETF\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sgbs_come_from_the_store_and_etfs_from_the_list(self):
        code, out, _ = run(["--json", "listed", "--db", self.db_path])
        rows = json.loads(out)
        self.assertEqual([r["symbol"] for r in rows], ["SGBTEST28"])  # no ETF list: SGBs only
        self.assertEqual(rows[0]["kind"], "SGB")
        self.assertEqual(rows[0]["close"], "8259")
        _, out, _ = run(["--json", "listed", "--db", self.db_path, "--etf-list", self.etf])
        rows = {r["symbol"]: r for r in json.loads(out)}
        self.assertEqual(set(rows), {"SGBTEST28", "GOLDTEST"})
        self.assertEqual(rows["GOLDTEST"]["metal"], "gold")
        self.assertEqual(rows["GOLDTEST"]["return_1m"], str(((Decimal(6259) / Decimal(6238) - 1) * 100).quantize(Decimal("0.1"))))

    def test_an_etf_list_without_the_columns_is_an_error(self):
        bad = os.path.join(self.tmp.name, "bad.csv")
        with open(bad, "w") as fh:
            fh.write("A,B\n1,2\n")
        code, _, err = run(["listed", "--db", self.db_path, "--etf-list", bad])
        self.assertEqual(code, 2)
        self.assertIn("expected a Symbol column", err)


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "metals.json")
        self.rates = os.path.join(self.tmp.name, "rates.json")

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, *args):
        return run(["ledger", "--file", self.file, "--rates", self.rates] + list(args))

    def test_add_list_value_remove(self):
        code, out, _ = self.ledger("add", "--date", "2026-10-25", "--metal", "gold", "--form", "coin", "--grams", "10",
                                   "--fineness", "999", "--price", "78000", "--making", "1200", "--gst", "2376")
        self.assertEqual(code, 0)
        self.assertIn("paid ₹81,576.00", out)
        self.ledger("add", "--date", "2026-10-26", "--metal", "gold", "--form", "jewellery", "--grams", "8",
                    "--fineness", "916", "--price", "56000", "--making", "8000")
        _, out, _ = run(["--json", "ledger", "--file", self.file, "--rates", self.rates, "value"])
        res = json.loads(out)
        self.assertEqual(res["paid"], "145576.00")
        self.assertIsNone(res["value"])  # no rates recorded: never a guessed one
        self.assertEqual(res["missing_rates"], ["gold 916", "gold 999"])
        with open(self.rates, "w") as fh:
            json.dump({"date": "2026-10-27", "source": "a published rate (test)",
                       "rates": [{"metal": "gold", "fineness": "999", "per_gram": "8000"},
                                 {"metal": "gold", "fineness": "916", "per_gram": "7300"}]}, fh)
        _, out, _ = run(["--json", "ledger", "--file", self.file, "--rates", self.rates, "value"])
        res = json.loads(out)
        self.assertEqual(res["value"], "138400.00")  # 10 x 8000 + 8 x 7300
        self.assertEqual(res["rows"][0]["gain"], "-1576.00")
        self.assertEqual(res["rows"][1]["fine_grams"], "7.3280")
        code, out, _ = self.ledger("remove", "2")
        self.assertIn("removed #2", out)
        _, out, _ = self.ledger("list")
        self.assertIn("#1 2026-10-25 gold coin", out)
        self.assertNotIn("#2", out)

    def test_cost_per_fine_gram(self):
        e = metals.purchase("2026-10-25", "gold", "jewellery", "10", "916", "60000", "9000", "2070")
        c = metals.cost_basis(e)
        self.assertEqual(c["fine_grams"], Decimal("9.1600"))
        self.assertEqual(c["per_fine_gram"], Decimal("7758.73"))  # 71070 / 9.16
        self.assertEqual(c["extra_pct"], Decimal("18.5"))

    def test_bad_bills_are_refused(self):
        for kwargs, msg in ((dict(metal="platinum"), "gold or silver"), (dict(fineness="1200"), "at most 1000"),
                            (dict(grams="0"), "above zero"), (dict(date="25/10/2026"), "YYYY-MM-DD"),
                            (dict(form="statue"), "form must be")):
            args = dict(date="2026-10-25", metal="gold", form="coin", grams="1", fineness="999", price="8000")
            args.update(kwargs)
            with self.assertRaises(metals.MetalsError) as cm:
                metals.purchase(**args)
            self.assertIn(msg, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
