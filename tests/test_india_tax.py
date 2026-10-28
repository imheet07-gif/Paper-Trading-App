import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal

from _load import FIX, ROOT, load

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

tax = load("core", "tax")
lots = load("core", "lots")
bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import pandas as pd  # noqa: E402
    import aftertax  # noqa: E402
    import panel  # noqa: E402
    from test_profiles import path, store  # noqa: E402
    from test_research import weekdays  # noqa: E402

D = Decimal


def t(date, side, qty, price, isin="INE000X01001"):
    return {"date": date, "isin": isin, "symbol": isin[-4:], "side": side, "qty": qty, "price": price}


def year(years, fy):
    return next(y for y in years if y["fy"] == fy)


class RatesFile(unittest.TestCase):
    def test_every_rule_is_dated_and_sourced(self):
        r = tax.load_rates()
        for key in ("short_term", "long_term", "dividends"):
            for rule in r[key]:
                dt.date.fromisoformat(rule["from"])
                self.assertIn("incometaxindia.gov.in", rule["_source"], key)
        for key in ("long_term_exemption", "cess"):
            for rule in r[key]:
                self.assertRegex(rule["fy_from"], r"^FY\d{4}-\d{2}$")
                self.assertIn("incometaxindia.gov.in", rule["_source"], key)
        for key in ("holding", "grandfathering", "set_off"):
            self.assertIn("incometaxindia.gov.in", r[key]["_source"], key)
        self.assertEqual(r["holding"]["long_term_after_months"], 12)  # what core/lots.py applies
        self.assertTrue(r["left_out"])


class Rates(unittest.TestCase):
    def test_short_term_rate_changes_on_23_july_2024(self):
        y = tax.assess([t("2024-05-02", "buy", 100, "100"), t("2024-07-22", "sell", 50, "200"),
                        t("2024-07-23", "sell", 50, "200")])[0]
        self.assertEqual(y["taxable"], {"short 15%": D(5000), "short 20%": D(5000)})
        self.assertEqual(y["gains_tax"], D("1750.00"))  # 750 + 1,000
        self.assertEqual(y["cess"], D("70.00"))
        self.assertEqual(y["tax"], D("1820.00"))

    def test_one_long_term_limit_for_all_of_fy2024_25_set_against_the_higher_rate_first(self):
        y = tax.assess([t("2023-01-02", "buy", 100, "1000", "INE000X01001"), t("2024-07-01", "sell", 100, "2000", "INE000X01001"),
                        t("2023-01-02", "buy", 100, "1000", "INE000X01002"), t("2024-08-01", "sell", 100, "2000", "INE000X01002")])[0]
        self.assertEqual(y["long_exemption"], D(125000))
        self.assertEqual(y["taxable"], {"long 10%": D(75000)})  # the 12.5% gain is all inside the limit
        self.assertEqual(y["tax"], D("7800.00"))  # 7,500 and 4% cess

    def test_long_term_exempt_before_1_april_2018_taxed_from_it(self):
        before = tax.assess([t("2016-01-04", "buy", 100, "100"), t("2018-03-29", "sell", 100, "5000")],
                            fmv={"INE000X01001": "100"})[0]
        self.assertEqual((before["fy"], before["exempt"], before["tax"]), ("FY2017-18", D(490000), D("0.00")))
        after = tax.assess([t("2016-01-04", "buy", 100, "100"), t("2018-04-02", "sell", 100, "5000")],
                           fmv={"INE000X01001": "100"})[0]
        self.assertEqual(after["taxable"], {"long 10%": D(390000)})  # 4,90,000 less the 1 lakh limit
        self.assertEqual(after["tax"], D("40560.00"))  # 39,000 and 4% cess

    def test_exemption_limit_is_exclusive(self):
        at = tax.assess([t("2020-01-01", "buy", 1, "0"), t("2021-06-01", "sell", 1, "100000")])[0]
        self.assertEqual(at["tax"], D("0.00"))
        over = tax.assess([t("2020-01-01", "buy", 1, "0"), t("2021-06-01", "sell", 1, "100010")])[0]
        self.assertEqual(over["gains_tax"], D("1.00"))

    def test_cess_is_three_percent_to_fy2017_18_then_four(self):
        old = tax.assess([t("2017-05-02", "buy", 100, "100"), t("2017-09-01", "sell", 100, "200")])[0]
        new = tax.assess([t("2018-05-02", "buy", 100, "100"), t("2018-09-03", "sell", 100, "200")])[0]
        self.assertEqual((old["gains_tax"], old["cess"]), (D("1500.00"), D("45.00")))
        self.assertEqual((new["gains_tax"], new["cess"]), (D("1500.00"), D("60.00")))

    def test_dates_outside_the_file_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Income-tax Act, 2025"):
            tax.assess([t("2026-01-02", "buy", 1, "100"), t("2026-04-01", "sell", 1, "110")])
        with self.assertRaisesRegex(ValueError, "outside the dates"):
            tax.assess([t("2011-01-03", "buy", 1, "100"), t("2012-03-30", "sell", 1, "110")])

    def test_a_sale_with_no_recorded_buy_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no recorded buy"):
            tax.assess([t("2021-01-04", "buy", 5, "100"), t("2021-02-01", "sell", 8, "120")])


