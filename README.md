# India Trader

Claude skills for Indian equities (NSE and BSE), built on
[trader-skill](https://github.com/Jakeschincariol/trader-skill) by Jake
Schincariol (MIT). The goal: analyse and optimise an existing portfolio and
generate signals that have earned trust on unseen data. It never places an
order: it writes order sheets, and you place the trades yourself.

For personal use today, built to become a Claude plugin anyone can install:
broker-neutral underneath, with importers per broker and the CAS statement
as the import that works for everyone.

**Status: building the signals engine.** Foundations are in place
(instruments, calendar, verified charges, broker and CAS imports, a
portfolio overview page). The main track now is the research layer: a
survivorship-free NSE history from 2012, corporate action adjustment, and
a backtester with a fixed validation protocol. The eleven `trader-*`
skills are still the US originals, documented in
[docs/trader-skill-README.md](docs/trader-skill-README.md).

## What works today

| tool | what it does |
| --- | --- |
| `core/nse_calendar.py` | NSE clock in IST: pre-open, normal market, closing session, T+1 settlement dates. Holidays come from NSE's own holiday file (`update-holidays` refreshes them each December); a year without one is an error, never a guess. |
| `core/money.py` | Rupee formatting with Indian grouping (1,23,45,678.90), lakh/crore shorthand, whole-share sizing, tick-size rounding. Decimal throughout. |
| `core/charges.py` | Every charge on a cash-market trade (brokerage, STT, exchange fee, SEBI fee, stamp duty, GST, DP charge) at Zerodha. Rates live in `core/charges.json` with their source; output says UNVERIFIED until matched to a real contract note. |
| `core/zerodha_console.py` | Reads Zerodha Console holdings and equity tradebook exports (.xlsx or .csv): stocks and mutual funds with Decimal quantities and prices, fills grouped into orders, and the expected charges for any day to check against its contract note. Flags zero-cost holdings (demergers), discrepant and pledged units. |
| `core/cas.py` | Reads a CAS PDF from NSDL, CDSL, CAMS or KFintech (via casparser, offline) into broker-neutral holdings, drops personal details, masks account numbers, and reconciles it against a broker's holdings export. Password from a prompt, never stored. |
| `core/lots.py` | FIFO lots from any broker's trades: realized gains by financial year, short or long term, with sells that have no recorded buy reported apart rather than given an invented cost. Open lots reconcile against holdings. |
| `dashboard/build.py` | Builds your portfolio dashboard as one self-contained HTML file: headline numbers, allocation, holdings with their checks, realized gains by year, modelled charges, and everything that needs a look. No network requests; opens offline in any browser, phone included. |
| `core/bhavcopy.py` | NSE daily prices and delivery data for every listed security (delisted ones included, so backtests have no survivorship bias) into a local SQLite store, from 2012, both bhavcopy formats. Prices in integer paise, exactly as published. |
| `core/corporate_actions.py` | NSE corporate actions turned into adjustment factors (bonus, split, consolidation, rights at theoretical ex-rights price, demergers from the price move) and checked against the actual ex-date move; unexplained jumps reported. |
| `research/backtest.py` | Backtests a signal on the NSE history with the rules that keep it honest: delisted stocks included, next-day-open fills, no trading into locked circuits, real charges and slippage, a point-in-time universe, and fixed dev / validation / holdout periods with every run logged (deflated Sharpe). numpy and pandas. |
| `research/scan.py` | The daily scan (v0.2, paper only). `update` fetches the latest NSE data (retrying files not published yet); `run` refuses stale data, ranks today's universe with a validated strategy, writes an evidence card per candidate (inputs behind the score, liquidity against the position, delivery, circuit hits, corporate actions, trade-for-trade days, red flags) and, on the last trading day of a month only, an order sheet. Writes `private/scan/DATE.html` and `-orders.csv`. Uses the backtest's own selection function, so it trades exactly what was validated. |
| `research/paper.py` | Paper ledger in exact rupees (`trader/paper.json`): fills the order sheet at the next open the way a day limit order would, with the verified charges. |
| `research/strategies.json` | The strategies the scan may run, with every parameter and the evidence behind them. |
| `research/regimes.py` | Market regime filters (index trend, breadth), with NSE index series joined across the 2015 renames (CNX 500 became Nifty 500). |
| `research/FINDINGS.md` | Dated record of every experiment, including the failures. |
| `research/signals.py` | Candidate signals: 12-1 and 6-1 momentum, 52-week high, low volatility, momentum per unit of volatility, and NSE delivery accumulation. None is trusted until it passes validation. |
| `core/instruments.py` | One record per stock keyed by ISIN, joining NSE's equity list with Kite instrument tokens, tick and lot sizes (Angel One's scrip list optional, for its free historical data). Mismatches between sources are reported. |

```bash
python3 core/nse_calendar.py clock
python3 core/money.py 12345678.9
python3 core/charges.py --broker zerodha --round-trip --qty 10 --price 2875.40
python3 core/instruments.py build --nse EQUITY_L.csv --kite instruments.csv --angel OpenAPIScripMaster.json
python3 core/zerodha_console.py holdings private/holdings.xlsx
python3 core/zerodha_console.py verify-charges private/tradebook-EQ.xlsx private/pnl-EQ.xlsx
python3 core/cas.py holdings private/cas.pdf --reconcile private/holdings.xlsx
python3 dashboard/build.py --holdings private/holdings.xlsx --tradebook private/tradebook-*.xlsx --pnl private/pnl.xlsx
python3 core/bhavcopy.py backfill --start 2012-01-01
python3 core/corporate_actions.py fetch --start 2012-01-01
python3 core/corporate_actions.py check
python3 research/backtest.py --signal momentum_12_1 --period dev
python3 research/scan.py update                       # each trading evening, after about 7 pm IST
python3 research/scan.py run --capital 1000000        # first run starts the paper ledger
python3 research/scan.py run                          # later runs
python3 -m unittest discover -s tests
```

Standard-library Python 3.8+ for `core/`. Reading a CAS PDF needs `pip
install casparser`; `research/` needs numpy and pandas.

## The plan

Signals first; the portfolio overview stays a side page because brokers
already do it well.

| step | work | status |
| --- | --- | --- |
| Foundations | Instruments, calendar, charges verified against a broker ledger, Zerodha and CAS imports, FIFO lots, overview page | done |
| Market history | NSE bhavcopy and delivery data from 2012, corporate action factors checked against prices, ISINs joined across splits | done |
| Backtester | Honest simulation and the dev / validation / holdout protocol | done |
| Price signals | Momentum, 52-week high, low volatility, delivery accumulation, regime filters | first pass done: momentum per unit of volatility validated, timing filters failed (research/FINDINGS.md) |
| Event signals | Results dates and earnings drift, insider (SEBI PIT) and promoter buying, pledges, bulk and block deals, mutual fund accumulation | next, alongside paper trading |
| Evidence cards and daily scan | Each candidate with its reasons, sources, scorecard and red flags, for manual review; order sheets only | v0.2 built: paper trading from the 2026-10-30 rebalance |
| Paper forward test | 3 to 6 months on paper before any real money | last |
| Later | Portfolio X-ray and optimiser, Indian tax module, more brokers | parked |

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

**Nothing here places an order.** The output is an order sheet; you
place each trade yourself in your broker. Broker order tools are denied in
`.claude/settings.json`, and should be set to Blocked in your Claude
connector settings too. Paper trading is the default. Charges and tax
figures are estimates until verified against your own contract notes and a
chartered accountant.

MIT. Original pack by [Jake Schincariol](https://opusjake.ai); India edition
by imheet07-gif.
