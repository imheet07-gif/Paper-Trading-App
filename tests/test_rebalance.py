import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX

rebalance = load("trader-rebalance", "rebalance")
check = load("trader-risk", "check")

D = os.path.join(FIX, "trader-rebalance")


def read(name):
    with open(os.path.join(D, name)) as fh:
        return json.load(fh)


def setup(cash_add=0, prices=None, **cfg_over):
    pf = read("portfolio.json")
    pf["cash"] = str(Decimal(pf["cash"]) + cash_add)
    px = rebalance.parse_prices(read("prices.json"))
    px.update({k: Decimal(str(v)) for k, v in (prices or {}).items()})
    cfg = read("config.json")
    cfg.update(cfg_over)
    return pf, px, rebalance.parse_targets(read("targets.json"), cfg.get("blocked")), cfg


def orders(res):
    return [(o["side"], o["symbol"], o["qty"]) for o in res["orders"]]


class TheExample(unittest.TestCase):
    """The account trader-budget's example would hold once its sheet filled."""

    def test_just_after_buying_nothing_trades(self):
        res = rebalance.plan(*setup())
        self.assertEqual(res["orders"], [])
        self.assertEqual([r["symbol"] for r in res["rows"]], ["RELIANCE", "TCS", "HDFCBANK", "ITC", "INFY"])
        for r in res["rows"]:
            self.assertLess(abs(r["drift"]), 5, r["symbol"])
        self.assertEqual(res["rows"][0]["target_pct"], Decimal("25.00"))

    def test_new_cash_buys_only_what_left_the_band(self):
        res = rebalance.plan(*setup(cash_add=20000))
        self.assertEqual(orders(res), [("buy", "RELIANCE", 4)])
        hold = [r["symbol"] for r in res["rows"] if r["action"] == "hold"]
        self.assertEqual(hold, ["TCS", "HDFCBANK", "ITC", "INFY"])
        self.assertEqual(res["charges"], Decimal("6.65"))
        self.assertEqual(res["cash_after"], Decimal("24546.79"))

    def test_bigger_deposit_buys_everything_keeps_the_buffer_and_passes_risk(self):
        pf, px, w, cfg = setup(cash_add=150000)
        res = rebalance.plan(pf, px, w, cfg)
        self.assertEqual([o[1] for o in orders(res)], ["RELIANCE", "TCS", "HDFCBANK", "ITC", "INFY"])
        self.assertGreaterEqual(res["cash_after"], res["cash_buffer"])
        risk = check.check(res["orders"], dict(cfg, max_order_inr=100000), portfolio=pf, prices=px, today="2026-10-27")
        self.assertEqual(risk["block"], 0, risk["results"])


class Drift(unittest.TestCase):
    def test_sells_come_before_buys_in_whole_shares(self):
        res = rebalance.plan(*setup(prices={"RELIANCE": 2400}))
        sides = [o["side"] for o in res["orders"]]
        self.assertEqual(sides[0], "sell")
        self.assertEqual(res["orders"][0]["symbol"], "RELIANCE")
        self.assertEqual(sides, sorted(sides, key=lambda s: s != "sell"))
        self.assertLessEqual(res["orders"][0]["qty"], 17)
        self.assertIn("drifted", res["orders"][0]["reason"])
        for o in res["orders"]:
            self.assertIsInstance(o["qty"], int)
            self.assertEqual((o["product"], o["order_type"], o["exchange"]), ("delivery", "market", "NSE"))

    def test_sell_charges_include_the_dp_charge(self):
        res = rebalance.plan(*setup(prices={"RELIANCE": 2400}))
        sell = res["orders"][0]
        self.assertGreater(Decimal(sell["est_charges"]), Decimal("15.34"))

    def test_band(self):
        self.assertEqual(rebalance.plan(*setup(cash_add=20000), band=8)["orders"], [])
        self.assertEqual(len(rebalance.plan(*setup(cash_add=20000), band=1)["orders"]), 5)

    def test_untracked_holdings_are_left_alone_unless_asked(self):
        pf, px, w, cfg = setup()
        pf["positions"].append({"symbol": "M&M", "qty": 2})
        px["M&M"] = Decimal("3000")
        res = rebalance.plan(pf, px, w, cfg)
        mm = [r for r in res["rows"] if r["symbol"] == "M&M"][0]
        self.assertEqual(mm["action"], "untracked, left alone")
        self.assertNotIn("M&M", [o["symbol"] for o in res["orders"]])
        res = rebalance.plan(pf, px, w, cfg, sell_untracked=True)
        self.assertIn(("sell", "M&M", 2), orders(res))

    def test_missing_price_is_an_error(self):
        pf, px, w, cfg = setup()
        del px["ITC"]
        with self.assertRaisesRegex(rebalance.RebalanceError, "no price for ITC"):
            rebalance.plan(pf, px, w, cfg)

    def test_fractional_holdings_are_an_error(self):
        pf, px, w, cfg = setup()
        pf["positions"][0]["qty"] = "17.5"
        with self.assertRaisesRegex(rebalance.RebalanceError, "whole shares"):
            rebalance.plan(pf, px, w, cfg)

    def test_without_a_broker_charges_are_not_counted(self):
        res = rebalance.plan(*setup(cash_add=20000, broker=None))
        self.assertIsNone(res["charges"])
        self.assertNotIn("est_charges", res["orders"][0])

    def test_max_order_and_scaling(self):
        res = rebalance.plan(*setup(cash_add=200000))
        self.assertTrue(all(Decimal(o["value"]) <= 50000 for o in res["orders"]))
        self.assertTrue(any("max order" in n for n in res["notes"]))
        pf, px, w, cfg = setup(cash_add=60000)
        pf["positions"].append({"symbol": "M&M", "qty": 10})
        px["M&M"] = Decimal("3000")
        res = rebalance.plan(pf, px, w, cfg)
        self.assertTrue(any("buys scaled" in n for n in res["notes"]), res["notes"])
        self.assertGreaterEqual(res["cash_after"], res["cash_buffer"])


class Cli(unittest.TestCase):
    def test_write_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            doc = read("portfolio.json")
            doc["cash"] = "30153.44"
            with open(pf, "w") as fh:
                json.dump(doc, fh)
            out = os.path.join(d, "orders.json")
            base = ["--prices", os.path.join(D, "prices.json"), "--targets", os.path.join(D, "targets.json"),
                    "--config", os.path.join(D, "config.json")]
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = rebalance.main(["--portfolio", pf, "--write", out] + base)
            self.assertEqual(code, 0)
            self.assertIn("BUY 4 RELIANCE on NSE", buf.getvalue())
            self.assertIn("price bands and circuit limits", buf.getvalue())
            self.assertIn("not financial advice", buf.getvalue())
            with open(out, encoding="utf-8") as fh:
                written = json.load(fh)
            self.assertEqual(written["created_by"], "trader-rebalance")
            self.assertEqual(written["currency"], "INR")
            with redirect_stdout(io.StringIO()):
                code = rebalance.main(["--portfolio", os.path.join(D, "portfolio.json")] + base)
            self.assertEqual(code, 1)
            err = io.StringIO()
            with redirect_stderr(err):
                code = rebalance.main(["--portfolio", os.path.join(d, "none.json")] + base)
            self.assertEqual(code, 1)
            self.assertIn("no portfolio", err.getvalue())


if __name__ == "__main__":
    unittest.main()