class Grandfathering(unittest.TestCase):
    buy = t("2017-01-02", "buy", 100, "100")

    def test_cost_is_the_31_january_2018_value(self):
        y = tax.assess([self.buy, t("2019-08-01", "sell", 100, "3500")], fmv={"INE000X01001": "2000"})[0]
        self.assertEqual(y["long"], D(150000))  # 3,500 - 2,000, not 3,500 - 100
        self.assertEqual(y["taxable"], {"long 10%": D(50000)})

    def test_a_value_above_the_sale_price_cannot_make_a_loss(self):
        y = tax.assess([self.buy, t("2019-08-01", "sell", 100, "3000")], fmv={"INE000X01001": "4000"})[0]
        self.assertEqual((y["long"], y["carried_forward"]), (D(0), []))

    def test_a_value_below_cost_keeps_the_cost(self):
        y = tax.assess([t("2017-01-02", "buy", 100, "3000"), t("2019-08-01", "sell", 100, "2500")],
                       fmv={"INE000X01001": "2000"})[0]
        self.assertEqual(y["carried_forward"], [{"fy": "FY2019-20", "kind": "long", "amount": D(50000)}])

    def test_buys_from_1_february_2018_are_not_grandfathered(self):
        y = tax.assess([t("2018-02-01", "buy", 100, "100"), t("2019-08-01", "sell", 100, "3000")])[0]
        self.assertEqual(y["long"], D(290000))

    def test_missing_value_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "55\\(2\\)\\(ac\\)"):
            tax.assess([self.buy, t("2019-08-01", "sell", 100, "3000")])


class SetOff(unittest.TestCase):
    def test_carry_forward_fixture_worked_by_hand(self):
        with open(os.path.join(FIX, "india_tax", "carry_forward.json")) as fh:
            years = tax.assess(json.load(fh)["trades"])
        y19, y20, y21, y22 = (year(years, f) for f in ("FY2019-20", "FY2020-21", "FY2021-22", "FY2022-23"))
        self.assertEqual(y19["carried_forward"], [{"fy": "FY2019-20", "kind": "short", "amount": D(30000)}])
        # the long-term loss may not meet the short-term gain; the old short-term loss does
        self.assertEqual(y20["tax"], D("0.00"))
        self.assertEqual(y20["carried_forward"], [{"fy": "FY2019-20", "kind": "short", "amount": D(10000)},
                                                  {"fy": "FY2020-21", "kind": "long", "amount": D(40000)}])
        self.assertEqual(y21["taxable"], {"short 15%": D(30000)})
        self.assertEqual(y21["tax"], D("4680.00"))
        self.assertEqual(y21["carried_forward"], [{"fy": "FY2020-21", "kind": "long", "amount": D(40000)}])
        # 2,50,000 long-term gain less the 40,000 loss, less the 1 lakh limit, at 10%
        self.assertEqual(y22["taxable"], {"long 10%": D(110000)})
        self.assertEqual(y22["tax"], D("11440.00"))
        self.assertEqual(y22["carried_forward"], [])

    def test_this_years_short_term_loss_meets_long_term_gains_too(self):
        y = tax.assess([t("2020-01-01", "buy", 100, "100", "INE000X01001"), t("2021-06-01", "sell", 100, "3000", "INE000X01001"),
                        t("2021-05-03", "buy", 100, "500", "INE000X01002"), t("2021-06-01", "sell", 100, "100", "INE000X01002")])[0]
        self.assertEqual(y["set_off"], D(40000))
        self.assertEqual(y["taxable"], {"long 10%": D(150000)})  # 2,90,000 - 40,000 - 1,00,000

    def test_losses_last_eight_years(self):
        loss = [t("2012-05-02", "buy", 100, "600"), t("2012-08-01", "sell", 100, "100")]  # FY2012-13, 50,000
        eighth = tax.assess(loss + [t("2020-05-04", "buy", 100, "100", "INE000X01002"),
                                    t("2020-08-03", "sell", 100, "300", "INE000X01002")])
        self.assertEqual(year(eighth, "FY2020-21")["tax"], D("0.00"))
        ninth = tax.assess(loss + [t("2021-05-03", "buy", 100, "100", "INE000X01002"),
                                   t("2021-08-02", "sell", 100, "300", "INE000X01002")])
        self.assertEqual(year(ninth, "FY2021-22")["taxable"], {"short 15%": D(20000)})

    def test_losses_on_exempt_long_term_sales_are_dropped(self):
        years = tax.assess([t("2015-01-02", "buy", 100, "500"), t("2017-06-01", "sell", 100, "100"),
                            t("2017-07-03", "buy", 100, "100", "INE000X01002"),
                            t("2017-09-01", "sell", 100, "300", "INE000X01002")])
        y = year(years, "FY2017-18")
        self.assertEqual((y["exempt"], y["taxable"], y["carried_forward"]), (D(-40000), {"short 15%": D(20000)}, []))


