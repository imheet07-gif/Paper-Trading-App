import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

scr = load("trader-screener", "screen")
DIR = os.path.join(FIX, "trader-screener")
METRICS = os.path.join(DIR, "metrics.json")
PRICES = os.path.join(DIR, "prices.json")
RULES = os.path.join(ROOT, "trader-screener", "rules.example.json")


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scr.main(args)
    return code, out.getvalue(), err.getvalue()


def companies(prices=True):
    rows = scr.load_metrics(METRICS)
    if prices:
        scr.add_prices(rows, PRICES)
    return rows


class Prices(unittest.TestCase):
    def test_pe_and_earnings_yield(self):
        by = {c["symbol"]: c for c in companies()}
        self.assertEqual(by["ALPHA"]["pe"], 39.2)      # 3410 / 87.1
        self.assertEqual(by["CHARLIE"]["pe"], 17.8)    # 3537.8 / 199.1
        self.assertEqual(by["ECHO"]["earnings_yield"], 8.04)
        self.assertEqual(by["DELTA"]["price"], 901.1)

    def test_no_pe_for_losses(self):
        rows = [{"symbol": "LOSS", "eps_ttm": -1.0}]
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "prices.json")
            with open(p, "w") as fh:
                json.dump({"prices": {"LOSS": 10}}, fh)
            scr.add_prices(rows, p)
        self.assertNotIn("pe", rows[0])
        self.assertEqual(rows[0]["earnings_yield"], -10.0)


class Screen(unittest.TestCase):
    def setUp(self):
        self.spec = scr.load_rules(RULES)
        self.results = scr.screen(companies(), self.spec)

    def test_the_example_screen(self):
        passed = [(r["symbol"], r["score"]) for r in self.results if r["passed"]]
        self.assertEqual(passed, [("CHARLIE", 0.75), ("DELTA", 0.25)])
        self.assertEqual(self.results[0]["isin"], "INE000C01011")
        fails = {r["symbol"]: r["fails"] for r in self.results if not r["passed"]}
        self.assertEqual(fails["ALPHA"], ["pe 39.2, needs <= 35"])
        self.assertEqual(fails["ECHO"], ["operating_margin is missing"])
        self.assertEqual(fails["BRAVO"], ["revenue_growth_yoy 8.6, needs >= 10",
                                          "operating_margin is missing"])

    def test_missing_can_be_skipped(self):
        spec = dict(self.spec, missing="skip")
        res = {r["symbol"]: r for r in scr.screen(companies(), spec)}
        self.assertTrue(res["ECHO"]["passed"])
        self.assertEqual(res["ECHO"]["skipped"], ["operating_margin is missing"])
        self.assertIn("operating_margin", res["ECHO"]["rank_gaps"])

    def test_every_op(self):
        c = {"symbol": "DELTA", "sector": "Consumer Staples", "pe": 28.3}
        cases = [({"metric": "pe", "op": "between", "value": [10, 30]}, True),
                 ({"metric": "pe", "op": "between", "value": [10, 20]}, False),
                 ({"metric": "pe", "op": "<", "value": 28.3}, False),
                 ({"metric": "pe", "op": "!=", "value": 1}, True),
                 ({"metric": "symbol", "op": "in", "value": ["delta", "echo"]}, True),
                 ({"metric": "symbol", "op": "not in", "value": ["DELTA"]}, False),
                 ({"metric": "sector", "op": "==", "value": "consumer staples"}, True),
                 ({"metric": "sector", "op": ">", "value": 3}, False),
                 ({"metric": "beta", "op": "<", "value": 1}, None)]
        for rule, want in cases:
            self.assertEqual(scr.check(c, rule)[0], want, rule)

    def test_bad_rules_are_refused(self):
        for bad in ({"rules": [{"metric": "pe", "op": "~", "value": 1}]},
                    {"rules": [{"metric": "pe", "op": "between", "value": 3}]},
                    {"rules": [{"metric": "pe", "op": "<"}]},
                    {"rules": [], "rank": [{"metric": "pe", "order": "up"}]},
                    {"rules": [], "missing": "maybe"}):
            with self.assertRaises(scr.ScreenError):
                scr.check_rules(bad)


class Cli(unittest.TestCase):
    def test_text_report_and_shortlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "trader", "shortlist.json")
            code, text, err = run([METRICS, "--rules", RULES, "--prices", PRICES, "--out", out])
            self.assertEqual(code, 0, err)
            with open(out) as fh:
                sl = json.load(fh)
        self.assertIn("2 of 5 companies pass", text)
        self.assertIn("Prices as of 2026-10-27", text)
        self.assertIn("not a recommendation", text)
        self.assertNotIn(chr(0x2014), text)
        self.assertEqual([p["symbol"] for p in sl["passed"]], ["CHARLIE", "DELTA"])
        self.assertEqual(len(sl["failed"]), 3)

    def test_csv_with_units_and_nothing_passing(self):
        csv_path = os.path.join(DIR, "companies.csv")
        rows = scr.load_metrics(csv_path)
        self.assertEqual(rows[0]["eps_ttm"], 87.1)              # ₹87.10
        self.assertEqual(rows[1]["eps_ttm"], 164.7)             # Rs. 164.70
        self.assertEqual(rows[2]["eps_ttm"], 31.8)              # INR 31.80
        self.assertEqual(rows[0]["revenue_cr"], 123456.78)      # 1,23,456.78
        self.assertEqual(rows[0]["revenue_growth_yoy"], 16.4)
        self.assertIsNone(rows[1]["operating_margin"])
        code, text, _ = run([csv_path, "--rules", RULES])
        self.assertEqual(code, 1)   # no prices, so no pe: nothing passes
        self.assertIn("pe is missing", text)

    def test_bad_input_exit_2(self):
        code, _, err = run(["/nonexistent.json", "--rules", RULES])
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_top_limits_the_shortlist(self):
        code, text, _ = run([METRICS, "--rules", RULES, "--prices", PRICES, "--top", "1",
                             "--json"])
        self.assertEqual(code, 0)
        self.assertEqual([p["symbol"] for p in json.loads(text)["passed"]], ["CHARLIE"])

    def test_ticker_column_is_read_as_symbol_and_duplicates_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "m.json")
            with open(p, "w") as fh:
                json.dump([{"ticker": "tcs", "pe": 30}], fh)
            self.assertEqual(scr.load_metrics(p)[0]["symbol"], "TCS")
            with open(p, "w") as fh:
                json.dump([{"symbol": "TCS"}, {"ticker": "tcs"}], fh)
            with self.assertRaises(scr.ScreenError):
                scr.load_metrics(p)


if __name__ == "__main__":
    unittest.main()
