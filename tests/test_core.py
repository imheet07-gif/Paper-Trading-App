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


def with_test_broker(rates):
    """Made-up rates that exercise the cap, the floor and per-order DP charges."""
    r = json.loads(json.dumps(rates))
    r["brokers"]["testbroker"] = {
        "_source": "test values", "verified": False,
        "brokerage": {"delivery": {"pct": "0.1", "cap": "20", "floor": "5"}},
        "dp_charge_incl_gst": "20", "dp_charge_basis": "per_sell_order"}
    return r


def read(name):
    with open(os.path.join(CORE, name), encoding="utf-8") as fh:
        return fh.read()


class Calendar(unittest.TestCase):
    def test_weekday_holiday_and_weekend(self):
        self.assertFalse(cal.is_trading_day(dt.date(2026, 10, 20)))  # Dussehra, a Tuesday
        self.assertFalse(cal.is_trading_day(dt.date(2026, 10, 24)))  # Saturday
        self.assertTrue(cal.is_trading_day(dt.date(2026, 10, 27)))

    def test_2026_list_is_nses_own(self):
        # NSE's holiday master: 20 entries, 16 on weekdays, including the
        # Maharashtra civic election that broker-published lists left out
        h = cal.holidays(2026)
        self.assertEqual(len(h), 20)
        self.assertEqual(sum(d.weekday() < 5 for d in h), 16)
        self.assertFalse(cal.is_trading_day(dt.date(2026, 1, 15)))

    def test_update_holidays_from_nse_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.json")
            with open(path, "w") as fh:
                json.dump({"years": {"2025": {"2025-12-25": "Christmas"}}}, fh)
            years = cal.update_holidays(read("nse_holiday_master.json"), path, fetched="2026-10-27")
            self.assertEqual(years, [2026])
            got = cal.load_holidays(path)
            self.assertEqual(sorted(got), [2025, 2026])  # other years kept
            self.assertEqual(got[2026]["2026-10-20"], "Dussehra")

    def test_missing_segment_is_an_error(self):
        with self.assertRaises(ValueError):
            cal.parse_holiday_master('{"FO": []}')

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
        # Rs 1,00,000 buy: STT 100, stamp 15, NSE txn 3.07, SEBI 0.10, GST 18% of 3.17
        c = charges.trade_charges("zerodha", "buy", 100, "1000", rates=self.rates)
        self.assertEqual(c["brokerage"], Decimal("0.00"))
        self.assertEqual(c["stt"], Decimal("100.00"))
        self.assertEqual(c["stamp_duty"], Decimal("15.00"))
        self.assertEqual(c["exchange_txn"], Decimal("3.07"))
        self.assertEqual(c["assumptions"], [])
        self.assertEqual(c["sebi_fee"], Decimal("0.10"))
        self.assertEqual(c["gst"], Decimal("0.57"))
        self.assertEqual(c["dp_charge"], Decimal("0.00"))
        self.assertEqual(c["total"], Decimal("118.74"))

    def test_sell_has_dp_and_no_stamp(self):
        c = charges.trade_charges("zerodha", "sell", 100, "1000", rates=self.rates)
        self.assertEqual(c["stamp_duty"], Decimal("0.00"))
        self.assertEqual(c["dp_charge"], Decimal("15.34"))

    def test_brokerage_cap_and_floor(self):
        rates = with_test_broker(self.rates)
        # 0.1% of 1,00,000 = 100, capped at 20
        big = charges.trade_charges("testbroker", "buy", 100, "1000", rates=rates)
        self.assertEqual(big["brokerage"], Decimal("20.00"))
        # 0.1% of 1,000 = 1, raised to the Rs 5 floor
        small = charges.trade_charges("testbroker", "buy", 1, "1000", rates=rates)
        self.assertEqual(small["brokerage"], Decimal("5.00"))

    def test_unconfigured_product_is_an_error(self):
        with self.assertRaises(KeyError):
            charges.trade_charges("testbroker", "buy", 1, "100", product="intraday",
                                  rates=with_test_broker(self.rates))

    def test_unknown_broker_is_an_error(self):
        with self.assertRaises(KeyError):
            charges.trade_charges("angelone", "buy", 1, "100", rates=self.rates)

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
        a = charges.batch_charges(trades, "testbroker", with_test_broker(self.rates))
        self.assertEqual([r["charges"]["dp_charge"] for r in a["rows"]], [Decimal("20.00"), Decimal("20.00")])

    def test_contract_note_rounds_stt_once_per_day(self):
        # two fills of Rs 400: STT 0.40 each; per fill that rounds to 0 + 0, per day to Rs 1
        trades = [{"date": "2026-08-06", "symbol": "A", "side": "sell", "qty": 4, "price": "100"},
                  {"date": "2026-08-06", "symbol": "A", "side": "sell", "qty": 4, "price": "100"}]
        note = charges.contract_note(trades, "zerodha", self.rates)["2026-08-06"]
        self.assertEqual(note["stt"], Decimal("1"))
        self.assertEqual(note["exchange_txn"], Decimal("0.0246"))  # 800 x 0.00307%, 4 decimals
        self.assertEqual(note["dp_charge"], Decimal("15.34"))  # one scrip

    def test_contract_note_bse_exclusive_rate(self):
        t = {"date": "2026-08-06", "symbol": "B", "side": "sell", "qty": 1000, "price": "10", "exchange": "BSE"}
        unknown = charges.contract_note([t], "zerodha", self.rates)["2026-08-06"]
        self.assertEqual(unknown["exchange_txn"], Decimal("0.3750"))
        self.assertIn("B: BSE exclusive flag unknown", unknown["assumptions"][0])
        excl = charges.contract_note([dict(t, bse_exclusive=True)], "zerodha", self.rates)["2026-08-06"]
        self.assertEqual((excl["exchange_txn"], excl["assumptions"]), (Decimal("0.2750"), []))

    def test_round_trip_cost_percent(self):
        r = charges.round_trip("zerodha", 100, "1000", rates=self.rates)
        self.assertEqual(r["total"], Decimal("118.74") + r["sell"]["total"])
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
    # Rows cut from the real files of 2026-10-27; see tests/fixtures/core/README.md
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
        self.assertEqual(r["angel_tick_size"], "0.1")  # Angel publishes 10 paise

    def test_bse_index_and_derivative_rows_are_not_nse_equities(self):
        # the BSE RELIANCE rows carry token 500325 and must not overwrite the NSE ones
        r = self.reg["instruments"]["INE002A01018"]
        self.assertEqual(r["kite_exchange_token"], 2885)
        self.assertEqual(r["angel_token"], "2885")
        self.assertEqual(self.reg["count"], 7)

    def test_be_and_bz_series_join_both_brokers(self):
        be = self.reg["instruments"]["INE224E01036"]
        self.assertEqual((be["series"], be["kite_token"], be["angel_token"]), ("BE", 6913, "27"))
        bz = self.reg["instruments"]["INE890C01046"]
        self.assertEqual((bz["series"], bz["tick_size"], bz["angel_tick_size"]), ("BZ", "0.01", "0.01"))

    def test_rights_entitlement_is_reported_missing(self):
        p = "\n".join(self.reg["problems"])
        self.assertIn("SHIVAM-RE1: not in Kite's list", p)
        self.assertIn("SHIVAM-RE1: not in Angel One's list", p)
        self.assertEqual(len(self.reg["problems"]), 2)

    def test_tick_size_disagreement_is_reported(self):
        angel = read("angel_scrips.sample.json").replace(
            '"symbol": "HDFCBANK-EQ", "name": "HDFCBANK", "expiry": "", "strike": "-1.000000", '
            '"lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": "5.000000"',
            '"symbol": "HDFCBANK-EQ", "name": "HDFCBANK", "expiry": "", "strike": "-1.000000", '
            '"lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": "10.000000"')
        reg = inst.build(read("EQUITY_L.sample.csv"), read("kite_instruments.sample.csv"), angel)
        self.assertIn("HDFCBANK: tick size differs, Kite 0.05 vs Angel 0.1", "\n".join(reg["problems"]))

    def test_find_by_isin_symbol_and_name(self):
        self.assertEqual(inst.find(self.reg, "INE467B01029")[0]["nse_symbol"], "TCS")
        self.assertEqual(inst.find(self.reg, "hdfcbank")[0]["isin"], "INE040A01034")
        self.assertEqual(inst.find(self.reg, "consultancy")[0]["nse_symbol"], "TCS")

    def test_nse_list_alone_still_builds(self):
        reg = inst.build(read("EQUITY_L.sample.csv"))
        self.assertEqual(reg["count"], 7)
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
