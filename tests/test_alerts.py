import datetime as dt
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, ROOT

alerts = load("trader-alerts", "alerts")
bc = load("core", "bhavcopy")

EXAMPLE = os.path.join(ROOT, "trader-alerts", "alerts.example.json")


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def row(day, isin, symbol, close, prev, series="EQ"):
    """One made-up bhavcopy row, prices in rupees (stored in paise)."""
    c, p = int(Decimal(close) * 100), int(Decimal(prev) * 100)
    return {"date": day, "isin": isin, "symbol": symbol, "series": series, "open": p, "high": max(c, p),
            "low": min(c, p), "close": c, "last": c, "prev_close": p, "volume": 1000, "value": 1000 * c, "trades": 10}


def store(path):
    """A made-up market store: TCS on two days, ITC only in a non-cash series on its last day."""
    db = bc.connect(path)
    bc.store_day(db, [row("2026-10-26", "INE467B01029", "TCS", "3100", "3050"),
                      row("2026-10-27", "INE467B01029", "TCS", "3205.5", "3100"),
                      row("2026-10-27", "INE154A01025", "ITC", "395", "410"),
                      row("2026-10-27", "INE009A01021", "INFY", "1450", "1500"),
                      row("2026-10-27", "INE000X01010", "ODDLOT", "10", "10", series="N1")])
    db.commit()
    db.close()


