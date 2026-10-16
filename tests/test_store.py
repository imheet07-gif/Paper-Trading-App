import json
from datetime import date, datetime
from decimal import Decimal as D

from papertrader import Engine, EngineConfig, Instrument, Leg, OrderType, Quote, Side
from papertrader.store import SESSION_TTL, Store

NOW = datetime(2026, 10, 20, 10)
INST = Instrument("X", "NIFTY", "CE", 75, D("0.05"), D(24000), date(2026, 10, 27))


def test_password_and_session_lifecycle():
    s = Store()
    u = s.create_user("Heet", "pw-long-enough")
    assert s.check_password("heet", "pw-long-enough") == u
    assert s.check_password("heet", "nope") is None and s.check_password("ghost", "x") is None
    tok = s.new_session(u, now=1000)
    assert s.user_for_session(tok, now=1001) == u
    assert s.user_for_session(tok, now=1000 + SESSION_TTL + 1) is None
    s.end_session(tok)
    assert s.user_for_session(tok, now=1001) is None
    raw = s.db.execute("SELECT token_hash FROM sessions").fetchall()
    assert raw == [] and tok not in str(s.db.execute("SELECT * FROM users").fetchall())


def test_engine_state_roundtrip_is_exact():
    e = Engine({"X": INST}, EngineConfig())
    q = {"X": Quote("X", NOW, D("100"), D("100.5"), D("100"))}
    e.submit([Leg("X", Side.BUY, 2)], OrderType.MARKET, NOW, q, strategy="a")
    e.submit([Leg("X", Side.SELL, 1, D("120"))], OrderType.LIMIT, NOW, q, strategy="b")
    state = e.to_state()
    e2 = Engine({"X": INST}, EngineConfig())
    e2.load_state(json.loads(json.dumps(state)))  # through JSON, as the store does
    assert e2.to_state() == state
    assert e2.cash == e.cash and e2.positions["X"].net_qty == 150
    e2.on_quotes(NOW, {"X": Quote("X", NOW, D("121"), D("121.5"), D("121"))})  # resting order still works
    assert e2.orders[2].status.value == "FILLED"


def test_instruments_roundtrip():
    s = Store()
    s.save_instruments({"X": INST})
    assert s.load_instruments() == {"X": INST}
