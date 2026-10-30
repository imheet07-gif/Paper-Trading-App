# Proposal: short and long horizons, and the market data gaps

A suggestion from the owner, 2026-10-29, for the agents to discuss.
Nothing here is claimed, built or tested, and no shared file is changed.
Once the owner approves, tasks go into HANDOVER.md, and each strategy's
settings go into `research/FINDINGS.md` before any run.

## 1. What the owner asked for

1. **Beyond monthly.** Today everything tested is one horizon: rank at
   month end and hold a month. The owner wants three:
   - **long term:** finding value stocks to hold for years;
   - **short term:** trading on charts;
   - **monthly:** the existing work.
2. **Close the cheap gaps against a Bloomberg Terminal** that matter for
   one Indian equity investor:
   - institutional flows;
   - macro data;
   - a peer comparison table.

## 2. Three books, side by side

Each horizon is its own **book**. A book has its own:

- candidates;
- tests, under the same protocol;
- paper ledger and scorecard;
- tax treatment.

The owner decides how money is split between books. The books never
borrow evidence from each other: a stock passing the value screen says
nothing about whether it is a good swing trade.

| book | holding | decides on | data | status |
| --- | --- | --- | --- | --- |
| **Long term (value)** | 1 to 3 years or more | Fundamentals and valuation | Results XBRL (F3), prices | New (section 3) |
| **Monthly (momentum)** | About a month | `momentum_quality` ranking | Prices | Exists: validated, and ahead of the fund after tax (FINDINGS, 2026-10-29) |
| **Short term (charts)** | 2 to 20 sessions | Chart setups on daily bars | Daily OHLC, volume, delivery | New (section 4) |

**Tax differs by book, and the card says so.**

- **Long term:** held over 12 months, so gains are taxed at the long-term
  rate above the yearly limit (`core/tax_rates.json`).
- **Monthly:** almost all short term (FINDINGS after-tax check).
- **Short term:** frequent trading can be treated as business income, not
  capital gains. That is a question for the owner's chartered accountant,
  not something the project decides. The card flags it and the after-tax
  report shows both treatments.

## 3. Long-term book: finding value stocks

### 3.1 What it shows (context, no test needed)

- **Company page:**
  - ten-plus years of quarterly results from the store;
  - margins, ROE, interest coverage and growth (the F6 metrics);
  - each figure dated.
- **Valuation against its own history:** today's P/E, P/B and price to
  sales against the stock's own range since 2014, as percentiles. "Cheap
  for itself" is often more useful than cheap against the market.
- **Peer table (M3 below):** the same metrics for the company and its
  industry peers, side by side.
- **Ownership:** promoter, FII, DII and MF stakes and pledges over time
  (context, history from 2021).

### 3.2 What it tests

The F6 batch tests each metric with a monthly rebalance and the next
month's return. Value and quality pay off over years, so a monthly test
can miss them. The F6 entry already names "a quarterly-rebalance
version" as a later idea. This is that idea, made concrete. It is
pre-registered after F6's dev results are in, so nothing is chosen with
hindsight from inside F6.

- **Horizon:** 12-month forward returns, with a yearly rebalance.
- **Overlapping portfolios:** twelve staggered portfolios, one started
  each month and each held 12 months, are averaged. Without this, dev
  has only about six yearly decisions to learn from.
- **Candidates, fixed in advance:**
  - **value:** the F6 `f_value` composite;
  - **quality-value:** average rank of `f_quality` and `f_value`;
  - **an ROE version of the "magic formula":** earnings yield plus ROE,
    since ROCE needs debt, which is not available;
  - **cheap for itself:** P/E percentile against the stock's own history,
    lowest first, among stocks passing a quality floor
    (`f_profit_share_8q` at least 0.75).
- **Costs and tax:** real charges, and the after-tax report
  (`research/aftertax.py`) with long-term treatment.

**Honest limits.**

- Fundamentals start in 2013, so dev for a 12-month hold runs from about
  mid-2014 to 2020.
- Validation, 2021 to 2023, holds only three yearly cycles, and they
  were strong years for small and mid caps.
- The verdict will be weaker than for the monthly book. The forward paper
  ledger matters more here and needs years, not months.
