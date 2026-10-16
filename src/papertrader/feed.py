"""Live-feed plumbing: poll a fetch function, keep the latest snapshot, report health.

An adapter (Upstox, Angel One, ...) only has to supply `fetch() -> {symbol: Quote}`.
Everything else (scheduling, backoff, stale detection, market hours) lives here
and is tested against fake fetchers.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from .models import Quote

log = logging.getLogger("papertrader.feed")
IST = ZoneInfo("Asia/Kolkata")
OPEN, CLOSE = dtime(9, 15), dtime(15, 30)


def market_open(now: datetime) -> bool:
    """NSE equity/F&O session by clock only (Mon-Fri 09:15-15:30 IST). Does not
    know exchange holidays; a holiday just looks like an open market with a stale feed."""
    t = now.astimezone(IST) if now.tzinfo else now
    return t.weekday() < 5 and OPEN <= t.time() <= CLOSE


@dataclass(frozen=True)
class FeedStatus:
    state: str                 # starting | live | stale | down | closed
    message: str
    last_ok_age: float | None  # seconds since last successful poll
    failures: int


class PolledSource:
    """QuoteSource backed by a background poller."""

    def __init__(self, fetch: Callable[[], Mapping[str, Quote]], *, interval: float = 2.0,
                 stale_after: float = 10.0, idle_interval: float = 60.0,
                 is_open: Callable[[datetime], bool] = market_open,
                 max_backoff: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._fetch, self.interval, self.stale_after = fetch, interval, stale_after
        self.idle_interval, self._is_open, self.max_backoff = idle_interval, is_open, max_backoff
        self._clock = clock
        self._lock = threading.Lock()
        self._quotes: dict[str, Quote] = {}
        self._last_ok: float | None = None
        self._failures = 0
        self._error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- polling ---------------------------------------------------------------
    def poll_once(self) -> None:
        try:
            quotes = dict(self._fetch())
        except Exception as e:  # any adapter failure is a feed failure, never a crash
            with self._lock:
                self._failures += 1
                self._error = f"{type(e).__name__}: {e}"[:200]
            log.warning("feed poll failed (%d): %s", self._failures, self._error)
            return
        with self._lock:
            self._quotes, self._last_ok = quotes, self._clock()
            self._failures, self._error = 0, ""

    def next_delay(self, now: datetime) -> float:
        with self._lock:
            failures = self._failures
        if failures:
            return min(self.interval * 2 ** failures, self.max_backoff)
        return self.interval if self._is_open(now) else self.idle_interval

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="feed-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.next_delay(datetime.now(IST)))

    # ---- QuoteSource ---------------------------------------------------------------
    def quotes_at(self, now: datetime) -> tuple[datetime, dict[str, Quote]]:
        with self._lock:
            return now, dict(self._quotes)  # each Quote keeps its own ts, so stale ones get refused

    def spot(self, underlying: str, now: datetime | None = None) -> Decimal | None:
        with self._lock:
            q = self._quotes.get(underlying)
        return q.ltp if q else None

    def status(self, now: datetime | None = None) -> FeedStatus:
        now = now or datetime.now(IST)
        with self._lock:
            age = None if self._last_ok is None else self._clock() - self._last_ok
            failures, error = self._failures, self._error
        if age is None:
            return FeedStatus("down" if failures else "starting", error or "waiting for first data", None, failures)
        if failures:
            return FeedStatus("down", error, age, failures)
        if age > self.stale_after and self._is_open(now):
            return FeedStatus("stale", f"no update for {age:.0f}s", age, failures)
        if not self._is_open(now):
            return FeedStatus("closed", "market closed; showing last prices", age, failures)
        return FeedStatus("live", "", age, failures)
