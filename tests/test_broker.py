import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, ROOT

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
size = load("trader-budget", "size")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
TODAY = "2026-10-30"
# illustrative round prices, not quotes
PRICES = {"RELIANCE": "1400", "TCS": "3000", "HDFCBANK": "950", "ITC": "410", "INFY": "1500"}
BATCH = list(PRICES)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


def line(sym="RELIANCE", qty=17, side="buy"):
    return {"symbol": sym, "exchange": "NSE", "side": side, "qty": qty, "product": "delivery", "order_type": "market"}


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=None):
        return ticket.parse_yes(reply, order or line(), BATCH)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes RELIANCE", "yes 17 RELIANCE", "yes 17"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "go", "do it", "yeah", "yes to all", "yes, all of them", "yes both",
                  "yes TCS", "yes 20", "yes but make it 10", "yes?", "no", "not yes", "yes, wait"):
            self.assertFalse(self.yes(r), r)

    def test_the_fingerprint_is_trader_risks(self):
        o = dict(line(), reason="anything", ref_price="1400")
        self.assertEqual(ticket.fingerprint(o), check.fingerprint(o))


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        rows = size.parse_weights(json.load(open(TARGETS)))
        with open(CFG_PATH) as fh:
            self.cfg = json.load(fh)
        res = size.size(rows, self.cfg, prices={k: Decimal(v) for k, v in PRICES.items()})
        size.write_orders(self.orders, size.orders_from(res), res, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "paper_broker.json")
        self.pf = os.path.join(self.d, "paper_portfolio.json")
        self.prices = os.path.join(self.d, "prices.json")
        with open(self.prices, "w") as fh:
            json.dump({"prices": PRICES}, fh)

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra):
        return list(extra) + ["--orders", self.orders, "--config", CFG_PATH, "--risk", self.risk,
                              "--state", self.state, "--today", TODAY]

    def run_risk(self, today=TODAY):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", CFG_PATH, "--portfolio", os.path.join(self.d, "none"),
                               "--prices", self.prices, "--today", today, "--out", self.risk,
                               "--instruments", "/nonexistent/instruments.json"])

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertIn("not ready", err)

    def test_yes_then_a_paper_fill_with_charges(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("PAPER (no money moves)", text)
        self.assertIn("BUY 17 shares of RELIANCE on NSE, delivery, market order", text)
        self.assertIn("READY: trader-risk PASS", text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertIn("Not recorded", text)
        code, _, err = run(self.args("fill", "--index", "1", "--portfolio", self.pf))
        self.assertIn("no yes recorded", err)

        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes 17 RELIANCE"))[0], 0)
        code, text, _ = run(self.args("fill", "--index", "1", "--portfolio", self.pf))
        self.assertEqual(code, 0, text)
        self.assertIn("PAPER FILL: BUY 17 RELIANCE at ₹1,400.00 = ₹23,800.00, charges ₹28.26", text)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertIs(pf["paper"], True)
        self.assertEqual(pf["cash"], "76171.74")  # 1,00,000 - 23,800 - 28.26
        self.assertEqual(pf["positions"][0]["lots"][0], {"id": "RELIANCE-1", "date": TODAY, "qty": 17, "cost": "23828.26"})
        self.assertIn("DONE: paper-filled", run(self.args("show", "--index", "1"))[1])
        self.assertEqual(run(self.args("fill", "--index", "1", "--portfolio", self.pf))[0], 1)  # once only
        self.assertIn("1 paper-filled", run(["log", "--state", self.state, "--today", TODAY])[1])

    def test_a_sell_relieves_the_oldest_lot(self):
        pf = {"paper": True, "cash": "0", "positions": [{"symbol": "ITC", "qty": 30, "lots": [
            {"id": "ITC-1", "date": "2026-01-02", "qty": 20, "cost": "8000"},
            {"id": "ITC-2", "date": "2026-06-02", "qty": 10, "cost": "4200"}]}]}
        f = ticket.apply_fill(pf, line("ITC", 25, "sell"), Decimal("410"), {"broker": "zerodha"}, TODAY)
        self.assertEqual([l["id"] for l in pf["positions"][0]["lots"]], ["ITC-2"])
        self.assertEqual(pf["positions"][0]["lots"][0], {"id": "ITC-2", "date": "2026-06-02", "qty": 5, "cost": "2100.00"})
        self.assertEqual(f["value"], "10250.00")
        self.assertEqual(Decimal(pf["cash"]), Decimal("10250") - Decimal(f["charges"]))
        with self.assertRaises(ticket.TicketError):
            ticket.apply_fill(pf, line("ITC", 50, "sell"), Decimal("410"), {}, TODAY)

    def test_only_a_paper_portfolio_is_ever_filled(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        with open(self.pf, "w") as fh:
            json.dump({"cash": "1000000", "positions": []}, fh)  # a real account file
        code, _, err = run(self.args("fill", "--index", "1", "--portfolio", self.pf))
        self.assertEqual(code, 1)
        self.assertIn("not marked as a paper portfolio", err)

    def test_an_edited_line_is_not_ready_and_yesterday_does_not_count(self):
        self.run_risk(today="2026-10-29")
        self.assertIn("not today", run(self.args("show", "--index", "1"))[1])
        self.run_risk()
        with open(self.orders) as fh:
            doc = json.load(fh)
        doc["orders"][0]["qty"] = 18
        with open(self.orders, "w") as fh:
            json.dump(doc, fh)
        self.assertIn("was not in the last risk check", run(self.args("show", "--index", "1"))[1])

    def test_decline_is_recorded(self):
        self.run_risk()
        self.assertEqual(run(self.args("decline", "--index", "2", "--reply", "no thanks"))[0], 0)
        self.assertIn("DONE: declined", run(self.args("show", "--index", "2"))[1])


if __name__ == "__main__":
    unittest.main()
