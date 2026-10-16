"""The paper-trading engine.

Pure logic: no network, no wall clock. Callers pass quotes and `now`, which
makes every run replayable from recorded data.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping

from .charges import ChargeSchedule, Charges
from .fills import FillRules, limit_price, market_price, quote_problem
from .journal import Journal
from .models import (
    D, Fill, Instrument, Leg, Order, OrderStatus, OrderType, Position, Quote, Side,
)


@dataclass(frozen=True)
class EngineConfig:
    starting_cash: Decimal = Decimal("500000")
    fill_rules: FillRules = field(default_factory=FillRules)
    charges: ChargeSchedule = field(default_factory=ChargeSchedule)
    # Flat margin estimate per short lot, not SPAN. Placeholder values:
    margin_per_lot: Mapping[str, Decimal] = field(default_factory=dict)
    default_margin_per_lot: Decimal = Decimal("150000")


UNTAGGED = "untagged"


@dataclass(frozen=True)
class StrategyStats:
    strategy: str
    realized_pnl: Decimal    # gross of charges
    unrealized_pnl: Decimal
    charges: Decimal
    net_pnl: Decimal         # realized + unrealized - charges
    open_positions: int


@dataclass(frozen=True)
class Snapshot:
    ts: datetime
    cash: Decimal
    positions_value: Decimal
    equity: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    charges_paid: Decimal
    net_pnl: Decimal  # equity - starting cash (after charges)
    unpriced: tuple[str, ...]  # symbols marked at average price for lack of a quote


class Engine:
    def __init__(self, instruments: Mapping[str, Instrument], config: EngineConfig | None = None,
                 journal: Journal | None = None, account_id: str = "default"):
        self.account_id = account_id
        self.instruments = dict(instruments)
        self.config = config or EngineConfig()
        self.journal = journal or Journal()
        self.cash = self.config.starting_cash
        self.positions: dict[str, Position] = {}
        self.orders: dict[int, Order] = {}
        self.charges_paid = Decimal(0)
        # Per-strategy virtual books. Margin and cash stay account-level; these
        # only attribute P&L, so strategies that offset each other still sum to
        # the account's total.
        self.strategy_positions: dict[tuple[str, str], Position] = {}
        self.strategy_charges: dict[str, Decimal] = {}
        self._next_id = 1

    # ---- persistence --------------------------------------------------------

    def to_state(self) -> dict:
        """JSON-safe snapshot of everything needed to resume this account."""
        pos = lambda p: {"symbol": p.symbol, "net_qty": p.net_qty,  # noqa: E731
                         "avg_price": str(p.avg_price), "realized_pnl": str(p.realized_pnl)}
        return {
            "cash": str(self.cash), "charges_paid": str(self.charges_paid), "next_id": self._next_id,
            "positions": [pos(p) for p in self.positions.values()],
            "strategy_positions": [{"strategy": t, **pos(p)}
                                   for (t, _), p in self.strategy_positions.items()],
            "strategy_charges": {t: str(c) for t, c in self.strategy_charges.items()},
            "orders": [{
                "id": o.id, "type": o.order_type.value, "created": o.created.isoformat(),
                "strategy": o.strategy, "status": o.status.value, "reason": o.reason,
                "legs": [{"symbol": l.symbol, "side": l.side.value, "lots": l.lots,
                          "limit_price": None if l.limit_price is None else str(l.limit_price)}
                         for l in o.legs],
                "fills": [{"symbol": x.symbol, "side": x.side.value, "qty": x.qty,
                           "price": str(x.price), "ts": x.ts.isoformat(), "charges": str(x.charges)}
                          for x in o.fills],
            } for o in self.orders.values()],
        }

    def load_state(self, st: dict) -> None:
        pos = lambda d: Position(d["symbol"], d["net_qty"], D(d["avg_price"]), D(d["realized_pnl"]))  # noqa: E731
        self.cash, self.charges_paid, self._next_id = D(st["cash"]), D(st["charges_paid"]), st["next_id"]
        self.positions = {d["symbol"]: pos(d) for d in st["positions"]}
        self.strategy_positions = {(d["strategy"], d["symbol"]): pos(d) for d in st["strategy_positions"]}
        self.strategy_charges = {t: D(c) for t, c in st["strategy_charges"].items()}
        self.orders = {}
        for d in st["orders"]:
            o = Order(d["id"], tuple(Leg(l["symbol"], Side(l["side"]), l["lots"],
                                         None if l["limit_price"] is None else D(l["limit_price"]))
                                     for l in d["legs"]),
                      OrderType(d["type"]), datetime.fromisoformat(d["created"]), d["strategy"],
                      OrderStatus(d["status"]), d["reason"])
            o.fills = [Fill(o.id, x["symbol"], Side(x["side"]), x["qty"], D(x["price"]),
                            datetime.fromisoformat(x["ts"]), D(x["charges"])) for x in d["fills"]]
            self.orders[o.id] = o

    # ---- orders -----------------------------------------------------------

    def submit(self, legs: list[Leg], order_type: OrderType, now: datetime,
               quotes: Mapping[str, Quote], strategy: str = "") -> Order:
        """Place a basket. All legs fill together or not at all. `strategy` is a
        free-text tag stored with the order and journal for later comparison."""
        order = Order(self._next_id, tuple(legs), order_type, now, strategy or UNTAGGED)
        self._next_id += 1
        self.orders[order.id] = order
        self._log(now, "order", {
            "type": order_type.value,
            "legs": [(l.symbol, l.side.value, l.lots, l.limit_price) for l in legs],
        }, order.id)

        problem = self._validate(order, now)
        if problem:
            return self._finish(order, OrderStatus.REJECTED, problem, now)
        self._try_fill(order, now, quotes)
        return order

    def on_quotes(self, now: datetime, quotes: Mapping[str, Quote]) -> list[Order]:
        """Re-test resting limit orders against fresh quotes."""
        filled = []
        for order in [o for o in self.orders.values() if o.status is OrderStatus.OPEN]:
            self._try_fill(order, now, quotes)
            if order.status is OrderStatus.FILLED:
                filled.append(order)
        return filled

    def cancel(self, order_id: int, now: datetime) -> Order:
        order = self.orders[order_id]
        if order.status is OrderStatus.OPEN:
            self._finish(order, OrderStatus.CANCELLED, "cancelled by user", now)
        return order

    def end_of_day(self, now: datetime) -> None:
        """Day orders: cancel everything still resting."""
        for order in [o for o in self.orders.values() if o.status is OrderStatus.OPEN]:
            self._finish(order, OrderStatus.CANCELLED, "day order expired", now)

    # ---- marking and settlement ---------------------------------------------

    def mark(self, now: datetime, quotes: Mapping[str, Quote]) -> Snapshot:
        value = Decimal(0)
        unrealized = Decimal(0)
        unpriced = []
        for pos in self.positions.values():
            if pos.net_qty == 0:
                continue
            price = self._mark_price(pos.symbol, quotes)
            if price is None:
                price = pos.avg_price
                unpriced.append(pos.symbol)
            value += pos.net_qty * price
            unrealized += pos.net_qty * (price - pos.avg_price)
        realized = sum((p.realized_pnl for p in self.positions.values()), Decimal(0))
        equity = self.cash + value
        snap = Snapshot(now, self.cash, value, equity, unrealized, realized,
                        self.charges_paid, equity - self.config.starting_cash, tuple(unpriced))
        self._log(now, "mark", {
            "equity": equity, "unrealized": unrealized, "realized": realized,
            "charges": self.charges_paid, "unpriced": unpriced,
        })
        return snap

    def strategy_report(self, quotes: Mapping[str, Quote]) -> dict[str, StrategyStats]:
        """P&L attributed to each strategy tag, marked at mid."""
        tags = {t for t, _ in self.strategy_positions} | set(self.strategy_charges)
        out = {}
        for tag in sorted(tags):
            realized = unrealized = Decimal(0)
            open_count = 0
            for (t, symbol), sp in self.strategy_positions.items():
                if t != tag:
                    continue
                realized += sp.realized_pnl
                if sp.net_qty:
                    open_count += 1
                    price = self._mark_price(symbol, quotes)
                    if price is not None:
                        unrealized += sp.net_qty * (price - sp.avg_price)
            charges = self.strategy_charges.get(tag, Decimal(0))
            out[tag] = StrategyStats(tag, realized, unrealized, charges,
                                     realized + unrealized - charges, open_count)
        return out

    def settle_expiry(self, on_date: date, settlement_prices: Mapping[str, Decimal],
                      now: datetime) -> list[str]:
        """Cash-settle expired options at the official settlement price (e.g. from
        bhavcopy). Raises KeyError if a needed underlying has no price."""
        settled = []
        for order in [o for o in self.orders.values() if o.status is OrderStatus.OPEN]:
            if any((i := self.instruments[l.symbol]).expiry and i.expiry <= on_date
                   for l in order.legs):
                self._finish(order, OrderStatus.CANCELLED, "contract expired", now)
        expired = lambda i: i.is_option and i.expiry is not None and i.expiry <= on_date  # noqa: E731

        def intrinsic(inst: Instrument) -> tuple[Decimal, Decimal]:
            spot = D(settlement_prices[inst.underlying])
            value = max(spot - inst.strike, Decimal(0)) if inst.kind == "CE" \
                else max(inst.strike - spot, Decimal(0))
            return spot, value

        total_charge = Decimal(0)
        for (tag, symbol), sp in self.strategy_positions.items():
            inst = self.instruments[symbol]
            if sp.net_qty == 0 or not expired(inst):
                continue
            _, value = intrinsic(inst)
            charge = Decimal(0)
            if sp.net_qty > 0 and value > 0:
                charge = self.config.charges.exercise(value * sp.net_qty).total
            self.strategy_charges[tag] = self.strategy_charges.get(tag, Decimal(0)) + charge
            total_charge += charge
            self._reduce(sp, -sp.net_qty, value)
        for pos in self.positions.values():
            inst = self.instruments[pos.symbol]
            if pos.net_qty == 0 or not expired(inst):
                continue
            spot, value = intrinsic(inst)
            qty = pos.net_qty
            self.cash += qty * value
            self._reduce(pos, -qty, value)
            self._log(now, "settle", {
                "symbol": pos.symbol, "qty": qty, "settlement": spot, "intrinsic": value,
            })
            settled.append(pos.symbol)
        self.cash -= total_charge
        self.charges_paid += total_charge
        return settled

    # ---- internals ----------------------------------------------------------

    def _validate(self, order: Order, now: datetime) -> str | None:
        if not order.legs:
            return "empty order"
        seen = set()
        for leg in order.legs:
            inst = self.instruments.get(leg.symbol)
            if inst is None:
                return f"unknown instrument {leg.symbol}"
            if leg.symbol in seen:
                return f"duplicate leg {leg.symbol}"
            seen.add(leg.symbol)
            if leg.lots <= 0:
                return "lots must be positive"
            if inst.expiry and inst.expiry < now.date():
                return f"{leg.symbol} has expired"
            if order.order_type is OrderType.LIMIT:
                if leg.limit_price is None or leg.limit_price <= 0:
                    return "limit order needs a positive limit price"
                if leg.limit_price % inst.tick_size != 0:
                    return f"limit price off tick for {leg.symbol}"
        return None

    def _try_fill(self, order: Order, now: datetime, quotes: Mapping[str, Quote]) -> None:
        rules = self.config.fill_rules
        prices: dict[str, Decimal] = {}
        for leg in order.legs:
            inst = self.instruments[leg.symbol]
            q = quotes.get(leg.symbol)
            problem = quote_problem(q, now, rules)
            if order.order_type is OrderType.MARKET:
                if problem:
                    return self._finish(order, OrderStatus.REJECTED, f"{leg.symbol}: {problem}", now)
                prices[leg.symbol] = market_price(leg.side, q, inst, rules)
            else:
                if inst.expiry and inst.expiry < now.date():
                    return self._finish(order, OrderStatus.REJECTED, f"{leg.symbol} has expired", now)
                if problem:
                    return  # keep resting
                p = limit_price(leg.side, leg.limit_price, q, rules)
                if p is None:
                    return
                prices[leg.symbol] = p
        self._execute(order, prices, now)

    def _execute(self, order: Order, prices: dict[str, Decimal], now: datetime) -> None:
        cash = self.cash
        positions = copy.deepcopy(self.positions)
        strat_pos = copy.deepcopy(self.strategy_positions)
        strat_charges = dict(self.strategy_charges)
        fills, total_charges = [], Decimal(0)
        for leg in order.legs:
            inst = self.instruments[leg.symbol]
            qty = leg.lots * inst.lot_size
            price = prices[leg.symbol]
            charge = self.config.charges.trade(leg.side, price * qty).total
            cash -= leg.side.sign * price * qty + charge
            total_charges += charge
            pos = positions.setdefault(leg.symbol, Position(leg.symbol))
            self._reduce(pos, leg.side.sign * qty, price)
            sp = strat_pos.setdefault((order.strategy, leg.symbol), Position(leg.symbol))
            self._reduce(sp, leg.side.sign * qty, price)
            strat_charges[order.strategy] = strat_charges.get(order.strategy, Decimal(0)) + charge
            fills.append(Fill(order.id, leg.symbol, leg.side, qty, price, now, charge))
        margin = self._margin_required(positions)
        if cash < margin:
            return self._finish(order, OrderStatus.REJECTED,
                                f"insufficient funds: need {margin}, have {cash}", now)
        self.cash, self.positions = cash, positions
        self.strategy_positions, self.strategy_charges = strat_pos, strat_charges
        self.charges_paid += total_charges
        order.fills.extend(fills)
        for f in fills:
            self._log(now, "fill", {
                "symbol": f.symbol, "side": f.side.value, "qty": f.qty,
                "price": f.price, "charges": f.charges,
            }, order.id)
        self._finish(order, OrderStatus.FILLED, "", now)

    @staticmethod
    def _reduce(pos: Position, signed_qty: int, price: Decimal) -> None:
        """Apply a signed fill to a position: extend, reduce, close or flip."""
        if pos.net_qty == 0 or (pos.net_qty > 0) == (signed_qty > 0):
            total = pos.net_qty + signed_qty
            pos.avg_price = (pos.avg_price * abs(pos.net_qty) + price * abs(signed_qty)) / abs(total)
            pos.net_qty = total
            return
        closed = min(abs(signed_qty), abs(pos.net_qty))
        direction = 1 if pos.net_qty > 0 else -1
        pos.realized_pnl += direction * closed * (price - pos.avg_price)
        pos.net_qty += signed_qty
        if pos.net_qty == 0:
            pos.avg_price = Decimal(0)
        elif (pos.net_qty > 0) != (direction > 0):  # flipped through zero
            pos.avg_price = price

    def margin_used(self) -> Decimal:
        return self._margin_required(self.positions)

    def _margin_required(self, positions: Mapping[str, Position]) -> Decimal:
        """Flat per-lot estimate on short legs, reduced for defined-risk
        vertical spreads to the spread's max loss. Not SPAN."""
        total = Decimal(0)
        groups: dict[tuple, dict] = defaultdict(lambda: {"short": [], "long": []})
        for pos in positions.values():
            if pos.net_qty == 0:
                continue
            inst = self.instruments[pos.symbol]
            lots = abs(pos.net_qty) // inst.lot_size
            flat = self.config.margin_per_lot.get(inst.underlying, self.config.default_margin_per_lot)
            if not inst.is_option:
                if pos.net_qty < 0:
                    total += lots * flat
                continue
            g = groups[(inst.underlying, inst.expiry, inst.kind)]
            g["lot_size"], g["flat"] = inst.lot_size, flat
            g["short" if pos.net_qty < 0 else "long"].extend([inst.strike] * lots)
        for (_, _, kind), g in groups.items():
            longs = list(g["long"])
            for ks in g["short"]:
                best = None
                for kl in longs:
                    risk = max(kl - ks, 0) if kind == "CE" else max(ks - kl, 0)
                    if best is None or risk < best[0]:
                        best = (risk, kl)
                if best is None:
                    total += g["flat"]
                else:
                    longs.remove(best[1])
                    total += min(best[0] * g["lot_size"], g["flat"])
        return total

    def _mark_price(self, symbol: str, quotes: Mapping[str, Quote]) -> Decimal | None:
        q = quotes.get(symbol)
        if q is None:
            return None
        if q.bid > 0 and q.ask >= q.bid:
            return q.mid
        return q.ltp

    def _log(self, ts: datetime, kind: str, payload: dict, order_id: int | None = None) -> None:
        order = self.orders.get(order_id) if order_id is not None else None
        self.journal.record(ts, kind, payload, order_id, self.account_id,
                            order.strategy if order else "")

    def _finish(self, order: Order, status: OrderStatus, reason: str, now: datetime) -> Order:
        order.status, order.reason = status, reason
        self._log(now, "status", {"status": status.value, "reason": reason}, order.id)
        return order
