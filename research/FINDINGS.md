# Research findings

A dated record of what was tried, on which period, and what it showed.
Failures stay here too: the point is to remember them. Every run is also
in `runs.jsonl`. Periods: dev 2013-2020, validation 2021-2023, holdout
2024 onward (untouched).

Common setup unless noted: NSE stocks with an INE ISIN in the EQ series,
price at least Rs 20, 60-day median traded value at least Rs 2 crore, a
year of history, no failed corporate action check in the past year (median
403 stocks in dev, 765 in validation). Monthly rebalance at the close,
fills at the next open, top 20 equal weight, charges from
`core/charges.json`, 15 bps slippage a side, DP charges, delisted holdings
closed at a 50% haircut, uninvested cash at 6% a year.

## 2026-10-27: first candidates on dev

| signal | IC | t | top-20 CAGR | Sharpe | max DD |
| --- | --- | --- | --- | --- | --- |
| momentum_quality (12-1 momentum / 1y volatility) | 0.045 | 2.50 | 26.2% | 0.82 | -38.5% |
| momentum_6_1 | 0.031 | 2.09 | 17.8% | 0.50 | -49.1% |
| momentum_12_1 | 0.034 | 1.92 | 17.5% | 0.49 | -52.2% |
| low_volatility | 0.055 | 2.49 | 13.3% | 0.56 | -26.1% |
| high_52w | 0.051 | 2.31 | 10.7% | 0.29 | -51.5% |
| delivery_accumulation | 0.034 | 2.93 | 5.8% | 0.04 | -59.2% |
| equal-weight universe | | | 8.8% | 0.20 | -58.2% |

momentum_quality was robust on dev to top 10 / 30 and to 30 bps slippage
(Sharpe 0.68 to 0.81). Weak years: 2016 (-27% against 0%), 2013.

## 2026-10-27: validation, momentum_quality (chosen on dev, unchanged)

IC 0.056, t 2.74, positive in 71% of months, deciles 0.99 monotonic, top
decile 2.3% a month over the bottom. Top 20: CAGR 47.4% against 34.6% for
the universe, Sharpe 1.49 against 1.41, max drawdown -33.4% against -23.2%,
2022 -7% against +2%. **Ranking validated; the tradeable edge is in return,
not yet in risk.**

## 2026-10-27: regime filters and momentum + low volatility on dev

Filters: Nifty 500 above its 200-day average (else cash, or else 50%
invested); breadth (at least 40% of eligible stocks above their own
200-day average, else 50%). Combination: mean of the percentile ranks of
momentum_quality and low_volatility.

| signal | regime | CAGR | Sharpe | max DD |
| --- | --- | --- | --- | --- |
| momentum_quality | none | 26.2% | 0.82 | -38.5% |
| momentum_quality | trend, half | 23.1% | 0.79 | -31.0% |
| momentum_lowvol (IC 0.072, t 3.11) | none | 17.8% | 0.72 | -32.5% |
| momentum_lowvol | trend, cash | 17.9% | 0.93 | -16.8% |
| momentum_lowvol | trend, half | 18.0% | 0.87 | -18.1% |
| any | breadth | worse than no filter | | |

momentum_lowvol with the trend filter was robust on dev (Sharpe 0.79 to
0.93, drawdown -17% to -20% across top 10 / 30 and 30 bps).

## 2026-10-27: validation, momentum_lowvol + trend to cash (chosen on dev, unchanged)

**Failed.** CAGR 24.5% against 34.6% for the universe, Sharpe 1.18
against 1.41, max drawdown -24.4% against -23.2%, 2022 -12% against +2%.
The ranking still held (IC 0.063, t 2.58) but less orderly (deciles 0.50).
Likely cause: the Nifty 500 crossed its 200-day average repeatedly in
2022, so the filter sold and missed rebounds. Not tuned on validation; a
market-timing filter is not adopted. Drawdowns will be handled by position
sizing and diversifying signals instead.

## 2026-10-27: event signals on dev

