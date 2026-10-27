# India Trader

Claude skills for Indian equities (NSE and BSE), built on
[trader-skill](https://github.com/Jakeschincariol/trader-skill) by Jake
Schincariol (MIT). The goal: analyse and optimise an existing portfolio,
generate signals that have earned trust on unseen data, and never place an
order without your explicit yes.

**Status: Phase 0 (foundations).** The eleven `trader-*` skills are still
the US originals, kept as the starting point for the port. They are
documented in [docs/trader-skill-README.md](docs/trader-skill-README.md).
The India-specific work so far lives in `core/`.

## What works today

| tool | what it does |
| --- | --- |
| `core/nse_calendar.py` | NSE clock in IST: pre-open, normal market, closing session, 2026 holidays, T+1 settlement dates. A year without a published holiday list is an error, never a guess. |
| `core/money.py` | Rupee formatting with Indian grouping (1,23,45,678.90), lakh/crore shorthand, whole-share sizing, tick-size rounding. Decimal throughout. |
| `core/charges.py` | Every charge on a cash-market trade (brokerage, STT, exchange fee, SEBI fee, stamp duty, GST, DP charge) for Zerodha and Angel One. Rates live in `core/charges.json` with their source; output says UNVERIFIED until matched to a real contract note. |
| `core/instruments.py` | One record per stock keyed by ISIN, joining NSE's equity list with Kite and Angel One instrument tokens, tick and lot sizes. Mismatches between sources are reported. |

```bash
python3 core/nse_calendar.py clock
python3 core/money.py 12345678.9
python3 core/charges.py --broker zerodha --round-trip --qty 10 --price 2875.40
python3 core/instruments.py build --nse EQUITY_L.csv --kite instruments.csv --angel OpenAPIScripMaster.json
python3 -m unittest discover -s tests
```

Standard-library Python 3.8+, nothing to install. The analytics layer
(Phase 1) will add numpy and pandas.

## The plan

| phase | work |
| --- | --- |
| 0 Foundations | Instruments, calendar, rupees and charges, holdings import (Kite, Angel One, tradebook, CAS), paper engine |
| 1 Portfolio X-ray and optimiser | Look-through of mutual funds, concentration, risk contribution, cost- and tax-aware optimiser |
| 2 Core skill ports | budget, risk, broker, tax (Indian FY, STCG/LTCG, FIFO), alerts, rebalance |
| 3 India-native intelligence | Mutual fund portfolios, shareholding patterns, insider (SEBI PIT) and pledge disclosures, bulk and block deals, exchange announcements, red flags |
| 4 Data warehouse and backtester | Survivorship-free bhavcopy history, point-in-time data, Indian costs |
| 5 Signal research | Factor and event signals that pass out-of-sample validation, evidence cards |
| 6 Paper forward test | 3 to 6 months paper before any live money |

## Your data stays out of the repo

Holdings, tradebooks, CAS statements, contract notes and broker keys go in
`private/` (git-ignored) on your own machine. Broker keys live in
environment variables or your MCP client config, never in the chat and
never in a file in this repo.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. You can lose money. Decide for yourself, and talk to a
SEBI-registered adviser if you need advice. Sharing these signals with
others as recommendations would need SEBI Research Analyst registration;
this project is for personal use.

**No trade goes through until you say yes**, and paper trading is the
default. Charges and tax figures are estimates until verified against your
own contract notes and a chartered accountant.

MIT. Original pack by [Jake Schincariol](https://opusjake.ai); India edition
by imheet07-gif.
