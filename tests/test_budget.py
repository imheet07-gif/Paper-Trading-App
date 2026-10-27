import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, ROOT

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
# illustrative round prices, not quotes
PRICES = {"RELIANCE": Decimal("1400"), "TCS": Decimal("3000"), "HDFCBANK": Decimal("950"),
          "ITC": Decimal("410"), "INFY": Decimal("1500")}
SHARES = {"RELIANCE": 17, "TCS": 8, "HDFCBANK": 20, "ITC": 34, "INFY": 6}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def rows(path=TARGETS):
    with open(path) as fh:
        return size.parse_weights(json.load(fh))


def shares(result):
    return {p["symbol"]: p["shares"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheExample(unittest.TestCase):
    """Rs 1,00,000, 10% cash, 25% cap, the example targets at round prices."""

    def test_without_charges(self):
        r = size.size(rows(), cfg(broker=None), prices=PRICES)
        self.assertEqual(shares(r), SHARES)
        self.assertEqual(r["cash_buffer"], Decimal("10000.00"))
        self.assertEqual(r["cap"], Decimal("25000.00"))
        self.assertEqual(r["invested"], Decimal("89740"))
        self.assertEqual(r["idle"], Decimal("260"))
        self.assertIsNone(r["charges"])
        self.assertEqual([p["symbol"] for p in r["positions"] if p["capped"]], ["RELIANCE"])

    def test_with_zerodha_charges_kept_inside_the_money(self):
        r = size.size(rows(), cfg(), prices=PRICES)
        self.assertEqual(shares(r), SHARES)
        self.assertEqual(r["charges"], Decimal("106.56"))
        self.assertEqual(r["idle"], Decimal("153.44"))
        self.assertEqual(r["invested"] + r["charges"] + r["idle"], r["investable"])

    def test_charges_that_do_not_fit_take_a_share_off(self):
        # Rs 10,000 exactly in one stock at Rs 1,000: ten shares plus charges would overspend
        r = size.size([{"symbol": "ABC", "isin": "", "name": "", "weight": Decimal(1)}],
                      cfg(cash_buffer_pct=0, max_position_pct=100, min_order_inr=0),
                      budget=10000, prices={"ABC": Decimal("1000")})
        self.assertEqual(shares(r), {"ABC": 9})
        self.assertLessEqual(r["invested"] + r["charges"], Decimal("10000"))
        self.assertGreaterEqual(r["idle"], 0)

    def test_inline_weights_match_the_file(self):
        w = size.parse_weights(size.weights_from_arg("RELIANCE=30,TCS=25,HDFCBANK=20,ITC=15,INFY=10"))
        self.assertEqual(shares(size.size(w, cfg(), prices=PRICES)), SHARES)

    def test_no_position_crosses_the_cap(self):
        r = size.size(rows(), cfg(), prices=PRICES)
        for p in r["positions"]:
            self.assertLessEqual(p["value"], r["cap"], p["symbol"])


class Rules(unittest.TestCase):
    def test_blocked_symbols_are_dropped_and_shared_out(self):
        r = size.size(rows(), cfg(blocked=["ITC"]), prices=PRICES)
        self.assertNotIn("ITC", shares(r))
        self.assertEqual(r["dropped"], [{"symbol": "ITC", "reason": "on your blocked list"}])

    def test_a_share_dearer_than_its_slice_is_dropped(self):
        prices = dict(PRICES, INFY=Decimal("30000"))
        r = size.size(rows(), cfg(), prices=prices)
        self.assertNotIn("INFY", shares(r))
        self.assertIn("one share costs", r["dropped"][0]["reason"])

    def test_under_the_minimum_order_is_dropped(self):
        r = size.size(rows(), cfg(min_order_inr=10000), prices=PRICES)
        self.assertNotIn("INFY", shares(r))
        self.assertIn("minimum order", r["dropped"][0]["reason"])

    def test_top_n(self):
        r = size.size(rows(), cfg(), prices=PRICES, top=2)
        self.assertEqual(sorted(shares(r)), ["RELIANCE", "TCS"])

    def test_prices_are_never_guessed(self):
        with self.assertRaisesRegex(size.SizeError, "need a price for: INFY"):
            size.size(rows(), cfg(), prices={k: v for k, v in PRICES.items() if k != "INFY"})

    def test_unknown_broker_is_an_error_not_a_default(self):
        with self.assertRaisesRegex(size.SizeError, "no rates for broker"):
            size.size(rows(), cfg(broker="nobody"), prices=PRICES)

    def test_duplicates(self):
        with self.assertRaisesRegex(size.SizeError, "listed twice"):
            size.size(size.parse_weights([{"symbol": "TCS", "weight": 1}, {"symbol": "tcs", "weight": 2}]),
                      cfg(), prices=PRICES)
        same_isin = [{"symbol": "AAA", "isin": "INE000A01011", "weight": 1},
                     {"symbol": "BBB", "isin": "INE000A01011", "weight": 1}]
        with self.assertRaisesRegex(size.SizeError, "share the ISIN"):
            size.size(size.parse_weights(same_isin), cfg(), prices={"AAA": 1, "BBB": 1})

    def test_bad_inputs(self):
        with self.assertRaisesRegex(size.SizeError, "no budget"):
            size.size(rows(), cfg(budget=None), prices=PRICES)
        with self.assertRaisesRegex(size.SizeError, "not a number"):
            size.parse_weights({"TCS": "lots"})
        with self.assertRaisesRegex(size.SizeError, "cash_buffer_pct"):
            size.size(rows(), cfg(cash_buffer_pct=100), prices=PRICES)


class OrderSheet(unittest.TestCase):
    def test_lines_are_whole_share_delivery_market_buys(self):
        r = size.size(rows(), cfg(), prices=PRICES)
        orders = size.orders_from(r, "example")
        first = orders[0]
        self.assertEqual({k: first[k] for k in ("symbol", "exchange", "side", "qty", "product", "order_type")},
                         {"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": 17,
                          "product": "delivery", "order_type": "market"})
        self.assertEqual(first["value"], "23800.00")
        self.assertIn("est_charges", first)
        self.assertIn("cap", first["reason"])
        for o in orders:
            self.assertIsInstance(o["qty"], int)
            self.assertNotIn("notional", o)

    def test_isin_is_carried_through(self):
        w = size.parse_weights([{"symbol": "TCS", "isin": "ine467b01029", "weight": 1}])
        r = size.size(w, cfg(max_position_pct=100), prices=PRICES)
        self.assertEqual(size.orders_from(r)[0]["isin"], "INE467B01029")


class Cli(unittest.TestCase):
    def test_write_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            prices = os.path.join(d, "prices.json")
            with open(prices, "w") as fh:
                json.dump({"as_of": "2026-10-27", "prices": {k: str(v) for k, v in PRICES.items()}}, fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", CFG, "--prices", prices, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("₹1,00,000", text)
            self.assertIn("BUY 17 RELIANCE on NSE", text)
            self.assertIn("tick sizes, price bands and circuit limits", text)
            self.assertIn("not financial advice", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["created_by"], "trader-budget")
            self.assertEqual(doc["currency"], "INR")
            self.assertIs(doc["paper"], True)
            self.assertEqual(doc["prices_as_of"], "2026-10-27")
            self.assertEqual([o["qty"] for o in doc["orders"]], [17, 8, 20, 34, 6])

            code, text, _ = run(["--weights", "TCS=1", "--config", CFG, "--prices", prices, "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(text)["orders"][0]["qty"], 8)

    def test_missing_prices_file(self):
        code, _, err = run(["--weights", "TCS=1", "--config", CFG, "--prices", "/nonexistent.json"])
        self.assertEqual(code, 1)
        self.assertIn("no prices", err)


if __name__ == "__main__":
    unittest.main()
