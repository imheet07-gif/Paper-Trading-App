# Paper Trading App

Paper trading for Indian index/stock options: broker-style web UI, simulated
account, realistic fills and charges. Multi-user, with strategy tags for
comparing approaches. No real orders are ever placed.

## Run the demo (simulated prices)

    pip install -e '.[dev]'
    python -m papertrader.web.app      # http://127.0.0.1:8000
    pytest

The demo uses a **simulated** market (`sim.py`), not real prices.

## Accounts and hosting

Users sign up with a name and password (scrypt-hashed; sessions are stored hashed).
Account state is saved to SQLite, so restarts keep everyone's trades. Settings:

| Variable | Meaning |
| --- | --- |
| `PT_DB` | database file (default `papertrader.db`) |
| `PT_INVITE_CODE` | required to sign up; **mandatory** if `PT_HOST` is not localhost |
| `PT_HOST` / `PT_PORT` | listen address (default `127.0.0.1:8000`) |
| `PT_SECURE_COOKIES=1` | set when serving over HTTPS (do this for any public host) |

Serve it behind an HTTPS reverse proxy before exposing it. Repeated failed logins
lock that name for a minute. Passwords cannot be recovered; there is no reset flow yet.

## Layout

- `engine.py`, `fills.py`, `charges.py`: pure paper-trading logic (no network, no clock)
- `accounts.py`: one engine per user over a shared, tagged journal
- `data.py`: `QuoteSource` interface, replay and delayed sources
- `web/`: FastAPI API and the single-page UI

## Before real use

- Charge rates in `charges.py` and margin figures are **placeholders**; verify them.
- Real data needs a broker adapter implementing `QuoteSource` (Upstox/Angel One).
- Do not share one person's broker-fed data with friends without confirming the
  provider's and NSE's redistribution terms; `allow_shared_delayed` is off by default.