- Better balance-sheet data (debt, cash flow) would allow fuller
  screens, such as a Piotroski-style score. Those wait for a free source.

## 4. Short-term book: trading on charts

### 4.1 What the evidence so far says

From FINDINGS, this week:

- Stops and targets at 2 ATR cut drawdowns but did not improve the
  Sharpe on validation at any risk level.
- Limit buys missed often: 247 times in 95 months, because strong stocks
  open more than 1% above the last close.
- A round trip costs about 0.3% in delivery trades. A swing setup needs
  an average edge per trade well above that.

Chart trading is therefore tested as setups with their own entries and
exits, not as stops added to the monthly book.

### 4.2 Data

- **Daily bars** (open, high, low, close, volume, delivery %) for every
  NSE stock since 2012 are already stored. That supports swing trades of
  2 to 20 sessions.
- **No intraday history.** NSE does not publish free intraday history.
  Intraday trading would need minute data, for example through the
  owner's Kite historical data add-on, read-only. It is out of scope
  until the owner decides it is worth paying for.

### 4.3 Charts in the app

- **A candlestick chart per stock:**
  - volume and delivery % below;
  - moving averages (20, 50 and 200 day), 52-week high and low;
  - corporate action ex-dates and results dates marked;
  - drawn as inline SVG from the store, in the app's existing style.
- **Setup markers:** past and current signals from the tested setups,
  with each setup's tested record next to it.
- **Alerts:** `trader-alerts` rings when a stock on the watchlist
  triggers a setup.

### 4.5 Plain-English screener (owner approved 2026-10-30)

The owner saw social posts of Claude driving TradingView with the mouse
to screen, chart and backtest. The useful part is asking for a screen in
plain words. That runs faster and more honestly on the local store than
by clicking a website.

- **What the owner types:** for example, "NSE stocks with RSI(14) below
  30, above their 200-day average, volume at least twice the 50-day
  average".
- **What Claude does:** turns it into a fixed, readable rule from a
  library of indicators computed from the stored daily bars. The library
  covers:
  - moving averages;
  - RSI;
  - ATR;
  - 52-week high and low;
  - volume and delivery ratios;
  - gaps;
  - the F6 fundamentals.
- **What the owner sees first:** the rule as it will run, before it runs.
- **How it runs:** over every eligible NSE stock as of the latest
  stored session, in seconds, with no website involved.
- **Output:**
  - matching stocks;
  - each one's chart (4.3) and card context;
  - the label "screen, untested".
- **Turning a screen into a strategy:** a screen the owner wants to trade
  becomes a setup. It is pre-registered and tested like those in 4.4, and
  until then it is never a pick.
- **Where it lives:**
  - the indicator library in a new `research/indicators.py`, shared with
    the setups;
  - `trader-screener` reading rules from it;
  - optionally a Screen tab in the app.
- **End-of-day only.** "RSI below 30 right now" during market hours needs
  live quotes (Kite, read-only), which is a later step.

### 4.6 Pine Script export, for viewing on TradingView (owner approved 2026-10-30)

For each setup that passes testing, and for any saved screen, generate a
matching TradingView Pine Script indicator. The owner can then see the
same signals on TradingView's charts.

- **One-way and for viewing only.** Nothing flows back from TradingView
  into the project's tests.
- **Built from the same rule text** as the project's own code, with a
  header that names:
  - the setup;
  - its pre-registration date;
  - its tested record;
  - and that it is untested if it is.
- **Known differences, listed in the header:**
  - TradingView's data, adjustments and holiday handling can differ from
    NSE's own files, so a signal can land a day apart or not at all;
  - TradingView's Strategy Tester leaves out delisted stocks and Indian
    charges unless set up for them. Its backtest numbers are not the
    project's and are never logged as a test.
- **No orders.** No alert or order hooks are generated. If the owner's
  TradingView account is linked to a broker, an indicator from this
  project must never trade through it (CLAUDE.md: nothing places an
  order). No Claude computer use or browser control is run against a
  broker-linked TradingView session.
- **Where it lives:** a new `research/pine.py`, with tests comparing its
  output against fixed samples.

### 4.4 Setups to test (each pre-registered on its own)

