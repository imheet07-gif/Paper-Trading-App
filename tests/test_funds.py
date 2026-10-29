import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal

from _load import FIX, load

funds = load("core", "funds")
DIR = os.path.join(FIX, "funds")


def text(name):
    with open(os.path.join(DIR, name), encoding="utf-8") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = funds.main(args)
    return code, out.getvalue(), err.getvalue()


class Funds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "m.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_layouts_read_by_header(self):
        rows = funds.parse(text("navall_sample.txt"))
        self.assertEqual(len(rows), 5)  # headings and the blank line skipped
        self.assertEqual(rows[4], {"code": "149373", "name": "Axis Nifty 50 Index Fund - Direct Plan - Growth Option",
                                   "isin": "INF846K013D2", "nav": "13.8175", "date": "2026-10-28"})
        self.assertEqual(funds.parse("Scheme Code;Scheme Name;Net Asset Value;Date\n1;X;N.A.;01-Jan-2013\n"), [])
        hist = funds.parse(text("history_sample.txt"))
        self.assertEqual([(r["code"], r["date"], r["nav"]) for r in hist],
                         [("900001", "2013-01-01", "100"), ("900002", "2013-01-01", "99"), ("900001", "2013-01-02", "101.5")])

    def test_plan_and_option_are_not_repeated_in_the_name(self):
        rows = funds.parse("Scheme Code;NAV Name;Plan;Option;Net Asset Value;Date\n"
                           "1;X Index Fund - Direct Plan - Growth;Direct Plan;Growth;10;01-Jan-2013\n")
        self.assertEqual(rows[0]["name"], "X Index Fund - Direct Plan - Growth")

    def test_waits_for_another_writer(self):
        db = funds.connect(self.db)
        self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], funds.BUSY_MS)

    def test_an_unknown_layout_is_an_error(self):
        with self.assertRaises(funds.FundsError):
            funds.parse("Code;Fund;Price\n1;X;2\n")
        with self.assertRaises(funds.FundsError):
            funds.parse("no semicolons here\n")

    def test_find_direct_growth(self):
        hits = funds.find(funds.parse(text("navall_sample.txt")), "nifty 50 index", direct=True, growth=True)
        self.assertEqual([c for c, _ in hits], ["151165", "149373"])  # plan and option read from their own columns

    def test_fetch_in_windows_keeps_only_the_schemes_asked(self):
        asked = []

        def get(url):
            asked.append(url)
            return text("history_sample.txt").encode() if len(asked) == 1 else b""
        db = funds.connect(self.db)
        n, failed = funds.fetch(db, ["900001"], "2013-01-01", "2013-02-15", get=get, pause=0, log=None,
                                raw=os.path.join(self.tmp.name, "raw"))
        self.assertEqual((n, failed), (2, []))
        self.assertEqual(len(asked), 2)  # 46 days: two windows of at most 30
        self.assertIn("frmdt=01-Jan-2013&todt=30-Jan-2013", asked[0])
        self.assertEqual(funds.history(db, "900001"), [("2013-01-01", Decimal("100")), ("2013-01-02", Decimal("101.5"))])
        self.assertEqual(funds.history(db, "900002"), [])

    def test_a_failed_window_is_skipped_and_retried_next_run(self):
        calls = []

        def flaky(url):
            calls.append(url)
            if len(calls) == 1:
                raise TimeoutError("The read operation timed out")
            return text("history_sample.txt").encode()
        raw = os.path.join(self.tmp.name, "raw")
        db = funds.connect(self.db)
        n, failed = funds.fetch(db, ["900001"], "2013-01-01", "2013-02-15", get=flaky, pause=0, log=None, raw=raw)
        self.assertEqual(failed, ["2013-01-01 to 2013-01-30"])
        self.assertEqual(n, 2)  # the second window still read
        self.assertFalse(os.path.exists(os.path.join(raw, "history_2013-01-01_2013-01-30.txt")))  # not cached
        n, failed = funds.fetch(db, ["900001"], "2013-01-01", "2013-02-15", get=flaky, pause=0, log=None, raw=raw)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 3)  # only the failed window was asked for again

    def test_cli_import_show_and_find(self):
        path = os.path.join(DIR, "history_sample.txt")
        code, out, _ = run(["--db", self.db, "import", path, "--scheme", "900001"])
        self.assertEqual(code, 0)
        self.assertIn("2 NAVs stored", out)
        code, out, _ = run(["--db", self.db, "show", "900001"])
        self.assertIn("2 NAVs from 2013-01-01", out)
        code, _, err = run(["--db", self.db, "show", "999"])
        self.assertEqual(code, 2)
        code, out, _ = run(["find", "nifty 50 index", "--direct", "--growth", "--file", os.path.join(DIR, "navall_sample.txt")])
        self.assertIn("149373", out)


if __name__ == "__main__":
    unittest.main()
