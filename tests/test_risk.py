import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
inst = load("core", "instruments")
size = load("trader-budget", "size")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
TODAY = "2026-10-27"
# illustrative round prices, not quotes
PRICES = {"RELIANCE": "1400", "TCS": "3000", "HDFCBANK": "950", "ITC": "410", "INFY": "1500"}


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def example_orders():
    """The order sheet trader-budget writes for its example."""
    with open(TARGETS) as fh:
        rows = size.parse_weights(json.load(fh))
    res = size.size(rows, cfg(), prices={k: Decimal(v) for k, v in PRICES.items()})
    return size.orders_from(res)


def buy(sym, qty, **extra):
    o = {"symbol": sym, "exchange": "NSE", "side": "buy", "qty": qty, "product": "delivery", "order_type": "market"}
    o.update(extra)
    return o


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i):
    return " | ".join(res["results"][i]["reasons"])


class TheExample(unittest.TestCase):
    def test_the_budget_sheet_passes_on_a_fresh_paper_account(self):
        res = check.check(example_orders(), cfg(), prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], "10153.44")  # the buffer plus what trader-budget left idle
        self.assertIs(res["paper"], True)
        self.assertEqual(res["results"][0]["value_inr"], "23800.00")
        self.assertEqual(res["results"][0]["est_charges"], "28.26")

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(example_orders() + [buy("RELIANCE", 1)], cfg(), prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("position:", why)
        self.assertIn("duplicate:", why)

    def test_what_is_not_checked_is_said(self):
        res = check.check([buy("TCS", 1)], cfg(), prices=PRICES, today=TODAY)
        self.assertIn("tick sizes", res["not_checked"])
        self.assertIn("price bands and circuit limits", res["not_checked"])

    def test_fingerprint_ignores_the_reason_and_follows_the_line(self):
        o = example_orders()[0]
        self.assertEqual(check.fingerprint(dict(o, reason="anything")), check.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, qty="17")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty=18)), check.fingerprint(o))


