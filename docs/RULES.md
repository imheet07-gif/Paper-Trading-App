# Rules: the daily brief and the strategy research

Draft for the owner's review, 2026-10-27. Nothing here is built or tested
yet. Once agreed, each strategy's settings below are copied unchanged into
`research/FINDINGS.md` as a dated pre-registration before any run, and the
daily brief is built as a task in `HANDOVER.md`.

## 1. The goal, and what it means in practice

The owner wants two things each trading day:

1. **The trades of the day**: a short, ready-to-enter list, or a plain "no
   trades today".
2. **The pulse of the market**: an honest read of what the market did and
   what is changing underneath it.

Three facts shape how this works:

- **Data is end of day.** Prices arrive from NSE after about 7 pm IST. The
  brief is written in the evening for the next morning's open. Nothing
  here is intraday.
- **Trading every day is not supported by the evidence.** A round trip
  costs about 0.3% here (STT on both sides, stamp duty, exchange and SEBI
  fees, GST, DP charge, slippage). The one validated strategy holds for a
  month. On most days the correct output is "no trades today, here is what
  to watch". A daily trade list that is never empty would be overtrading.
- **The pulse informs and does not time.** Market-timing filters (Nifty 500
  against its 200-day average, breadth) failed validation in 2022
  (FINDINGS.md). The pulse tells you what is going on. It never switches
  the strategy in or out of the market.

## 2. How this differs from the original trader-skill

