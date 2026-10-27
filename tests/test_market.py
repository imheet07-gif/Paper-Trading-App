import datetime as dt
import io
import json
import os
import tempfile
import unittest
import zipfile
from decimal import Decimal

from _load import FIX, load

bc = load("core", "bhavcopy")
ca = load("core", "corporate_actions")
MKT = os.path.join(FIX, "market")
DAYS = ["20241231", "20250101", "20250102", "20250103"]


def read(name):
    with open(os.path.join(MKT, name), encoding="utf-8") as fh:
        return fh.read()


def zipped(text, name="x.csv"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def fake_nse(calls=None):
    """Answers bhavcopy, delivery and corporate action URLs from fixtures; 404 (None) otherwise."""
    files = {}
    for d in DAYS:
        day = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
        u = bc.urls(day)
        files[u["prices"]] = zipped(read("udiff_%s.csv" % d))
        files[u["delivery"]] = read("mto_%s.dat" % d).encode()

    def get(url):
        if calls is not None:
            calls.append(url)
        if "corporates-corporateActions" in url:
            return read("nse_corporate_actions.json").encode() if "from_date=01-01-2025" in url else b"[]"
        return files.get(url)
    return get


class Parsing(unittest.TestCase):
    def test_paise(self):
        self.assertEqual(bc.paise("581.1"), 58110)
        self.assertEqual(bc.paise("14657.46"), 1465746)
        self.assertIsNone(bc.paise(""))

    def test_old_format_with_isin(self):
        rows = bc.parse_old(read("old_20150101.csv"))
        self.assertEqual([r["symbol"] for r in rows], ["20MICRONS", "3IINFOTECH"])
        r = rows[0]
        self.assertEqual((r["date"], r["isin"], r["close"], r["prev_close"], r["volume"], r["trades"]),
                         ("2015-01-01", "INE144J01027", 3040, 3060, 37604, 200))

    def test_old_format_two_digit_year(self):
        self.assertEqual(bc.old_date("13-Jul-20"), "2020-07-13")
        self.assertEqual(bc.old_date("01-JAN-2015"), "2015-01-01")
        with self.assertRaises(ValueError):
            bc.old_date("2020-07-13")

    def test_old_format_without_isin_gives_nothing(self):
        self.assertEqual(bc.parse_old(read("old_20050103_no_isin.csv")), [])

    def test_udiff_keeps_stocks(self):
        rows = {r["symbol"]: r for r in bc.parse_udiff(read("udiff_20250101.csv"))}
        self.assertEqual(rows["SURYAROSNI"]["close"], 28920)
        self.assertEqual(rows["SURYAROSNI"]["prev_close"], 59465)  # NSE does not adjust it on the ex-date
        self.assertEqual(rows["SURYAROSNI"]["isin"], "INE335A01020")
        self.assertIn("SGBJUN28", rows)  # gold bonds are STK too; series filters them later

    def test_delivery(self):
        d = bc.parse_mto(read("mto_20250101.dat"))
        self.assertEqual(d[("SURYAROSNI", "EQ")], (298403, 3571))

    def test_indices(self):
        rows = {r["name"]: r for r in bc.parse_indices(read("ind_close_all_01072024.csv"))}
        self.assertEqual(rows["Nifty 50"]["close"], 24141.95)
        self.assertEqual(rows["Nifty 50"]["date"], "2024-07-01")
        self.assertIsNone(rows["India VIX"]["pe"])
        self.assertEqual(bc.parse_indices("<!DOCTYPE html><html>"), [])

    def test_url_switches_format(self):
        self.assertIn("/historical/EQUITIES/2024/JUL/cm05JUL2024bhav", bc.urls(dt.date(2024, 7, 5))["prices"])
        self.assertIn("BhavCopy_NSE_CM_0_0_0_20240708_F_0000", bc.urls(dt.date(2024, 7, 8))["prices"])
        self.assertTrue(bc.urls(dt.date(2025, 1, 1))["delivery"].endswith("MTO_01012025.DAT"))


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ca.connect(os.path.join(self.tmp.name, "m.db"))
        self.calls = []
        bc.backfill(self.db, dt.date(2024, 12, 30), dt.date(2025, 1, 3), pause=0, get=fake_nse(self.calls), log=None)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_days_stored_and_missing_recorded(self):
        days = [d for (d,) in self.db.execute("SELECT DISTINCT date FROM prices ORDER BY date")]
        self.assertEqual(days, ["2024-12-31", "2025-01-01", "2025-01-02", "2025-01-03"])
        missing = self.db.execute("SELECT date FROM fetched WHERE kind='prices' AND status='missing'").fetchall()
        self.assertEqual(missing, [("2024-12-30",)])  # no fixture for that day: recorded, not retried

    def test_rerun_fetches_nothing(self):
        before = len(self.calls)
        bc.backfill(self.db, dt.date(2024, 12, 30), dt.date(2025, 1, 3), pause=0, get=fake_nse(self.calls), log=None)
        self.assertEqual(len(self.calls), before)

    def test_parallel_downloads_store_the_same(self):
        other = ca.connect(os.path.join(self.tmp.name, "p.db"))
        bc.backfill(other, dt.date(2024, 12, 30), dt.date(2025, 1, 3), pause=0, get=fake_nse(), log=None, workers=3)
        q = "SELECT * FROM prices ORDER BY date, isin, series"
        self.assertEqual(other.execute(q).fetchall(), self.db.execute(q).fetchall())
        other.close()

    def test_delivery_joined_by_symbol_and_series(self):
        r = self.db.execute("SELECT deliv_qty, deliv_bp FROM prices WHERE symbol='SURYAROSNI' AND date='2025-01-01'").fetchone()
        self.assertEqual(r, (298403, 3571))


class CorporateActions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ca.connect(os.path.join(self.tmp.name, "m.db"))
        get = fake_nse()
        bc.backfill(self.db, dt.date(2024, 12, 31), dt.date(2025, 1, 3), pause=0, get=get, log=None)
        ca.store(self.db, ca.fetch(dt.date(2025, 1, 1), dt.date(2025, 1, 31), get=get, pause=0))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_subjects(self):
        p = ca.parse_subject
        self.assertEqual(p("Bonus 4:1"), {"kind": "bonus", "ratio": (Decimal(4), Decimal(1))})
        self.assertEqual(p("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share")["fv"],
                         (Decimal(10), Decimal(1)))
        self.assertEqual(p("Face Value Split (Sub-Division) - From Rs5/- Per Share To Rs 1/- Per Share")["fv"],
                         (Decimal(5), Decimal(1)))
        self.assertEqual(p("Rights 5:41 @ Premium Rs 109/-")["premium"], Decimal(109))
        self.assertEqual(p("Interim Dividend - Rs 12 Per Share Special Dividend - Rs 6 Per Share")["dividend"], Decimal(18))
        self.assertEqual(p("Demerger")["kind"], "restructure")
        # bonus and split in one subject: both apply (seen on NSE in several spellings)
        for s, kind, f in (("Bonus 1:1 And Face Value Split From Rs.10/- To Re.1/-", "bonus+split", Decimal("0.05")),
                           ("Bonus 1:2 And Face Value Split Rs.10/- To Rs.5/-", "bonus+split", Decimal(1) / 3),
                           ("Bonus 1 : 1 / Face Value Split From 10/- To Face Value 2/-", "bonus+split", Decimal("0.1")),
                           ("Consolidation Of Shares From Rs 1/- Per Share To Rs 10/- Per Share", "consolidation",
                            Decimal(10))):
            got = p(s)
            self.assertEqual(got["kind"], kind, s)
            self.assertAlmostEqual(ca.factor(got)[0], f, places=20)
        self.assertEqual(p("Annual General Meeting")["kind"], "other")
        self.assertEqual(p("Distribution - Rs 2 Per Unit Consisting Of Interest Rs 1.20 Per Unit")["kind"], "other")

    def test_factors(self):
        self.assertEqual(ca.factor({"kind": "bonus", "ratio": (Decimal(1), Decimal(1))})[0], Decimal("0.5"))
        self.assertEqual(ca.factor({"kind": "bonus", "ratio": (Decimal(4), Decimal(1))})[0], Decimal("0.2"))
        self.assertEqual(ca.factor({"kind": "split", "fv": (Decimal(10), Decimal(2))})[0], Decimal("0.2"))
        self.assertEqual(ca.factor({"kind": "consolidation", "fv": (Decimal(1), Decimal(10))})[0], Decimal(10))
        # rights 1:4 at 80 (face 10 + premium 70) with the stock at 100: TERP 96
        f, how = ca.factor({"kind": "rights", "ratio": (Decimal(1), Decimal(4)), "premium": Decimal(70)},
                           Decimal(100), Decimal(10))
        self.assertEqual((f, how), (Decimal("0.96"), "rights TERP"))
        self.assertEqual(ca.factor({"kind": "rights", "ratio": (Decimal(1), Decimal(4)), "premium": Decimal(95)},
                                   Decimal(100), Decimal(10))[0], Decimal(1))

    def test_stale_isin_resolved_through_prices(self):
        r = self.db.execute("SELECT isin_reported, isin, factor, check_note FROM corporate_actions "
                            "WHERE symbol='SURYAROSNI' AND kind='bonus'").fetchone()
        self.assertEqual(r, ("INE335A01012", "INE335A01020", "0.5", None))

    def test_adjusted_series_has_no_fake_crash(self):
        s = {d: (c, a) for d, c, a in ca.adjusted(self.db, "INE335A01020")}
        self.assertEqual(s["2024-12-31"], (Decimal("594.65"), Decimal("297.3250")))
        self.assertEqual(s["2025-01-01"][1], Decimal("289.2000"))
        g = [a for _, _, a in ca.adjusted(self.db, "INE276A01018")]
        moves = [abs(b / a - 1) for a, b in zip(g, g[1:])]
        self.assertLess(max(moves), Decimal("0.05"))  # 4:1 bonus on 2025-01-03 adjusted away

    def test_wrong_factor_is_flagged(self):
        self.db.execute("DELETE FROM corporate_actions")
        rows = [r for r in json.loads(read("nse_corporate_actions.json")) if r["symbol"] == "GARFIBRES"]
        for r in rows:
            if r["subject"] == "Bonus 4:1":
                r["subject"] = "Bonus 1:1"  # wrong on purpose
        ca.store(self.db, rows)
        note = self.db.execute("SELECT check_note FROM corporate_actions WHERE symbol='GARFIBRES' AND kind='bonus'").fetchone()[0]
        self.assertIn("factor may be wrong", note)

    def test_unexplained_jump_reported(self):
        self.db.execute("DELETE FROM corporate_actions")
        jumps = ca.unexplained_jumps(self.db)
        self.assertEqual(sorted((i, d) for i, d, _ in jumps),
                         [("INE276A01018", "2025-01-03"), ("INE335A01020", "2025-01-01")])



class IsinChains(unittest.TestCase):
    """A face value split usually brings a new ISIN: the history must join up."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ca.connect(os.path.join(self.tmp.name, "m.db"))
        rows = []
        for d, isin, close in (("2025-01-01", "INE000X01011", 100000), ("2025-01-02", "INE000X01011", 101000),
                               ("2025-01-03", "INE000X01029", 20400), ("2025-01-06", "INE000X01029", 20600)):
            rows.append({"date": d, "isin": isin, "symbol": "XYZ", "series": "EQ", "open": close, "high": close,
                         "low": close, "close": close, "last": close, "prev_close": None, "volume": 1, "value": close,
                         "trades": 1})
        # a different company reusing the symbol a year later must not join the chain
        rows.append(dict(rows[-1], date="2026-03-02", isin="INE999Z01015"))
        bc.store_day(self.db, rows)
        ca.store(self.db, [{"exDate": "03-Jan-2025", "symbol": "XYZ", "isin": "INE000X01011", "faceVal": "2",
                            "series": "EQ",
                            "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"}])

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_links_and_chain(self):
        self.assertEqual(ca.links(self.db), {"INE000X01011": "INE000X01029"})
        self.assertEqual(ca.chain("INE000X01011", ca.links(self.db)), ["INE000X01011", "INE000X01029"])
        self.assertEqual(ca.chain("INE999Z01015", ca.links(self.db)), ["INE999Z01015"])

    def test_split_checked_across_the_isin_change(self):
        r = self.db.execute("SELECT isin, factor, check_note FROM corporate_actions").fetchone()
        self.assertEqual(r, ("INE000X01029", "0.2", None))  # stored on the latest ISIN, move checked: +1%

    def test_two_events_on_one_day_are_checked_together(self):
        # a 1:1 bonus and a 10 -> 1 split listed as separate rows: factor 0.05 in all
        self.db.execute("DELETE FROM corporate_actions")
        # Rs 1,010 before; Rs 50.50 after is exactly 1,010 x 0.05
        self.db.execute("UPDATE prices SET close=5050, open=5050 WHERE date>='2025-01-03' AND date<'2026-01-01'")
        ca.store(self.db, [
            {"exDate": "03-Jan-2025", "symbol": "XYZ", "isin": "INE000X01011", "faceVal": "1", "series": "EQ",
             "subject": "Bonus 1:1"},
            {"exDate": "03-Jan-2025", "symbol": "XYZ", "isin": "INE000X01011", "faceVal": "1", "series": "EQ",
             "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"}])
        notes = [n for (n,) in self.db.execute("SELECT check_note FROM corporate_actions")]
        self.assertEqual(notes, [None, None])  # each alone would fail; together the move is 0%
        self.assertEqual(ca.failed_checks(self.db), [])

    def test_failed_check_is_listed(self):
        self.db.execute("DELETE FROM corporate_actions")
        ca.store(self.db, [{"exDate": "03-Jan-2025", "symbol": "XYZ", "isin": "INE000X01011", "faceVal": "5",
                            "series": "EQ", "subject": "Bonus 1:1"}])  # real move was a 5x split
        self.assertEqual(ca.failed_checks(self.db), [("INE000X01029", "2025-01-03")])

    def test_adjusted_history_is_continuous(self):
        s = ca.adjusted(self.db, "INE000X01011")
        self.assertEqual([a for _, _, a in s], [Decimal("200.0000"), Decimal("202.0000"), Decimal("204.0000"),
                                                Decimal("206.0000")])
        self.assertEqual(ca.unexplained_jumps(self.db), [])


if __name__ == "__main__":
    unittest.main()
