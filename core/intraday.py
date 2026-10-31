#!/usr/bin/env python3
"""Intraday candles for every NSE stock, from Kite Connect, into data/intraday.db. Standard library only.

Read-only by construction: the only Kite addresses this module calls are
the login session and the historical candles (HOSTS and PATHS below; a test
checks it). Nothing here can place, modify or cancel an order.

Setup on the owner's machine, once:

  1. A Kite Connect app with historical data, redirect URL
     http://127.0.0.1:8765/kite (the local app must not be running during login).
  2. Its key and secret in the git-ignored .env file at the repository root:
         KITE_API_KEY=...
         KITE_API_SECRET=...
     Never on the command line, never in the repo, never printed.
  3. data/instruments.json built with Kite's list (core/instruments.py build
     --nse ... --kite ...): it maps each NSE ISIN to its Kite token.

Every day (Kite's session ends early each morning):

    python3 core/intraday.py login                 # opens Kite's login; the token is kept for the day
    python3 core/intraday.py fetch --start 2017-01-01      # history, resumable, every stock in the registry
    python3 core/intraday.py today                 # after the close: the day's bars for every stock
    python3 core/intraday.py status

Candles are as Kite serves them: unadjusted prices for the session (09:15 to
15:30 IST), stored as integer paise, time in UTC seconds. Kite only lists
stocks listed today: research on these bars has survivorship bias, and says
so. Interval limits per request (INTERVAL_DAYS) are Kite's documented
maxima as understood here; a refusal halves the window and retries.
"""

import argparse
import datetime as dt
import hashlib
import http.server
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bhavcopy  # noqa: E402
import instruments as instruments_mod  # noqa: E402

ROOT = bhavcopy.ROOT
DB = os.path.join(ROOT, "data", "intraday.db")
ENV = os.path.join(ROOT, ".env")
SESSION = os.path.join(ROOT, "private", "kite_session.json")
API = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login?v=3&api_key=%s"
HOSTS = ("api.kite.trade", "kite.zerodha.com")
PATHS = ("/session/token", "/instruments/historical/", "/connect/login")  # the only paths ever called
REDIRECT_PORT = 8765
INTERVAL_DAYS = {"minute": 60, "3minute": 100, "5minute": 100, "10minute": 100, "15minute": 200,
                 "30minute": 200, "60minute": 400, "day": 2000}
PAUSE = 0.35  # Kite allows about 3 historical requests a second
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  token INTEGER NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,
  open INTEGER, high INTEGER, low INTEGER, close INTEGER, volume INTEGER,
  PRIMARY KEY (token, interval, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS instruments (
  token INTEGER PRIMARY KEY, isin TEXT NOT NULL, symbol TEXT, series TEXT);
CREATE TABLE IF NOT EXISTS fetched (
  token INTEGER NOT NULL, interval TEXT NOT NULL, day_from TEXT NOT NULL, day_to TEXT NOT NULL, bars INTEGER,
  PRIMARY KEY (token, interval, day_from));
"""


class KiteError(Exception):
    pass


# ---------------------------------------------------------------- credentials

def read_env(path=ENV):
    """KEY=VALUE lines from the git-ignored .env file (no shell expansion); the environment wins."""
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("KITE_API_KEY", "KITE_API_SECRET"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def credentials(env_path=ENV):
    env = read_env(env_path)
    key, secret = env.get("KITE_API_KEY"), env.get("KITE_API_SECRET")
    if not key or not secret:
        raise KiteError("KITE_API_KEY and KITE_API_SECRET are not set: put them in %s (git-ignored)" % env_path)
    return key, secret


def checksum(api_key, request_token, api_secret):
    """Kite's session checksum: SHA-256 of key + request token + secret."""
    return hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()


def save_session(token, path=SESSION, today=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"access_token": token, "day": (today or dt.datetime.now(IST).date()).isoformat()}, fh)