class Limits(unittest.TestCase):
    def one(self, order, **over):
        res = check.check([order], cfg(**over), prices=PRICES, today=TODAY)
        return res["results"][0]

    def test_max_and_min_order(self):
        self.assertIn("over your ₹50,000 max order",
                      " ".join(self.one(buy("TCS", 20), max_position_pct=100, cash_buffer_pct=0)["reasons"]))
        self.assertIn("under your ₹1,000 minimum", " ".join(self.one(buy("ITC", 2))["reasons"]))

    def test_blocked_list_by_symbol_or_isin(self):
        r = self.one(buy("ITC", 10), blocked=["ITC"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])
        r = self.one(buy("ITC", 10, isin="INE154A01025"), blocked=["INE154A01025"])
        self.assertIn("blocked list", r["reasons"][0])

    def test_fo_is_off_unless_allowed(self):
        opt = buy("NIFTY26OCT25000CE", 75, order_type="limit", limit_price="120")
        r = self.one(opt, max_order_inr=100000)
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("futures or options", " ".join(r["reasons"]))
        r = self.one(opt, allow_options=True, max_order_inr=100000)
        self.assertNotIn("asset:", " ".join(r["reasons"]))
        self.assertIn("lot sizes", " ".join(r["warnings"]))

    def test_real_symbol_shapes_pass_and_junk_does_not(self):
        prices = {"M&M": "3000", "BAJAJ-AUTO": "9000", "3MINDIA": "30000"}
        for s in prices:
            r = check.check([buy(s, 1)], cfg(max_order_inr=100000, max_position_pct=100, min_order_inr=0),
                            prices=prices, today=TODAY)["results"][0]
            self.assertNotIn("asset:", " ".join(r["reasons"]), s)
        r = self.one(buy("BTC/INR", 1, ref_price="100"))
        self.assertIn("listed shares only", " ".join(r["reasons"]))

    def test_format_rules(self):
        self.assertIn("whole number", " ".join(self.one(buy("TCS", "1.5"))["reasons"]))
        self.assertIn("whole number", " ".join(self.one(buy("TCS", 0))["reasons"]))
        self.assertIn("not a rupee amount", " ".join(self.one(buy("TCS", 1, notional="3000"))["reasons"]))
        self.assertIn("delivery (CNC) only", " ".join(self.one(buy("TCS", 1, product="intraday"))["reasons"]))
        self.assertIn("NSE or BSE", " ".join(self.one(buy("TCS", 1, exchange="NYSE"))["reasons"]))
        self.assertIn("needs a positive limit_price", " ".join(self.one(buy("TCS", 1, order_type="limit"))["reasons"]))
        self.assertIn("side must be buy or sell", " ".join(self.one(dict(buy("TCS", 1), side="hold"))["reasons"]))

    def test_a_limit_price_is_used_and_flagged(self):
        r = self.one(buy("TCS", 1, order_type="limit", limit_price="2990.05"))
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["value_inr"], "2990.05")
        self.assertIn("tick size", r["warnings"][0])

    def test_lines_need_a_price(self):
        self.assertIn("no price for ZZZZ", " ".join(self.one(buy("ZZZZ", 2))["reasons"]))
        r = self.one(buy("ZZZZ", 2, ref_price="1000"))
        self.assertEqual(r["verdict"], "PASS")
        self.assertIn("ref_price", r["warnings"][0])

    def test_margin(self):
        r = check.check([buy("TCS", 5)], cfg(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": "10000", "positions": []}, prices=PRICES, today=TODAY)["results"][0]
        self.assertIn("margin", " ".join(r["reasons"]))

    def test_unknown_broker_is_an_error(self):
        with self.assertRaisesRegex(check.RiskError, "no rates for broker"):
            check.check([buy("TCS", 1)], cfg(broker="nobody"), prices=PRICES, today=TODAY)


class Isins(unittest.TestCase):
    def reg(self):
        with open(os.path.join(FIX, "core", "EQUITY_L.sample.csv"), encoding="utf-8-sig") as fh:
            return inst.build(fh.read())

    def test_registry_fills_the_isin_and_blocks_by_it(self):
        res = check.check([buy("TCS", 1)], cfg(blocked=["INE467B01029"]), prices=PRICES, today=TODAY, registry=self.reg())
        self.assertEqual(res["results"][0]["isin"], "INE467B01029")
        self.assertIn("blocked list", reasons(res, 0))

    def test_a_wrong_isin_blocks_the_line(self):
        res = check.check([buy("TCS", 1, isin="INE002A01018")], cfg(), prices=PRICES, today=TODAY, registry=self.reg())
        self.assertIn("isin: TCS is listed on NSE with ISIN INE467B01029", reasons(res, 0))

    def test_unknown_symbol_and_no_registry_are_said(self):
        res = check.check([buy("ITC", 10)], cfg(), prices=PRICES, today=TODAY, registry=self.reg())
        self.assertEqual(res["results"][0]["verdict"], "PASS")
        self.assertIn("not in the instrument registry", " ".join(res["results"][0]["warnings"]))
        res = check.check([buy("ITC", 10)], cfg(), prices=PRICES, today=TODAY)
        self.assertIn("ISINs not checked", " ".join(res["warnings"]))


class Account(unittest.TestCase):
    PF = {"cash": "5000", "positions": [{"symbol": "ITC", "qty": 40,
                                          "lots": [{"date": "2026-10-01", "qty": 40, "cost": "16000"}]}]}

    def test_short_sales_are_blocked(self):
        sell = dict(buy("ITC", 50), side="sell")
        r = check.check([sell], cfg(), portfolio=self.PF, prices=PRICES, today=TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        sell["qty"] = 10
        r = check.check([sell], cfg(), portfolio=self.PF, prices=PRICES, today=TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["est_charges"], "19.59")  # core/charges.py, with the Rs 15.34 DP charge on a delivery sell

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [dict(buy("ITC", 30), side="sell"), buy("HDFCBANK", 12)]
        res = check.check(orders, cfg(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("HDFCBANK", 12)], cfg(max_position_pct=100), portfolio=self.PF, prices=PRICES,
                          today=TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_sheet_limit(self):
        orders = [buy(s, 1) for s in ("TCS", "INFY", "RELIANCE")]
        res = check.check(orders, cfg(max_orders_per_day=2, max_position_pct=100), prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("sheet limit", reasons(res, 2))

    def test_live_and_no_broker_warn(self):
        res = check.check([buy("TCS", 1)], cfg(paper=False, broker=None), prices=PRICES, today=TODAY)
        text = " ".join(res["warnings"])
        self.assertIn("LIVE", text)
        self.assertIn("charges are not estimated", text)


class Cli(unittest.TestCase):
    def test_writes_verdicts_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            orders = os.path.join(d, "orders.json")
            with open(orders, "w") as fh:
                json.dump({"orders": example_orders()}, fh)
            prices = os.path.join(d, "prices.json")
            with open(prices, "w") as fh:
                json.dump({"as_of": TODAY, "prices": PRICES}, fh)
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--instruments", "/nonexistent/instruments.json", "--orders", orders, "--config", CFG_PATH, "--portfolio", os.path.join(d, "none"),
                                   "--prices", prices, "--today", TODAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            self.assertIn("Not checked: tick sizes", buf.getvalue())
            self.assertIn("not financial advice", buf.getvalue())
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["prices_as_of"], TODAY)
            self.assertEqual(len(rec["results"]), 5)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))

            with open(orders, "w") as fh:
                json.dump({"orders": example_orders() + [buy("RELIANCE", 1)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--instruments", "/nonexistent/instruments.json", "--orders", orders, "--config", CFG_PATH, "--portfolio", os.path.join(d, "none"),
                                   "--prices", prices, "--today", TODAY, "--no-write"])
            self.assertEqual(code, 1)

    def test_missing_orders_file(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())


if __name__ == "__main__":
    unittest.main()
