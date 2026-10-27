import datetime as dt
import unittest
from decimal import Decimal

from _load import load

lots = load("core", "lots")


def t(date, side, qty, price, isin="INE009A01021", symbol="INFY", time=""):
    return {"date": date, "time": time, "isin": isin, "symbol": symbol, "side": side, "qty": qty, "price": price}


class Years(unittest.TestCase):
    def test_financial_year(self):
        self.assertEqual(lots.financial_year(dt.date(2026, 3, 31)), "FY2025-26")
        self.assertEqual(lots.financial_year(dt.date(2026, 4, 1)), "FY2026-27")
        self.assertEqual(lots.financial_year(dt.date(1999, 12, 1)), "FY1999-00")

    def test_long_term_is_more_than_twelve_months(self):
        buy = dt.date(2024, 2, 29)
        self.assertFalse(lots.is_long_term(buy, dt.date(2025, 2, 28)))  # exactly 12 months on
        self.assertTrue(lots.is_long_term(buy, dt.date(2025, 3, 1)))


class Fifo(unittest.TestCase):
    def test_oldest_lot_goes_first(self):
        m = lots.match([t("2024-01-10", "buy", 10, "100"), t("2025-06-10", "buy", 10, "200"),
                        t("2025-07-01", "sell", 15, "250")])
        r = m["realized"]
        self.assertEqual([(x["qty"], x["buy_price"], x["term"]) for x in r],
                         [(10, Decimal("100"), "long"), (5, Decimal("200"), "short")])
        self.assertEqual(sum(x["gain"] for x in r), Decimal("1500") + Decimal("250"))
        self.assertEqual(m["open"]["INE009A01021"], [{"date": dt.date(2025, 6, 10), "qty": 5, "price": Decimal("200")}])

    def test_sell_without_a_buy_is_unmatched_not_invented(self):
        r = lots.match([t("2024-01-10", "buy", 5, "100"), t("2024-02-10", "sell", 8, "120")])["realized"]
        self.assertEqual([(x["qty"], x["gain"]) for x in r], [(5, Decimal("100")), (3, None)])
        y = lots.by_year(r)[0]
        self.assertEqual((y["gain"], y["unmatched_qty"], y["unmatched_symbols"]), (Decimal("100"), 3, ["INFY"]))

    def test_same_day_buy_is_matched_before_the_sell(self):
        r = lots.match([t("2024-01-10", "sell", 5, "110", time="10:00"), t("2024-01-10", "buy", 5, "100", time="11:00")])
        self.assertEqual(r["realized"][0]["gain"], None)  # the sell came first in time
        r = lots.match([t("2024-01-10", "sell", 5, "110"), t("2024-01-10", "buy", 5, "100")])
        self.assertEqual(r["realized"][0]["gain"], Decimal("50"))  # no times: buys first

    def test_isins_are_kept_apart(self):
        m = lots.match([t("2024-01-10", "buy", 5, "100"), t("2024-02-10", "sell", 5, "120", isin="INE002A01018",
                                                            symbol="RELIANCE")])
        self.assertIsNone(m["realized"][0]["gain"])
        self.assertIn("INE009A01021", m["open"])

    def test_reconcile_with_holdings(self):
        m = lots.match([t("2024-01-10", "buy", 5, "100")])
        lines = {x["isin"]: x["status"] for x in lots.reconcile(m["open"], [
            {"isin": "INE009A01021", "symbol": "INFY", "quantity": Decimal("5")},
            {"isin": "INE1TAE01010", "symbol": "TMCV", "quantity": Decimal("10")}])}
        self.assertEqual(lines, {"INE009A01021": "match", "INE1TAE01010": "held, no recorded buy"})


if __name__ == "__main__":
    unittest.main()
