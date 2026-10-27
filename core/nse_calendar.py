#!/usr/bin/env python3
"""NSE trading calendar and clock, in IST.

India has no daylight saving, so IST is always UTC+05:30. The cash market
(capital market segment) runs:

  09:00-09:08  pre-open order entry (09:08-09:15 order matching, no new orders)
  09:15-15:30  normal market
  15:40-16:00  closing session (trades at the closing price only)

Holidays are published once a year by NSE circular, usually in December for
the following year. A year with no list here is an error, never a guess:
add the circular's dates to HOLIDAYS before trusting the clock for that year.

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
"""

import argparse
import datetime as dt
import json
import sys

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")

PRE_OPEN = (dt.time(9, 0), dt.time(9, 15))
PRE_OPEN_ENTRY_END = dt.time(9, 8)
NORMAL = (dt.time(9, 15), dt.time(15, 30))
CLOSING = (dt.time(15, 40), dt.time(16, 0))

# Weekday trading holidays, equity and equity derivative segments.
# 2026: NSE circular NSE/FAOP/71777 (Dec 12 2025), cross-checked against
# broker-published lists on 2026-10-27. Republish from the equity segment
# circular if any date is ever disputed.
HOLIDAYS = {
    2026: {
        "2026-01-26": "Republic Day",
        "2026-03-03": "Holi",
        "2026-03-26": "Shri Ram Navami",
        "2026-03-31": "Shri Mahavir Jayanti",
        "2026-04-03": "Good Friday",
        "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
        "2026-05-01": "Maharashtra Day",
        "2026-05-28": "Bakri Id",
        "2026-06-26": "Muharram",
        "2026-09-14": "Ganesh Chaturthi",
        "2026-10-02": "Mahatma Gandhi Jayanti",
        "2026-10-20": "Dussehra",
        "2026-11-10": "Diwali Balipratipada",
        "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
        "2026-12-25": "Christmas",
    },
}

# {"YYYY-MM-DD": {"name": ..., "start": "HH:MM", "end": "HH:MM"}}, IST.
# Muhurat 2026 timings are not in yet: add them from the NSE circular.
SPECIAL_SESSIONS = {}


class UnknownYear(ValueError):
    pass


def holidays(year):
    if year not in HOLIDAYS:
        raise UnknownYear("no NSE holiday list for %d: add it from the NSE circular" % year)
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
    a = ap.parse_args(argv)
    try:
        if a.cmd == "clock":
            print(json.dumps(status(_now(a.now)), indent=2))
        elif a.cmd == "holidays":
            for d, name in sorted(holidays(a.year).items()):
                print(d.isoformat(), d.strftime("%a"), name)
        elif a.cmd == "settle":
            print(settlement_date(dt.date.fromisoformat(a.date)).isoformat())
    except UnknownYear as e:
        print("error:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
