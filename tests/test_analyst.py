import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal

from _load import load

results = load("trader-analyst", "results")
fin = load("core", "financials")
ev = load("core", "events")
bc = load("core", "bhavcopy")

ALPHA, BANK = "INE001A01011", "INE002B01012"
ENDS = ["2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]


def store(path):
    """Made-up results: ALPHA, five consolidated quarters and one standalone; BANK, two quarters a year apart."""
    db = fin.connect(path)
    ev.connect(path).close()
    bc.store_day(db, [{"date": "2025-01-30", "isin": i, "symbol": s, "series": "EQ", "open": 1, "high": 1, "low": 1,
                       "close": 1, "last": 1, "prev_close": 1, "volume": 1, "value": 1, "trades": 1}
                      for i, s in ((ALPHA, "ALPHA"), (BANK, "BANKX"))])
    cols = ", ".join("seq symbol isin nature audited period_start period_end revenue other_income total_income pbt tax "
                     "net_profit net_profit_owners eps_basic eps_diluted interest_earned interest_expended "
                     "operating_profit provisions rounding broadcast_at xbrl".split())
    rows = []
    for k, end in enumerate(ENDS):
        rev = 1000_0000000 + k * 50_0000000  # Rs 1,000 crore rising by Rs 50 crore a quarter
        other = 10_0000000 if k < 4 else 100_0000000  # the last quarter leans on other income
        rows.append(("a%d" % k, "ALPHA", ALPHA, "Consolidated", "Unaudited", None, end, str(rev), str(other), None,
                     str(200_0000000 + k * 10_0000000), None, str(150_0000000 + k * 10_0000000), None,
                     str(Decimal("10.0") + k), None, None, None, None, None, None, end + "T18:00:00", None))
    rows.append(("s0", "ALPHA", ALPHA, "Standalone", "Audited", None, ENDS[-1], "500000000", None, None, None, None,
                 "50000000", None, "2.5", None, None, None, None, None, None, ENDS[-1] + "T18:00:00", None))
    for k, end in enumerate(("2023-12-31", "2024-12-31")):
        rows.append(("b%d" % k, "BANKX", BANK, "Standalone", "Unaudited", None, end, None, None, None,
                     str(300_0000000), None, str(200_0000000 + k * 40_0000000), None, "5", None,
                     str(2000_0000000), str(1200_0000000), str(500_0000000), str(100_0000000), None,
                     end + "T18:00:00", None))
    db.executemany("INSERT INTO financials (%s) VALUES (%s)" % (cols, ",".join("?" * 23)), rows)
    db.execute("INSERT INTO results VALUES ('r1','ALPHA',?, '2025-01-01','2025-03-31','Q4','Consolidated','Audited',"
               "'2025-04-20T17:00:00')", (ALPHA,))
    db.commit()
    db.close()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = results.main(args)
    return code, out.getvalue(), err.getvalue()


class Analyst(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "m.db")
        store(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_revised_filing_wins_over_the_original(self):
        db = results.connect(self.db)
        original = db.execute("SELECT * FROM financials WHERE seq = 'a4'").fetchone()
        db.execute("DELETE FROM financials WHERE seq = 'a4'")
        db.execute("INSERT INTO financials (seq, symbol, isin, nature, period_end, revenue, net_profit, broadcast_at) "
                   "VALUES ('a4r', 'ALPHA', ?, 'Consolidated', ?, '1200000000', '1700000000', ?)",
                   (ALPHA, ENDS[-1], ENDS[-1] + "T19:00:00"))
        db.execute("INSERT INTO financials VALUES (%s)" % ",".join("?" * len(original)), original)  # stored after
        db.commit()
        qs, _ = results.quarters(db, ALPHA)
        self.assertEqual((qs[0]["period_end"], qs[0]["net_profit"]), (ENDS[-1], Decimal("1700000000")))

    def test_quarters_growth_ttm_and_one_offs(self):
        c = results.company(results.connect(self.db), "ALPHA", 4)
        self.assertEqual(c["nature"], "Consolidated")
        q = c["quarters"][0]
        self.assertEqual(q["period_end"], "2024-12-31")
        self.assertEqual(q["year_ago"], "2023-12-31")
        self.assertEqual(q["top_line_yoy"], Decimal("20.0"))  # 1,200 crore against 1,000
        self.assertEqual(q["profit_yoy"], Decimal("26.7"))  # 190 against 150
        self.assertEqual(q["eps_yoy"], Decimal("40.0"))  # 14 against 10
        self.assertEqual(q["net_margin"], Decimal("15.8"))
        self.assertTrue(any("other income is 42%" in f for f in q["flags"]))
        self.assertEqual(c["quarters"][1]["flags"], [])
        t = c["ttm"]
        self.assertEqual(t["from"], "2024-03-31")
        self.assertEqual(t["top_line"], Decimal(4500_0000000))  # 1,050 + 1,100 + 1,150 + 1,200 crore
        self.assertEqual(t["eps_basic"], Decimal("50.0"))
        self.assertIn("2025-03-31", c["notes"][0])  # later results published, not in the numbers

    def test_standalone_on_request(self):
        c = results.company(results.connect(self.db), "ALPHA", 4, "Standalone")
        self.assertEqual([q["period_end"] for q in c["quarters"]], ["2024-12-31"])
        self.assertIsNone(c["ttm"])

    def test_banks_read_their_own_lines(self):
        c = results.company(results.connect(self.db), BANK, 4)
        q = c["quarters"][0]
        self.assertTrue(c["bank"])
        self.assertEqual(q["net_interest_income"], Decimal(800_0000000))
        self.assertEqual(q["profit_yoy"], Decimal("20.0"))
        self.assertEqual(q["net_margin"], Decimal("12.0"))  # profit over interest earned
        self.assertEqual(c["symbol"], "BANKX")

    def test_report_metrics_file_and_errors(self):
        out_file = os.path.join(self.tmp.name, "metrics.json")
        code, out, err = run(["ALPHA", "BANKX", "NOSUCH", "--db", self.db, "--metrics", out_file])
        self.assertEqual(code, 1)  # one name not found
        self.assertIn("NOSUCH: no stored prices", err)
        self.assertIn("NOTE: results for the quarter ending 2025-03-31", out)
        self.assertIn("interest earned", out)
        with open(out_file) as fh:
            rows = {r["symbol"]: r for r in json.load(fh)["companies"]}
        self.assertEqual(rows["ALPHA"]["revenue_ttm_cr"], 4500.0)
        self.assertEqual(rows["ALPHA"]["revenue_growth_yoy"], 20.0)
        self.assertIsNone(rows["BANKX"]["revenue_ttm_cr"])  # two quarters: no four-quarter total
        code, out, _ = run(["ALPHA", "--db", self.db, "--json"])
        self.assertEqual(json.loads(out)["companies"][0]["quarters"][0]["eps_basic"], "14.0")

    def test_no_store(self):
        code, _, err = run(["ALPHA", "--db", os.path.join(self.tmp.name, "none.db")])
        self.assertEqual(code, 2)
        self.assertIn("no market store", err)


if __name__ == "__main__":
    unittest.main()