def load_session(path=SESSION, now=None):
    """Today's access token, or None: Kite ends sessions early each morning, so yesterday's is useless."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        s = json.load(fh)
    now = now or dt.datetime.now(IST)
    day = dt.date.fromisoformat(s.get("day", "1970-01-01"))
    # valid from login until 06:00 IST the next morning
    expires = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(6, 0), IST)
    return s.get("access_token") if now < expires else None


# ---------------------------------------------------------------- http

def _check_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname not in HOSTS or not any(u.path.startswith(p) for p in PATHS):
        raise KiteError("refusing to call %s: only Kite's login and historical data are allowed" % url)


def http(url, headers=None, data=None, timeout=60):
    _check_url(url)
    req = urllib.request.Request(url, headers=dict(headers or {}, **{"X-Kite-Version": "3"}),
                                 data=urllib.parse.urlencode(data).encode() if data else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        raise KiteError("%s %s" % (e.code, body.get("message") or e.reason)) from None


def session_token(api_key, api_secret, request_token, call=http):
    res = call(API + "/session/token", data={"api_key": api_key, "request_token": request_token,
                                             "checksum": checksum(api_key, request_token, api_secret)})
    token = (res.get("data") or {}).get("access_token")
    if not token:
        raise KiteError("Kite gave no access token: %s" % res.get("message", "unknown error"))
    return token


def wait_for_request_token(port=REDIRECT_PORT, timeout=300):
    """A one-shot local server on 127.0.0.1 that catches Kite's redirect and returns its request_token."""
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("request_token") and (q.get("status") or ["success"])[0] == "success":
                got["token"] = q["request_token"][0]
                msg = b"Logged in to Kite for today. You can close this tab."
            else:
                msg = b"Kite login did not succeed. Run the login again."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg)

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    srv.timeout = 1
    end = time.time() + timeout
    while "token" not in got and time.time() < end:
        srv.handle_request()
    srv.server_close()
    if "token" not in got:
        raise KiteError("no login within %d seconds" % timeout)
    return got["token"]


# ---------------------------------------------------------------- candles

def parse_candles(res):
    """Kite's historical answer -> [(ts UTC seconds, open, high, low, close in paise, volume)]."""
    out = []
    for c in (res.get("data") or {}).get("candles") or []:
        t = dt.datetime.fromisoformat(c[0].replace("+0530", "+05:30"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=IST)
        o, h, l, cl = (int(round(float(x) * 100)) for x in c[1:5])
        out.append((int(t.timestamp()), o, h, l, cl, int(c[5] or 0)))
    return out


def windows(start, end, interval):
    """[(from, to)] date windows no longer than Kite allows for the interval."""
    days = INTERVAL_DAYS[interval]
    out, a = [], start
    while a <= end:
        b = min(a + dt.timedelta(days=days - 1), end)
        out.append((a, b))
        a = b + dt.timedelta(days=1)
    return out


def candles(api_key, access, token, interval, a, b, call=http):
    url = "%s/instruments/historical/%d/%s?%s" % (API, token, interval, urllib.parse.urlencode(
        {"from": "%s 09:00:00" % a.isoformat(), "to": "%s 15:59:59" % b.isoformat()}))
    return parse_candles(call(url, headers={"Authorization": "token %s:%s" % (api_key, access)}))


def connect(path=DB):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA busy_timeout = 60000")
    db.executescript(SCHEMA)
    return db


def universe(registry):
    """[(kite token, isin, symbol, series)] for every registry stock Kite lists."""
    out = []
    for isin, s in (registry or {}).get("instruments", {}).items():
        if s.get("kite_token"):
            out.append((int(s["kite_token"]), isin, s.get("nse_symbol"), s.get("series")))
    return sorted(out, key=lambda r: r[2] or "")


def store(db, token, interval, bars):
    db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
                   [(token, interval) + b for b in bars])


