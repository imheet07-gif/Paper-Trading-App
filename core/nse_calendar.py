#!/usr/bin/env python3
"""NSE trading calendar and clock, in IST.

India has no daylight saving, so IST is always UTC+05:30. The cash market
(capital market segment) runs:

  09:00-09:08  pre-open order entry (09:08-09:15 order matching, no new orders)
  09:15-15:30  normal market
  15:40-16:00  closing session (trades at the closing price only)

Holidays are published once a year by NSE, usually in December for the
following year. A year with no list is an error, never a guess: refresh
nse_holidays.json with `update-holidays` before trusting the clock for it.

Special sessions (Muhurat trading on Diwali, the odd Saturday live session)
are announced by separate circulars. Add them to SPECIAL_SESSIONS when NSE
publishes the timings.

Settlement is T+1 for the cash market. settlement_date() uses trading
holidays; clearing holidays occasionally differ, so check the NSE clearing
circular for a date that matters.

  python3 core/nse_calendar.py clock
  python3 core/nse_calendar.py clock --now 2026-10-20T10:00:00+05:30
  python3 core/nse_calendar.py holidays 2026
  python3 core/nse_calendar.py settle 2026-10-19
  python3 core/nse_calendar.py update-holidays holiday-master.json
"""

import argparse
import datetime as dt
import json
import os
import sys

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")

PRE_OPEN = (dt.time(9, 0), dt.time(9, 15))
PRE_OPEN_ENTRY_END = dt.time(9, 8)
NORMAL = (dt.time(9, 15), dt.time(15, 30))
CLOSING = (dt.time(15, 40), dt.time(16, 0))

# Trading holidays live in nse_holidays.json beside this file, written by
# `update-holidays` from NSE's own holiday master
# (https://www.nseindia.com/api/holiday-master?type=trading), capital market
# segment. Weekend entries are kept as published; they change nothing.
HERE = os.path.dirname(os.path.abspath(__file__))
HOLIDAYS_FILE = os.path.join(HERE, "nse_holidays.json")


