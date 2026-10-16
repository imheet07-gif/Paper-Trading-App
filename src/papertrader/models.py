"""Plain data types shared by the engine. No I/O, no clock access."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


def D(x) -> Decimal:
    """Build a Decimal without binary-float artefacts."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    OPEN = "OPEN"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Instrument:
    """Contract details. Load these from the broker's instrument master."""
    symbol: str
    underlying: str
    kind: str  # "CE" | "PE" | "FUT" | "EQ"
    lot_size: int
    tick_size: Decimal
    strike: Decimal | None = None
    expiry: date | None = None

    @property
    def is_option(self) -> bool:
        return self.kind in ("CE", "PE")


@dataclass(frozen=True)
class Quote:
    symbol: str
    ts: datetime
    bid: Decimal
    ask: Decimal
    ltp: Decimal | None = None

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Leg:
    symbol: str
    side: Side
    lots: int
    limit_price: Decimal | None = None  # required for LIMIT orders


@dataclass
class Order:
    id: int
    legs: tuple[Leg, ...]
    order_type: OrderType
    created: datetime
    status: OrderStatus = OrderStatus.OPEN
    reason: str = ""
    fills: list["Fill"] = field(default_factory=list)


@dataclass(frozen=True)
class Fill:
    order_id: int
    symbol: str
    side: Side
    qty: int  # units, not lots
    price: Decimal
    ts: datetime
    charges: Decimal


@dataclass
class Position:
    symbol: str
    net_qty: int = 0  # signed units; negative = short
    avg_price: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)  # gross of charges