def fetch(db, api_key, access, stocks, start, end, interval="5minute", call=http, pause=PAUSE, log=print):
    """Every window not yet fetched for every stock, oldest first, resumable. The last window
    of a run that reaches today is fetched again next time (the day may not have been complete)."""
    if interval not in INTERVAL_DAYS:
        raise KiteError("interval must be one of %s" % ", ".join(INTERVAL_DAYS))
    db.executemany("INSERT OR REPLACE INTO instruments VALUES (?,?,?,?)", stocks)
    today = dt.datetime.now(IST).date()
    total, done = 0, 0
    for n, (token, isin, symbol, _) in enumerate(stocks, 1):
        have = {r[0] for r in db.execute("SELECT day_from FROM fetched WHERE token = ? AND interval = ? AND day_to < ?",
                                         (token, interval, today.isoformat()))}
        got = 0
        todo = [(a, b) for a, b in windows(start, end, interval) if a.isoformat() not in have]
        while todo:
            a, b = todo.pop(0)
            try:
                bars = candles(api_key, access, token, interval, a, b, call)
            except KiteError as e:
                msg = str(e).lower()
                if ("interval" in msg or "range" in msg) and b > a:
                    mid = a + (b - a) // 2
                    todo[:0] = [(a, mid), (mid + dt.timedelta(days=1), b)]  # too long a window: halve it
                    continue
                if msg.startswith("403"):
                    raise KiteError("Kite refused the session (%s): run `intraday.py login` again" % e)
                if msg.startswith("429"):
                    time.sleep(2)
                    todo.insert(0, (a, b))
                    continue
                if log:
                    log("%s %s to %s: %s; tried again next run" % (symbol, a, b, e))
                continue
            store(db, token, interval, bars)
            db.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)",
                       (token, interval, a.isoformat(), b.isoformat(), len(bars)))
            db.commit()
            got += len(bars)
            if pause:
                time.sleep(pause)
        total += got
        done += 1
        if log and got:
            log("%s (%d of %d): %d bars" % (symbol, n, len(stocks), got))
    return total, done


def status(db):
    return {"instruments": db.execute("SELECT COUNT(*) FROM instruments").fetchone()[0],
            "bars": db.execute("SELECT COUNT(*) FROM bars").fetchone()[0],
            "first": db.execute("SELECT MIN(ts) FROM bars").fetchone()[0],
            "last": db.execute("SELECT MAX(ts) FROM bars").fetchone()[0],
            "stocks_with_bars": db.execute("SELECT COUNT(DISTINCT token) FROM bars").fetchone()[0]}


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description="Intraday candles from Kite Connect (read-only).")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--registry", default=instruments_mod.DEFAULT_OUT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="log in to Kite for today (opens the browser)")
    f = sub.add_parser("fetch", help="history for every stock in the registry, resumable")
    f.add_argument("--start", required=True, help="YYYY-MM-DD")
    f.add_argument("--end", help="YYYY-MM-DD (default today)")
    f.add_argument("--interval", default="5minute", choices=sorted(INTERVAL_DAYS))
    f.add_argument("--symbols", nargs="*", help="only these NSE symbols")
    t = sub.add_parser("today", help="today's bars for every stock (after the close)")
    t.add_argument("--interval", default="5minute", choices=sorted(INTERVAL_DAYS))
    sub.add_parser("status")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "login":
            key, secret = credentials()
            url = LOGIN % key
            print("Opening Kite's login page. If it does not open, visit:\n  %s" % url)
            webbrowser.open(url)
            request_token = wait_for_request_token()
            save_session(session_token(key, secret, request_token))
            print("Logged in for today. The session is kept in private/ (git-ignored) and ends early tomorrow.")
            return 0
        db = connect(a.db)
        if a.cmd == "status":
            s = status(db)
            fmt = lambda ts: dt.datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d %H:%M") if ts else "-"
            print("%d instruments, %d with bars, %d bars, from %s to %s IST" % (
                s["instruments"], s["stocks_with_bars"], s["bars"], fmt(s["first"]), fmt(s["last"])))
            return 0
        key, _ = credentials()
        access = load_session()
        if not access:
            raise KiteError("no Kite session for today: run `python3 core/intraday.py login` first")
        registry = instruments_mod.load_registry(a.registry)
        stocks = universe(registry)
        if not stocks:
            raise KiteError("no Kite tokens in %s: build it with --kite (core/instruments.py)" % a.registry)
        if a.cmd == "fetch" and a.symbols:
            want = {x.upper() for x in a.symbols}
            stocks = [s for s in stocks if (s[2] or "").upper() in want]
        today = dt.datetime.now(IST).date()
        if a.cmd == "today":
            start = end = today
        else:
            start = dt.date.fromisoformat(a.start)
            end = dt.date.fromisoformat(a.end) if a.end else today
        n, k = fetch(db, key, access, stocks, start, end, a.interval)
        print("%d bars stored for %d stocks (%s)" % (n, k, a.interval))
        db.close()
        return 0
    except KiteError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