def load_holidays(path=HOLIDAYS_FILE):
    """{year: {"YYYY-MM-DD": name}}. No file means no year is known, so every date errors."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}
    return {int(y): days for y, days in doc["years"].items()}


def parse_holiday_master(text, segment="CM"):
    """NSE holiday-master JSON -> {year: {"YYYY-MM-DD": description}} for one segment."""
    rows = json.loads(text).get(segment)
    if not rows:
        raise ValueError("no %s segment in the holiday file" % segment)
    out = {}
    for r in rows:
        d = dt.datetime.strptime(r["tradingDate"], "%d-%b-%Y").date()
        out.setdefault(d.year, {})[d.isoformat()] = r["description"].strip()
    return out


def update_holidays(text, path=HOLIDAYS_FILE, fetched=None, segment="CM"):
    """Replace the years present in an NSE holiday file; keep the other years."""
    new = parse_holiday_master(text, segment)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        doc = {"years": {}}
    doc["_source"] = ("NSE holiday master, %s segment: https://www.nseindia.com/api/holiday-master?type=trading"
                      % segment)
    doc["fetched"] = fetched or dt.date.today().isoformat()
    for y, days in new.items():
        doc["years"][str(y)] = dict(sorted(days.items()))
    doc["years"] = dict(sorted(doc["years"].items()))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return sorted(new)


HOLIDAYS = load_holidays()

# {"YYYY-MM-DD": {"name": ..., "start": "HH:MM", "end": "HH:MM"}}, IST.
# Muhurat 2026 timings are not in yet: add them from the NSE circular.
SPECIAL_SESSIONS = {}


class UnknownYear(ValueError):
    pass


def holidays(year):
    if year not in HOLIDAYS:
        raise UnknownYear("no NSE holiday list for %d: run update-holidays with NSE's file" % year)
    return {dt.date.fromisoformat(k): v for k, v in HOLIDAYS[year].items()}


def is_trading_day(d):
    """A normal session day: a weekday that is not an NSE holiday."""
    if d.weekday() >= 5:
        return False
    return d not in holidays(d.year)


def special_session(d):
    s = SPECIAL_SESSIONS.get(d.isoformat())
    if not s:
        return None
    return dict(s, start=dt.time.fromisoformat(s["start"]), end=dt.time.fromisoformat(s["end"]))


def to_ist(when):
    if when.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return when.astimezone(IST)


def phase(when):
    """What the cash market is doing at this moment.

    One of: pre_open_entry, pre_open_matching, open, closing_session,
    special_session, closed.
    """
    t = to_ist(when)
    d, clock = t.date(), t.time()
    sp = special_session(d)
    if sp and sp["start"] <= clock < sp["end"]:
        return "special_session"
    if not is_trading_day(d):
        return "closed"
    if PRE_OPEN[0] <= clock < PRE_OPEN_ENTRY_END:
        return "pre_open_entry"
    if PRE_OPEN_ENTRY_END <= clock < PRE_OPEN[1]:
        return "pre_open_matching"
    if NORMAL[0] <= clock < NORMAL[1]:
        return "open"
    if CLOSING[0] <= clock < CLOSING[1]:
        return "closing_session"
    return "closed"


def next_trading_day(d):
    d += dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


def next_open(when):
    """The next normal-market open (09:15 IST) at or after `when`."""
    t = to_ist(when)
    d = t.date()
    if not (is_trading_day(d) and t.time() < NORMAL[0]):
        d = next_trading_day(d)
    return dt.datetime.combine(d, NORMAL[0], IST)


PUBLISHED_BY = dt.time(19, 0)  # NSE end-of-day files are normally up by this time, IST


def latest_published_session(when):
    """The latest session whose end-of-day files should be published by `when`:
    today after 7 pm IST on a trading day, otherwise the previous session."""
    t = to_ist(when)
    d = t.date()
    if not (is_trading_day(d) and t.time() >= PUBLISHED_BY):
        d -= dt.timedelta(days=1)
        while not is_trading_day(d):
            d -= dt.timedelta(days=1)
    return d


def settlement_date(trade_date):
    """T+1: the next trading day after the trade date."""
    return next_trading_day(trade_date)


def status(when):
    t = to_ist(when)
    p = phase(t)
    out = {"now_ist": t.isoformat(timespec="minutes"), "phase": p,
           "trading_day": is_trading_day(t.date())}
    name = HOLIDAYS.get(t.year, {}).get(t.date().isoformat())
    if name:
        out["holiday"] = name
    if p != "open":
        out["next_open_ist"] = next_open(t).isoformat(timespec="minutes")
    else:
        out["closes_ist"] = dt.datetime.combine(t.date(), NORMAL[1], IST).isoformat(timespec="minutes")
    return out


def _now(text):
    if not text:
        return dt.datetime.now(dt.timezone.utc)
    v = dt.datetime.fromisoformat(text)
    return v if v.tzinfo else v.replace(tzinfo=IST)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE calendar and clock (IST).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clock", help="is the market open now")
    c.add_argument("--now", help="ISO time; no offset means IST")
    h = sub.add_parser("holidays", help="list a year's trading holidays")
    h.add_argument("year", type=int)
    s = sub.add_parser("settle", help="T+1 settlement date for a trade date")
    s.add_argument("date")
    u = sub.add_parser("update-holidays", help="load NSE's holiday-master JSON into nse_holidays.json")
    u.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "clock":
            print(json.dumps(status(_now(a.now)), indent=2))
        elif a.cmd == "holidays":
            for d, name in sorted(holidays(a.year).items()):
                print(d.isoformat(), d.strftime("%a"), name)
        elif a.cmd == "settle":
            print(settlement_date(dt.date.fromisoformat(a.date)).isoformat())
        elif a.cmd == "update-holidays":
            with open(a.file, encoding="utf-8") as fh:
                years = update_holidays(fh.read())
            print("updated", ", ".join(map(str, years)), "in", HOLIDAYS_FILE)
    except UnknownYear as e:
        print("error:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