Data: NSE insider disclosures (corporates-pit, 337,344 transactions,
complete 2016 to April 2026) and results filings (corporates-financial-results,
complete 2013 to 2024). Both keyed on the moment NSE published them, never
the trade or period date. Settings fixed before testing: promoters,
promoter group, directors and KMP; market purchases and sales only; 63
trading days; results reaction over the two sessions around the release,
against the equal-weight eligible universe.

| signal | period | IC | t | top-bot | CAGR | Sharpe | max DD | excess |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| insider_buying | dev_2016 | 0.023 | 1.32 | 0.93% | 12.8% | 0.36 | -63.8% | +4.1% |
| momentum_quality (same years) | dev_2016 | 0.032 | 1.56 | 1.40% | 20.0% | 0.60 | -38.6% | +11.3% |
| results_reaction | dev | 0.003 | 0.42 | 0.05% | 6.3% | 0.11 | -63.5% | -2.5% |

**Neither passes dev; no validation look is spent on them.**

- insider_buying ranks in the right direction but weakly (t 1.32), and a
  top 20 of the heaviest buyers draws down more than the market. Its top
  names were checked against the disclosures (Bajaj Holdings and Tanla
  promoters buying through spring 2019) and are real: the data is right,
  the edge is small. Much Indian promoter buying is creeping acquisition
  in already-controlled companies, which says little about the next month.
- results_reaction shows no drift at all in India's liquid universe over
  2013 to 2020 (deciles not ordered). The first run found 16% of results
  rows naming an ISIN retired years earlier by a split; after remapping
  them through the symbol the result was unchanged.

Not tried, to keep the variant count honest: buys only, other windows,
size thresholds, or combining either with momentum. Any of those is a new
pre-registered idea, not a tweak of these.

Live data: NSE's structured insider feed stops in May 2026 and the results
feed in February 2025, when SEBI's Integrated Filing took over; results now
arrive as "Integrated Filing- Financial" announcements. A signal built on
either would need a new live source before it could run in the scan (see
the survey below).

## 2026-10-27: where event data lives now (survey, no signal tested)

Broker APIs (Kite Connect, Angel One SmartAPI) carry prices, instruments
and the account only; filings come from the exchange. Checked against
NSE's own endpoints, whole months fetched in weekly chunks (the monthly
answer is not truncated: August 2026 is 21,026 rows either way).

- **Results.** `corporate-announcements` is live to the previous day.
  - August 2024 (old results feed complete): 1,011 of 1,473 results
    filings also appeared there as "Financial Result Updates". They
    appeared a median 72 minutes *before* the results feed's
    `broadCastDate` (p10 267 minutes, p90 7). The results signal tested
    above was therefore sometimes timed late. A delay that small cannot
    explain an IC of 0.003.
  - August 2026: that heading is gone. Results come as "Outcome of Board
    Meeting" announcements with the standard text "has submitted to the
    Exchange, the financial results for the period ended <date>" (1,607
    rows, 1,574 companies; 1,426 companies in August 2024). The period can
    be read from the text, and `an_dt` gives the second. "Integrated
    Filing- Financial" carries only a few dozen a month.
- **Insider trades (SEBI PIT).** `corporates-pit` answers but is empty
  after 2 May 2026, even unfiltered. The announcements feed holds only
  sporadic Regulation 7(2) filings (under "Structural Digital Database"),
  not the roughly 1,100 a month of 2025. No live replacement found.
- **Substantial holders (SAST Regulation 29).** `corporate-sast-reg29` is
  live (686 rows in September 2026) with history from at least January
  2016.
  - Its rows are filed when a holder crosses 5% or moves 2% or more.
  - It carries `promoterType`, `acquisitionMode` (open market,
    inter-se transfer, preferential allotment), `acqSaleType`, the
    percentages and a publication `timestamp`, and identifies stocks by
    symbol only, with no ISIN.
  - It records bigger, rarer moves than PIT, so a signal built on it
    (for example open-market promoter acquisitions) is a new idea needing
    its own dev test, not a feed for insider_buying.
- Not reachable: BSE's API (403). Guessed endpoint names (`corporates-sast-reg29`, `corporate-sast-reg31`, `corporates-pledgedata`) returned nothing.

## 2026-10-27: event data brought up to date

