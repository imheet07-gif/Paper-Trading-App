from datetime import date

import pytest
from fastapi.testclient import TestClient

from papertrader.web.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app(tick_seconds=0.05)) as c:
        yield c


def signed_in(client, name="heet"):
    assert client.post("/api/login", json={"name": name}).status_code == 200
    return client


def test_requires_login_and_names_cannot_be_hijacked(client):
    assert client.get("/api/funds").status_code == 401
    signed_in(client, "heet")
    with TestClient(client.app) as other:
        assert other.post("/api/login", json={"name": "heet"}).status_code == 409
    assert client.post("/api/login", json={"name": "!"}).status_code == 422


def test_chain_then_market_order_with_strategy_tag(client):
    signed_in(client)
    exp = client.get("/api/expiries").json()[0]
    ch = client.get("/api/chain", params={"underlying": "NIFTY", "expiry": exp}).json()
    assert ch["lot_size"] == 75 and len(ch["rows"]) == 41
    sym = ch["rows"][20]["ce"]["symbol"]
    o = client.post("/api/orders", json={"legs": [{"symbol": sym, "side": "BUY", "lots": 1}],
                                         "order_type": "MARKET", "strategy": "straddle"}).json()
    assert o["status"] == "FILLED" and o["strategy"] == "straddle"
    pos = client.get("/api/positions").json()
    assert pos["net"][0]["lots"] == 1 and pos["by_strategy"][0]["strategy"] == "straddle"
    strat = client.get("/api/strategies").json()
    assert strat[0]["strategy"] == "straddle" and strat[0]["charges"] > 0
    funds = client.get("/api/funds").json()
    assert funds["cash"] < funds["starting_cash"]
    assert client.get("/api/leaderboard").json()[0]["name"] == "heet"


def test_resting_limit_order_can_be_cancelled(client):
    signed_in(client)
    exp = client.get("/api/expiries").json()[0]
    sym = client.get("/api/chain", params={"underlying": "NIFTY", "expiry": exp}
                     ).json()["rows"][20]["ce"]["symbol"]
    o = client.post("/api/orders", json={"legs": [{"symbol": sym, "side": "BUY", "lots": 1,
                                                   "limit_price": "0.05"}],
                                         "order_type": "LIMIT"}).json()
    assert o["status"] == "OPEN"
    assert client.delete(f"/api/orders/{o['id']}").json()["status"] == "CANCELLED"
    assert client.delete("/api/orders/999").status_code == 404


def test_bad_orders_are_rejected_not_crashed(client):
    signed_in(client)
    r = client.post("/api/orders", json={"legs": [{"symbol": "NOPE", "side": "BUY", "lots": 1}],
                                         "order_type": "MARKET"}).json()
    assert r["status"] == "REJECTED" and "unknown" in r["reason"]
    assert client.post("/api/orders", json={"legs": [], "order_type": "MARKET"}).status_code == 422
