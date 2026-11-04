"""research/retail.py: the rules file, the minimum-capital check and the inventory. Standard library only."""

import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal

from _load import ROOT, load  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "research"))
import retail  # noqa: E402

bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")


class Rules(unittest.TestCase):
    def setUp(self):
        self.r = retail.load()

    def test_every_block_has_a_source_and_the_limits_are_off(self):
        for k in retail.REQUIRED:
            self.assertTrue(self.r[k]["source"].strip(), k)
        self.assertEqual(self.r["capital_levels"]["value"], [20000, 1000000])
        self.assertIs(self.r["leverage"]["value"], False)
        self.assertIs(self.r["short_selling"]["value"], False)
        self.assertEqual(self.r["liquidity"]["max_share_of_median_value"], 0.01)
        self.assertEqual(self.r["walk_forward"]["periods"], ["dev"])

    def test_no_published_rate_is_copied_in(self):
        values = [json.dumps({k: v for k, v in block.items() if k not in ("source", "rule", "modelled_as")})
                  for block in self.r.values() if isinstance(block, dict)]
        self.assertNotIn("15.34", " ".join(values))  # the DP charge is read from charges.json, never a value here
        self.assertEqual(retail.dp_charge(self.r), Decimal("15.34"))

    def bad(self, change):
        r = json.loads(json.dumps(self.r))
        change(r)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(r, fh)
        try:
            with self.assertRaises(ValueError):
                retail.load(fh.name)
        finally:
            os.unlink(fh.name)

    def test_refuses_leverage_shorts_the_holdout_and_missing_sources(self):
        self.bad(lambda r: r["leverage"].update(value=True))
        self.bad(lambda r: r["short_selling"].update(value=True))
        self.bad(lambda r: r["walk_forward"].update(periods=["dev", "holdout"]))
        self.bad(lambda r: r["liquidity"].update(source=""))
        self.bad(lambda r: r.pop("tax"))
        self.bad(lambda r: r["min_capital"].update(max_fixed_charge_share=0))
        self.bad(lambda r: r["max_capital"].update(max_skipped_share=1))
        self.bad(lambda r: r.pop("max_capital"))


class MinCapital(unittest.TestCase):
    def setUp(self):
        self.r = retail.load()
        self.dp = Decimal("15.34")

    def test_a_slice_is_whole_shares_and_carries_the_dp_charge(self):
        # Rs 15.34 at most 0.5% of the position: at least Rs 3,068, in whole shares
        self.assertEqual(retail.min_slice(100, self.r, self.dp), Decimal(3100))  # 31 shares
        self.assertEqual(retail.min_slice(5000, self.r, self.dp), Decimal(5000))  # one share is enough
        self.assertEqual(retail.min_slice("3068", self.r, self.dp), Decimal(3068))
        with self.assertRaises(ValueError):
            retail.min_slice(0, self.r, self.dp)

    def test_equal_money_needs_the_largest_slice_in_every_position(self):
        self.assertEqual(retail.min_capital([100, 5000], self.r, dp=self.dp), Decimal(10000))
        self.assertEqual(retail.min_capital([100] * 3, self.r, positions=20, dp=self.dp), Decimal(62000))
        self.assertIsNone(retail.min_capital([], self.r))

    def test_an_idea_failing_at_the_low_level_is_flagged_with_its_minimum(self):
        per = [Decimal(x) for x in (9000, 12000, 15000, 18000, 19000, 19500, 19900, 19990, 19999, 60000)]
        m = retail.idea_minimum(per, self.r)
        self.assertEqual(m["minimum"], Decimal(19999))  # 90% of dates covered
        self.assertEqual(m["fails"], [])
        self.assertEqual(m["covered"]["20000"], 0.9)
        per[-2] = Decimal(25000)
        m = retail.idea_minimum(per + [None], self.r)
        self.assertEqual(m["minimum"], Decimal(25000))
        self.assertEqual(m["fails"], [Decimal(20000)])
        self.assertEqual(m["dates"], 10)

    def test_maximum_capital_from_the_liquidity_limit(self):
        self.assertEqual(self.r["max_capital"]["max_skipped_share"], 0.10)
        # 20 positions, 1% of a Rs 2 crore median: above Rs 40 lakh the pick is passed over
        self.assertEqual(retail.liquidity_threshold(Decimal(20000000), 20, self.r), Decimal(4000000))
        self.assertEqual(retail.liquidity_threshold(None, 20, self.r), Decimal(0))
        t = [Decimal(x) for x in (500000, 2000000, 3000000, 4000000, 5000000, 6000000, 7000000, 8000000, 9000000,
                                  10000000)]
        m = retail.idea_maximum(t, self.r)
        self.assertEqual(m["maximum"], Decimal(2000000))  # one of ten (10%) passed over at Rs 20 lakh, two above it
        self.assertEqual(m["skipped_at"], {"20000": 0.0, "1000000": 0.1})
        self.assertEqual(m["fails"], [])
        m = retail.idea_maximum([Decimal(800000)] * 2 + t[2:], self.r)
        self.assertEqual(m["maximum"], Decimal(800000))
        self.assertEqual(m["fails"], [Decimal(1000000)])
        self.assertIsNone(retail.idea_maximum([], self.r)["maximum"])

    def test_quantile_is_nearest_rank(self):
        self.assertEqual(retail.quantile([5, 1, 3, 2, 4], "0.5"), 3)
        self.assertEqual(retail.quantile([1, 2], "0.9"), 2)
        self.assertIsNone(retail.quantile([], "0.5"))


