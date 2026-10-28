import datetime as dt
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import load

ins = load("trader-pelosi", "insiders")
ev = load("core", "events")
deals = load("core", "deals")

TODAY = dt.date(2026, 10, 28)


def store(path):
    """A made-up store: two companies, an insider buy, an ESOP, a pledge, a SAST sale and a block deal."""
    db = ev.connect(path)
    deals.connect(path).close()
    ins_rows = [
        ("1", "p1", "ALPHA", "INE001A01011", "TEST PERSON ONE", "Promoter Group", "Market Purchase", "Buy",
         "Equity Shares", 3000, 411000, "2026-10-20", "2026-10-20", "2026-10-22T18:03:00"),
        ("2", "p1", "ALPHA", "INE001A01011", "TEST PERSON TWO", "Employees", "ESOP", "Buy",
         "Equity Shares", 100, 10000, "2026-10-21", "2026-10-21", "2026-10-23T10:00:00"),
        ("3", "p2", "BETA", "INE002B01012", "TEST HOLDER CO PVT LTD", "Promoters", "Pledge Creation", "Pledge",
         "Equity Shares", 50000, None, "2026-10-15", "2026-10-15", "2026-10-24T12:00:00"),
        ("4", "p1", "ALPHA", "INE001A01011", "TEST PERSON ONE", "Promoter Group", "Market Sale", "Sell",
         "Equity Shares", 10, 1000, "2026-01-02", "2026-01-02", "2026-01-05T12:00:00"),  # too old for the first check
    ]
    db.executemany("INSERT INTO insider VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ins_rows)
    db.execute("INSERT INTO sast VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("900", "BETA", "INE002B01012", "Test Holder Co Private Limited", 1, "Open market", "Sale", "Reg29(2)",
                None, 250000, 7000000, None, "2.05", "38.84", "2026-10-19", "2026-10-20", "2026-10-22T18:05:00"))
    db.execute("INSERT INTO deals VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("block", "2026-10-27", "ALPHA", "INE001A01011", "ALPHA LTD", "TEST FUND", "buy", 200000, "150.5", None))
    db.commit()
    db.close()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ins.main(args, today=TODAY)
    return code, out.getvalue(), err.getvalue()


class Insiders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "m.db")
        self.state = os.path.join(self.tmp.name, "state", "pelosi.json")
        store(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def new(self, *extra):
        return run(["--db", self.db, "--json", "new", "--state", self.state,
                    "--portfolio", os.path.join(self.tmp.name, "none.json")] + list(extra))

    def test_first_check_then_only_what_is_new(self):
        code, out, _ = self.new()
        self.assertEqual(code, 0)
        got = json.loads(out)["rows"]
        keys = [r["key"] for r in got]
        self.assertIn("insider:1", keys)
        self.assertIn("insider:3", keys)  # a pledge is shown by default, labelled as one
        self.assertIn("sast:900", keys)
        self.assertNotIn("insider:2", keys)  # an ESOP is not a market trade: only with --all
        self.assertNotIn("insider:4", keys)  # older than the first check's 30 days
        self.assertTrue(any(k.startswith("deal:block") for k in keys))
        code, out, _ = self.new()
        self.assertEqual(code, 1)  # nothing new: exit 1, so a loop can stay quiet
        self.assertEqual(json.loads(out)["rows"], [])
        db = ins.connect(self.db)
        db.execute("INSERT INTO insider VALUES ('5','p2','BETA','INE002B01012','TEST PERSON THREE','Director',"
                   "'Market Purchase','Buy','Equity Shares',500,90000,'2026-10-27','2026-10-27','2026-10-28T19:00:00')")
        db.commit()
        db.close()
        code, out, _ = self.new()
        self.assertEqual([r["key"] for r in json.loads(out)["rows"]], ["insider:5"])

    def test_rows_carry_the_delay_side_and_size(self):
        _, out, _ = self.new()
        r = {x["key"]: x for x in json.loads(out)["rows"]}
        self.assertEqual(r["insider:1"]["side"], "buy")
        self.assertEqual(r["insider:1"]["delay_days"], 2)
        self.assertEqual(r["insider:1"]["value"], "411000")
        self.assertEqual(r["sast:900"]["side"], "sell")
        self.assertEqual(r["sast:900"]["pct"], "2.05")
        self.assertEqual(r["sast:900"]["role"], "promoter")
        self.assertEqual(r["sast:900"]["delay_days"], 2)
        self.assertEqual(r["insider:3"]["kind"], "pledge")
        deal = [x for k, x in r.items() if k.startswith("deal:")][0]
        self.assertEqual(deal["value"], "30100000")

    def test_filters_by_company_person_and_holdings(self):
        _, out, _ = self.new("--company", "BETA")
        self.assertEqual({r["symbol"] for r in json.loads(out)["rows"]}, {"BETA"})
        os.remove(self.state)
        _, out, _ = self.new("--person", "test holder co private limited")
        self.assertEqual(sorted(r["key"] for r in json.loads(out)["rows"]), ["insider:3", "sast:900"])  # both spellings
        os.remove(self.state)
        pf = os.path.join(self.tmp.name, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"positions": [{"symbol": "ALPHA", "qty": 10}]}, fh)
        _, out, _ = run(["--db", self.db, "new", "--state", self.state, "--held", "--portfolio", pf])
        self.assertIn("[you hold this]", out)
        self.assertNotIn("BETA", out.split("By company")[0])

    def test_company_and_person_views(self):
        code, out, _ = run(["--db", self.db, "company", "ALPHA", "--days", "400", "--all"])
        self.assertEqual(code, 0)
        self.assertIn("ESOP", out)
        self.assertIn("sold 10 shares", out)
        self.assertIn("fast, never early", out)
        _, out, _ = run(["--db", self.db, "--json", "person", "Test Person One", "--days", "400"])
        self.assertEqual(sorted(r["key"] for r in json.loads(out)["rows"]), ["insider:1", "insider:4"])

    def test_status_says_which_source_went_quiet(self):
        db = ins.connect(self.db)
        db.execute("DELETE FROM insider WHERE disclosed_at > '2026-06-01'")
        db.commit()
        db.close()
        _, out, _ = run(["--db", self.db, "status"])
        self.assertIn("insider: nothing published after 2026-01-05", out)
        self.assertIn("empty since 2026-05-02", out)

    def test_seen_keys_are_pruned(self):
        old = {"checked_at": "2025-01-01", "seen": {"insider:x": "2024-01-01"}}
        _, state = ins.new_since(ins.connect(self.db), old, TODAY, ins.KINDS)
        self.assertNotIn("insider:x", state["seen"])

    def test_no_store_is_an_error(self):
        code, _, err = run(["--db", os.path.join(self.tmp.name, "nope.db"), "status"])
        self.assertEqual(code, 2)
        self.assertIn("no market store", err)

    def test_kinds(self):
        self.assertEqual(ins.kind_of("Market Purchase"), "market")
        self.assertEqual(ins.kind_of("Open market"), "market")
        self.assertEqual(ins.kind_of("Pledge Creation"), "pledge")
        self.assertEqual(ins.kind_of("Revokation of Pledge"), "pledge")
        self.assertEqual(ins.kind_of("ESOP"), "other")
        self.assertEqual(ins.kind_of("Inter-se-Transfer"), "other")


if __name__ == "__main__":
    unittest.main()