class Dividends(unittest.TestCase):
    def test_exempt_then_115bbda_then_slab(self):
        years = tax.assess([], [("2016-08-01", "1200000"), ("2019-08-01", "50000"), ("2020-08-03", "10000")],
                           slab_pct="30")
        y16, y19, y20 = (year(years, f) for f in ("FY2016-17", "FY2019-20", "FY2020-21"))
        self.assertEqual((y16["dividend_tax"], y16["cess"]), (D("20000.00"), D("600.00")))  # 10% above 10 lakh, 3% cess
        self.assertEqual(y19["tax"], D("0.00"))
        self.assertEqual((y20["dividend_tax"], y20["tax"]), (D("3000.00"), D("3120.00")))

    def test_slab_rate_is_required_and_checked(self):
        with self.assertRaisesRegex(ValueError, "slab rate"):
            tax.assess([], [("2021-08-02", "1000")])
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            tax.assess([], [("2021-08-02", "1000")], slab_pct="300")
        self.assertEqual(tax.assess([], [("2019-08-01", "1000")])[0]["tax"], D("0.00"))  # no slab needed

    def test_slab_rate_is_never_written(self):
        src = ""
        for rel in (("core", "tax.py"), ("research", "aftertax.py")):
            with open(os.path.join(ROOT, *rel)) as fh:
                src += fh.read()
        self.assertNotRegex(src, r"slab[^\n]*(json\.dump|\.write\()")