**The rules for each setup:**

- A signal on day t's close.
- Entry at day t+1's open, or skipped if the stock opens beyond a fixed
  gap.
- An exit by stop, target or time limit, checked on daily bars, with the
  stop counted first when one bar touches both (as in the exit test).
- Real charges and slippage.
- At most N open positions, so the book is a portfolio, not a list of
  trades.

| setup | signal, in outline (exact numbers fixed at pre-registration) |
| --- | --- |
| 52-week-high breakout | Close above the prior 52-week high on volume well above its 50-day average and delivery above its own normal |
| Pullback in an uptrend | Price above its 200-day average, 50-day above 200-day, and a pullback to near the 20-day average |
| Short-term oversold in an uptrend | Uptrend as above, and a 2- to 3-day drop to an extreme (an RSI(2)-style reading) |
| Volatility contraction breakout | The narrowest range of the last 7 sessions, then a close above that range |
| Post-results gap | A gap of more than a fixed % on the first session after results, held if it closes in the direction of the gap |

**The bar.** Each setup is judged on the stated bar, on dev and then
validation:

- average net return per trade after charges;
- hit rate;
- profit factor;
- the book's Sharpe against the equal-weight universe;
- the deflated Sharpe counting every setup tried.

Short-term tests produce thousands of trades, so the statistics are
firmer than for the monthly book. But many setups get tried, so the
count of attempts matters more. As with F6, the bar is a t-statistic
above 3, here on the mean net return per trade.

**Build needed.** An event backtester in `research/` (new file). It
reuses `panel.py`, `exits.py`, the charges and the run log, and adds
per-trade entries and exits within the month. The monthly backtester
cannot do this.

## 5. Market data gaps (from the Bloomberg comparison)

| id | work | source | note |
| --- | --- | --- | --- |
| M1 | FII and DII daily cash flows in the store and on the market pulse | NSE's daily FII/DII figures, with the API checked like the others | Collected each evening. History from NSE is short. A longer FPI history is published by NSDL, which is not on the cloud allow-list |
| M2 | Macro series: policy rate and bond yields, CPI inflation, IIP, GDP, GST collections | RBI (database and policy statements), the statistics ministry, the GST portal | Context only. Each value stored with its release time. These hosts are not on the cloud allow-list, so either the owner adds them or the fetch runs on the owner's computer |
| M3 | Peer table | The store: F3 results, prices, NSE industry list | Display work. The F6 metrics for a company and its industry peers, sortable |

All three are free. M1 and M2 feed the market pulse as description only;
timing filters failed validation and nothing changes an order on them.
M1 history could later be tested as a signal, once enough has been
collected.

## 6. Suggested order

| step | work | touches |
| --- | --- | --- |
| 1 | M3 peer table and the valuation-against-own-history view | new `research/` module, card or app tab |
| 2 | Candlestick chart in the app | new static file, a line in `app/` |
| 2a | Indicator library and the plain-English screener (4.5) | new `research/indicators.py`, `trader-screener/` |
| 3 | M1 FII and DII flows | new `core/` module, a line in the scan's update |
| 4 | Event backtester | new `research/` file |
| 5 | Short-term setups, pre-registered and run on dev | `research/`, FINDINGS |
| 5a | Pine Script export for setups that pass, and for saved screens (4.6) | new `research/pine.py` |
| 6 | Long-term tests, pre-registered after F6's dev results | `research/`, FINDINGS |
| 7 | M2 macro series | new `core/` module; hosts decided first |

Steps 1 to 3 and 2a are cheap and useful on their own. Steps 4 and 5 are the
main build for the short-term book.

## 7. Decisions for the owner

Made by the owner, 2026-10-30: add the plain-English screener (4.5) and
the Pine Script export (4.6).

Still open:

1. Approve the three books and the order above, or change it.
2. Approve the five short-term setups as the first batch, or add or
   strike setups before pre-registration.
3. Approve the four long-term candidates, pre-registered after F6's dev
   results.
4. For M2: add the RBI, statistics ministry and GST hosts to the cloud
   allow-list, or run that fetch on your computer.
5. Intraday data (paid, through Kite): leave out of scope for now?
