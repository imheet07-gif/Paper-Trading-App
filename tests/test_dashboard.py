import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout

from _load import ROOT, load
from test_zerodha_console import fixture_xlsx

dash = load("dashboard", "build")


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.out = os.path.join(d, "dash.html")
        with redirect_stdout(io.StringIO()):
            code = dash.main(["--holdings", fixture_xlsx(d, "console_holdings"),
                             "--tradebook", fixture_xlsx(d, "console_tradebook"), fixture_xlsx(d, "console_tradebook"),
                             "--pnl", fixture_xlsx(d, "console_pnl"), "--out", self.out])
        self.assertEqual(code, 0)
        with open(self.out, encoding="utf-8") as fh:
            self.page = fh.read()

    def tearDown(self):
        self.tmp.cleanup()

    def test_self_contained_no_network(self):
        self.assertIsNone(re.search(r"https?://|src=|<link|@import|url\(", self.page))

    def test_sections_and_checks(self):
        for s in ("Where the money sits", "Holdings", "Realized gains by financial year", "Needs a look",
                  "TMCV", "cost missing", "PARAG PARIKH"):
            self.assertIn(s, self.page)

    def test_overlapping_tradebooks_are_not_double_counted(self):
        tb = dash.zc.parse_tradebook(dash.zc.read_any(fixture_xlsx(self.tmp.name, "console_tradebook")))["trades"]
        self.assertEqual(len(dash.merge_trades(tb, tb)), 4)
        self.assertEqual(dash.build_model(dash.zc.parse_holdings(dash.zc.read_any(
            fixture_xlsx(self.tmp.name, "console_holdings"))), dash.merge_trades(tb, tb))["fills"], 4)

    def test_empty_years_are_filled(self):
        ys = dash.all_years([{"fy": "FY2022-23", "gain": 1, "short": 1, "long": 0, "unmatched_qty": 0},
                             {"fy": "FY2024-25", "gain": 1, "short": 1, "long": 0, "unmatched_qty": 0}])
        self.assertEqual([y["fy"] for y in ys], ["FY2022-23", "FY2023-24", "FY2024-25"])
        self.assertTrue(ys[1]["empty"])

    def test_default_output_is_git_ignored(self):
        self.assertEqual(dash.DEFAULT_OUT, os.path.join(ROOT, "private", "dashboard.html"))


if __name__ == "__main__":
    unittest.main()
