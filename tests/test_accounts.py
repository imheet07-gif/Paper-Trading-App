from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest

from papertrader import (
    Accounts, DataMode, DelayedSource, EngineConfig, Instrument, Leg, OrderStatus, OrderType,
    Quote, ReplaySource, Side,
)
from papertrader.fills import FillRules

T0 = datetime(2026, 10, 20, 10, 0, 0)
INST = Instrument("NIFTYCE", "NIFTY", "CE", 75, D("0.05"), D(24000), date(2026, 10, 27))
CFG = EngineConfig(fill_rules=FillRules(slippage_ticks=0))


def snap(ts, price):
    return ts, {INST.symbol: Quote(INST.symbol, ts, D(price), D(price), D(price))}


def feed():
    return ReplaySource([snap(T0 + timedelta(minutes=m), 100 + m) for m in range(0, 31)])


def buy(lots=1, limit=None):
    return Leg(INST.symbol, Side.BUY, lots, limit)


def test_accounts_are_isolated_and_journal_is_tagged():
    acc = Accounts({INST.symbol: INST})
    acc.create("heet", feed(), DataMode.OWN_KEYS, CFG)
    acc.create("friend", feed(), DataMode.OWN_KEYS, CFG)
    now = T0 + timedelta(minutes=5)
    acc.submit("heet", [buy()], OrderType.MARKET, now, strategy="momentum")
    assert acc.get("heet").engine.positions and not acc.get("friend").engine.positions
    ev = acc.journal.events(kind="fill", account_id="heet")
    assert len(ev) == 1 and ev[0]["strategy"] == "momentum"
    assert acc.journal.events(kind="fill", account_id="friend") == []


def test_shared_delayed_is_blocked_unless_enabled():
    acc = Accounts({INST.symbol: INST})
    with pytest.raises(PermissionError):
        acc.create("friend", DelayedSource(feed()), DataMode.SHARED_DELAYED)
    ok = Accounts({INST.symbol: INST}, allow_shared_delayed=True)
    ok.create("friend", DelayedSource(feed()), DataMode.SHARED_DELAYED)


def test_delayed_account_trades_on_old_prices_with_consistent_clock():
    acc = Accounts({INST.symbol: INST}, allow_shared_delayed=True)
    acc.create("live", feed(), DataMode.OWN_KEYS, CFG)
    acc.create("late", DelayedSource(feed(), timedelta(minutes=15)), DataMode.SHARED_DELAYED, CFG)
    now = T0 + timedelta(minutes=20)
    live = acc.submit("live", [buy()], OrderType.MARKET, now)
    late = acc.submit("late", [buy()], OrderType.MARKET, now)
    assert live.fills[0].price == D(120)
    assert late.fills[0].price == D(105)  # the 10:05 snapshot
    assert late.fills[0].ts == T0 + timedelta(minutes=5)  # journal clock is the delayed clock


def test_tick_fans_quotes_out_to_resting_orders_and_leaderboard_ranks():
    acc = Accounts({INST.symbol: INST})
    acc.create("a", feed(), DataMode.OWN_KEYS, CFG)
    acc.create("b", feed(), DataMode.OWN_KEYS, CFG)
    t = T0 + timedelta(minutes=10)  # price 110
    resting = acc.submit("a", [buy(limit=D("105"))], OrderType.LIMIT, t)
    assert resting.status is OrderStatus.OPEN
    acc.submit("b", [buy()], OrderType.MARKET, t)  # buys at 110
    # nothing crosses at t+1 (111)
    assert acc.tick(t + timedelta(minutes=1)) == {}
    board = acc.leaderboard(T0 + timedelta(minutes=30))  # price 130
    assert [a for a, _ in board] == ["b", "a"]
    assert board[0][1].unrealized_pnl == D(20) * 75
