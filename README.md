# Paper Trading App

Paper trading for Indian index/stock options: broker-style web UI, simulated
account, realistic fills and charges. Multi-user, with strategy tags for
comparing approaches. No real orders are ever placed.

## Run the demo (simulated prices)

    pip install -e '.[dev]'
    python -m papertrader.web.app      # http://127.0.0.1:8000
    pytest

The demo uses a **simulated** market (`sim.py`), not real prices. Accounts live
in memory and sign-up is name-only; both are placeholders for real auth/storage.

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
