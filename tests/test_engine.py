from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest

from papertrader import (
    ChargeSchedule, Engine, EngineConfig, Instrument, Leg, OrderStatus, OrderType, Quote, Side,
)
from papertrader.fills import FillRules

NOW = datetime(2026, 10, 20, 10, 0, 0)
EXPIRY = date(2026, 10, 27)
ZERO = ChargeSchedule(brokerage_per_order=D(0), stt_sell_rate=D(0), stt_exercise_rate=D(0),
                      exchange_rate=D(0), sebi_rate=D(0), stamp_buy_rate=D(0), gst_rate=D(0))


def opt(strike, kind):
    return Instrument(f"NIFTY{EXPIRY:%y%m%d}{strike}{kind}", "NIFTY", kind, 75, D("0.05"),
                      D(strike), EXPIRY)


CE24000, CE24100 = opt(24000, "CE"), opt(24100, "CE")
INSTR = {i.symbol: i for i in (CE24000, CE24100)}


def q(inst, bid, ask, ts=NOW):
    return Quote(inst.symbol, ts, D(bid), D(ask), D(bid))


def make(charges=ZERO, **rules):
    cfg = EngineConfig(starting_cash=D(500000), charges=charges,
                       fill_rules=FillRules(**{"slippage_ticks": 0, **rules}),
                       default_margin_per_lot=D(150000))
    return Engine(INSTR, cfg)


def buy(inst, lots=1, limit=None):
    return Leg(inst.symbol, Side.BUY, lots, limit)


def sell(inst, lots=1, limit=None):
    return Leg(inst.symbol, Side.SELL, lots, limit)


def test_market_buy_fills_at_ask_plus_slippage():
    e = make(slippage_ticks=2)
    o = e.submit([buy(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "100", "100.5")})
    assert o.status is OrderStatus.FILLED
    assert o.fills[0].price == D("100.60")
    assert e.cash == D(500000) - D("100.60") * 75


def test_market_sell_fills_at_bid_minus_slippage():
    e = make(slippage_ticks=1)
    e.submit([sell(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "100", "100.5")})
    assert e.positions[CE24000.symbol].net_qty == -75
    assert e.positions[CE24000.symbol].avg_price == D("99.95")


def test_stale_quote_rejects_market_order():
    e = make()
    old = q(CE24000, "100", "100.5", NOW - timedelta(seconds=60))
    o = e.submit([buy(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: old})
    assert o.status is OrderStatus.REJECTED and "stale" in o.reason
    assert e.cash == D(500000)


def test_wide_spread_rejects():
    e = make()
    o = e.submit([buy(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "50", "100")})
    assert o.status is OrderStatus.REJECTED and "spread" in o.reason


def test_limit_buy_rests_then_fills_at_ask_not_limit():
    e = make()
    o = e.submit([buy(CE24000, limit=D("100"))], OrderType.LIMIT, NOW,
                 {CE24000.symbol: q(CE24000, "101", "101.5")})
    assert o.status is OrderStatus.OPEN
    later = NOW + timedelta(seconds=5)
    filled = e.on_quotes(later, {CE24000.symbol: q(CE24000, "98", "99", later)})
    assert filled == [o] and o.fills[0].price == D("99")  # price improvement is allowed


def test_limit_never_fills_worse_than_limit():
    e = make()
    o = e.submit([buy(CE24000, limit=D("100"))], OrderType.LIMIT, NOW,
                 {CE24000.symbol: q(CE24000, "100", "100.05")})
    assert o.status is OrderStatus.OPEN


def test_conservative_limit_needs_strict_cross_and_fills_at_limit():
    e = make(conservative_limits=True)
    quotes = {CE24000.symbol: q(CE24000, "99.5", "100")}
    o = e.submit([buy(CE24000, limit=D("100"))], OrderType.LIMIT, NOW, quotes)
    assert o.status is OrderStatus.OPEN  # ask == limit is only a touch
    later = NOW + timedelta(seconds=1)
    e.on_quotes(later, {CE24000.symbol: q(CE24000, "99", "99.5", later)})
    assert o.fills[0].price == D("100")


def test_round_trip_pnl_and_charges_reconcile_by_hand():
    sched = ChargeSchedule()  # real-looking schedule, hand-checked below
    e = make(charges=sched)
    e.submit([buy(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "100", "100")})
    e.submit([sell(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "110", "110")})
    pos = e.positions[CE24000.symbol]
    assert pos.net_qty == 0 and pos.realized_pnl == D(10) * 75  # 750 gross
    # buy turnover 7500, sell turnover 8250
    buy_c = sched.trade(Side.BUY, D(7500))
    sell_c = sched.trade(Side.SELL, D(8250))
    assert buy_c.stt == 0 and sell_c.stt == D("8.25")           # 0.1% of 8250
    assert buy_c.stamp == D("0.23") and sell_c.stamp == 0       # 0.003% of 7500
    assert buy_c.exchange == D("2.63") and sell_c.exchange == D("2.89")  # 0.03503% of turnover
    assert buy_c.gst == D("4.08") and sell_c.gst == D("4.12")   # 18% of (20 + exchange + sebi)
    snap = e.mark(NOW, {})
    assert snap.charges_paid == buy_c.total + sell_c.total
    assert snap.net_pnl == D(750) - snap.charges_paid
    assert e.cash == D(500000) + 750 - snap.charges_paid


def test_position_flip_resets_average():
    e = make()
    quotes = {CE24000.symbol: q(CE24000, "100", "100")}
    e.submit([buy(CE24000)], OrderType.MARKET, NOW, quotes)
    e.submit([sell(CE24000, 2)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "120", "120")})
    pos = e.positions[CE24000.symbol]
    assert pos.net_qty == -75 and pos.avg_price == D(120) and pos.realized_pnl == D(20) * 75