`core/events.py` now also stores results from the announcements feed
(January 2025 on) and SAST Regulation 29 filings (all years), and the app's
update refreshes every event source.

- **Results**, companies with a stamped release per quarter:
  1,996 to 2,132 for the five quarters to December 2024 (results feed);
  2,099 to 2,252 for each quarter from June 2025 to June 2026
  (announcements). September 2026 is filling now (356 so far).
  - **Gap:** the March 2025 quarter has 1,354. That season (April to May
    2025) was announced as plain "Outcome of Board Meeting held on ..."
    without the standard results wording or an XBRL flag (TCS, Infosys,
    HDFC Bank, Reliance). 534 other board outcomes that month were not
    results, so the heading alone cannot be trusted. Left as a known gap
    rather than guessed.
  - Where both sources cover a release (January to February 2025, 4,515
    pairs), the announcement came first, a median 177 minutes before the
    results feed's stamp. The earliest is kept.
- **SAST Regulation 29**: 52,151 filings from 2015. NSE spells the mode
  as both "Open market" and "Open Market" (2022 is almost all lower
  case); the store normalises it. Promoter open-market acquisitions per
  year: 527 (2015), 673, 640, 1,459, 1,917, 1,908, 1,158, 1,347, 984,
  546 (2024), 1,446 (2025), 1,465 (2026 to date).
- **Insider trades** stay at 2 May 2026; no live source.

## Where this leaves us

- Validated: the momentum_quality ranking. Candidate core for the scan.
- Not adopted: trend and breadth timing filters.
- Not adopted: insider buying and results-day drift (failed dev).
- Next: risk control by sizing rather than timing, and mutual fund
  accumulation as the remaining independent idea.

## 2026-10-27: position sizing, settings fixed before testing (second agent; no runs yet)

