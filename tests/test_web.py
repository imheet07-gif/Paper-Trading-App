import pytest
from fastapi.testclient import TestClient

from papertrader.web.app import create_app

PW = "correct-horse"


def client_for(**kw):
    return TestClient(create_app(tick_seconds=0.05, **kw))


@pytest.fixture
def client():
    with client_for() as c:
        yield c


def signed_in(client, name="heet"):
    assert client.post("/api/signup", json={"name": name, "password": PW}).status_code == 200
    return client


def first_call(client):
    exp = client.get("/api/expiries").json()[0]
    ch = client.get("/api/chain", params={"underlying": "NIFTY", "expiry": exp}).json()
    return ch, ch["rows"][20]["ce"]["symbol"]


def test_requires_login_and_validates_signup(client):
    assert client.get("/api/funds").status_code == 401
    assert client.post("/api/signup", json={"name": "!", "password": PW}).status_code == 422
    assert client.post("/api/signup", json={"name": "heet", "password": "short"}).status_code == 422
    signed_in(client, "Heet")
    with TestClient(client.app) as other:
        assert other.post("/api/signup", json={"name": "heet", "password": PW}).status_code == 409


def test_login_logout_and_generic_failure(client):
    signed_in(client)
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401
    bad_pw = client.post("/api/login", json={"name": "heet", "password": "wrongwrong"})
    no_user = client.post("/api/login", json={"name": "nobody", "password": "wrongwrong"})
    assert bad_pw.status_code == no_user.status_code == 401 and bad_pw.json() == no_user.json()
    assert client.post("/api/login", json={"name": "HEET", "password": PW}).status_code == 200
    assert client.get("/api/me").json() == {"name": "heet"}


def test_lockout_after_repeated_failures(client):
    signed_in(client)
    for _ in range(5):
        assert client.post("/api/login", json={"name": "heet", "password": "wrongwrong"}).status_code == 401
    assert client.post("/api/login", json={"name": "heet", "password": PW}).status_code == 429


def test_invite_code_gates_signup():
    with client_for(invite_code="s3cret") as c:
        assert c.post("/api/signup", json={"name": "amy", "password": PW}).status_code == 403
        assert c.post("/api/signup", json={"name": "amy", "password": PW, "invite": "nope"}).status_code == 403
        assert c.post("/api/signup", json={"name": "amy", "password": PW, "invite": "s3cret"}).status_code == 200


def test_cookie_flags_and_headers():
    with client_for(secure_cookies=True) as c:
        r = c.post("/api/signup", json={"name": "amy", "password": PW})
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        assert r.headers["x-frame-options"] == "DENY"
        assert "default-src 'self'" in r.headers["content-security-policy"]


def test_chain_then_market_order_with_strategy_tag(client):
    signed_in(client)
    ch, sym = first_call(client)
    assert ch["lot_size"] == 75 and len(ch["rows"]) == 41
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


def test_accounts_are_isolated_between_users(client):
    signed_in(client, "heet")
    _, sym = first_call(client)
    client.post("/api/orders", json={"legs": [{"symbol": sym, "side": "BUY", "lots": 1}],
                                     "order_type": "MARKET"})
    with TestClient(client.app) as friend:
        friend.post("/api/signup", json={"name": "amy", "password": PW})
        assert friend.get("/api/positions").json()["net"] == []
        assert friend.get("/api/orders").json() == []


def test_resting_limit_order_can_be_cancelled(client):
    signed_in(client)
    _, sym = first_call(client)
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


def test_state_survives_restart(tmp_path):
    db = str(tmp_path / "pt.db")
    with client_for(db_path=db) as c:
        signed_in(c)
        _, sym = first_call(c)
        c.post("/api/orders", json={"legs": [{"symbol": sym, "side": "BUY", "lots": 1}],
                                    "order_type": "MARKET", "strategy": "alpha"})
        before = c.get("/api/funds").json()["cash"]
    with client_for(db_path=db) as c2:  # fresh process, same database
        assert c2.post("/api/login", json={"name": "heet", "password": PW}).status_code == 200
        assert c2.get("/api/funds").json()["cash"] == before
        assert c2.get("/api/positions").json()["by_strategy"][0]["strategy"] == "alpha"
        assert [o["strategy"] for o in c2.get("/api/orders").json()] == ["alpha"]
