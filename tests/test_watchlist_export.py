"""core/watchlist_export.py: a name-free store summary for an outside watchlist tracker.

The store here is made up: two stocks over 25 sessions, one of which
changed ISIN (a face value split) so that its older filings sit under the
old ISIN, and a shareholding filing that carries an ISIN that never traded.
"""

import datetime as dt
import json
import os
import tempfile
import unittest

from _load import load

wx = load("core", "watchlist_export")
bc = load("core", "bhavcopy")
fin = load("core", "financials")
ev = load("core", "events")
sh = load("core", "shareholding")
dl = load("core", "deals")

OLD, NEW, BANK = "INE001A01011", "INE001A01029", "INE002B01012"
WATCHLIST = """\
# a tracker config
themes:
  compounders:
    title: Compounders
stocks:
  - {symbol: ALPHA, yahoo: ALPHA.NS, name: Alpha, theme: compounders}
  - symbol: "bankx"
    yahoo: BANKX.NS
  - {symbol: GHOST, yahoo: GHOST.NS}   # not on NSE
indices:
  - {id: NIFTY50, symbol: NOTASTOCK, yahoo: "^NSEI"}
"""
SECRET_NAMES = ("Ramesh Private Person", "Some Client Llp", "Holder Name Pvt")


def sessions(n, last="2026-10-30"):
    out, d = [], dt.date.fromisoformat(last)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out[::-1]


def bar(day, isin, symbol, close, prev, volume, deliv_bp):
    return {"date": day, "isin": isin, "symbol": symbol, "series": "EQ", "open": close, "high": close, "low": close,
            "close": close, "last": close, "prev_close": prev, "volume": volume, "value": volume * close // 100,
            "trades": 1, "deliv_qty": None, "deliv_bp": deliv_bp}


def store(path):
    db = fin.connect(path)
    for mod in (ev, sh, dl):
        mod.connect(path).close()
    days = sessions(25)
    split = days[3]  # ALPHA trades under OLD until the split, then under NEW
    rows = []
    for k, day in enumerate(days):
        isin = OLD if day < split else NEW
        last = k == len(days) - 1
        rows.append(bar(day, isin, "ALPHA", 11000 if last else 10000, 10000, 2000 if last else 1000,
                        8000 if last else 5000))
        rows.append(bar(day, BANK, "BANKX", 50000, 50000, 500, 4000))
    db.executemany("INSERT INTO prices VALUES (:date,:isin,:symbol,:series,:open,:high,:low,:close,:last,"
                   ":prev_close,:volume,:value,:trades,:deliv_qty,:deliv_bp)", rows)
    db.execute("INSERT INTO fetched VALUES (?, 'prices', 'ok', 2, 'https://nsearchives.nseindia.com/p.zip')",
               (days[-1],))
    cols = ("seq", "symbol", "isin", "nature", "audited", "period_end", "revenue", "net_profit", "net_profit_owners",
            "eps_basic", "interest_earned", "interest_expended", "provisions", "broadcast_at", "xbrl")
    fins = [
        # the year-ago quarter is under the old ISIN: found over the chain
        ("a1", "ALPHA", OLD, "Consolidated", "Unaudited", "2025-06-30", "1000", "100", "90", "1.0", None, None, None,
         "2025-08-01T18:00:00", "https://x/a1.xml"),
        ("a2", "ALPHA", NEW, "Consolidated", "Unaudited", "2026-06-30", "1200", "150", "135", "1.5", None, None, None,
         "2026-08-01T18:00:00", "https://x/a2.xml"),
        # a revision of the same quarter, published later: it wins
        ("a3", "ALPHA", NEW, "Consolidated", "Unaudited", "2026-06-30", "1250", "160", "144", "1.6", None, None, None,
         "2026-08-20T18:00:00", "https://x/a3.xml"),
        ("a4", "ALPHA", NEW, "Standalone", "Unaudited", "2026-06-30", "900", "80", None, "0.8", None, None, None,
         "2026-08-01T18:00:00", "https://x/a4.xml"),
        # published after the export's cutoff: not seen
        ("a5", "ALPHA", NEW, "Consolidated", "Unaudited", "2026-09-30", "9999", "999", "999", "9.9", None, None, None,
         "2026-11-30T18:00:00", "https://x/a5.xml"),
        ("b1", "BANKX", BANK, "Standalone", "Unaudited", "2025-06-30", None, "50", None, "5", "1000", "600", "20",
         "2025-07-20T18:00:00", "https://x/b1.xml"),
        ("b2", "BANKX", BANK, "Standalone", "Unaudited", "2026-06-30", None, "40", None, "4", "1100", "650", "30",
         "2026-07-20T18:00:00", "https://x/b2.xml"),
    ]
    db.executemany("INSERT INTO financials (%s) VALUES (%s)" % (", ".join(cols), ",".join("?" * len(cols))), fins)
    db.execute("INSERT INTO results VALUES ('r1', 'BANKX', ?, '2026-07-01', '2026-09-30', 'Q2', 'Standalone', "
               "'Unaudited', '2026-10-28T17:00:00')", (BANK,))
    db.executemany("INSERT INTO shareholding (record_id, symbol, isin, period_end, promoter_pct, public_pct, "
                   "employee_trusts_pct, revised, submitted_on, revised_on, broadcast_at, xbrl) "
                   "VALUES (?, ?, ?, ?, ?, ?, NULL, 0, NULL, NULL, ?, ?)", [
        ("s1", "ALPHA", OLD, "2026-06-30", "60.00", "40.00", "2026-07-15T12:00:00", "https://x/s1.xml"),
        ("s2", "ALPHA", NEW, "2026-09-30", "58.50", "41.50", "2026-10-15T12:00:00", "https://x/s2.xml"),
        # BANKX's filing carries an ISIN that never traded: matched on symbol
        ("s3", "BANKX", "IN9002B01010", "2026-09-30", "51.00", "49.00", "2026-10-16T12:00:00", "https://x/s3.xml"),
    ])
    db.executemany("INSERT INTO shareholding_detail (record_id, isin, period_end, promoter, fii, fpi_cat1, fpi_cat2, "
                   "dii, mutual_funds, insurance, retail_small, retail_large, pledged) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                       ("s1", OLD, "2026-06-30", "60.00", "10.00", "8.00", "1.00", "15.00", "9.00", "3.00", "10.00",
                        "5.00", 0),
                       ("s2", NEW, "2026-09-30", "58.50", "12.25", "9.00", "1.25", "14.00", "8.50", "3.00", "10.50",
                        "5.00", 1),
                   ])
    db.executemany("INSERT INTO shareholding_holders VALUES ('s2', ?, '2026-09-30', NULL, ?, ?, 'public', 1, 0, NULL, "
                   "'2.0', 100)", [(NEW, SECRET_NAMES[2], "K")])
    db.executemany("INSERT INTO deals VALUES (?, ?, 'ALPHA', ?, 'Alpha Ltd', ?, ?, ?, ?, NULL)", [
        ("bulk", days[-1], NEW, SECRET_NAMES[1], "buy", 100, "110.00"),
        ("bulk", days[-2], NEW, SECRET_NAMES[1] + " Two", "buy", 50, "100.50"),
        ("block", days[-1], NEW, SECRET_NAMES[1], "sell", 10, "110.00"),
        ("bulk", "2026-09-01", NEW, SECRET_NAMES[1], "buy", 999, "1.00"),  # outside the window
    ])
    db.execute("INSERT INTO insider VALUES ('d1', 'p1', 'ALPHA', ?, ?, 'Promoters', 'Market Purchase', 'Buy', "
               "'Equity Shares', 300, 33000, NULL, NULL, '2026-10-20T10:00:00')", (NEW, SECRET_NAMES[0]))
    db.execute("INSERT INTO sast VALUES ('x1', 'ALPHA', ?, ?, 0, 'Open market', 'Acquisition', 'Reg29', 1000, NULL, "
               "5000, '2.1', NULL, '5.2', NULL, NULL, '2026-10-21T10:00:00')", (NEW, SECRET_NAMES[2]))
    db.commit()
    db.close()
    return days


