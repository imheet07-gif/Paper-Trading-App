"""Many paper accounts over shared infrastructure.

Each account has its own Engine (cash, positions, orders) and its own data
source. One journal records everything, tagged by account and strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping

from .data import QuoteSource
from .engine import Engine, EngineConfig, Snapshot
from .journal import Journal
from .models import Instrument, Leg, Order, OrderType


class DataMode(str, Enum):
    OWN_KEYS = "own_keys"                # the user's own broker credentials
    SHARED_DELAYED = "shared_delayed"    # operator's feed, delayed; needs sign-off
    DEMO = "demo"                        # simulated prices, no real data involved


@dataclass
class Account:
    id: str
    engine: Engine
    source: QuoteSource
    mode: DataMode


class Accounts:
    def __init__(self, instruments: Mapping[str, Instrument], journal: Journal | None = None,
                 allow_shared_delayed: bool = False):
        """allow_shared_delayed stays False until redistribution of the operator's
        data feed has been cleared with the exchange/broker in writing."""
        self.instruments = dict(instruments)
        self.journal = journal or Journal()
        self.allow_shared_delayed = allow_shared_delayed
        self._accounts: dict[str, Account] = {}

    def create(self, account_id: str, source: QuoteSource, mode: DataMode,
               config: EngineConfig | None = None) -> Account:
        if account_id in self._accounts:
            raise ValueError(f"account {account_id!r} already exists")
        if mode is DataMode.SHARED_DELAYED and not self.allow_shared_delayed:
            raise PermissionError("shared delayed data is disabled; use own keys")
        engine = Engine(self.instruments, config, self.journal, account_id)
        acct = self._accounts[account_id] = Account(account_id, engine, source, mode)
        return acct

    def get(self, account_id: str) -> Account:
        return self._accounts[account_id]

    def submit(self, account_id: str, legs: list[Leg], order_type: OrderType, now: datetime,
               strategy: str = "") -> Order:
        """Trade against the account's own view of the market (possibly delayed)."""
        a = self.get(account_id)
        clock, quotes = a.source.quotes_at(now)
        return a.engine.submit(legs, order_type, clock, quotes, strategy)

    def tick(self, now: datetime) -> dict[str, list[Order]]:
        """Push fresh quotes to every account; returns newly filled orders by account."""
        out = {}
        for a in self._accounts.values():
            clock, quotes = a.source.quotes_at(now)
            filled = a.engine.on_quotes(clock, quotes)
            if filled:
                out[a.id] = filled
        return out

    def leaderboard(self, now: datetime) -> list[tuple[str, Snapshot]]:
        rows = []
        for a in self._accounts.values():
            clock, quotes = a.source.quotes_at(now)
            rows.append((a.id, a.engine.mark(clock, quotes)))
        return sorted(rows, key=lambda r: r[1].net_pnl, reverse=True)
