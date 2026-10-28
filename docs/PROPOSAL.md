# Proposal: from one strategy to a trading desk

A suggestion from the owner, 2026-10-28, for the agents working on the app
to discuss. Nothing here is claimed, built or tested. It does not change
HANDOVER.md, the app or any shared file. Once agreed, tasks are claimed in
HANDOVER.md the usual way, and any strategy settings are copied unchanged
into `research/FINDINGS.md` as a pre-registration before any run.

## 1. The problem

The owner wants the time spent on this project to give useful results
every day, beyond testing one strategy. Today the app shows only the
research side: Update, Scan, the Strategy lab, the paper portfolio and
the runs log. The validated output is one monthly momentum list. That
list is close to what Indian momentum index funds already offer, such as
those tracking Nifty 200 Momentum 30 and Nifty 500 Momentum 50.

The original trader-skill was a toolkit of eleven tools, each giving
something usable, and was not built around one strategy. Most of those
tools are already ported here, but they are reachable only as skills in a
chat, not in the app.

## 2. What exists today

| tool | gives the owner | state |
| --- | --- | --- |
| trader-copycat | Named investors' adds and cuts from quarterly shareholding filings (holders above 1%), and bulk and block deals: the Indian counterpart of copying a 13F | ported, not in the app |
| trader-news | NSE announcements on holdings | ported, not in the app |
| trader-alerts | Price targets and stops on the NSE calendar | ported, not in the app |
| trader-screener | Ranks stocks against the owner's own rules | ported, not in the app |
| trader-budget, trader-risk, trader-broker | Any idea turned into sized, checked orders, then a paper fill after the owner's yes | ported, not in the app |
| trader-rebalance | Back to target weights | ported, not in the app |
| trader-analyst | Results, margins and growth per company | US original. India data in `core/financials.py` to February 2025 |
| trader-tax | Lots, gains, loss harvesting | US original. FIFO lots in `core/lots.py` |
| trader-pelosi | Congress trades | No India equivalent. Nearest: live SAST Regulation 29 promoter dealings in `core/events.py` |
| dashboard | Holdings, realized gains by financial year, charges | built, separate HTML page |

## 3. The proposal: one app, six tabs

1. **Portfolio.** The owner's real holdings from a broker export or CAS,
   P&L, realized gains by financial year and charges (the dashboard,
   brought into the app).
2. **Ideas.** Several sources side by side, each labelled with its
   evidence:
   - **Tested:** the validated strategy lists (today `momentum_quality_top20`).
   - **Screener:** the owner's own rules, for example earnings growing
     and price near its 52-week high.
   - **Copycat:** what named investors added or cut in the latest quarter.
   - **Big money:** today's bulk and block deals, and promoter buying and
     selling (SAST).
   - **Results:** who reported this week and what changed.
3. **Watch.** News and price alerts on holdings and on ideas the owner
   follows.
4. **Act.** Pick any idea, size it (budget), check it (risk), and get an
   order sheet or a paper fill. Nothing places an order (CLAUDE.md).
5. **Market pulse.** Described in section 5.
6. **Lab.** The current research tab, unchanged.

### Give every idea source a scorecard

This is the part that keeps the project honest while widening it.

- Each source in Ideas gets its own paper ledger from the day it is
  switched on. Its picks are recorded at the next open, with real charges,
  and tracked against the Nifty 500.
- Copycat, deals and SAST have no usable history (NSE publishes deals for
  the latest session only, and shareholding history starts in September
  2021), so they cannot be backtested. A forward scorecard is the only
  honest test available.
- After a few months the owner can see which sources earn their place.
- The rules for turning a source's picks into a ledger (which picks, how
  many, held how long) are written down before the source is switched on,
  the same as a pre-registration.

## 4. Daily brief and trades of the day

A one-page evening summary for the next morning's open. Data is end of
day, so nothing is intraday.

**Trades of the day** can come from only three places:

1. **Rebalance order sheets** of validated strategies, on their rebalance
   day.
2. **Forced exits**, when a holding:
   - moves to the trade-for-trade or surveillance segment;
   - has a delisting or suspension announced;
   - has a corporate action that fails its factor check.
3. **Ideas the owner chooses to act on** from the Ideas tab. These are
   labelled as the owner's choice, not a signal.

Otherwise the brief says "no trades today". A round trip costs about 0.3%
here, and nothing tested so far has an edge over a few days. Stop-losses
and profit targets on a tested strategy are new strategies and need
testing first.

## 5. Market pulse

Each line shows today's number and its percentile over the past year.
Everything comes from data the store already holds unless marked
**needs a feed**. The pulse is description only: timing filters failed
validation in 2022 (FINDINGS.md), so it never changes an order.

