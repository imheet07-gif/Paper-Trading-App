"""Quote sources. The engine only ever sees (time, quotes) from one of these.

`quotes_at(now)` returns the clock the account should treat as "now" together
with the quotes visible at that clock. A delayed source returns an earlier
clock, so the engine's stale-quote checks stay consistent with the data.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Mapping, Protocol

from .models import Quote


class QuoteSource(Protocol):
    def quotes_at(self, now: datetime) -> tuple[datetime, dict[str, Quote]]: ...


class ReplaySource:
    """Replays recorded snapshots: [(ts, {symbol: Quote})]. Also the test double
    for live adapters (Upstox, Angel One, ...), which implement the same method."""

    def __init__(self, snapshots: list[tuple[datetime, Mapping[str, Quote]]]):
        self._snaps = sorted(snapshots, key=lambda s: s[0])
        self._times = [t for t, _ in self._snaps]

    def quotes_at(self, now: datetime) -> tuple[datetime, dict[str, Quote]]:
        i = bisect_right(self._times, now)
        return now, dict(self._snaps[i - 1][1]) if i else {}


class DelayedSource:
    """Shows `inner`'s data as it was `delay` ago, with the clock shifted too."""

    def __init__(self, inner: QuoteSource, delay: timedelta = timedelta(minutes=15)):
        self.inner, self.delay = inner, delay

    def quotes_at(self, now: datetime) -> tuple[datetime, dict[str, Quote]]:
        return self.inner.quotes_at(now - self.delay)