This project started from [trader-skill](https://github.com/Jakeschincariol/trader-skill)
(Jake Schincariol, MIT), but it now does something quite different.

| | original trader-skill (US) | this project (India) |
| --- | --- | --- |
| Idea | Follow people: copy a fund's 13F, track Congress members' trades (the "Pelosi" skill), watch the news on what you hold | Find rules that worked on data they were not fitted to, then paper-trade them |
| Evidence that it works | None. It copies holdings and does not test whether copying pays | Every signal is backtested from 2013, with fixed dev, validation and holdout periods and every attempt logged |
| Where trades come from | Whatever you choose to copy or screen for | Only strategies that passed validation (`research/strategies.json`) |
| Orders | Can send an order to a broker (Alpaca) after you type yes; paper account by default | Never sends an order. It writes an order sheet and you enter it by hand |
| Market view | None | The daily pulse below (to be built) |
| Data | SEC EDGAR, House disclosures, news RSS, Yahoo prices | NSE bhavcopy from 2012 including delisted stocks, corporate actions checked against prices, NSE filings stamped with their publication time |

What carried over: the safety pipeline (budget, then risk check, then
ticket, then your yes), the copycat idea (rebuilt for India from
shareholding filings and bulk and block deals), the news and alerts
skills, and "paper first".

What was dropped, because India has no equivalent: Congress trading
disclosures. The nearest Indian source, insider and promoter dealings
under SEBI PIT and SAST, was tested here and failed (FINDINGS.md).

## 3. The daily brief

One page, written each trading evening after `scan.py update`, readable on
a phone. It has four sections, in this order.

### 3.1 Trades of the day

The only things that can appear here:

1. **The rebalance order sheet**, on the last NSE trading day of each month,
   for each strategy in `strategies.json`. It lists buys and sells with
   whole-share quantities, estimated charges and an evidence card per buy.
   The trades are for the next open.
2. **Forced exits**, on any day, for something the strategy cannot hold
   through:
   - the stock moves to the trade-for-trade or surveillance segment;
   - a delisting or suspension is announced;
   - a corporate action fails its factor check, so its prices can no longer
     be trusted.

   These rules are not tested for return. They exist because the backtest
   cannot model the situation (`backtest.py` already treats delisting with a
   haircut). Each one is shown with its reason and NSE's source line.
3. **Otherwise: "No trades today."** Followed by the days left to the next
   rebalance and how the paper portfolio is doing against the Nifty 500.

Never here: stop-losses, profit targets, "buy the dip" or any other daily
rule. Each of those changes the strategy, so it is a new strategy that has
to pass dev and validation first (section 4).

### 3.2 Market pulse

Each line gives today's number and where it sits in its own one-year range
(percentile), so "high" and "low" mean something. Everything comes from
data the store already holds unless marked **needs a feed**.

| line | what it is | source |
| --- | --- | --- |
| Index levels | Nifty 50 and Nifty 500: day, month and year change; distance from the 50- and 200-day averages | index closes |
| Volatility | India VIX level and its one-year percentile | index closes (India VIX is in NSE's index file) |
| Breadth | Advances against declines today; share of eligible stocks above their 200-day average; new 52-week highs against new lows | bhavcopy |
| Conviction | Market-wide delivery %, against its one-year normal: are moves being held or squared off intraday? | bhavcopy delivery data |
| Leadership | The five strongest and five weakest industries by one-month and 12-1 momentum, equal-weighted within each industry | bhavcopy and NSE industry list |
| Is momentum working? | Return over the past month of the top fifth of `momentum_quality` minus the bottom fifth, with its one-year percentile. A sharp negative reading is the 2016/2022-style rotation that hurts the strategy | bhavcopy |
| Stress | Count of stocks locked at upper or lower circuit; stocks newly moved to trade-for-trade | bhavcopy |
| Big money today | Bulk and block deals over Rs 10 crore, buys and sells, with named counterparties | `deals` table |
| Coming up | Results announced for the next five sessions; corporate action ex-dates on holdings and candidates | events, corporate actions |
| This quarter's ownership shifts | Stocks where mutual funds, FIIs or DIIs changed their stake by 1 point or more in the latest shareholding pattern; promoter pledge flags newly switched on | `shareholding_detail` |
| FII and DII cash flows | Daily net buying by foreign and domestic institutions | **needs a feed** (NSE publishes it daily; not stored yet) |

The pulse is description only. It never changes an order.

### 3.3 Portfolio watch

For each paper (later, real) holding:

- the stock's rank in its strategy today, and the cut-off it must stay
  above at the next rebalance;
- any new red flag from the evidence card;
- results or a corporate action within five sessions;
- bulk or block deals in the stock today.

This is information for the next rebalance. It is not a trade.

### 3.4 Watchlist

The stocks just outside the top 20 (ranks 21 to 30) that would come in at
the next rebalance if nothing changed, with their evidence cards. These
give you time to read about a company before it reaches the order sheet.

## 4. Research rules for every new strategy

These rules already govern the project (CLAUDE.md, HANDOVER.md). They are
repeated here because they decide what can ever reach section 3.1.

1. **Write the rules before running anything.** Signal, universe, number
   of stocks, rebalance, weights and pass mark go into FINDINGS.md first.
2. **Dev (2013 to 2020) first.** It passes dev if:
   - the ranking works (information coefficient t-statistic of 2 or more);
   - for a standalone strategy: its Sharpe ratio beats the equal-weight
     universe;
   - for a diversifier: the 50/50 combination with `momentum_quality_top20`
     has a higher Sharpe or a shallower worst drawdown than momentum alone,
     and its monthly returns correlate with momentum at below 0.7.

   The deflated Sharpe, counting every logged dev run, is reported with
   the result.
3. **Validation (2021 to 2023): one look**, with the dev settings unchanged.
   If it fails, the idea is recorded as failed. It is not tuned and tried
   again.
4. **Holdout (2024 on)** opens only when the owner asks.
5. **Paper trading for 3 to 6 months** before real money.
6. **A change is a new strategy.** Top 30 instead of 20, a stop-loss, other
   weights: back to step 1.
7. **Every run is logged** in `research/runs.jsonl`, failures included.

A caution on validation: 2021 to 2023 was a very strong market for Indian
mid and small caps, so most long-only strategies look good there. Weigh the
comparison against the universe, not the absolute return.

## 5. The candidate strategies

At most three new dev tests are planned (A1, A2, B), to keep the count of
attempts honest.

All of them share these settings:

- **Universe:** the same as `momentum_quality_top20`: price at least Rs 20,
  median daily traded value at least Rs 2 crore over 60 days, at least 250
  sessions of history, trade-for-trade days excluded.
- **Trading:** top 20, equal weight, rebalanced on the last NSE trading day
  of the month and filled at the next open, 15 bps slippage, real charges,
  2% trim band.

### A. Earnings surprise (from the reported results)

**Why:** this is a fundamental signal, not a price signal, so it can
diversify momentum. Its stock-market counterpart, the price reaction on
results day, failed here, but that measured the share price's response.
This measures the business's own numbers.

**What it cannot be:** a balance-sheet quality score. The stored results
carry no equity, debt or assets, so ROE, leverage and asset turnover cannot
be computed. Adding them would need the balance-sheet XBRL, which is filed
half-yearly. That is a separate data task.

**A1. `earnings_surprise` (standalone)**

- **Quarterly change:** for each company, the latest quarter's diluted EPS
  minus the same quarter a year earlier. Consolidated figures if filed,
  otherwise standalone. EPS is put on today's share count using the
  stored corporate action factors.
- **Score:** that change divided by the standard deviation of the same
  year-on-year change over the previous 8 quarters (standardised unexpected
  earnings).
- **Needs at least 9 quarters** of results: 8 to measure the typical
  change, plus the current one.
- **Usable:** from the session after the results' `broadcast_at`. After 120
  calendar days the score is dropped as stale.
- **Rank:** highest score first.
- **Data:** results XBRL from 2013 to February 2025. That covers both dev
  and validation (dev effectively starts in 2015, once 9 quarters have
  built up).

**A2. `momentum_earnings` (composite)**

- **Score:** the average of the percentile ranks of `momentum_quality` and
  `earnings_surprise`, 50/50, fixed now.
- **Stocks without a usable earnings score** are not eligible.
- **Why:** momentum backed by improving earnings should reverse less often
  than momentum alone.

**Live gap for both:** NSE stopped linking results XBRL in March 2025, so
these can be backtested now but cannot run in the daily scan until the
newer filings are found. Proposed order: test on dev first, and only
chase the live source if A1 or A2 passes.

### B. Low volatility as a second sleeve

- Two portfolios side by side, rebalanced together: half the capital in
  `momentum_quality` top 20, half in `low_volatility` top 20.
- The two sleeves are run as separate portfolios. Their scores are not
  averaged; that blend, `momentum_lowvol`, failed validation with a timing
  filter.
- **Why:** low volatility had the shallowest drawdown on dev (-26% against
  -38%), and it tends to hold up in the rotations where momentum loses.
- **Build needed:** the backtester runs one portfolio at a time. It needs a
  two-sleeve mode before this can be tested.

### C. Not testable yet: institutional accumulation and promoter pledges

- **The obstacle:** NSE's shareholding history starts in September 2021
  (21 quarters for TCS). That falls entirely inside validation and holdout,
  so there is no dev period to research on.
- **Why not test anyway:** testing it on the validation years would use up
  the one look and leave nothing unseen.
- **Disposition:** shown in the pulse ("ownership shifts") and on evidence
  cards as information. Revisit it as a strategy only if older patterns are
  found (BSE holds them, but its API refuses scripts today), or after
  enough new quarters build up for a fresh dev and validation split.

### D. Information only: bulk and block deals, named investors

NSE publishes only the latest session, so there is no history to test
these on. They appear in the pulse and on evidence cards, never as a
reason to trade.

### Already tested and not adopted (FINDINGS.md)

- Market-timing filters (trend, breadth)
- Insider buying (SEBI PIT)
- Promoter open-market buying (SAST)
- Results-day price reaction
- Position-sizing variants (inverse volatility, cash buffer)

## 6. Order of work

| step | work | depends on |
| --- | --- | --- |
| 1 | Daily brief, sections 3.1, 3.3 and 3.4 (uses what the scan already computes) | nothing |
| 2 | Market pulse, every line except FII and DII flows | nothing |
| 3 | Pre-register A1, A2 and B in FINDINGS.md exactly as above, then run dev | owner's approval of this file |
| 4 | Two-sleeve backtest mode for B | step 3 |
| 5 | Validation look for whatever passed dev | step 3 or 4 |
| 6 | Live results XBRL since March 2025 (only if A1 or A2 passed) | step 5 |
| 7 | FII and DII daily flows feed | nothing; optional |

## 7. Decisions for the owner

1. Is "no trades today" on most days acceptable? The alternative, a daily
   strategy, would need its own research. It would start with a much
   higher bar because of the costs.
2. Approve A1, A2 and B as written, or strike any of them before
   pre-registration. Changing them after a run is not possible.
3. Is it worth building the FII and DII flow feed for the pulse?
4. Is the forced-exits list in 3.1 right? Is anything missing that you would
   always want to sell on?
