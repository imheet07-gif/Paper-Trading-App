"""HTTP API + static UI. Demo mode: simulated prices, name-only sign-up.

Real auth and broker-backed data sources come later; accounts live in memory.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime
from decimal import Decimal
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..accounts import Accounts, DataMode
from ..data import QuoteSource
from ..engine import EngineConfig
from ..journal import Journal
from ..store import Store
from ..models import D, Leg, Order, OrderStatus, OrderType, Side
from ..sim import UNDERLYINGS, SimFeed, build_instruments, next_expiries

log = logging.getLogger("papertrader")
STATIC = Path(__file__).parent / "static"
MAX_FAILS, LOCK_SECONDS = 5, 60
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
    name: str = Field(max_length=64)
    password: str = Field(max_length=128)


class SignupIn(LoginIn):
    invite: str = Field(default="", max_length=128)


def create_app(feed: QuoteSource | None = None, tick_seconds: float = 1.0, db_path: str = ":memory:",
               invite_code: str | None = None, secure_cookies: bool = False) -> FastAPI:
    store = Store(db_path)
    journal = Journal(conn=store.db, lock=store.lock)
    instruments = {**store.load_instruments(), **build_instruments(date.today())}
    store.save_instruments(instruments)
    feed = feed or SimFeed(instruments)
    accounts = Accounts(instruments, journal)
    names: dict[str, str] = {}
    for u in store.users():
        names[u.id] = u.name
        state = store.load_state(u.id)
        if state:
            accounts.restore(u.id, state, feed, DataMode.DEMO, EngineConfig())
        else:
            accounts.create(u.id, feed, DataMode.DEMO, EngineConfig())
    fails: dict[str, list[float]] = {}  # name -> [count, locked_until]

    def persist(uid: str) -> None:
        store.save_state(uid, accounts.get(uid).engine.to_state())

    def demo_settle(now: datetime) -> None:
        """Demo only: cash-settle expired contracts at the simulated spot. Real
        data must settle at the exchange's official price (bhavcopy)."""
        close = dtime(15, 30)
        due = [d for d in {i.expiry for i in instruments.values() if i.expiry}
               if datetime.combine(d, close) <= now]
        if not due:
            return
        prices = {u: SimFeed.spot(u, now) for u in UNDERLYINGS}
        for uid in accounts.ids():
            eng = accounts.get(uid).engine
            if eng.settle_expiry(max(due), prices, now):
                persist(uid)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def loop():
            while True:
                await asyncio.sleep(tick_seconds)
                try:
                    now = datetime.now()
                    for uid in accounts.tick(now):
                        persist(uid)
                    demo_settle(now)
                except Exception:  # keep the loop alive; one bad tick must not stop fills
                    log.exception("tick failed")
        task = asyncio.create_task(loop())
        yield
        task.cancel()

    app = FastAPI(title="Paper Trader", lifespan=lifespan)
    app.state.accounts = accounts
    app.state.store = store

    @app.middleware("http")
    async def security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
            " frame-ancestors 'none'")
        return resp

    def me(pt_session: str | None = Cookie(default=None)) -> str:
        user = store.user_for_session(pt_session) if pt_session else None
        if not user:
            raise HTTPException(401, "not signed in")
        return user.id

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

    def start_session(response: Response, user) -> None:
        response.set_cookie("pt_session", store.new_session(user), httponly=True, samesite="lax",
                            secure=secure_cookies, max_age=30 * 24 * 3600)

    # ---- auth ------------------------------------------------------------------
    @app.post("/api/signup")
    async def signup(body: SignupIn, response: Response):
        name = body.name.strip()
        if not NAME_RE.match(name):
            raise HTTPException(422, "name must be 2-24 letters, digits, space, . _ -")
        if len(body.password) < 8:
            raise HTTPException(422, "password must be at least 8 characters")
        if invite_code and not hmac.compare_digest(body.invite.encode(), invite_code.encode()):
            raise HTTPException(403, "invalid invite code")
        try:
            user = await asyncio.to_thread(store.create_user, name, body.password)
        except ValueError:
            raise HTTPException(409, "that name is taken") from None
        names[user.id] = user.name
        accounts.create(user.id, feed, DataMode.DEMO, EngineConfig())
        persist(user.id)
        start_session(response, user)
        return {"name": user.name}

    @app.post("/api/login")
    async def login(body: LoginIn, response: Response):
        key = body.name.strip().lower()
        rec = fails.get(key)
        if rec and rec[1] > time.time():
            raise HTTPException(429, "too many attempts; try again in a minute")
        user = await asyncio.to_thread(store.check_password, body.name.strip(), body.password)
        if not user:
            rec = fails.setdefault(key, [0, 0.0])
            rec[0] += 1
            if rec[0] >= MAX_FAILS:
                rec[0], rec[1] = 0, time.time() + LOCK_SECONDS
            raise HTTPException(401, "wrong name or password")
        fails.pop(key, None)
        start_session(response, user)
        return {"name": user.name}

    @app.post("/api/logout")
    async def logout(response: Response, pt_session: str | None = Cookie(default=None)):
        if pt_session:
            store.end_session(pt_session)
        response.delete_cookie("pt_session")
        return {}

    @app.get("/api/me")
    async def whoami(acct: str = Depends(me)):
        return {"name": names[acct]}

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
        persist(acct)
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
        out = order_json(a.engine.cancel(order_id, datetime.now()))
        persist(acct)
        return out

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
        return [{"name": names[n], "net_pnl": f(s.net_pnl), "equity": f(s.equity)}
                for n, s in accounts.leaderboard(datetime.now())]

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    return app


def main() -> None:  # python -m papertrader.web.app
    import uvicorn
    invite = os.environ.get("PT_INVITE_CODE") or None
    host = os.environ.get("PT_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not invite:
        raise SystemExit("Refusing to listen publicly without PT_INVITE_CODE (anyone could sign up).")
    app = create_app(db_path=os.environ.get("PT_DB", "papertrader.db"),
                     invite_code=invite,
                     secure_cookies=os.environ.get("PT_SECURE_COOKIES") == "1")
    uvicorn.run(app, host=host, port=int(os.environ.get("PT_PORT", "8000")))


if __name__ == "__main__":
    main()
