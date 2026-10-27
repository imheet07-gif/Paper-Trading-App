"""Paper-trading ledger for the daily scan, in exact rupees.

The ledger is a JSON file (default trader/paper.json, git-ignored) holding
cash, positions, the orders waiting for the next open, every fill and the
value at each scan. Orders are the scan's order sheet; they fill on paper
the way a limit order entered at the open would: at the open if the open
is within the limit, at the limit if the day's range reached it, otherwise
not at all. Charges come from core/charges.py (the verified Zerodha model).

Nothing here talks to a broker. The real trades the user places by hand
are a separate record (their tradebook).
"""

import datetime as dt
import json
import os
import sys
from decimal import ROUND_DOWN, Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import charges as charges_mod  # noqa: E402
from money import D, paise  # noqa: E402

DEFAULT_LEDGER = os.path.join(ROOT, "trader", "paper.json")


def new(strategy, capital, started):
    return {"strategy": strategy, "started": started, "capital": str(paise(capital)), "cash": str(paise(capital)),
            "positions": {}, "pending": None, "fills": [], "unfilled": [], "values": []}


def load(path=DEFAULT_LEDGER):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(ledger, path=DEFAULT_LEDGER):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)  # never leave a half-written ledger


def value(ledger, closes):
    """Cash plus positions at the given closes {isin: Decimal}; a missing close uses the last known price."""
    total = D(ledger["cash"])
    for isin, p in ledger["positions"].items():
        px = closes.get(isin)
        if px is not None:
            p["last_price"] = str(px)
        total += p["qty"] * D(p["last_price"])
    return paise(total)


def fill_price(side, limit, open_, high, low, slippage):
    """Paper fill of a day limit order placed for the open, or None.

    Buy: at the open (plus slippage, capped at the limit) if the open is at
    or below the limit; at the limit if the day's low reached it. Sell
    mirrors it. A stock that did not trade, or never reached the limit,
    does not fill.
    """
    if open_ is None or high is None or low is None:
        return None
    if side == "buy":
        if open_ <= limit:
            return min(open_ * (1 + slippage), limit)
        return limit if low <= limit else None
    if open_ >= limit:
        return max(open_ * (1 - slippage), limit)
    return limit if high >= limit else None


def buy_cost_fraction(rates=None):
    """Buy-side charges as a fraction of trade value (STT, stamp duty, exchange
    and SEBI fees, GST on fees), from core/charges.json."""
    r = rates or charges_mod.load_rates()
    st, ex = r["statutory"], r["exchange_txn_pct"]
    fees = D(ex["NSE"]) / 100 + D(st["sebi_fee_per_crore"]) / Decimal(10 ** 7)
    return (D(st["stt_pct"]["delivery"]["buy"]) + D(st["stamp_pct"]["delivery"]["buy"])) / 100 + fees * (
        1 + D(st["gst_pct"]) / 100)


def fill_pending(ledger, day, bars, slippage, broker="zerodha"):
    """Fill the pending orders on `day` from bars {isin: (open, high, low)} in rupees.

    Sells first, so their proceeds fund the buys; a buy larger than the
    cash left is cut to the whole shares the cash covers. Returns the fills.
    """
    pend = ledger.get("pending")
    if not pend:
        return []
    slip = D(slippage)
    reserve = 1 + buy_cost_fraction()
    done, cash = [], D(ledger["cash"])
    orders = sorted(pend["orders"], key=lambda o: o["side"] != "sell")
    for o in orders:
        isin, side, qty, limit = o["isin"], o["side"], int(o["qty"]), D(o["limit"])
        b = bars.get(isin)
        px = fill_price(side, limit, *(D(x) if x is not None else None for x in b), slippage=slip) if b else None
        if px is None:
            ledger["unfilled"].append(dict(o, date=day, reason="did not trade at or inside the limit"))
            continue
        px = px.quantize(Decimal("0.01"))
        if side == "buy":
            per_share = px * reserve  # leave room for the buy-side charges
            affordable = int((cash / per_share).to_integral_value(rounding=ROUND_DOWN))
            if affordable < qty:
                qty = affordable
            if qty <= 0:
                ledger["unfilled"].append(dict(o, date=day, reason="not enough cash"))
                continue
        else:
            held = ledger["positions"].get(isin, {}).get("qty", 0)
            qty = min(qty, held)
            if qty <= 0:
                continue
        note = charges_mod.contract_note([{"date": day, "symbol": isin, "side": side, "qty": qty, "price": px}],
                                         broker)[day]
        cost = note["total"]
        if side == "buy":
            cash -= qty * px + cost
            p = ledger["positions"].setdefault(isin, {"symbol": o["symbol"], "qty": 0, "cost": "0"})
            p["qty"] += qty
            p["cost"] = str(D(p["cost"]) + qty * px + cost)
            p["last_price"] = str(px)
        else:
            cash += qty * px - cost
            p = ledger["positions"][isin]
            p["cost"] = str(D(p["cost"]) * (p["qty"] - qty) / p["qty"]) if p["qty"] else "0"
            p["qty"] -= qty
            if p["qty"] == 0:
                del ledger["positions"][isin]
        fill = {"date": day, "isin": isin, "symbol": o["symbol"], "side": side, "qty": qty, "price": str(px),
                "charges": str(paise(cost))}
        ledger["fills"].append(fill)
        done.append(fill)
    ledger["cash"] = str(paise(cash))
    ledger["pending"] = None
    return done


def orders_for(ledger, targets, nav, prices, ticks, top, names, trim_band=Decimal("0.02"), band=Decimal("0.01")):
    """The order sheet: move the paper portfolio to equal weight in `targets`.

    targets: ISINs to hold; prices: {isin: Decimal close}; ticks: {isin: Decimal};
    names: {isin: symbol}.
    Buys carry a limit `band` above the close and sells `band` below, on the
    instrument's tick. Changes under trim_band of the target are skipped
    (not worth their costs), as in the backtest.
    """
    from money import round_to_tick, whole_shares
    want = {i: paise(nav / top) for i in targets}
    out = []
    for isin, p in sorted(ledger["positions"].items()):
        if isin not in want:
            out.append({"isin": isin, "symbol": p["symbol"], "side": "sell", "qty": p["qty"], "reason": "left the top %d" % top})
    for isin in targets:
        px = prices[isin]
        held = ledger["positions"].get(isin, {}).get("qty", 0)
        target_qty = whole_shares(want[isin], px)
        diff = target_qty - held
        if held and abs(diff) * px <= want[isin] * trim_band:
            continue
        if diff > 0:
            out.append({"isin": isin, "symbol": names[isin], "side": "buy", "qty": diff,
                        "reason": "new" if not held else "top up"})
        elif diff < 0:
            out.append({"isin": isin, "symbol": names[isin], "side": "sell", "qty": -diff,
                        "reason": "trim to equal weight"})
    for o in out:
        px, tick = prices[o["isin"]], ticks[o["isin"]]
        o["ref_price"] = str(px)
        if o["side"] == "buy":
            o["limit"] = str(round_to_tick(px * (1 + band), tick, "down"))
        else:
            o["limit"] = str(round_to_tick(px * (1 - band), tick, "up"))
    return out


def today_iso():
    return dt.date.today().isoformat()
