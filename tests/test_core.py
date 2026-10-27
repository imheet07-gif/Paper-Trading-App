import datetime as dt
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal

from _load import FIX, load

cal = load("core", "nse_calendar")
money = load("core", "money")
charges = load("core", "charges")
inst = load("core", "instruments")

CORE = os.path.join(FIX, "core")
IST = cal.IST


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=IST)


def read(name):
    with open(os.path.join(CORE, name), encoding="utf-8") as fh:
        return fh.read()


class Calendar(unittest.TestCase):
    def test_weekday_holiday_and_weekend(self):
        self.assertFalse(cal.is_trading_day(dt.date(2026, 10, 20)))  # Dussehra, a Tuesday
        self.assertFalse(cal.is_trading_day(dt.date(2026, 10, 24)))  # Saturday
        self.assertTrue(cal.is_trading_day(dt.date(2026, 10, 27)))

    def test_fifteen_weekday_holidays_in_2026(self):
        h = cal.holidays(2026)
        self.assertEqual(len(h), 15)
        self.assertTrue(all(d.weekday() < 5 for d in h))

    def test_unknown_year_is_an_error_not_a_guess(self):
        with self.assertRaises(cal.UnknownYear):
            cal.is_trading_day(dt.date(2027, 1, 4))

    def test_phases_through_a_day(self):
        day = "2026-10-27T"
        cases = [("08:59", "closed"), ("09:00", "pre_open_entry"), ("09:08", "pre_open_matching"),
                 ("09:15", "open"), ("15:29", "open"), ("15:30", "closed"),
                 ("15:40", "closing_session"), ("16:00", "closed")]
        for clock, want in cases:
            self.assertEqual(cal.phase(at(day + clock)), want, clock)

    def test_utc_input_is_converted(self):
        # 04:00 UTC is 09:30 IST
        t = dt.datetime(2026, 10, 27, 4, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(cal.phase(t), "open")

    def test_naive_datetime_refused(self):
        with self.assertRaises(ValueError):
            cal.phase(dt.datetime(2026, 10, 27, 10, 0))

    def test_next_open_skips_holiday_and_weekend(self):
        # Friday 2026-10-02 is Gandhi Jayanti: from Thursday evening, next open is Monday
        self.assertEqual(cal.next_open(at("2026-10-01T16:30")), at("2026-10-05T09:15"))
        # before the bell on a trading day: the same day
        self.assertEqual(cal.next_open(at("2026-10-27T07:00")), at("2026-10-27T09:15"))

    def test_t_plus_one_over_a_holiday(self):
        self.assertEqual(cal.settlement_date(dt.date(2026, 10, 19)), dt.date(2026, 10, 21))
        self.assertEqual(cal.settlement_date(dt.date(2026, 10, 23)), dt.date(2026, 10, 26))

    def test_status_names_the_holiday(self):
        s = cal.status(at("2026-10-20T11:00"))
        self.assertEqual(s["holiday"], "Dussehra")
        self.assertEqual(s["next_open_ist"], "2026-10-21T09:15+05:30")

    def test_special_session_opens_a_closed_day(self):
        cal.SPECIAL_SESSIONS["2026-11-08"] = {"name": "test session", "start": "18:00", "end": "19:00"}
        try:
            self.assertEqual(cal.phase(at("2026-11-08T18:30")), "special_session")
            self.assertEqual(cal.phase(at("2026-11-08T17:59")), "closed")
        finally:
            cal.SPECIAL_SESSIONS.clear()


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(money.inr("12345678.9"), "₹1,23,45,678.90")
        self.assertEqual(money.inr("999"), "₹999.00")
        self.assertEqual(money.inr("100000", decimals=0), "₹1,00,000")
        self.assertEqual(money.inr("-1500.5"), "-₹1,500.50")

    def test_lakh_and_crore(self):
        self.assertEqual(money.inr_short("12345678"), "₹1.23 crore")
        self.assertEqual(money.inr_short("450000"), "₹4.5 lakh")
        self.assertEqual(money.inr_short("99999"), "₹99,999")

    def test_float_input_keeps_its_decimal_value(self):
        self.assertEqual(money.D(0.1), Decimal("0.1"))

    def test_whole_shares_never_rounds_up(self):
        self.assertEqual(money.whole_shares("50000", "2875.40"), 17)
        self.assertEqual(money.whole_shares("2875.39", "2875.40"), 0)
        with self.assertRaises(ValueError):
            money.whole_shares("100", "0")

    def test_tick_rounding(self):
        self.assertEqual(money.round_to_tick("2875.43", "0.05"), Decimal("2875.45"))
        self.assertEqual(money.round_to_tick("2875.43", "0.05", "down"), Decimal("2875.40"))
        self.assertEqual(money.round_to_tick("2875.41", "0.10", "up"), Decimal("2875.50"))


class Charges(unittest.TestCase):
    def setUp(self):
        self.rates = charges.load_rates()

    def test_zerodha_delivery_buy(self):
        # Rs 1,00,000 buy: STT 100, stamp 15, NSE txn 2.97, SEBI 0.10, GST 18% of 3.07
        c = charges.trade_charges("zerodha", "buy", 100, "1000", rates=self.rates)
        self.assertEqual(c["brokerage"], Decimal("0.00"))
        self.assertEqual(c["stt"], Decimal("100.00"))
        self.assertEqual(c["stamp_duty"], Decimal("15.00"))
        self.assertEqual(c["exchange_txn"], Decimal("2.97"))
        self.assertEqual(c["sebi_fee"], Decimal("0.10"))
        self.assertEqual(c["gst"], Decimal("0.55"))
        self.assertEqual(c["dp_charge"], Decimal("0.00"))
        self.assertEqual(c["total"], Decimal("118.62"))

    def test_sell_has_dp_and_no_stamp(self):
        c = charges.trade_charges("zerodha", "sell", 100, "1000", rates=self.rates)
        self.assertEqual(c["stamp_duty"], Decimal("0.00"))
        self.assertEqual(c["dp_charge"], Decimal("15.34"))

    def test_angel_brokerage_cap_and_floor(self):
        # 0.1% of 1,00,000 = 100, capped at 20
        big = charges.trade_charges("angelone", "buy", 100, "1000", rates=self.rates)
        self.assertEqual(big["brokerage"], Decimal("20.00"))
        # 0.1% of 1,000 = 1, raised to the Rs 5 floor
        small = charges.trade_charges("angelone", "buy", 1, "1000", rates=self.rates)
        self.assertEqual(small["brokerage"], Decimal("5.00"))

    def test_unconfigured_product_is_an_error(self):
        with self.assertRaises(KeyError):
            charges.trade_charges("angelone", "buy", 1, "100", product="intraday", rates=self.rates)

    def test_fractional_qty_refused(self):
        with self.assertRaises(ValueError):
            charges.trade_charges("zerodha", "buy", "1.5", "100", rates=self.rates)

    def test_nothing_is_verified_until_a_contract_note_matches(self):
        c = charges.trade_charges("zerodha", "buy", 1, "100", rates=self.rates)
        self.assertFalse(c["verified"])

    def test_dp_basis_per_scrip_per_day_vs_per_order(self):
        trades = [{"date": "2026-10-27", "symbol": "TCS", "side": "sell", "qty": 1, "price": "3000"},
                  {"date": "2026-10-27", "symbol": "TCS", "side": "sell", "qty": 1, "price": "3001"}]
        z = charges.batch_charges(trades, "zerodha", self.rates)
        self.assertEqual([r["charges"]["dp_charge"] for r in z["rows"]], [Decimal("15.34"), Decimal("0.00")])
        a = charges.batch_charges(trades, "angelone", self.rates)
        self.assertEqual([r["charges"]["dp_charge"] for r in a["rows"]], [Decimal("23.60"), Decimal("23.60")])

    def test_round_trip_cost_percent(self):
        r = charges.round_trip("zerodha", 100, "1000", rates=self.rates)
        self.assertEqual(r["total"], Decimal("118.62") + r["sell"]["total"])
        self.assertGreater(r["pct_of_turnover"], Decimal("0.2"))

    def test_every_rate_block_names_its_source(self):
        blocks = [self.rates["statutory"], self.rates["exchange_txn_pct"]] + list(self.rates["brokers"].values())
        for b in blocks:
            self.assertIn("_source", b)
            self.assertIn("verified", b)

    def test_cli_marks_unverified(self):
        out = io.StringIO()
        with redirect_stdout(out):
            charges.main(["--broker", "zerodha", "--side", "buy", "--qty", "10", "--price", "100"])
        self.assertIn("UNVERIFIED", out.getvalue())


class Instruments(unittest.TestCase):
    def setUp(self):
        self.reg = inst.build(read("EQUITY_L.sample.csv"), read("kite_instruments.sample.csv"),
                              read("angel_scrips.sample.json"))

    def test_keyed_by_isin_with_both_brokers(self):
        r = self.reg["instruments"]["INE002A01018"]
        self.assertEqual(r["nse_symbol"], "RELIANCE")
        self.assertEqual(r["kite_token"], 738561)
        self.assertEqual(r["angel_token"], "2885")
        self.assertEqual(r["listed"], "1995-11-29")
        self.assertEqual(r["tick_size"], "0.1")
        self.assertEqual(r["angel_tick_size"], "0.1")

    def test_bse_and_index_rows_are_not_nse_equities(self):
        # Kite's BSE RELIANCE row has a different token and must not overwrite the NSE one
        self.assertEqual(self.reg["instruments"]["INE002A01018"]["kite_exchange_token"], 2885)
        self.assertEqual(self.reg["count"], 4)

    def test_problems_are_reported_not_hidden(self):
        p = "\n".join(self.reg["problems"])
        self.assertIn("SAMPLEBE: not in Kite", p)
        self.assertIn("HDFCBANK: tick size differs", p)

    def test_non_eq_series_joins_angel_by_series(self):
        self.assertEqual(self.reg["instruments"]["INE000Z01019"]["angel_token"], "99999")

    def test_find_by_isin_symbol_and_name(self):
        self.assertEqual(inst.find(self.reg, "INE467B01029")[0]["nse_symbol"], "TCS")
        self.assertEqual(inst.find(self.reg, "hdfcbank")[0]["isin"], "INE040A01034")
        self.assertEqual(inst.find(self.reg, "consultancy")[0]["nse_symbol"], "TCS")

    def test_nse_list_alone_still_builds(self):
        reg = inst.build(read("EQUITY_L.sample.csv"))
        self.assertEqual(reg["count"], 4)
        self.assertEqual(reg["problems"], [])
        self.assertNotIn("kite_token", reg["instruments"]["INE002A01018"])

    def test_cli_build_then_find(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "instruments.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                inst.main(["build", "--nse", os.path.join(CORE, "EQUITY_L.sample.csv"),
                           "--kite", os.path.join(CORE, "kite_instruments.sample.csv"), "--out", out])
                code = inst.main(["find", "RELIANCE", "--registry", out])
            self.assertEqual(code, 0)
            self.assertIn('"isin": "INE002A01018"', buf.getvalue())
            with open(out) as fh:
                self.assertEqual(json.load(fh)["sources"], ["nse", "kite"])


if __name__ == "__main__":
    unittest.main()