class Inventory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "m.db")
        db = ca.connect(self.path)
        rows = []
        for d in ("2024-01-01", "2024-01-02"):
            for isin, sym, series in (("INE001A01010", "AAA", "EQ"), ("INE002A01010", "BBB", "EQ"),
                                      ("INF001A01010", "GOLDETF", "EQ"), ("IN0020160001", "SGBJUN24", "GB"),
                                      ("INE003A01010", "CCC", "BE")):
                rows.append({"date": d, "isin": isin, "symbol": sym, "series": series, "open": 100, "high": 100,
                             "low": 100, "close": 100, "last": 100, "prev_close": 100, "volume": 1, "value": 100,
                             "trades": 1})
        rows.append({"date": "2024-01-01", "isin": "INE004A01010", "symbol": "DDD", "series": "ZZ", "open": 1,
                     "high": 1, "low": 1, "close": 1, "last": 1, "prev_close": 1, "volume": 1, "value": 1, "trades": 1})
        bc.store_day(db, rows)
        db.commit()
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_and_ranges_by_kind(self):
        before = os.path.getmtime(self.path), os.path.getsize(self.path)
        inv = retail.inventory(self.path)
        self.assertEqual((os.path.getmtime(self.path), os.path.getsize(self.path)), before)  # read-only
        self.assertEqual(inv["last_session"], "2024-01-02")
        by = {g["kind"]: g for g in inv["groups"]}
        self.assertEqual(by["equity"]["instruments"], 2)
        self.assertEqual(by["equity"]["rows"], 4)
        self.assertEqual((by["equity"]["first"], by["equity"]["last"]), ("2024-01-01", "2024-01-02"))
        self.assertEqual(by["etf"]["instruments"], 1)
        self.assertEqual(by["sgb"]["on_last_session"], 1)
        self.assertEqual(by["equity_t2t"]["instruments"], 1)
        other = by["other"]
        self.assertEqual((other["series"], other["on_last_session"]), ("ZZ", 0))
        self.assertEqual([g["kind"] for g in inv["groups"]][:3], ["equity", "etf", "sgb"])
        text = retail.render_inventory(inv)
        self.assertIn("1 other series as stored", text)
        self.assertIn("series ZZ", retail.render_inventory(inv, show_all=True))

    def test_a_missing_store_is_an_error_and_is_not_created(self):
        missing = os.path.join(self.tmp.name, "none.db")
        with self.assertRaises(FileNotFoundError):
            retail.inventory(missing)
        self.assertFalse(os.path.exists(missing))


if __name__ == "__main__":
    unittest.main()
