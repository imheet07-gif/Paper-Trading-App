"""HTTP API + static UI. Demo mode: simulated prices, name-only sign-up.

Real auth and broker-backed data sources come later; accounts live in memory.
"""
from __future__ import annotations

import asyncio
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..accounts import Accounts, DataMode
from ..data import QuoteSource
from ..engine import EngineConfig
from ..models import D, Leg, Order, OrderStatus, OrderType, Side
from ..sim import UNDERLYINGS, SimFeed, build_instruments, next_expiries

STATIC = Path(__file__).parent / "static"
NAME_RE = re.compile(r"^[A-Za-z0-9_ .-]{2,24}$")


def f(x) -> float | None:
    return None if x is None else float(x)


class LegIn(BaseModel):
    symbol: str
    side: Side
    lots: int = Field(gt=0, le=1000)
    limit_price: Decimal | None = None


class OrderIn(BaseModel):
    legs: list[LegIn] = Field(min_length=1, max_length=8)
    order_type: OrderType
    strategy: str = Field(default="", max_length=32)


class LoginIn(BaseModel):
    name: str


def create_app(feed: QuoteSource | None = None, tick_seconds: float = 1.0) -> FastAPI:
    instruments = build_instruments(date.today())
    feed = feed or SimFeed(instruments)
    accounts = Accounts(instruments)
    tokens: dict[str, str] = {}  # session token -> account id

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def loop():
            while True:
                await asyncio.sleep(tick_seconds)
                accounts.tick(datetime.now())
        task = asyncio.create_task(loop())
        yield
        task.cancel()

    app = FastAPI(title="Paper Trader", lifespan=lifespan)
    app.state.accounts = accounts

    def me(pt_session: str | None = Cookie(default=None)) -> str:
        if not pt_session or pt_session not in tokens:
            raise HTTPException(401, "not signed in")
        return tokens[pt_session]

    # ---- helpers -------------------------------------------------------------
    def order_json(o: Order) -> dict:
        return {
            "id": o.id, "status": o.status.value, "reason": o.reason, "type": o.order_type.value,
            "strategy": o.strategy, "time": o.created.strftime("%H:%M:%S"),
            "legs": [{"symbol": l.symbol, "side": l.side.value, "lots": l.lots,
                      "limit_price": f(l.limit_price),
                      "fill_price": f(next((x.price for x in o.fills if x.symbol == l.symbol), None))}
                     for l in o.legs],
        }

    def view(acct_id: str):
        a = accounts.get(acct_id)
        clock, quotes = a.source.quotes_at(datetime.now())
        return a, clock, quotes

    # ---- auth ------------------------------------------------------------------
    @app.post("/api/login")
    async def login(body: LoginIn, response: Response, pt_session: str | None = Cookie(default=None)):
        name = body.name.strip()
        if not NAME_RE.match(name):
            raise HTTPException(422, "name must be 2-24 letters, digits, space, . _ -")
        existing = tokens.get(pt_session) if pt_session else None
        if existing == name:
            return {"name": name}
        try:
            accounts.get(name)
            raise HTTPException(409, "that name is taken")
        except KeyError:
            pass
        accounts.create(name, feed, DataMode.DEMO, EngineConfig())
        token = secrets.token_urlsafe(24)
        tokens[token] = name
        response.set_cookie("pt_session", token, httponly=True, samesite="lax",
                            max_age=60 * 60 * 24 * 30)
        return {"name": name}

    @app.get("/api/me")
    async def whoami(acct: str = Depends(me)):
        return {"name": acct}

    # ---- market data -----------------------------------------------------------
    @app.get("/api/indices")
    async def indices(acct: str = Depends(me)):
        now = datetime.now()
        return [{"name": n, "spot": f(SimFeed.spot(n, now)),
                 "change_pct": f((SimFeed.spot(n, now) / UNDERLYINGS[n][0] - 1) * 100)}
                for n in UNDERLYINGS]

    @app.get("/api/expiries")
    async def expiries(acct: str = Depends(me)):
        return [d.isoformat() for d in next_expiries(date.today())]

    @app.get("/api/chain")
    async def chain(underlying: str, expiry: date, acct: str = Depends(me)):
        if underlying not in UNDERLYINGS:
            raise HTTPException(404, "unknown underlying")
        _, _, quotes = view(acct)
        rows: dict[Decimal, dict] = {}
        lot = UNDERLYINGS[underlying][2]
        for inst in instruments.values():
            if inst.underlying != underlying or inst.expiry != expiry:
                continue
            q = quotes.get(inst.symbol)
            rows.setdefault(inst.strike, {"strike": f(inst.strike)})[inst.kind.lower()] = {
                "symbol": inst.symbol,
                "bid": f(q.bid) if q else None, "ask": f(q.ask) if q else None,
                "ltp": f(q.mid) if q else None}
        spot = SimFeed.spot(underlying, datetime.now())
        return {"underlying": underlying, "spot": f(spot), "lot_size": lot,
                "rows": [rows[k] for k in sorted(rows)]}

    # ---- trading ---------------------------------------------------------------
    @app.post("/api/orders")
    async def place(body: OrderIn, acct: str = Depends(me)):
        legs = [Leg(l.symbol, l.side, l.lots, l.limit_price) for l in body.legs]
        order = accounts.submit(acct, legs, body.order_type, datetime.now(), body.strategy.strip())
        return order_json(order)

    @app.get("/api/orders")
    async def orders(acct: str = Depends(me)):
        a = accounts.get(acct)
        return [order_json(o) for o in sorted(a.engine.orders.values(), key=lambda o: -o.id)]

    @app.delete("/api/orders/{order_id}")
    async def cancel(order_id: int, acct: str = Depends(me)):
        a = accounts.get(acct)
        if order_id not in a.engine.orders:
            raise HTTPException(404, "no such order")
        return order_json(a.engine.cancel(order_id, datetime.now()))

    @app.get("/api/positions")
    async def positions(acct: str = Depends(me)):
        a, _, quotes = view(acct)
        e = a.engine

        def row(pos, strategy=None):
            inst = instruments[pos.symbol]
            q = quotes.get(pos.symbol)
            ltp = q.mid if q else pos.avg_price
            return {"symbol": pos.symbol, "strategy": strategy, "qty": pos.net_qty,
                    "lots": pos.net_qty // inst.lot_size, "avg": f(pos.avg_price), "ltp": f(ltp),
                    "pnl": f(pos.realized_pnl + pos.net_qty * (ltp - pos.avg_price)),
                    "closed": pos.net_qty == 0}
        net = [row(p) for p in e.positions.values()]
        by_strategy = [row(p, t) for (t, _), p in e.strategy_positions.items()]
        return {"net": net, "by_strategy": by_strategy}

    @app.get("/api/funds")
    async def funds(acct: str = Depends(me)):
        a, clock, quotes = view(acct)
        snap = a.engine.mark(clock, quotes)
        margin = a.engine.margin_used()
        return {"cash": f(snap.cash), "margin_used": f(margin), "available": f(snap.cash - margin),
                "equity": f(snap.equity), "unrealized": f(snap.unrealized_pnl),
                "realized": f(snap.realized_pnl), "charges": f(snap.charges_paid),
                "net_pnl": f(snap.net_pnl), "starting_cash": f(a.engine.config.starting_cash)}

    @app.get("/api/strategies")
    async def strategies(acct: str = Depends(me)):
        a, _, quotes = view(acct)
        return [{"strategy": s.strategy, "realized": f(s.realized_pnl),
                 "unrealized": f(s.unrealized_pnl), "charges": f(s.charges),
                 "net_pnl": f(s.net_pnl), "open_positions": s.open_positions}
                for s in a.engine.strategy_report(quotes).values()]

    @app.get("/api/leaderboard")
    async def leaderboard(acct: str = Depends(me)):
        return [{"name": n, "net_pnl": f(s.net_pnl), "equity": f(s.equity)}
                for n, s in accounts.leaderboard(datetime.now())]

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    return app


def main() -> None:  # python -m papertrader.web.app
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
