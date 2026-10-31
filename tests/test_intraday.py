import datetime as dt
import io
import json
import os
import re
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import ROOT, load

iday = load("core", "intraday")

# made up, in the layout Kite documents for historical candles; verify on the first real call
ANSWER = {"status": "success", "data": {"candles": [
    ["2026-10-30T09:15:00+0530", 100.5, 101.0, 100.0, 100.75, 1200],
    ["2026-10-30T09:20:00+0530", 100.75, 102.25, 100.5, 102.0, 3400]]}}
REGISTRY = {"instruments": {
    "INE001A01011": {"nse_symbol": "ALPHA", "series": "EQ", "kite_token": 111},
    "INE002B01019": {"nse_symbol": "BETA", "series": "EQ", "kite_token": 222},
    "INE003C01017": {"nse_symbol": "NOKITE", "series": "EQ"}}}


class Intraday(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = iday.connect(os.path.join(self.tmp.name, "i.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_candles_are_paise_and_utc(self):
        bars = iday.parse_candles(ANSWER)
        self.assertEqual(bars[0][1:], (10050, 10100, 10000, 10075, 1200))
        self.assertEqual(dt.datetime.fromtimestamp(bars[0][0], dt.timezone.utc).strftime("%H:%M"), "03:45")

    def test_windows_respect_the_interval_limit(self):
        w = iday.windows(dt.date(2017, 1, 1), dt.date(2017, 12, 31), "5minute")
        self.assertTrue(all((b - a).days < iday.INTERVAL_DAYS["5minute"] for a, b in w))
        self.assertEqual((w[0][0], w[-1][1]), (dt.date(2017, 1, 1), dt.date(2017, 12, 31)))
        self.assertEqual(len(iday.windows(dt.date(2017, 1, 1), dt.date(2017, 3, 31), "minute")), 2)

    def test_only_login_and_historical_data_can_be_called(self):
        for bad in ("https://api.kite.trade/orders/regular", "https://api.kite.trade/orders",
                    "https://api.kite.trade/portfolio/holdings", "https://evil.example/instruments/historical/1/day",
                    "http://api.kite.trade/instruments/historical/1/day", "https://api.kite.trade/gtt/triggers"):
            with self.assertRaises(iday.KiteError, msg=bad):
                iday.http(bad)
        with open(os.path.join(ROOT, "core", "intraday.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotRegex(src, r"/orders|/gtt|place_order|modify_order|cancel_order")

    def test_checksum_and_session_token(self):
        self.assertEqual(iday.checksum("k", "r", "s"), __import__("hashlib").sha256(b"krs").hexdigest())
        sent = {}

        def call(url, data=None, headers=None):
            sent.update(url=url, data=data)
            return {"status": "success", "data": {"access_token": "tok"}}
        self.assertEqual(iday.session_token("k", "s", "r", call), "tok")
        self.assertTrue(sent["url"].endswith("/session/token"))
        self.assertNotIn("s", sent["data"].values())  # the secret is never sent, only its checksum
        with self.assertRaises(iday.KiteError):
            iday.session_token("k", "s", "r", lambda *a, **k: {"status": "error", "message": "bad"})

    def test_credentials_from_dotenv_never_printed(self):
        env = os.path.join(self.tmp.name, ".env")
        with open(env, "w") as fh:
            fh.write("# kite\nKITE_API_KEY=key123\nKITE_API_SECRET='sec456'\n")
        old = {k: os.environ.pop(k, None) for k in ("KITE_API_KEY", "KITE_API_SECRET")}
        try:
            self.assertEqual(iday.credentials(env), ("key123", "sec456"))
            with self.assertRaises(iday.KiteError) as cm:
                iday.credentials(os.path.join(self.tmp.name, "none"))
            self.assertNotIn("sec456", str(cm.exception))
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_session_is_private_and_ends_next_morning(self):
        path = os.path.join(self.tmp.name, "private", "s.json")
        iday.save_session("tok", path, today=dt.date(2026, 10, 30))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        at = lambda d, h: dt.datetime(2026, 10, d, h, 0, tzinfo=iday.IST)
        self.assertEqual(iday.load_session(path, now=at(30, 20)), "tok")
        self.assertEqual(iday.load_session(path, now=at(31, 5)), "tok")
        self.assertIsNone(iday.load_session(path, now=at(31, 7)))

    def test_universe_is_every_registry_stock_kite_lists(self):
        self.assertEqual([r[2] for r in iday.universe(REGISTRY)], ["ALPHA", "BETA"])

    def test_fetch_is_resumable_and_halves_a_refused_window(self):
        calls = []

        def call(url, headers=None, data=None):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "token key:acc")
            m = re.search(r"from=(\d{4}-\d{2}-\d{2}).+to=(\d{4}-\d{2}-\d{2})", url)
            a, b = (dt.date.fromisoformat(x) for x in m.groups())
            if (b - a).days > 40:
                raise iday.KiteError("400 interval exceeds max limit")
            return ANSWER
        stocks = iday.universe(REGISTRY)
        n, k = iday.fetch(self.db, "key", "acc", stocks, dt.date(2020, 1, 1), dt.date(2020, 3, 31), call=call,
                          pause=0, log=None)
        self.assertEqual(k, 2)
        self.assertGreater(len(calls), 2)  # windows were halved until accepted
        self.assertEqual(self.db.execute("SELECT COUNT(DISTINCT token) FROM bars").fetchone()[0], 2)
        first = len(calls)
        iday.fetch(self.db, "key", "acc", stocks, dt.date(2020, 1, 1), dt.date(2020, 3, 31), call=call,
                   pause=0, log=None)
        self.assertEqual(len(calls), first)  # everything already fetched: nothing asked again
        self.assertEqual(self.db.execute("SELECT isin FROM instruments WHERE token = 111").fetchone()[0], "INE001A01011")

    def test_an_expired_session_stops_the_run(self):
        def call(url, headers=None, data=None):
            raise iday.KiteError("403 Incorrect `api_key` or `access_token`.")
        with self.assertRaisesRegex(iday.KiteError, "intraday.py login. again"):
            iday.fetch(self.db, "key", "acc", iday.universe(REGISTRY), dt.date(2020, 1, 1), dt.date(2020, 1, 5),
                       call=call, pause=0, log=None)

    def test_cli_status_and_missing_login(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = iday.main(["--db", os.path.join(self.tmp.name, "i.db"), "status"])
        self.assertEqual(code, 0)
        self.assertIn("0 bars", out.getvalue())


if __name__ == "__main__":
    unittest.main()