class Clock(unittest.TestCase):
    def test_phases_on_a_trading_day(self):
        self.assertEqual(alerts.market_status(ist("2026-10-28T09:05"))["phase"], "pre_open_entry")
        self.assertEqual(alerts.market_status(ist("2026-10-28T09:10"))["phase"], "pre_open_matching")
        st = alerts.market_status(ist("2026-10-28T10:00"))
        self.assertEqual(st["phase"], "open")
        self.assertTrue(st["open"])
        self.assertEqual(st["date"], "2026-10-28")
        self.assertEqual(alerts.market_status(ist("2026-10-28T15:45"))["phase"], "closing_session")
        self.assertFalse(alerts.market_status(ist("2026-10-28T16:30"))["open"])

    def test_holidays_and_weekends_from_nse_holidays_json(self):
        st = alerts.market_status(ist("2026-10-20T10:00"))  # Dussehra in core/nse_holidays.json
        self.assertFalse(st["open"])
        self.assertEqual(st["holiday"], "Dussehra")
        self.assertTrue(st["next_open_ist"].startswith("2026-10-21T09:15"))
        sat = alerts.market_status(ist("2026-10-31T10:00"))
        self.assertTrue(sat["next_open_ist"].startswith("2026-11-02T09:15"))

    def test_times_without_an_offset_are_ist(self):
        self.assertEqual(ist("2026-10-28T09:15"), ist("2026-10-28T03:45:00+00:00"))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_a_year_without_a_holiday_list_is_an_error(self):
        with self.assertRaisesRegex(alerts.AlertError, "update-holidays"):
            alerts.market_status(ist("2031-01-06T10:00"))

    def test_clock_command_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-10-28T10:00"])
        self.assertEqual(code, 0)
        self.assertIn("NSE OPEN", text)
        code, text, _ = run(["clock", "--now", "2026-10-20T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Dussehra", text)


class Prices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "market.db")
        store(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_gives_the_latest_cash_market_close(self):
        q, errors = alerts.store_quotes(["TCS", "ODDLOT", "NOPE"], self.db)
        self.assertEqual(q["TCS"]["price"], Decimal("3205.5"))
        self.assertEqual(q["TCS"]["prev_close"], Decimal("3100"))
        self.assertEqual((q["TCS"]["isin"], q["TCS"]["time"]), ("INE467B01029", "2026-10-27"))
        self.assertNotIn("ODDLOT", q)  # odd-lot series is not a cash-market price
        self.assertEqual(len(errors), 2)

    def test_file_source_and_no_guessing(self):
        p = os.path.join(self.tmp.name, "kite.json")
        with open(p, "w") as fh:
            json.dump({"as_of": "2026-10-28T10:05+05:30", "prices": {"tcs": "3,210.25", "BAD": "n/a"}}, fh)
        q, errors = alerts.file_quotes(["TCS", "BAD"], p)
        self.assertEqual(q["TCS"]["price"], Decimal("3210.25"))
        self.assertIsNone(q["TCS"]["prev_close"])
        self.assertIn("no price for BAD", errors[0])
        with self.assertRaisesRegex(alerts.AlertError, "--prices-file"):
            alerts.get_quotes(["TCS"], "file")

    def test_quote_writes_prices_json_for_the_other_skills(self):
        out = os.path.join(self.tmp.name, "trader", "prices.json")
        code, text, _ = run(["quote", "TCS", "ITC", "--db", self.db, "--write", out, "--now", "2026-10-28T08:00"])
        self.assertEqual(code, 0)
        self.assertIn("₹3,205.50", text)
        self.assertIn("not live quotes", text)
        with open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"TCS": "3205.50", "ITC": "395.00"})
        self.assertEqual(doc["currency"], "INR")
        self.assertEqual(doc["detail"]["TCS"]["isin"], "INE467B01029")
        self.assertTrue(doc["as_of"].startswith("2026-10-28T08:00"))

    def test_holdings_reads_symbols(self):
        pf = os.path.join(self.tmp.name, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": "0", "positions": [{"symbol": "ITC", "qty": 10}]}, fh)
        tg = os.path.join(ROOT, "trader-budget", "targets.example.json")
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--targets", tg, "--db", self.db, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["INFY", "ITC", "TCS"])

    def test_missing_store_is_an_error(self):
        code, _, err = run(["quote", "TCS", "--db", os.path.join(self.tmp.name, "none.db")])
        self.assertEqual(code, 1)
        self.assertIn("no market store", err)


class Rules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "market.db")
        store(self.db)
        self.rules = os.path.join(self.tmp.name, "alerts.json")
        self.state = os.path.join(self.tmp.name, "state.json")
        with open(EXAMPLE) as fh:
            doc = json.load(fh)
        with open(self.rules, "w") as fh:
            json.dump(doc, fh)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, now="2026-10-28T10:00", *extra):
        return run(["check", "--rules", self.rules, "--state", self.state, "--db", self.db, "--now", now,
                    "--prices-out", "", *extra])

    def test_the_example_rules(self):
        code, text, _ = self.check()
        self.assertEqual(code, 0)
        self.assertIn("Opening bell", text)
        self.assertIn("TCS last ₹3,205.50 (2026-10-27), at or above your alert level of ₹3,200.00", text)
        self.assertNotIn("ALERT ITC", text)  # 395 is not at or below the 380 stop
        self.assertIn("no price for HDFCBANK", text)
        self.assertIn("INFY moved -3.33% to ₹1,450.00 (from ₹1,500.00)", text)
        self.assertIn("ex-date", text)
        self.assertIn("enter it yourself in your broker", text)

    def test_each_rule_rings_once(self):
        self.check()
        code, text, _ = self.check("2026-10-28T11:00")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        code, text, _ = self.check("2026-10-29T10:00")  # a new day: the bell and the pct move ring again
        self.assertIn("Opening bell", text)
        self.assertIn("INFY moved", text)
        self.assertNotIn("TCS", text)  # above rings once until reset
        self.assertEqual(run(["reset", "tcs-above-3200", "--state", self.state])[0], 0)
        self.assertIn("TCS", self.check("2026-10-30T10:00")[1])

    def test_a_stop_rings_with_its_note(self):
        with open(self.rules, "w") as fh:
            json.dump({"rules": [{"id": "itc-stop", "symbol": "ITC", "type": "stop", "price": "400", "note": "my stop"}]}, fh)
        self.assertIn("ITC last ₹395.00 (2026-10-27), at or below your stop of ₹400.00 (my stop)", self.check()[1])

    def test_no_bell_when_closed(self):
        code, text, _ = self.check("2026-10-20T10:00")
        self.assertNotIn("Opening bell", text)

    def test_add_list_and_validation(self):
        rules = os.path.join(self.tmp.name, "new.json")
        self.assertEqual(run(["add", "--rules", rules, "--symbol", "tcs", "--above", "3200.5"])[0], 0)
        code, _, err = run(["add", "--rules", rules, "--symbol", "tcs", "--above", "3200.5"])
        self.assertIn("already exists", err)
        code, _, err = run(["add", "--rules", rules, "--symbol", "itc", "--stop", "-5"])
        self.assertEqual(code, 1)
        self.assertIn("not a positive number", err)
        code, text, _ = run(["list", "--rules", rules, "--state", self.state])
        self.assertIn("tcs-above-3200.5", text)
        with self.assertRaises(alerts.AlertError):
            alerts.validate({"id": "x", "type": "teleport", "symbol": "TCS"})

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", os.path.join(self.tmp.name, "none.json"), "--db", self.db])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