| line | source |
| --- | --- |
| Nifty 50 and Nifty 500: day, month and year change; distance from the 50- and 200-day averages | index closes |
| India VIX and its percentile | index closes |
| Advances against declines; share of stocks above their 200-day average; new 52-week highs against lows | bhavcopy |
| Market-wide delivery % against its one-year normal | bhavcopy |
| Strongest and weakest five industries by one-month and 12-1 momentum | bhavcopy, NSE industry list |
| Is momentum working: one-month return of the top fifth of `momentum_quality` minus the bottom fifth | bhavcopy |
| Stocks locked at circuit; stocks newly in trade-for-trade | bhavcopy |
| Bulk and block deals over Rs 10 crore | `deals` |
| Results and ex-dates in the next five sessions | events, corporate actions |
| Mutual fund, FII and DII stake changes of 1 point or more this quarter; pledge flags newly on | `shareholding_detail` |
| FII and DII daily cash flows | **needs a feed** |

## 6. Research, continued in the Lab

The protocol is unchanged (CLAUDE.md, HANDOVER.md):

- **Dev, 2013 to 2020.**
- **Validation, 2021 to 2023:** one look, with the settings unchanged.
- **Holdout, 2024 on:** owner only.
- **Paper trading for 3 to 6 months** before real money.
- **Every run logged**, and any change counts as a new strategy.

**First, an honesty check on what is already validated.**

- The backtester counts charges and slippage but not capital gains tax.
- A monthly top 20 realizes mostly short-term gains. Those are taxed at
  20%. An index fund's internal trading is not taxed in the holder's
  hands.
- Proposed fix:
  - add tax to the backtest report;
  - compare `momentum_quality_top20` after tax with the published returns
    of a Nifty momentum index over the same years.

  If it does not beat the index fund after tax, the fund is the better
  way to own momentum.
- Tax rates go into a data file with their source line, read from the
  Income Tax Department's own text, per CLAUDE.md.

**Candidate tests (at most three, settings to be pre-registered):**

- **A1 `earnings_surprise`:**
  - **Score:** latest quarter's diluted EPS minus the same quarter a year
    earlier, divided by the standard deviation of that change over the
    previous 8 quarters.
  - **Needs:** at least 9 quarters of results.
  - **Usable:** from the session after `broadcast_at`, and stale after 120
    days.
  - **Data:** 2013 to February 2025.
  - **Live gap:** can run live only after the post-March 2025 XBRL is
    found.
- **A2 `momentum_earnings`:** the average of the percentile ranks of
  `momentum_quality` and `earnings_surprise`, 50/50.
- **B, a low-volatility sleeve:**
  - **Allocation:** half the capital in `momentum_quality` top 20 and half
    in `low_volatility` top 20, run as two portfolios, not a blended score.
  - **Build needed:** a two-sleeve backtest mode.

Shared settings: the existing universe filters, top 20, equal weight,
monthly rebalance, next-open fills, 15 bps slippage, real charges.

**Pass marks:**

- **Ranking:** information coefficient t-statistic of 2 or more on dev.
- **Standalone:** beats the equal-weight universe on Sharpe.
- **Diversifier:**
  - the 50/50 combination with `momentum_quality_top20` improves Sharpe or
    the worst drawdown;
  - its monthly returns correlate with momentum at below 0.7.
- **Deflated Sharpe:** reported, counting every logged run.

**Data limits found while writing this:**

- The stored results have no balance sheet, so ROE and leverage cannot be
  scored.
- Shareholding history starts in September 2021, so institutional
  accumulation and pledges cannot get a dev test. They appear in Ideas and
  the pulse as information.

## 7. Suggested order, and how to avoid conflicts

Most of the app work touches `app/server.py`, `app/static/` and
`research/jobs.py`, which more than one agent edits. To keep merges
small:

- Each tab gets its own new files: its own job module and its own static
  script. The shared files only gain a line to register it.
- One agent owns the app shell, meaning tab navigation and the shared
  layout. Others add tabs through that agent's registration point.

| step | work | touches |
| --- | --- | --- |
| 1 | Tab shell in the app (navigation only) | `app/` (one owner) |
| 2 | Ideas tab: copycat, screener, deals and SAST, labelled by evidence | new job and static files; the skills' CLIs |
| 3 | Act tab: budget, risk and paper fill from any idea | new job and static files |
| 4 | Scorecards: a paper ledger per idea source | `research/paper.py` or a new module |
| 5 | Market pulse and daily brief | new module, new tab |
| 6 | Portfolio tab from the dashboard | `dashboard/`, new tab |
| 7 | After-tax backtest and the momentum index comparison | `research/backtest.py`, a tax data file |
| 8 | Port trader-analyst and trader-tax to India | `trader-analyst/`, `trader-tax/` |
| 9 | Candidate tests A1, A2 and B | `research/` |
| 10 | Feeds: FII and DII flows; results XBRL since March 2025 | `core/` |

Steps 7 and 8 do not touch the app and can run in parallel with steps 1
to 6.

## 8. Decisions for the owner

1. The six-tab direction, or a smaller start: Ideas and Act first.
2. The forward scorecard per idea source, and how many picks each source
   puts into its ledger.
3. The three candidate tests as written, or struck before
   pre-registration.
4. Whether the FII and DII flow feed is worth building.
5. Which agent owns the app shell.