def test_basket_is_all_or_nothing():
    e = make()
    quotes = {CE24000.symbol: q(CE24000, "100", "100.5")}  # no quote for the second leg
    o = e.submit([buy(CE24000), sell(CE24100)], OrderType.MARKET, NOW, quotes)
    assert o.status is OrderStatus.REJECTED
    assert not e.positions and e.cash == D(500000)


def test_bull_call_spread_margin_is_max_loss_not_flat():
    e = make()
    quotes = {CE24000.symbol: q(CE24000, "200", "200"), CE24100.symbol: q(CE24100, "140", "140")}
    # Bear call spread: short the lower strike, long the higher one. Max loss = 100 * 75.
    o = e.submit([sell(CE24000), buy(CE24100)], OrderType.MARKET, NOW, quotes)
    assert o.status is OrderStatus.FILLED
    assert e._margin_required(e.positions) == D(100) * 75


def test_naked_short_uses_flat_margin_and_insufficient_funds_rejects():
    cfg = EngineConfig(starting_cash=D(100000), charges=ZERO, fill_rules=FillRules(slippage_ticks=0),
                       default_margin_per_lot=D(150000))
    e = Engine(INSTR, cfg)
    o = e.submit([sell(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "100", "100")})
    assert o.status is OrderStatus.REJECTED and "insufficient" in o.reason
    assert e.cash == D(100000)


def test_mark_to_market_uses_mid_and_flags_unpriced():
    e = make()
    e.submit([buy(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "100", "100")})
    snap = e.mark(NOW, {CE24000.symbol: q(CE24000, "108", "110")})
    assert snap.unrealized_pnl == D(9) * 75 and not snap.unpriced
    assert e.mark(NOW, {}).unpriced == (CE24000.symbol,)


def test_expiry_settlement_itm_and_otm():
    e = make()
    quotes = {CE24000.symbol: q(CE24000, "100", "100"), CE24100.symbol: q(CE24100, "50", "50")}
    e.submit([buy(CE24000), buy(CE24100)], OrderType.MARKET, NOW, quotes)
    cash_before = e.cash
    settled = e.settle_expiry(EXPIRY, {"NIFTY": D(24040)}, NOW + timedelta(days=7))
    assert set(settled) == {CE24000.symbol, CE24100.symbol}
    assert e.cash == cash_before + D(40) * 75  # 24000 CE worth 40, 24100 CE worthless
    assert e.positions[CE24000.symbol].realized_pnl == D(-60) * 75
    assert e.positions[CE24100.symbol].realized_pnl == D(-50) * 75


def test_expired_contract_is_rejected_and_validation_errors():
    e = make()
    late = datetime(2026, 11, 2, 10)
    assert e.submit([buy(CE24000)], OrderType.MARKET, late, {}).status is OrderStatus.REJECTED
    assert "off tick" in e.submit([buy(CE24000, limit=D("100.03"))], OrderType.LIMIT, NOW, {}).reason
    assert "unknown" in e.submit([Leg("NOPE", Side.BUY, 1)], OrderType.MARKET, NOW, {}).reason


def test_cancel_and_end_of_day():
    e = make()
    quotes = {CE24000.symbol: q(CE24000, "101", "101.5")}
    a = e.submit([buy(CE24000, limit=D("90"))], OrderType.LIMIT, NOW, quotes)
    b = e.submit([buy(CE24000, limit=D("91"))], OrderType.LIMIT, NOW, quotes)
    e.cancel(a.id, NOW)
    e.end_of_day(NOW)
    assert a.status is b.status is OrderStatus.CANCELLED


def test_journal_records_everything_for_replay():
    e = make()
    e.submit([buy(CE24000)], OrderType.MARKET, NOW, {CE24000.symbol: q(CE24000, "100", "100")})
    e.mark(NOW, {})
    kinds = [ev["kind"] for ev in e.journal.events()]
    assert kinds == ["order", "fill", "status", "mark"]
