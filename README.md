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

## Live feed

`feed.py` provides `PolledSource`: give it a `fetch() -> {symbol: Quote}` function (a broker
adapter) and it polls on a background thread, backs off on errors, and reports
`live / stale / down / closed` in the UI. Stale quotes are refused by the engine, so a dead
feed can never fill an order at an old price. Index spots come from quotes keyed by the
underlying's name (`"NIFTY"`, `"BANKNIFTY"`). Market hours are by clock only (no holiday calendar).

## Phone access (running on your own laptop)

The app only needs to be reachable from your phone; it does not need public hosting.
The laptop must stay on, awake and online, or prices and limit orders pause.

1. **Same Wi-Fi (simplest):** `PT_HOST=0.0.0.0 PT_INVITE_CODE=<code> python -m papertrader.web.app`,
   then open `http://<laptop-ip>:8000` on the phone. Plain HTTP on your home network only; don't
   do this on shared or public Wi-Fi. Browsers may not offer "install" without HTTPS.
2. **Anywhere, privately (recommended):** keep the app on `127.0.0.1` and reach it through a
   private network or tunnel such as Tailscale, which can also give you HTTPS. Follow that
   tool's own instructions; set `PT_SECURE_COOKIES=1` when you use HTTPS.

After creating your account, set `PT_ALLOW_SIGNUP=0` so nobody else can register.
Then use "Add to Home Screen" in the phone browser for an app-style icon (a minimal service
worker makes it installable; it caches nothing).

## iOS home-screen widget (Scriptable)

`widgets/scriptable/PaperTrader.js` shows net P&L, index prices, feed status and your top
strategies. It needs the app to be reachable from the phone over HTTPS (see above).

1. Install the free Scriptable app, create a new script and paste in `PaperTrader.js`.
2. In PaperTrader: Funds, "Home-screen widget token". Copy the token (shown once).
3. Run the script in Scriptable; enter the app URL and token (stored in the iOS Keychain).
4. Add a Scriptable widget to the home screen and pick the script.

The token is read-only (it opens `/api/widget` and nothing else, so it cannot trade), only its
hash is stored, and you can revoke or rotate it from the same place. iOS decides when widgets
refresh (often 15+ minutes), so treat it as a glance, not live tracking. Scriptable is iOS-only.

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