class Watchlist(unittest.TestCase):
    def test_symbols_from_the_stocks_key_only(self):
        self.assertEqual(wx.symbols_from_watchlist(WATCHLIST), ["ALPHA", "BANKX", "GHOST"])

    def test_no_symbols_is_an_error(self):
        with self.assertRaises(wx.ExportError):
            wx.symbols_from_watchlist("stocks: []\nindices:\n  - {symbol: X}\n")

    def test_a_bad_symbol_is_refused(self):
        with self.assertRaises(wx.ExportError):
            wx.check_symbols(["ALPHA", "DROP TABLE"])


class Export(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "market.db")
        cls.days = store(cls.db_path)
        cls.out = os.path.join(cls.tmp.name, "out")
        cls.path, cls.doc = wx.export(["ALPHA", "BANKX", "GHOST"], cls.out, cls.db_path, now="2026-10-31T20:00:00")
        cls.by = {s["symbol"]: s for s in cls.doc["stocks"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_files_written_for_the_session_and_as_latest(self):
        self.assertEqual(self.doc["session"], self.days[-1])
        self.assertTrue(self.path.endswith(self.days[-1] + ".json"))
        with open(os.path.join(self.out, "latest.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["session"], self.days[-1])

    def test_no_names_anywhere(self):
        text = json.dumps(self.doc)
        for name in SECRET_NAMES:
            self.assertNotIn(name, text)

    def test_price_against_the_sessions_before(self):
        p = self.by["ALPHA"]["price"]
        self.assertEqual((p["close"], p["prev_close"], p["change_pct"]), ("110.00", "100.00", "10.00"))
        self.assertEqual((p["delivery_pct"], p["delivery_pct_avg"], p["volume_vs_avg"]), ("80.00", "50.00", "2.00"))
        self.assertEqual(p["sessions_compared"], 20)
        self.assertEqual(p["sources"], ["https://nsearchives.nseindia.com/p.zip"])

    def test_isin_chain_reaches_the_old_isin(self):
        self.assertEqual(self.by["ALPHA"]["isin"], NEW)
        self.assertEqual(self.by["ALPHA"]["isin_chain"], [OLD, NEW])

    def test_results_latest_revision_consolidated_against_a_year_earlier(self):
        r = self.by["ALPHA"]["results"]["latest"]
        self.assertEqual((r["quarter_end"], r["nature"], r["top_line_rs"], r["net_profit_rs"]),
                         ("2026-06-30", "Consolidated", "1250", "144"))
        self.assertEqual((r["year_ago_quarter"], r["top_line_yoy_pct"], r["net_profit_yoy_pct"]),
                         ("2025-06-30", "25.0", "60.0"))
        self.assertEqual(r["source"], "https://x/a3.xml")

    def test_filings_after_the_cutoff_are_not_seen(self):
        self.assertNotIn("9999", json.dumps(self.by["ALPHA"]["results"]))

    def test_bank_lines_and_a_later_quarter_announced(self):
        res = self.by["BANKX"]["results"]
        r = res["latest"]
        self.assertTrue(r["bank"])
        self.assertEqual((r["top_line_rs"], r["net_interest_income_rs"], r["provisions_rs"]), ("1100", "450", "30"))
        self.assertEqual(r["net_profit_yoy_pct"], "-20.0")
        self.assertTrue(any("2026-09-30" in n for n in res["notes"]))
        self.assertEqual(res["announced_recently"], [{"quarter_end": "2026-09-30", "published": "2026-10-28"}])

    def test_shareholding_two_quarters_over_the_chain(self):
        s = self.by["ALPHA"]["shareholding"]
        self.assertEqual((s["quarter_end"], s["previous_quarter"], s["matched_on"]), ("2026-09-30", "2026-06-30", "isin"))
        self.assertEqual((s["pct"]["promoter"], s["pct"]["fii"], s["pct"]["fpi"], s["pct"]["retail"]),
                         ("58.50", "12.25", "10.25", "15.50"))
        self.assertEqual((s["change_pp"]["promoter"], s["change_pp"]["fii"]), ("-1.50", "2.25"))
        self.assertTrue(s["promoter_pledged"])

    def test_shareholding_by_symbol_says_so(self):
        s = self.by["BANKX"]["shareholding"]
        self.assertEqual((s["matched_on"], s["filing_isin"], s["pct"]["promoter"]), ("symbol", "IN9002B01010", "51.00"))
        self.assertIsNone(s["pct"]["fii"])

    def test_deals_as_totals_in_the_window(self):
        d = self.by["ALPHA"]["deals"]
        groups = {(g["kind"], g["side"]): g for g in d["groups"]}
        self.assertEqual(set(groups), {("bulk", "buy"), ("block", "sell")})
        self.assertEqual((groups["bulk", "buy"]["deals"], groups["bulk", "buy"]["shares"],
                          groups["bulk", "buy"]["value_rs"]), (2, 150, "16025.00"))
        self.assertEqual(d["sources"], sorted(dl.URLS.values()))

    def test_insider_and_sast_without_names(self):
        ins = self.by["ALPHA"]["insider"]["groups"]
        self.assertEqual(ins, [{"category": "Promoters", "transaction": "Buy", "mode": "Market Purchase", "filings": 1,
                                "shares": 300, "value_rs": "33000"}])
        sa = self.by["ALPHA"]["sast"]["disclosures"]
        self.assertEqual(len(sa), 1)
        self.assertEqual((sa[0]["promoter"], sa[0]["pct_after"]), (False, "5.2"))

    def test_a_symbol_not_on_nse_says_so(self):
        self.assertIsNone(self.by["GHOST"]["isin"])
        self.assertIn("not in the NSE store", self.by["GHOST"]["error"])

    def test_feed_ends_tell_none_from_not_collected(self):
        self.assertEqual(self.doc["feeds_end"]["prices"], self.days[-1])
        self.assertEqual(self.doc["feeds_end"]["insider"], "2026-10-20T10:00:00")

    def test_as_of_an_earlier_session(self):
        doc = wx.build(wx.connect(self.db_path), ["ALPHA"], on=self.days[-2])
        self.assertEqual(doc["session"], self.days[-2])
        self.assertEqual(doc["stocks"][0]["price"]["close"], "100.00")


class Configured(unittest.TestCase):
    def test_nothing_without_a_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(wx.run_configured(config=os.path.join(tmp, "none.json")))

    def test_config_paths_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "market.db")
            store(db_path)
            wl = os.path.join(tmp, "watchlist.yaml")
            with open(wl, "w", encoding="utf-8") as fh:
                fh.write(WATCHLIST)
            cfg = os.path.join(tmp, "cfg.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"watchlist": wl, "out": os.path.join(tmp, "out")}, fh)
            logs = []
            path = wx.run_configured(db_path, cfg, log=logs.append)
            self.assertTrue(os.path.exists(path))
            self.assertIn("GHOST", logs[0])

    def test_a_config_missing_a_path_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "cfg.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"watchlist": "x"}, fh)
            with self.assertRaises(wx.ExportError):
                wx.load_config(cfg)


if __name__ == "__main__":
    unittest.main()
