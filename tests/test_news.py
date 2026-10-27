import datetime as dt
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("trader-news", "news")
# real NSE corporate-announcements rows, cut on 2026-10-27 (tests/fixtures/events/README.md)
ANN = os.path.join(FIX, "events", "announcements_2024-2026.json")
SYMS = ["BHARTIARTL", "RPOWER", "PARKHOSPS", "GPIL", "UNIVCABLES"]
NOW = "2026-08-08T09:00"


def rows():
    with open(ANN, encoding="utf-8") as fh:
        return json.load(fh)


def run(args, get=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args, get=get)
    return code, out.getvalue(), err.getvalue()


class Parse(unittest.TestCase):
    def test_nse_rows(self):
        items = {it["symbol"]: it for it in news.parse(rows())}
        self.assertEqual(sorted(items), sorted(SYMS))
        r = items["RPOWER"]
        self.assertEqual(r["time"], dt.datetime(2026, 8, 6, 19, 42, 43, tzinfo=news.IST))
        self.assertEqual((r["id"], r["isin"], r["heading"]), ("nse:106729688", "INE614G01033", "Outcome of Board Meeting"))
        self.assertTrue(r["link"].startswith("https://nsearchives.nseindia.com/corporate/"))
        self.assertTrue(r["xbrl"])
        self.assertFalse(items["BHARTIARTL"]["xbrl"])

    def test_kinds_use_heading_and_summary(self):
        kinds = {it["symbol"]: it["kind"] for it in news.parse(rows())}
        self.assertEqual(kinds, {"BHARTIARTL": "results",      # "Financial Result Updates"
                                 "RPOWER": "results",          # board meeting outcome that carries results
                                 "PARKHOSPS": "board meeting",  # board meeting outcome that does not
                                 "GPIL": "results",            # "Integrated Filing- Financial"
                                 "UNIVCABLES": "press release"})
        self.assertEqual(news.kind_of("Resignation of Chief Financial Officer", ""), "people")
        self.assertEqual(news.kind_of("Credit Rating", "ICRA reaffirmed"), "credit rating")
        self.assertEqual(news.kind_of("Updates", "nothing in particular"), "other")

    def test_bad_rows_are_skipped(self):
        self.assertEqual(news.parse([{"symbol": "X"}, {"seq_id": "1", "an_dt": "nonsense", "symbol": "X"}]), [])
        self.assertEqual(len(news.parse({"data": rows()})), 5)
        with self.assertRaises(news.NewsError):
            news.parse("oops")


class Select(unittest.TestCase):
    def test_window_watch_list_and_newest_first(self):
        items = news.parse(rows())
        now = dt.datetime(2026, 8, 8, 9, 0, tzinfo=news.IST)
        got = news.select(items, ["RPOWER", "GPIL", "BHARTIARTL"], {}, now, days=3)
        self.assertEqual([it["symbol"] for it in got], ["GPIL", "RPOWER"])  # 2024 item is outside the window

    def test_seen_items_show_once(self):
        items = news.parse(rows())
        now = dt.datetime(2026, 8, 8, 9, 0, tzinfo=news.IST)
        state = {}
        first = news.select(items, SYMS, state, now, days=31)
        news.remember(first, state, now)
        self.assertEqual(news.select(items, SYMS, state, now, days=31), [])
        self.assertEqual(len(news.select(items, SYMS, state, now, days=31, show_all=True)), 4)
        later = now + dt.timedelta(days=news.KEEP_DAYS + 1)
        news.remember([], state, later)
        self.assertEqual(state["seen"], {})


class Cli(unittest.TestCase):
    def test_offline_report_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = os.path.join(tmp, "state", "news.json")
            code, text, err = run(["--symbols", "rpower", "gpil", "--offline", ANN, "--now", NOW, "--days", "5",
                                   "--state", st])
            self.assertEqual(code, 0, err)
            self.assertIn("! 06 Aug 19:42 IST  RPOWER", text)
            self.assertIn("read the PDF before acting", text)
            self.assertNotIn(chr(0x2014), text)
            code, text, _ = run(["--symbols", "RPOWER", "GPIL", "--offline", ANN, "--now", NOW, "--days", "5",
                                 "--state", st])
            self.assertEqual(code, 1)
            self.assertIn("Nothing new from NSE", text)

    def test_one_date_range_request_to_the_known_endpoint(self):
        urls = []

        def get(url):
            urls.append(url)
            return json.dumps(rows()).encode()
        code, text, _ = run(["--symbols", "GPIL", "--now", NOW, "--days", "2", "--dry-run", "--json"], get=get)
        self.assertEqual(code, 0)
        self.assertEqual(urls, ["https://www.nseindia.com/api/corporate-announcements?index=equities"
                                "&from_date=06-08-2026&to_date=08-08-2026"])
        self.assertEqual(json.loads(text)["items"][0]["kind"], "results")

    def test_symbols_from_the_portfolio(self):
        with tempfile.TemporaryDirectory() as tmp:
            pf = os.path.join(tmp, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": "0", "positions": [{"symbol": "GPIL", "qty": 5}]}, fh)
            code, text, _ = run(["--portfolio", pf, "--offline", ANN, "--now", NOW, "--dry-run", "--json"])
            self.assertEqual(json.loads(text)["watch"], ["GPIL"])

    def test_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            none = os.path.join(tmp, "none.json")
            code, _, err = run(["--portfolio", none, "--targets", none, "--offline", ANN])
            self.assertEqual(code, 2)
            self.assertIn("which stocks", err)
            code, _, err = run(["--symbols", "TCS", "--offline", none])
            self.assertIn("no such file", err)
            code, _, err = run(["--symbols", "TCS", "--days", "90", "--offline", ANN])
            self.assertIn("between 1 and 31", err)


if __name__ == "__main__":
    unittest.main()
