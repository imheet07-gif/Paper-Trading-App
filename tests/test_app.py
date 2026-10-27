import http.client
import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

from _load import ROOT, load

server = load("app", "server")


class Arguments(unittest.TestCase):
    def test_backtest_arguments_are_checked(self):
        a = server.build_args("backtest", {"signal": "momentum_quality", "top": 20, "period": "dev"})
        self.assertEqual(a[-2:], ["--signal", "momentum_quality"])
        c = server.build_args("backtest", {"composite": {"momentum_quality": 2, "low_volatility": 1, "high_52w": 0}})
        self.assertIn("momentum_quality=2,low_volatility=1", c)
        bad = [{"signal": "x; rm -rf /"}, {"signal": "momentum_quality", "period": "holdout"},
               {"signal": "momentum_quality", "period": "validation"}, {"signal": "momentum_quality", "top": 500},
               {"signal": "momentum_quality", "regime": "magic"}, {"composite": {"momentum_quality": 0}},
               {"composite": {"nope": 1}}, {"signal": "momentum_quality", "slippage_bps": -1}]
        for p in bad:
            with self.assertRaises(ValueError, msg=p):
                server.build_args("backtest", p)
        v = server.build_args("backtest", {"signal": "momentum_quality", "period": "validation", "confirm_validation": True})
        self.assertIn("validation", v)

    def test_only_known_tasks(self):
        for kind in ("place_order", "shell", "", None):
            with self.assertRaises(ValueError):
                server.build_args(kind, {})
        self.assertEqual(server.build_args("update", {}), [])
        with self.assertRaises(ValueError):
            server.build_args("scan", {"strategy": "../../etc/passwd"})
        with self.assertRaises(ValueError):
            server.build_args("save-strategy", {"result_id": "../x", "name": "a"})


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class FakeJobs:
            started = []

            def start(self, kind, args):
                FakeJobs.started.append((kind, args))
                return {"id": "deadbeef"}

            def public(self, jid):
                return {"id": jid, "state": "done", "log": []} if jid == "deadbeef" else None
        cls.Fake = FakeJobs
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        server.Handler.port = cls.httpd.server_address[1]
        server.Handler.token = "t0k3n"
        server.Handler.jobs = FakeJobs()
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", server.Handler.port, timeout=5)
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_page_carries_the_token_and_state_reads(self):
        status, page = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b't0k3n', page)
        status, st = self.req("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("signals", json.loads(st))

    def test_post_needs_the_token(self):
        status, _ = self.req("POST", "/api/jobs", {"kind": "update"})
        self.assertEqual(status, 403)
        status, _ = self.req("POST", "/api/jobs", {"kind": "update"}, {"X-App-Token": "t0k3n"})
        self.assertEqual(status, 202)
        self.assertEqual(self.Fake.started[-1], ("update", []))

    def test_foreign_host_is_refused(self):
        status, _ = self.req("GET", "/api/state", headers={"Host": "attacker.example:%d" % server.Handler.port})
        self.assertEqual(status, 403)
        status, _ = self.req("POST", "/api/jobs", {"kind": "update"},
                             {"Host": "attacker.example", "X-App-Token": "t0k3n"})
        self.assertEqual(status, 403)

    def test_bad_task_is_a_400_and_paths_cannot_escape(self):
        status, _ = self.req("POST", "/api/jobs", {"kind": "backtest", "params": {"signal": "evil"}}, {"X-App-Token": "t0k3n"})
        self.assertEqual(status, 400)
        for path in ("/files/scan/../../.env", "/api/backtests/..%2f..%2fx", "/files/scan/2026-10-26.json"):
            status, _ = self.req("GET", path)
            self.assertEqual(status, 404, path)

    def test_page_loads_nothing_from_outside(self):
        with open(os.path.join(ROOT, "app", "static", "index.html"), encoding="utf-8") as fh:
            page = fh.read()
        with open(os.path.join(ROOT, "app", "static", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertNotRegex(page + js, r"https?://(?!127\.0\.0\.1)")


if __name__ == "__main__":
    unittest.main()
