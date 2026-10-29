import datetime as dt
import importlib.util
import os
import re
import sys
import tempfile
import unittest

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None

from _load import ROOT, load  # noqa: E402

bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
fin = load("core", "financials")
if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import numpy as np  # noqa: E402
    import pandas as pd  # noqa: E402
    import fundamentals as fu  # noqa: E402
    import panel  # noqa: E402
    import signals  # noqa: E402
    from test_research import weekdays  # noqa: E402

A, B, BANK = "INE001F01011", "INE002F01019", "INE003F01017"
ENDS = ["2013-03-31", "2013-06-30", "2013-09-30", "2013-12-31", "2014-03-31", "2014-06-30", "2014-09-30",
        "2014-12-31", "2015-03-31"]


def published(end, days=40):
    return (dt.date.fromisoformat(end) + dt.timedelta(days=days)).isoformat() + "T18:00:00"


def row(seq, isin, end, nature="Consolidated", at=None, **v):
    r = {c: None for c in fin.COLUMNS}
    r.update(seq=seq, symbol=isin[-6:], isin=isin, nature=nature, period_end=end, broadcast_at=at or published(end))
    r.update({k: str(x) for k, x in v.items()})
    return r


def store(path):
    db = ca.connect(path)
    days = weekdays(dt.date(2013, 1, 1), 900)  # to mid-2016: past the staleness limit
    prices = []
    for d in days:
        for isin, px in ((A, 100.0), (B, 50.0), (BANK, 200.0)):
            p = int(px * 100)
            prices.append({"date": d.isoformat(), "isin": isin, "symbol": isin[-6:], "series": "EQ", "open": p,
                           "high": p, "low": p, "close": p, "last": p, "prev_close": p, "volume": 10_000_000,
                           "value": 10_000_000 * p, "trades": 1})
    bc.store_day(db, prices)
    db.commit()
    db.close()
    db = fin.connect(path)
    rows = []
    for k, end in enumerate(ENDS):
        # A grows 10% a year, profitable, small finance costs; year-end files carry reserves
        rev = 1000 * (1.1 ** (k / 4))
        march = end.endswith("03-31")
        rows.append(row("a%d" % k, A, end, revenue=rev, other_income=10, pbt=200, net_profit=150, net_profit_owners=150,
                        eps_diluted=1.5 + 0.1 * k, finance_costs=20, depreciation=30, exceptional=0, face_value=10,
                        paid_up=100, **({"reserves": 900} if march else {})))
        # B: a loss-maker with no finance costs
        rows.append(row("b%d" % k, B, end, revenue=500, other_income=50, pbt=-10, net_profit=-10, eps_diluted=-0.5,
                        finance_costs=0, depreciation=5, exceptional=0, face_value=1, paid_up=10))
        # a lender: interest earned, no revenue
        rows.append(row("l%d" % k, BANK, end, interest_earned=800, pbt=100, net_profit=80, eps_diluted=2 + 0.1 * k,
                        face_value=10, paid_up=1000))
    rows.append(row("s0", A, ENDS[-1], nature="Standalone", revenue=1, pbt=1, net_profit=1, eps_diluted=99))
    for r in rows:
        fin._insert(db, r)
    db.commit()
    db.close()
    return days


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Fundamentals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "m.db")
        cls.days = store(cls.path)
        cls.p = panel.load(cls.path)
        cls.f = fu._frames(cls.p)
        cls.col = {s: s for s in cls.p.close.columns}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def at(self, name, day, isin):
        """The score on the last trading day on or before `day`."""
        i = self.p.dates.searchsorted(pd.Timestamp(day), "right") - 1
        return self.f[name][isin].iloc[i]

    def test_a_figure_counts_from_the_session_after_its_broadcast(self):
        pub = published("2014-03-31")[:10]  # the year-on-year needs the March 2013 quarter too
        before = (pd.Timestamp(pub) - pd.Timedelta(days=1)).date().isoformat()
        after = (pd.Timestamp(pub) + pd.Timedelta(days=4)).date().isoformat()  # a trading day after a weekend too
        self.assertTrue(np.isnan(self.at("revenue_growth_q", before, A)))
        self.assertTrue(np.isnan(self.at("revenue_growth_q", pub, A)))  # published that evening: next session
        self.assertAlmostEqual(self.at("revenue_growth_q", after, A), 0.1, places=6)

    def test_consolidated_wins_over_standalone(self):
        late = published(ENDS[-1])[:10]
        day = (pd.Timestamp(late) + pd.Timedelta(days=3)).date().isoformat()
        self.assertAlmostEqual(self.at("revenue_growth_q", day, A), 0.1, places=6)  # not the standalone 1 rupee

    def test_trailing_ratios_roe_and_coverage(self):
        day = (pd.Timestamp(published(ENDS[-1])) + pd.Timedelta(days=3)).date().isoformat()
        self.assertAlmostEqual(self.at("net_margin", day, A), 600 / sum(1000 * 1.1 ** (k / 4) for k in range(5, 9)), 6)
        self.assertAlmostEqual(self.at("roe", day, A), 600 / 1000)  # TTM profit over paid-up 100 + reserves 900
        self.assertAlmostEqual(self.at("interest_coverage", day, A), (800 + 80) / 80)
        self.assertEqual(self.at("interest_coverage", day, B), fu.BEST)  # no finance costs: first
        self.assertEqual(self.at("other_income_share", day, B), fu.WORST)  # a loss: last, not dropped
        self.assertEqual(self.at("profit_share_8q", day, A), 1.0)
        self.assertEqual(self.at("profit_share_8q", day, B), 0.0)

    def test_lenders_get_no_revenue_or_coverage_metrics(self):
        day = (pd.Timestamp(published(ENDS[-1])) + pd.Timedelta(days=3)).date().isoformat()
        for m in ("revenue_growth_q", "operating_margin", "net_margin", "interest_coverage", "sales_to_price"):
            self.assertTrue(np.isnan(self.at(m, day, BANK)), m)
        self.assertFalse(np.isnan(self.at("eps_growth_q", day, BANK)))

    def test_scores_go_stale(self):
        last = pd.Timestamp(ENDS[-1])
        fresh = (last + pd.Timedelta(days=300)).date().isoformat()
        old = (last + pd.Timedelta(days=370)).date().isoformat()
        self.assertFalse(np.isnan(self.at("roe", fresh, A)))
        self.assertTrue(np.isnan(self.at("roe", old, A)))
        self.assertTrue(np.isnan(self.at("earnings_yield", old, A)))
        pub = pd.Timestamp(published(ENDS[-1])[:10])
        s_in = (pub + pd.Timedelta(days=60)).date().isoformat()
        s_out = (pub + pd.Timedelta(days=130)).date().isoformat()
        self.assertFalse(np.isnan(self.at("surprise", s_in, A)))
        self.assertTrue(np.isnan(self.at("surprise", s_out, A)))

    def test_priced_metrics_use_market_cap(self):
        day = (pd.Timestamp(published(ENDS[-1])) + pd.Timedelta(days=3)).date().isoformat()
        mcap = 100.0 * (100 / 10)  # price 100, shares paid-up 100 over face value 10
        self.assertAlmostEqual(self.at("earnings_yield", day, A), 600 / mcap)
        self.assertAlmostEqual(self.at("book_to_price", day, A), 1000 / mcap)
        self.assertAlmostEqual(self.at("small_size", day, A), -np.log(mcap))

    def test_eps_is_restated_through_splits(self):
        qs = [{"period_end": e, "eps_diluted": 10.0, "m": 0.5 if k < 4 else 1.0} for k, e in enumerate(ENDS[:5])]
        # a 2:1 split between the quarters: 10 then on half the old count is the same earnings
        self.assertAlmostEqual(fu.compute(qs)["eps_growth_q"], 1.0)  # 10 against 5 restated
        qs[-1]["m"] = 0.5
        self.assertAlmostEqual(fu.compute(qs)["eps_growth_q"], 0.0)

    def test_composites_need_two_parts(self):
        day = (pd.Timestamp(published(ENDS[-1])) + pd.Timedelta(days=3)).date().isoformat()
        self.assertGreater(self.at("quality", day, A), self.at("quality", day, B))

    def test_the_batch_is_the_one_declared(self):
        names = [k for k in signals.SIGNALS if k.startswith("f_")]
        self.assertEqual(len(names), 20)
        with open(os.path.join(ROOT, "research", "FINDINGS.md"), encoding="utf-8") as fh:
            text = fh.read()
        entry = text[text.index("fundamentals batch F6"):]
        declared = set(re.findall(r"`(f_[a-z0-9_]+)`", entry))
        self.assertEqual(declared, set(names))
        self.assertIs(signals.SIGNALS["f_roe"](self.p), self.f["roe"])  # computed once per panel


if __name__ == "__main__":
    unittest.main()
