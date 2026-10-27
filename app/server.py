#!/usr/bin/env python3
"""The local app: every page and every button of the project in one place.

  python3 app/server.py            # then open the address it prints
  python3 app/server.py --port 8765

Standard library only. It listens on 127.0.0.1, so nothing outside this
computer can reach it, refuses requests whose Host header is not this
computer (DNS rebinding), and needs a token minted at startup for anything
that runs a task. The only tasks it can run are the fixed commands in
research/jobs.py (update, scan, backtest on dev or validation, save a
candidate strategy), started as subprocesses with checked arguments, one
at a time. It cannot open the holdout and it cannot place an order.
"""

import argparse
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "core"))
import nse_calendar as cal  # noqa: E402

DB = os.path.join(ROOT, "data", "market.db")
STRATEGIES = os.path.join(ROOT, "research", "strategies.json")
RUNS = os.path.join(ROOT, "research", "runs.jsonl")
RESULTS = os.path.join(ROOT, "data", "app", "backtests")
SCAN_DIR = os.path.join(ROOT, "private", "scan")
STATIC = os.path.join(HERE, "static")
JOBS_PY = os.path.join(ROOT, "research", "jobs.py")

SIGNALS = ["momentum_quality", "momentum_12_1", "momentum_6_1", "high_52w", "low_volatility",
           "delivery_accumulation", "momentum_lowvol", "insider_buying", "results_reaction"]
EVENT_SIGNALS = ["insider_buying", "results_reaction"]  # score only stocks with a recent event
REGIMES = ["none", "trend200_cash", "trend200_half", "breadth40_half"]
NAME = re.compile(r"^[A-Za-z0-9_]{1,40}$")
RESULT_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_LOG = 400  # lines kept per job


# ---------------------------------------------------------------- jobs

class Jobs:
    """One task at a time: they all write the same SQLite store."""

    def __init__(self, python=sys.executable):
        self.python = python
        self.lock = threading.Lock()
        self.jobs = {}
        self.running = None

    def start(self, kind, args):
        with self.lock:
            if self.running and self.jobs[self.running]["state"] == "running":
                raise RuntimeError("another task is running: %s" % self.jobs[self.running]["kind"])
            jid = secrets.token_hex(4)
            job = {"id": jid, "kind": kind, "args": args, "state": "running", "log": [],
                   "started": dt.datetime.now().isoformat(timespec="seconds"), "result_id": None, "exit": None}
            self.jobs[jid] = job
            self.running = jid
        proc = subprocess.Popen([self.python, JOBS_PY, kind] + args, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._follow, args=(job, proc), daemon=True).start()
        return job

    def _follow(self, job, proc):
        for line in proc.stdout:
            line = line.rstrip()
            m = re.match(r"^RESULT_ID=(\S+)$", line)
            if m:
                job["result_id"] = m.group(1)
                continue
            job["log"].append(line)
            del job["log"][:-MAX_LOG]
        job["exit"] = proc.wait()
        job["state"] = "done" if job["exit"] == 0 else "failed"
        job["finished"] = dt.datetime.now().isoformat(timespec="seconds")

    def public(self, jid):
        j = self.jobs.get(jid)
        return None if j is None else {k: v for k, v in j.items()}


def build_args(kind, params):
    """Validate a task's parameters against fixed choices; return argv for jobs.py."""
    p = params or {}
    if kind == "update":
        return []
    if kind == "scan":
        args = ["--strategy", _strategy(p.get("strategy", "momentum_quality_top20"))]
        if p.get("capital"):
            cap = float(p["capital"])
            if not 10000 <= cap <= 1e10:
                raise ValueError("paper capital between Rs 10,000 and Rs 1,000 crore")
            args += ["--capital", str(int(cap))]
        return args
    if kind == "backtest":
        period = p.get("period", "dev")
        if period not in ("dev", "dev_2016", "validation"):
            raise ValueError("the app tests on dev or validation only")
        if period == "validation" and p.get("confirm_validation") is not True:
            raise ValueError("a validation run counts as a look: confirm it")
        top = int(p.get("top", 20))
        if not 5 <= top <= 50:
            raise ValueError("top between 5 and 50")
        slip = float(p.get("slippage_bps", 15))
        if not 0 <= slip <= 200:
            raise ValueError("slippage between 0 and 200 bps")
        regime = p.get("regime", "none")
        if regime not in REGIMES:
            raise ValueError("unknown regime")
        args = ["--top", str(top), "--slippage-bps", str(slip), "--regime", regime, "--period", period]
        weights = p.get("composite")
        if weights:
            parts = []
            for name, w in weights.items():
                if name not in SIGNALS:
                    raise ValueError("unknown signal %r" % name)
                w = float(w)
                if not 0 <= w <= 100:
                    raise ValueError("weights between 0 and 100")
                if w:
                    parts.append("%s=%g" % (name, w))
            if not parts:
                raise ValueError("give at least one weight above zero")
            args += ["--composite", ",".join(parts)]
        else:
            if p.get("signal") not in SIGNALS:
                raise ValueError("unknown signal")
            args += ["--signal", p["signal"]]
        note = str(p.get("note", ""))[:200]
        if note:
            args += ["--note", note]
        return args
    if kind == "save-strategy":
        rid, name = str(p.get("result_id", "")), str(p.get("name", ""))
        if not RESULT_ID.match(rid) or not NAME.match(name):
            raise ValueError("bad result id or name")
        return [rid, "--name", name]
    raise ValueError("unknown task %r" % kind)


def _strategy(name):
    if not NAME.match(name) or name not in read_json(STRATEGIES, {}):
        raise ValueError("unknown strategy")
    return name