`research/backtest.py` now takes `--sizing equal|inverse_vol`,
`--max-weight` (cap on one stock's share of the invested money) and
`--cash-buffer` (share always in cash). The defaults reproduce every
earlier run exactly. Volatility for inverse_vol is the one-year
(252-session) standard deviation of daily returns to the rebalance close,
the same measure momentum_quality divides by, so nothing is looked up
after the fact.

Variants, fixed now, all on `momentum_quality`, top 20, monthly, dev only:

1. `--sizing inverse_vol --max-weight 0.10`
2. `--sizing equal --cash-buffer 0.05`
3. `--sizing inverse_vol --max-weight 0.10 --cash-buffer 0.05`

Plus the equal-weight baseline rerun on the same code for comparison.
Judged on dev Sharpe and worst drawdown against that baseline, and on
the deflated Sharpe with every logged dev run counted. At most one of
them, unchanged, gets the single validation look; if none beats the
baseline on dev, none is validated and that is recorded here.
Not run yet: this session cannot reach NSE to build the market store.

## 2026-10-27: position sizing on dev (second agent; runs by the owner)

The four runs fixed in the section above, `momentum_quality`, top 20,
monthly, dev 2013 to 2020, on the owner's store:

| sizing | Sharpe | CAGR |
| --- | --- | --- |
| equal weight (baseline) | 0.82 | 26.2% |
| inverse vol, 10% cap | 0.78 | 24.1% |
| equal weight, 5% cash | 0.81 | 25.2% |
| inverse vol, 10% cap, 5% cash | 0.77 | 23.2% |

- The baseline matches the earlier dev run to the last digit (Sharpe
  0.817, CAGR 26.16%), so the new options change nothing by default.
- Inverse vol costs about 2 points a year and 0.04 of Sharpe. The likely
  reason: momentum_quality already divides by the same one-year
  volatility, so sizing by it again tilts twice toward slower stocks.
- A 5% cash buffer costs about 1 point a year at the 6% cash yield and
  leaves Sharpe almost unchanged, as it should.
- The worst drawdowns were printed but not logged (the log now records
  them, from these runs on). They are the one open check.

Verdict: none beats the baseline on dev Sharpe, so none gets the
validation look and the strategy stays equal weight, unless the printed
drawdowns show a variant cutting the baseline's -38.5% by a wide margin,
in which case this section gets a dated follow-up, not an edit.

## 2026-10-27: promoter buying under SAST Regulation 29, settings fixed before testing (second agent; no runs yet)

`promoter_buying` (research/event_signals.py): for each stock, the percent
of the company that promoters acquired in the open market, summed over
SAST Regulation 29 disclosures made public in the last 63 trading days
(about 90 calendar days). Only rows with `promoter = 1`, mode `Open
market` and side `Acquisition` count; inter-se transfers, non-promoters
and sales do not. A disclosure counts from the first trading day after
the calendar day NSE published it (most are stamped in the evening).
Stocks without such a disclosure have no score, as with insider_buying.

Fixed before any run: the 63-day window, the percent-of-company measure
(not rupees: promoters of small companies buy small rupee amounts), and
the test, one run only:

    python3 research/evaluate.py --period dev_2016 --signals promoter_buying --note "promoter_buying: SAST Reg 29 promoter open-market buys, 63 days, settings fixed in FINDINGS.md"

dev_2016 because SAST data starts in 2015 and so it sits beside the
logged insider_buying and momentum_quality runs on the same years
(momentum_quality there: Sharpe 0.60, IC 0.032, t 1.56). It is a
candidate only if the IC is positive with t of 2 or more, the top decile
beats the bottom, and the backtest Sharpe beats the equal-weight
universe's. Otherwise it is recorded as failed, like insider_buying.

## 2026-10-27: promoter buying under SAST Regulation 29 on dev_2016 (second agent; run by the owner)

The one run fixed in the section above, `promoter_buying`, dev_2016
(2016 to 2020), top 20, monthly:

| | IC | Sharpe | CAGR |
| --- | --- | --- | --- |
| promoter_buying | none: never 50 scored stocks | -0.05 | 2.4% |
| equal-weight universe (same years) | | 0.19 | |
| momentum_quality (same years, logged earlier) | 0.032 (t 1.56) | 0.60 | 20.0% |

- **Too sparse to rank.** In no month of the 59 did 50 or more of the
  roughly 430 eligible stocks have a promoter open-market purchase
  disclosed in the previous 63 trading days, so the IC could not be
  computed. Promoters who buy in the open market are mostly in companies
  below the universe's liquidity floor.
- **The backtest loses to cash.** Holding the few stocks with such
  purchases earned 2.4% a year against the 6% cash yield and the
  universe's Sharpe of 0.19.
- evaluate.py crashed printing the empty IC after the run was logged; it
  now prints n/a. The run was not repeated.

Verdict: failed on the criteria fixed before the run (no IC, Sharpe below
the universe). Not adopted, like insider_buying. A version that could be
measured would need more coverage (smaller companies, a longer window, or
promoter buying as one input to a composite); that is a new idea with its
own settings fixed before any run, not a rescue of this one.

## 2026-10-28: trade finder step 1, settings fixed before any test (owner agreed; second agent)

For the trade finder in HANDOVER.md. Fixed now and never tuned on results;
a change is a new version with its own record.

**Stops and targets (risk limits; no edge claimed until the exit test).**

- Average true range (ATR): the simple mean over the last 14 sessions of the
  true range, the largest of high minus low, high minus the previous close,
  and the previous close minus low, on prices adjusted for corporate
  actions, to the last stored session.
- Stop: entry minus 2 ATR. Target: entry plus 2 times the stop distance
  (4 ATR), a 1 to 2 risk to reward.
- Loss at the stop, charges included: low 0.5%, medium 1%, high 2% of the
  amount. The quantity is the whole number of shares whose loss at the stop,
  with the round trip's charges, stays within that, and whose cost fits the
  stock's share of the amount.

**Profiles.** Low: 5 stocks (or the index fund, if the after-tax check
finds it beats the strategy). Medium: 3. High: 1. Each from the top of the
tested ranking (`momentum_quality`), equal money per stock.

**Concentration test.** `momentum_quality`, monthly, the same settings as
the validated top 20, with top 1, 3 and 5 instead of 20: dev (2013 to 2020)
first, then one validation look (2021 to 2023) for each, recorded here
whatever it shows. Reported: CAGR, Sharpe, worst drawdown, worst month and
share of months losing, against the top 20 on the same years. No holdout.

## 2026-10-28: trade finder candidates widened to the top tenth (owner agreed; second agent)

Fixed before any new run. The trade finder (`research/advise.py`) offered
only the strategy's top 20, so a stock that failed a filter left a profile
short even when good candidates sat just below. From now on its candidates
are the top tenth of the tested ranking (`momentum_quality`), rounded up:
about 43 of 430 eligible stocks today. Never below it, however many fail a
filter.

**Why the tenth, from runs already logged (no new look).** The
`momentum_quality` evaluations in `runs.jsonl` (2026-10-27) give each tenth
of the ranking's mean monthly return above the average eligible stock:

| tenth of the ranking | dev 2013 to 2020 | validation 2021 to 2023 |
|---|---|---|
| top | +0.80% | +0.91% |
| second | +0.30% | +0.72% |
| third | +0.31% | +0.71% |
| bottom | -0.59% | -1.39% |

The top tenth beat the average in both periods and by the most; the next
two tenths beat it by less. The top 20 (about the top 5%) sits inside the
top tenth, so the validated strategy and the daily scan are unchanged:
this governs only which stocks the trade finder may offer.

**Shown on each card.** The pick's tenth of the ranking and that tenth's
record above, read from the latest logged dev and validation evaluations of
the signal. Never the holdout.

**Tests that follow this rule.** The concentration test (top 1, 3 and 5)
and the profile backtests rank the same way and take, each month, the
highest ranked stocks that pass the filters, drawn from the top tenth only.
If fewer pass, the profile holds fewer and the rest stays in cash. Same
protocol as the entry above: dev first, one validation look, no holdout.

## 2026-10-28: concentration and risk-level backtests, settings fixed before any run (second agent)

How the two tests promised above are run, fixed before the code has
seen any data. `research/profiles.py` runs both; nothing in it is tuned
on results, and a change is a new version with its own record.

**What is shared by both.**

- The signal, universe and month-end rebalance of
  `momentum_quality_top20` (`research/strategies.json`), as the trade
  finder uses them.
- Candidates are the top tenth of the eligible ranking, rounded up. In
  rank order, a stock is passed over if:
  - it was locked at a circuit on the rebalance session (high equal to
    low after a move of 2% or more);
  - the order would be more than 1% of its median traded value over 60
    sessions;
  - not even one whole share fits (for risk levels, `exits.plan` says
    so).

  The first k that pass are bought. If fewer pass, the rest stays in cash
  for the month.
- Orders as the trade finder writes them: a buy limit at the rebalance
  close plus 1%, filled at the next open plus 15 basis points of slippage
  (never above the limit), and not filled at all if the open is above the
  limit. Sells are at the open minus 15 basis points. A sell is blocked on
  a day locked at the lower circuit and tried again the next session.
- At each rebalance the new quantities replace the old: stocks no longer
  picked are sold, kept stocks are traded up or down to the new quantity,
  and stops and targets are reset.
- Money and costs:
  - Rs 1,00,000 at the start, compounding: each rebalance sizes on the
    portfolio's value at that close.
  - Whole shares, on unadjusted prices at the rebalance.
  - Zerodha delivery charges from `core/charges.json`, through
    `core/charges.py`, the DP charge included.
  - Cash earns 6% a year.
  - A stock without a price for 20 sessions is sold at half its last
    price, as in `backtest.py`.

**Concentration test.** Top 1, 3, 5 and, for a like-for-like comparison
inside the same engine, 20. Equal money per stock, no stops, held to the
next rebalance.

**Risk levels.** Low, medium and high as fixed above: 5, 3 and 1 stocks.
The quantity comes from `exits.plan`: the loss at the stop is at most
0.5%, 1% or 2% of the portfolio's value, charges included. The stop is
entry minus 2 ATR and the target is 2R, both from the buy limit, as on the
card.

From the entry day on, each session:

- An open at or below the stop sells at the open.
- Otherwise a low at or below the stop sells at the stop, less slippage.
- Otherwise a high at or above the target sells at the target.
- If the stop and the target are both touched in one session, the stop
  is assumed.
- A stock that is sold stays in cash until the next rebalance.

**Reported for each run.**

- CAGR, Sharpe (6.5% risk-free, as `backtest.py`) and the worst drawdown.
- The worst month and the share of months that lost money.
- The average share of money invested.
- Stops hit, targets hit, buys not filled, and months with fewer than k
  stocks.
- For comparison, the equal-weight universe over the same years.

**Order and looks.** Each run is logged to `runs.jsonl`. All of dev
(2013 to 2020) runs first. Then each of the seven runs gets one validation
look (2021 to 2023). No holdout: the CLI refuses it.

**When a risk level is offered (owner chose this, before any run).**

- Low must have a CAGR and a Sharpe above the equal-weight universe's, on
  both dev and validation. It is sold as the steadier choice, so it has to
  show both.
- Medium and high must have a CAGR above the equal-weight universe's, on
  both dev and validation. They are sold as more growth with more swings,
  so they are judged on growth. Their worst month and worst drawdown are
  shown on every answer.
- Fallback: a request for a level that failed is answered with the next
  safer level that passed (high, then medium, then low), and the answer
  says why. It is "no trade" only if low failed too.

## 2026-10-28: the exit test, settings fixed before any run (second agent)

The question from HANDOVER.md: do the stop and target cut
`momentum_quality`'s crashes (2016, 2022) without giving up the return?

**How.** `research/profiles.py --exit-test` runs each risk level a second
time with the same candidates, filters, orders, quantity (`exits.plan`)
and costs as its risk-level run, but holds every stock to the next
rebalance: no stop, no target. Each pair, with and without exits, is
compared on the same years. The exits are the ones fixed above: stop at
entry minus 2 ATR, target at 2R, stop first when a session touches both.
Dev first; then one validation look per level. No holdout.

**When the exits count as tested, per level.** On both dev and
validation, with exits the Sharpe must be higher and the worst drawdown
must be shallower than without them. Then the card may say the stop and
target were tested for that level. Otherwise the label stays "risk
limits, not a tested edge". The stops stay on every card either way: the
owner wants them as limits.

**Also reported, not judged:** CAGR, the 2016 and 2022 returns, the worst
month, stops and targets hit.

## 2026-10-29: risk levels v2, stock count plus a volatility target, the rest in the index (owner agreed; settings fixed before any run)

A new idea, not a fix to the levels that failed. Run by
`research/profiles.py --vol-levels`.

**Levels.**

| level | stocks | target yearly volatility of the stock part |
| --- | --- | --- |
| low | 20 | 10% |
| medium | 5 | 20% |
| high | 3 | 30% |

**Each month-end rebalance:**

1. Choose the stocks exactly as the concentration runs do: the top tenth
   of the ranking, the trade finder's filters, limit buys at the close
   plus 1%, whole shares, Zerodha charges, no stop and no target.
2. Measure the volatility of an equal-money basket of those stocks: the
   standard deviation of its daily returns over the last 63 sessions,
   times the square root of 252.
3. The share of the portfolio put in the stocks is the target divided by
   that volatility, never above 100%.
4. The rest goes into the Nifty 500 index, standing in for an index fund.
   It is bought or sold at the close of the day the stock orders fill,
   with the cash those orders leave, never borrowing. It earns the index's
   price return: no dividends, and no fund costs or tax (the expense
   ratios and tax treatment have not been read from the funds' and the
   Income Tax Department's own pages).
5. Money from buys that did not fill earns the 6% cash rate until the next
   rebalance.

Everything else is as in the concentration and risk-level entry above.

**When a level is offered.** The rule the owner chose (2026-10-28),
unchanged: low beats the equal-weight universe on CAGR and Sharpe; medium
and high on CAGR; on dev and on validation; a failed level hands over to
the next safer level that passed. The Nifty 500's own CAGR and Sharpe
are reported beside each run, not judged.

**Order and looks.** Dev first (2013 to 2020), then one validation look.
Validation has been seen by the earlier level runs, so a pass there is
weaker evidence than before; the holdout (2024 onward) stays closed until
the owner asks.

**The market's state on the card.** The Nifty 500 against its 200-day
average, and recent market volatility, may be shown as untested context.
They never change a trade: timing by trend and breadth failed validation
(2026-10-27).