class Lots(unittest.TestCase):
    def test_fractional_research_quantities_match_exactly(self):
        m = lots.match([t("2021-01-04", "buy", D("1.234567"), "100"), t("2021-02-01", "sell", D("1.234567"), "110")])
        self.assertEqual(m["open"], {})
        self.assertEqual(m["realized"][0]["gain"], D("12.34567"))


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class AfterTaxBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "m.db")
        cls.days = weekdays(dt.date(2016, 6, 1), 560)  # to the middle of 2018: two year ends
        n = len(cls.days)
        db = ca.connect(cls.db)
        store(db, cls.days, {"RISE%d" % k: path(n, 0.001 + 0.0005 * k, wiggle=0.004 + 0.001 * k) for k in range(4)})
        db.execute("INSERT INTO corporate_actions (ex_date, symbol, isin_reported, isin, series, subject, kind, dividend) "
                   "VALUES ('2017-09-05', 'RISE3', 'INE003P01010', 'INE003P01010', 'EQ', 'Dividend - Rs 2 Per Share', "
                   "'dividend', '2')")
        db.execute("CREATE TABLE IF NOT EXISTS index_prices (date TEXT NOT NULL, name TEXT NOT NULL, open REAL, high REAL, "
                   "low REAL, close REAL, pe REAL, pb REAL, div_yield REAL, PRIMARY KEY (date, name))")
        db.executemany("INSERT INTO index_prices (date, name, close, div_yield) VALUES (?, 'TEST', ?, 1.0)",
                       [(d.isoformat(), 1000 * (1.0004 ** k)) for k, d in enumerate(cls.days)])
        db.commit()
        db.close()
        cls.p = panel.load(cls.db)
        cls.start, cls.end = "2017-07-01", cls.days[-1].isoformat()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tax_leaves_the_portfolio_at_each_year_end(self):
        pre = aftertax.strategy(self.p, self.start, self.end, 1_000_000.0, None, pay_tax=False)
        post = aftertax.strategy(self.p, self.start, self.end, 1_000_000.0, None, pay_tax=True)
        self.assertEqual(pre["hook"].tax_paid, {})
        self.assertEqual(set(post["hook"].tax_paid), {"FY2017-18", "FY2018-19"})
        self.assertGreater(post["hook"].tax_paid["FY2017-18"], 0)
        self.assertLess(post["final"], pre["final"])
        # every sale was matched to a buy, and nothing is left open after the final sale
        self.assertEqual({s: q for s, q in post["hook"].open.items() if q}, {})
        paid = [y for y in post["years"] if y["fy"] == "FY2017-18"][0]
        self.assertAlmostEqual(post["hook"].tax_paid["FY2017-18"], float(paid["tax"]), places=2)

    def test_dividends_on_shares_held(self):
        res = aftertax.strategy(self.p, self.start, self.end, 1_000_000.0, None, pay_tax=False)
        paid = res["hook"].div_paid
        self.assertEqual([d for d, _ in paid], [dt.date(2017, 9, 5)])
        held = [x for x in res["hook"].trades if x["isin"] == "INE003P01010" and x["date"] < dt.date(2017, 9, 5)]
        shares = sum(x["qty"] if x["side"] == "buy" else -x["qty"] for x in held)
        self.assertAlmostEqual(float(paid[0][1]), float(shares) * 2, delta=0.01)

    def test_fund_is_taxed_once_and_grandfathered(self):
        f = aftertax.fund(self.db, "TEST", "2016-06-01", self.end, 1_000_000.0, 1.0)
        self.assertEqual([y["fy"] for y in f["years"]], ["FY2018-19"])
        y = f["years"][0]
        self.assertGreater(y["long"], 0)
        # the 31 January 2018 NAV is the cost, so the gain is only what came after it
        nav = aftertax.fund_nav(self.db, "TEST", "2016-06-01", self.end, 1.0)
        since = float(nav.iloc[-1] / nav.loc[:pd.Timestamp("2018-01-31")].iloc[-1] - 1)
        self.assertAlmostEqual(float(y["long"]) / (1_000_000.0 * float(nav.loc[:pd.Timestamp("2018-01-31")].iloc[-1])),
                               since, places=4)

    def test_fund_nav_adds_the_dividend_yield_and_takes_the_expense_ratio(self):
        gross = aftertax.fund_nav(self.db, "TEST", "2017-01-02", self.end, 0.0)
        net = aftertax.fund_nav(self.db, "TEST", "2017-01-02", self.end, 1.0)
        price = 1.0004 ** (len(gross) - 1)
        self.assertGreater(gross.iloc[-1], price)  # 1% a year of dividends on top of the price
        self.assertAlmostEqual(net.iloc[-1], price, delta=0.002)  # a 1% expense ratio takes them back off

    def test_year_table(self):
        idx = pd.DatetimeIndex(["2021-03-30", "2021-03-31", "2021-04-01", "2021-06-30"])
        nav = pd.Series([100.0, 110.0, 112.0, 120.0], index=idx)  # 10 of tax already left at 31 March
        rows = aftertax.fy_table(nav, [], {"FY2020-21": 10.0, "FY2021-22": 4.0}, final=116.0)
        self.assertAlmostEqual(rows[0]["before"], 0.2)
        self.assertAlmostEqual(rows[0]["after"], 0.1)
        self.assertAlmostEqual(rows[1]["before"], 120 / 110 - 1)
        self.assertAlmostEqual(rows[1]["after"], 116 / 110 - 1)

    def test_holdout_is_not_a_choice(self):
        self.assertNotIn("holdout", aftertax.PERIODS)


if __name__ == "__main__":
    unittest.main()