# ---------------------------------------------------------------- data for the pages

def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return default


def state():
    out = {"now": dt.datetime.now(cal.IST).isoformat(timespec="minutes")}
    try:
        db = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        last = db.execute("SELECT MAX(date) FROM fetched WHERE kind='prices' AND status='ok'").fetchone()[0]
        out["data_ends"] = last
        db.close()
    except sqlite3.Error:
        out["data_ends"] = None
    try:
        exp = cal.latest_published_session(dt.datetime.now(cal.IST)).isoformat()
        out["expected"] = exp
        out["stale"] = out["data_ends"] is None or out["data_ends"] < exp
    except cal.UnknownYear as e:
        out["expected"], out["stale"], out["calendar_error"] = None, None, str(e)
    strategies = read_json(STRATEGIES, {})
    out["strategies"] = [{"name": k, "status": v.get("status"), "description": v.get("description")}
                         for k, v in strategies.items() if not k.startswith("_")]
    out["signals"], out["regimes"], out["event_signals"] = SIGNALS, REGIMES, EVENT_SIGNALS
    out["has_scan"] = os.path.exists(os.path.join(SCAN_DIR, "latest.json"))
    out["has_dashboard"] = os.path.exists(os.path.join(ROOT, "private", "dashboard.html"))
    return out


def backtest_list():
    out = []
    if os.path.isdir(RESULTS):
        for f in sorted(os.listdir(RESULTS), reverse=True)[:200]:
            r = read_json(os.path.join(RESULTS, f))
            if r:
                out.append({"id": r["id"], "at": r["at"], "label": r["label"], "period": r["period"],
                            "top": r["config"]["top"], "regime": r["config"]["regime"], "trials": r["trials"],
                            "cagr": r["metrics"]["cagr"], "sharpe": r["metrics"]["sharpe"],
                            "max_drawdown": r["metrics"]["max_drawdown"], "bench_cagr": r["bench"]["cagr"]})
    return out


def runs_log():
    out = []
    try:
        with open(RUNS, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return out[-300:][::-1]


def index_series(name, start):
    """An NSE index's closes from `start`, joined across its 2015 rename."""
    aliases = {"Nifty 500": ("S&P CNX 500", "CNX 500", "Nifty 500"), "Nifty 50": ("S&P CNX Nifty", "CNX Nifty", "Nifty 50")}
    names = aliases.get(name)
    if not names or not DATE.match(start):
        return []
    db = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows = db.execute("SELECT date, close FROM index_prices WHERE name IN (%s) AND date >= ? ORDER BY date"
                      % ",".join("?" * len(names)), names + (start,)).fetchall()
    db.close()
    seen, out = set(), []
    for d, c in rows:
        if d not in seen:
            seen.add(d)
            out.append([d, c])
    return out


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    jobs = None
    token = None
    port = None
    server_version = "IndiaTrader"

    def log_message(self, fmt, *args):  # quiet: the terminal shows the address and errors only
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else (json.dumps(body, default=str) if not isinstance(body, str)
                                                     else body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as fh:
                self._send(200, fh.read(), ctype)
        except FileNotFoundError:
            self._send(404, {"error": "not found"})

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "this app only answers on 127.0.0.1"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path
        if path == "/":
            with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
                page = fh.read().replace("__TOKEN__", self.token)
            return self._send(200, page, "text/html; charset=utf-8")
        if path in ("/app.js", "/app.css"):
            return self._file(os.path.join(STATIC, path[1:]),
                              "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
        if path == "/api/state":
            return self._send(200, state())
        if path == "/api/scan":
            return self._send(200, read_json(os.path.join(SCAN_DIR, "latest.json"), {}))
        if path == "/api/backtests":
            return self._send(200, backtest_list())
        m = re.match(r"^/api/backtests/([0-9a-f\-]+)$", path)
        if m and RESULT_ID.match(m.group(1)):
            r = read_json(os.path.join(RESULTS, m.group(1) + ".json"))
            return self._send(200 if r else 404, r or {"error": "not found"})
        if path == "/api/runs":
            return self._send(200, runs_log())
        if path == "/api/index":
            return self._send(200, index_series((q.get("name") or [""])[0], (q.get("from") or [""])[0]))
        m = re.match(r"^/api/jobs/([0-9a-f]{8})$", path)
        if m:
            j = self.jobs.public(m.group(1))
            return self._send(200 if j else 404, j or {"error": "no such job"})
        m = re.match(r"^/files/scan/(\d{4}-\d{2}-\d{2})(\.html|-orders\.csv)$", path)
        if m:
            ctype = "text/html; charset=utf-8" if m.group(2) == ".html" else "text/csv; charset=utf-8"
            return self._file(os.path.join(SCAN_DIR, m.group(1) + m.group(2)), ctype)
        if path == "/files/dashboard.html":
            return self._file(os.path.join(ROOT, "private", "dashboard.html"), "text/html; charset=utf-8")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "this app only answers on 127.0.0.1"})
        if not secrets.compare_digest(self.headers.get("X-App-Token") or "", self.token):
            return self._send(403, {"error": "missing or wrong token"})
        if urlparse(self.path).path != "/api/jobs":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
            args = build_args(body.get("kind"), body.get("params"))
            job = self.jobs.start(body["kind"], args)
        except (ValueError, TypeError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        except RuntimeError as e:
            return self._send(409, {"error": str(e)})
        return self._send(202, self.jobs.public(job["id"]))


def serve(port=8765):
    Handler.jobs, Handler.token, Handler.port = Jobs(), secrets.token_urlsafe(24), port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("India Trader is running at http://127.0.0.1:%d  (Ctrl+C to stop)" % port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="The local app.")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    serve(a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
