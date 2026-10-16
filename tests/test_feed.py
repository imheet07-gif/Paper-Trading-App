from datetime import datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from papertrader.feed import IST, PolledSource, market_open
from papertrader.models import Quote

T = datetime(2026, 10, 19, 11, 0, tzinfo=IST)  # a Monday, mid-session


def quote(sym="NIFTY", px="24000"):
    return Quote(sym, T, D(px), D(px), D(px))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(fetch, clock=None, **kw):
    return PolledSource(fetch, clock=clock or Clock(), is_open=lambda now: True, **kw)


def test_market_hours_by_clock():
    assert market_open(T)
    assert not market_open(datetime(2026, 10, 19, 9, 14, tzinfo=IST))
    assert not market_open(datetime(2026, 10, 19, 15, 31, tzinfo=IST))
    assert not market_open(datetime(2026, 10, 17, 11, 0, tzinfo=IST))  # Saturday
    assert market_open(T.astimezone(ZoneInfo("UTC")))  # tz-aware inputs are converted


def test_live_then_stale_then_recovered():
    clock = Clock()
    src = make(lambda: {"NIFTY": quote()}, clock, stale_after=10)
    assert src.status(T).state == "starting"
    src.poll_once()
    assert src.status(T).state == "live" and src.spot("NIFTY") == D("24000")
    clock.t = 11
    assert src.status(T).state == "stale"
    src.poll_once()
    assert src.status(T).state == "live"


def test_failures_report_down_keep_last_data_and_back_off():
    clock, calls = Clock(), {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("503 from broker")
        return {"NIFTY": quote()}

    src = make(fetch, clock, interval=2, max_backoff=10)
    src.poll_once()
    assert src.next_delay(T) == 2
    src.poll_once()
    st = src.status(T)
    assert st.state == "down" and "503" in st.message and st.failures == 1
    assert src.spot("NIFTY") == D("24000")  # last good data is kept (the engine refuses it once stale)
    assert src.next_delay(T) == 4
    src.poll_once(); src.poll_once(); src.poll_once()
    assert src.next_delay(T) == 10  # capped
    calls["n"] = 0
    src.poll_once()
    assert src.status(T).state == "live" and src.next_delay(T) == 2


def test_first_poll_failure_is_down_not_starting():
    def boom():
        raise ConnectionError("no route")
    src = make(boom)
    src.poll_once()
    assert src.status(T).state == "down"


def test_closed_market_reports_closed_and_polls_slowly():
    src = PolledSource(lambda: {"NIFTY": quote()}, clock=Clock(), is_open=lambda now: False,
                       interval=2, idle_interval=60)
    src.poll_once()
    assert src.status(T).state == "closed" and src.next_delay(T) == 60
