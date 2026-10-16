"""Statutory and brokerage charges for options trades.

The rates below are a configurable schedule, NOT a source of truth. STT,
exchange and SEBI fees change; verify them against the current NSE circulars
and your broker's charge sheet before trusting P&L, and update `as_of`.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .models import Side

PAISE = Decimal("0.01")


def _r(x: Decimal) -> Decimal:
    return x.quantize(PAISE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Charges:
    brokerage: Decimal = Decimal(0)
    stt: Decimal = Decimal(0)
    exchange: Decimal = Decimal(0)
    sebi: Decimal = Decimal(0)
    stamp: Decimal = Decimal(0)
    gst: Decimal = Decimal(0)

    @property
    def total(self) -> Decimal:
        return self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst


@dataclass(frozen=True)
class ChargeSchedule:
    as_of: str = "UNVERIFIED-PLACEHOLDER"
    brokerage_per_order: Decimal = Decimal("20")      # flat per executed leg
    stt_sell_rate: Decimal = Decimal("0.001")         # on sell premium
    stt_exercise_rate: Decimal = Decimal("0.00125")   # on intrinsic, ITM long at expiry
    exchange_rate: Decimal = Decimal("0.0003503")     # on premium turnover
    sebi_rate: Decimal = Decimal("0.000001")          # Rs 10 per crore
    stamp_buy_rate: Decimal = Decimal("0.00003")      # on buy premium
    gst_rate: Decimal = Decimal("0.18")               # on brokerage + exchange + sebi

    def trade(self, side: Side, turnover: Decimal) -> Charges:
        """Charges for one executed option leg. turnover = price * quantity."""
        brokerage = self.brokerage_per_order
        stt = _r(turnover * self.stt_sell_rate) if side is Side.SELL else Decimal(0)
        exchange = _r(turnover * self.exchange_rate)
        sebi = _r(turnover * self.sebi_rate)
        stamp = _r(turnover * self.stamp_buy_rate) if side is Side.BUY else Decimal(0)
        gst = _r((brokerage + exchange + sebi) * self.gst_rate)
        return Charges(brokerage, stt, exchange, sebi, stamp, gst)

    def exercise(self, intrinsic_value: Decimal) -> Charges:
        """Cash settlement of an ITM long option at expiry (STT on intrinsic)."""
        return Charges(stt=_r(intrinsic_value * self.stt_exercise_rate))
