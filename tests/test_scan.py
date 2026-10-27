import datetime as dt
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from decimal import Decimal

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT, load  # noqa: E402

ca = load("core", "corporate_actions")
cal = load("core", "nse_calendar")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import paper  # noqa: E402
    import scan  # noqa: E402
    from test_research import market, weekdays  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the scan")
class PaperFills(unittest.TestCase):
    def test_limit_order_rules(self):
        f = paper.fill_price
        s = Decimal("0.0015")
        self.assertEqual(f("buy", Decimal(101), Decimal(100), Decimal(103), Decimal(99), s), Decimal("100.15"))
        self.assertEqual(f("buy", Decimal(101), Decimal(102), Decimal(103), Decimal(100), s), Decimal(101))  # dipped to it
        self.assertIsNone(f("buy", Decimal(101), Decimal(102), Decimal(103), Decimal(101.5), s))  # never reached
        self.assertEqual(f("buy", Decimal("100.10"), Decimal(100), Decimal(100), Decimal(100), s), Decimal("100.10"))
        self.assertEqual(f("sell", Decimal(99), Decimal(100), Decimal(101), Decimal(98), s), Decimal("99.85"))
        self.assertIsNone(f("sell", Decimal(99), None, None, None, s))  # did not trade

    def test_sells_fund_buys_and_cash_caps_quantity(self):
        led = paper.new("t", Decimal(1000), "2024-01-01")
        led["positions"]["A"] = {"symbol": "A", "qty": 10, "cost": "1000", "last_price": "100"}
        led["pending"] = {"date": "2024-01-01", "orders": [
            {"isin": "B", "symbol": "B", "side": "buy", "qty": 50, "limit": "110"},
            {"isin": "A", "symbol": "A", "side": "sell", "qty": 10, "limit": "90"}]}
        fills = paper.fill_pending(led, "2024-01-02", {"A": ("100", "101", "99"), "B": ("100", "101", "99")},
                                   Decimal(0))
        self.assertEqual([f["side"] for f in fills], ["sell", "buy"])
        self.assertNotIn("A", led["positions"])
        self.assertLess(led["positions"]["B"]["qty"], 50)  # cut to what the cash covers
        self.assertGreaterEqual(Decimal(led["cash"]), 0)
        self.assertIsNone(led["pending"])

    def test_orders_equal_weight_on_tick_with_trim_band(self):
        led = paper.new("t", Decimal(100000), "2024-01-01")
        led["positions"]["A"] = {"symbol": "A", "qty": 99, "cost": "9900", "last_price": "100"}
        led["positions"]["C"] = {"symbol": "C", "qty": 5, "cost": "500", "last_price": "100"}
        prices = {"A": Decimal(100), "B": Decimal("251.30"), "C": Decimal(100)}
        ticks = {"A": Decimal("0.05"), "B": Decimal("0.05"), "C": Decimal("0.05")}
        out = {o["isin"]: o for o in paper.orders_for(led, ["A", "B"], Decimal(20000), prices, ticks, 2,
                                                      {"A": "A", "B": "B"})}
        self.assertNotIn("A", out)  # 99 against a target of 100: inside the 2% band
        self.assertEqual((out["B"]["side"], out["B"]["qty"]), ("buy", 39))  # 10,000 / 251.30
        self.assertEqual(out["B"]["limit"], "253.80")  # 1% above, rounded down to the tick
        self.assertEqual((out["C"]["side"], out["C"]["qty"], out["C"]["limit"]), ("sell", 5, "99.00"))

    def test_buy_reserve_comes_from_charges(self):
        self.assertAlmostEqual(float(paper.buy_cost_fraction()), 0.001 + 0.00015 + 0.0000317 * 1.18, places=7)


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the scan")
class Freshness(unittest.TestCase):
    def test_expected_session(self):
        # Tuesday 2026-10-27: before 7 pm the Monday session counts, after it Tuesday's
        self.assertEqual(scan.expected_session(dt.datetime(2026, 10, 27, 16, 0, tzinfo=IST)), dt.date(2026, 10, 26))
        self.assertEqual(scan.expected_session(dt.datetime(2026, 10, 27, 19, 30, tzinfo=IST)), dt.date(2026, 10, 27))
        # Wednesday 2026-10-21 morning: Tuesday 10-20 was Dussehra, so Monday 10-19
        self.assertEqual(scan.expected_session(dt.datetime(2026, 10, 21, 9, 0, tzinfo=IST)), dt.date(2026, 10, 19))


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the scan")
class ScanReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "m.db")
        db = ca.connect(cls.db)
        cls.days = weekdays(dt.date(2013, 1, 1), 520)
        market(db, cls.days)
        for d in cls.days:
            db.execute("INSERT INTO fetched VALUES (?,?,?,?,?)", (d.isoformat(), "prices", "ok", 5, ""))
        db.commit()
        db.close()
        cls.instruments = os.path.join(cls.tmp.name, "instruments.json")
        with open(cls.instruments, "w") as fh:
            json.dump({"instruments": {"INE%03dT01010" % k: {"tick_size": "0.05"} for k in range(5)}}, fh)
        cls.saved = scan.INSTRUMENTS, scan.SECTORS
        scan.INSTRUMENTS, scan.SECTORS = cls.instruments, os.path.join(cls.tmp.name, "none.csv")
        cls.strategy = "test_top2"
        cls.saved_strat = scan.STRATEGIES
        scan.STRATEGIES = os.path.join(cls.tmp.name, "strategies.json")
        with open(cls.saved_strat) as fh:
            real = json.load(fh)["momentum_quality_top20"]
        real.update(top=2, universe=dict(real["universe"], min_value=1.0))
        with open(scan.STRATEGIES, "w") as fh:
            json.dump({cls.strategy: real}, fh)

    @classmethod
    def tearDownClass(cls):
        scan.INSTRUMENTS, scan.SECTORS = cls.saved
        scan.STRATEGIES = cls.saved_strat
        cls.tmp.cleanup()

    def month_end(self, after):
        d = [x for x in self.days if x > after]
        return next(x for x, y in zip(d, d[1:]) if y.month != x.month)

    def test_rebalance_day_from_stored_sessions(self):
        import sqlite3
        db = sqlite3.connect(self.db)
        me = self.month_end(self.days[300])
        self.assertTrue(scan.is_rebalance_day(me, db))
        self.assertFalse(scan.is_rebalance_day(me - dt.timedelta(days=1), db))
        db.close()

    def test_orders_then_fills_at_the_next_open(self):
        ledger = os.path.join(self.tmp.name, "paper.json")
        out = os.path.join(self.tmp.name, "out")
        me = self.month_end(self.days[320])
        r1 = scan.run(self.strategy, "100000", self.db, ledger, out, as_of=me)
        self.assertTrue(r1["rebalance"])
        self.assertEqual(len(r1["orders"]), 2)
        self.assertTrue(all(o["side"] == "buy" for o in r1["orders"]))
        self.assertTrue(os.path.exists(os.path.join(out, me.isoformat() + "-orders.csv")))
        with open(os.path.join(out, me.isoformat() + ".html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertNotIn("http", page)
        self.assertIn("Order sheet for the next open", page)
        nxt = self.month_end(me)
        r2 = scan.run(self.strategy, None, self.db, ledger, out, as_of=nxt)
        filled = [f["date"] for f in r2["ledger"]["fills"]]
        first_after = min(x for x in self.days if x > me).isoformat()
        self.assertTrue(filled and all(d == first_after for d in filled))  # at the next session's open
        self.assertGreater(r2["nav"], 0)

    def test_scan_picks_exactly_what_the_backtest_would(self):
        import pandas as pd
        import backtest
        import panel
        import signals
        me = self.month_end(self.days[330])
        res = scan.run(self.strategy, "100000", self.db, os.path.join(self.tmp.name, "c.json"),
                       os.path.join(self.tmp.name, "c"), as_of=me)
        p = panel.load(self.db)
        sc = signals.SIGNALS["momentum_quality"](p)
        elig = panel.universe(p, min_value=1.0)
        i = p.dates.get_loc(pd.Timestamp(me))
        self.assertEqual([c["isin"] for c in res["cards"]], backtest.select_top(sc, elig, i, 2))
        # the backtest buys exactly those at the next open
        bt = backtest.run(p, sc, me.isoformat(), None, backtest.Config(top=2), rebalance_dates=[pd.Timestamp(me)])
        first = [t[1] for t in bt["trades"] if t[2] == "buy"][:2]
        self.assertEqual(sorted(first), sorted(c["isin"] for c in res["cards"]))

    def test_stale_store_is_refused(self):
        with self.assertRaises(scan.Stale):
            scan.run(self.strategy, "100000", self.db, os.path.join(self.tmp.name, "x.json"),
                     os.path.join(self.tmp.name, "o"), now_ist=dt.datetime(2026, 10, 27, 20, 0, tzinfo=IST))

    def test_replay_cannot_touch_the_live_ledger(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(scan.main(["--db", self.db, "run", "--as-of", self.days[400].isoformat()]), 2)


if __name__ == "__main__":
    unittest.main()
