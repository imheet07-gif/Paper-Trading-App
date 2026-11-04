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

## 2026-10-28: concentration and risk levels on dev, and the exit test on dev (owner's machine)

Settings as fixed above, nothing changed after seeing results. Dev
2013 to 2020, Rs 1,00,000 start; the equal-weight universe made 8.8% a
year (Sharpe 0.20, worst drawdown -58.2%).

| run | CAGR | Sharpe | worst drawdown | worst month | months losing | invested | passes dev |
| --- | --- | --- | --- | --- | --- | --- | --- |
| top 1 | 34.5% | 0.73 | -72.1% | -44.7% | 36.5% | 79.1% | yes |
| top 3 | 27.3% | 0.71 | -61.6% | -39.1% | 46.9% | 82.0% | yes |
| top 5 | 26.9% | 0.77 | -35.9% | -21.1% | 36.5% | 84.5% | yes |
| top 20 | 20.8% | 0.71 | -30.1% | -16.7% | 33.3% | 85.6% | yes |
| low (5 stocks) | 8.9% | 0.53 | -4.9% | -2.0% | 34.4% | 11.8% | yes |
| medium (3 stocks) | 8.7% | 0.33 | -9.6% | -3.1% | 44.8% | 13.2% | no |
| high (1 stock) | 12.7% | 0.74 | -9.2% | -4.2% | 31.2% | 9.8% | yes |

Exit test, each level held to the rebalance without its stop and target:

| level | without exits: CAGR, Sharpe, worst drawdown, invested | with exits: Sharpe, worst drawdown | 2016 with / without | exits pass dev |
| --- | --- | --- | --- | --- |
| low | 11.1%, 0.61, -11.2%, 24.6% | 0.53, -4.9% | +5.0% / +1.5% | no |
| medium | 13.4%, 0.63, -21.2%, 29.9% | 0.33, -9.6% | +5.2% / -2.3% | no |
| high | 11.4%, 0.46, -27.3%, 22.2% | 0.74, -9.2% | +21.3% / +8.9% | yes |

(2022 is in validation, not dev.)

What it says:

- **Concentration.** Fewer stocks earned more and fell much further:
  the top 1 made 34.5% a year but lost 72% at its worst and 44.7% in
  one month. The top 5 kept most of the return (26.9%) with a drawdown
  near the top 20's.
- **The risk levels are mostly cash.** Sizing each stock so that a stop
  loses at most 0.5%, 1% or 2% of the amount, with the stop at 2 ATR,
  puts small positions on. Stops then empty them mid-month, and limit
  buys miss when a stock opens more than 1% up. On average only 10% to 13%
  of the money was in stocks; the rest earned the 6% cash rate. That is
  why their CAGR sits near the universe's while their drawdowns are tiny.
  Medium failed on dev by 0.1 point of CAGR (8.7% against 8.8%).
- **Exits.** The stop and target cut the worst drawdown at every level
  and helped in 2016. They lowered the Sharpe for low and medium, and
  raised it for high. Under the rule fixed before the run, only high
  passes on dev.
- **Unfilled buys** were frequent (247 in 95 months for the top 20):
  momentum stocks often open more than 1% above the last close.

Per the protocol, each of the seven runs and the exit test get one
validation look (2021 to 2023) next. No setting changes before it. A
version that keeps the risk levels' loss limits but invests the idle
cash (for example in an index fund), or a wider limit band, would be a
new idea with its own settings fixed first, not a fix to this one.

## 2026-10-29: the same runs on validation, one look each (owner's machine)

Validation 2021 to 2023, the settings unchanged. The equal-weight
universe made 34.6% a year (Sharpe 1.41) in these three strong years.

| run | CAGR | Sharpe | worst drawdown | worst month | months losing | invested | 2021 / 2022 / 2023 | passes validation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| top 1 | 77.0% | 1.26 | -63.7% | -47.4% | 36% | 85% | +167% / -18% / +117% | yes |
| top 3 | 43.0% | 1.19 | -32.3% | -13.6% | 42% | 76% | +82% / -7% / +57% | yes |
| top 5 | 36.4% | 1.09 | -24.7% | -11.8% | 47% | 77% | +64% / -8% / +58% | yes |
| top 20 | 37.1% | 1.47 | -23.6% | -13.7% | 25% | 76% | +55% / -1% / +62% | yes |
| low (5 stocks) | 9.7% | 0.72 | -3.0% | -1.5% | 28% | 11% | +13% / +3% / +12% | no |
| medium (3 stocks) | 12.2% | 0.88 | -4.4% | -2.1% | 33% | 14% | +13% / +4% / +18% | no |
| high (1 stock) | 12.3% | 0.84 | -4.8% | -1.8% | 33% | 9% | +18% / +2% / +16% | no |

Exit test on validation, each level without its stop and target:

| level | without exits: CAGR, Sharpe, worst drawdown | with exits: Sharpe, worst drawdown | 2022 with / without | exits pass validation |
| --- | --- | --- | --- | --- |
| low | 13.0%, 0.95, -7.7% | 0.72, -3.0% | +2.7% / +2.3% | no |
| medium | 16.6%, 1.15, -8.2% | 0.88, -4.4% | +3.9% / +3.5% | no |
| high | 18.7%, 1.26, -10.6% | 0.84, -4.8% | +2.1% / +3.3% | no |

Verdicts under the rules fixed before any run:

- **No risk level passes.** Each needed to beat the universe's CAGR on
  dev and validation; on validation all three grew at 10% to 12% against
  34.6%, because about 90% of the money sat in cash. Low and high passed
  dev, medium did not. By the fallback rule the trade finder now answers
  "no trade" at every level, and says why.
- **The exits are not a tested edge.** They passed for high on dev only.
  On validation they cut the worst drawdown at every level but lowered
  the Sharpe at every level. The card's label "risk limits, not a tested
  edge" stays.
- **Concentration, equal money, no stops:** every count beat the
  universe's CAGR in both periods. Fewer stocks swung far more: the top 1
  lost 64% to 72% at its worst in each period; the top 5 and top 20 kept
  their worst drawdowns near a quarter to a third.

What failed is the sizing, not the ranking: a loss limit per stock with
a 2 ATR stop leaves most of the money idle. Any new risk-level design
(for example the concentration counts as levels, or the loss limits with
the idle cash in an index fund) is a new idea. Its settings are fixed
here first, and since 2021 to 2023 has now been seen for these runs, its
clean test is the holdout, opened once, only when the owner asks.
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

## 2026-10-29: dividends, and a real index fund for v2's index part (owner agreed; before any v2 run)

Changes to the v2 settings above, made before any run of them:

- **Stock dividends.** `research/profiles.py` credits each cash dividend
  NSE recorded in the corporate actions (`corporate_actions.dividend`,
  rupees per share) as cash on its ex-date, for the shares held at the
  close before it. Gross, before tax: tax is the after-tax check's job.
  This applies to every `profiles.py` run from now on, so earlier runs
  and new ones are not compared as like for like; FINDINGS says which
  each used.
- **The index part is a real index fund's NAV, not the price index.** A
  fund's NAV has its dividends reinvested and its expense ratio and
  tracking error taken out. Nifty 500 index funds start around 2021, too
  late for dev, so the fund is a **Nifty 50 index fund, direct plan,
  growth option**: the one with the longest daily NAV history in AMFI's
  data, chosen on that rule alone, before any run, with
  `core/funds.py find`. The NAV of the day the stock orders fill is used,
  as a fund order placed that day before the cut-off. No exit load or
  stamp duty on fund purchases is modelled (not yet read from the
  publishers' pages). If the fund's NAV is missing for the period, the
  run stops with an error; it never falls back to the price index.

Everything else in v2 is unchanged.

## 2026-10-29: the index fund for v2, chosen by the rule (before any v2 run)

AMFI's list on 2026-10-28 had 23 schemes matching "nifty 50 index",
direct plan, growth option. The 360 ONE ELSS Tax Saver Nifty 50 Index
Fund was left out (a tax-saver fund with a three-year lock-in, not a
plain index fund). The NAV history of the other 22 was fetched from
2012-12-01 on the owner's machine.

| scheme | fund | first NAV | NAVs to 2026-10-28 |
| --- | --- | --- | --- |
| 118581 | Franklin India NSE Nifty 50 Index Fund | 2013-01-01 | 3,403 |
| 119063 | HDFC Nifty 50 Index Fund | 2013-01-01 | 3,403 |
| 118482 | Bandhan Nifty 50 Index Fund | 2013-01-02 | 3,411 |
| 119648 | Aditya Birla Sun Life Nifty 50 Index Fund | 2013-01-02 | 3,402 |
| 120716 | UTI Nifty 50 Index Fund | 2013-01-02 | 3,402 |
| 120307 | LIC MF Nifty 50 Index Fund | 2013-01-02 | 3,392 |
| 118881 | Taurus Nifty 50 Index Fund | 2013-01-03 | 3,407 |
| 15 others | | 2019-02-21 or later | |

The rule: the earliest first NAV; on a tie, the most NAVs stored; then the
lowest scheme code. Franklin and HDFC tie on the first NAV and on the
count, so the fund is **118581, Franklin India NSE Nifty 50 Index Fund,
Direct Plan, Growth**. No return figure was looked at to choose it.

## 2026-10-29: the after-tax check, settings fixed before any run (owner's agent)

The trade finder leads with an index fund if the fund beats the tested
strategy after tax (HANDOVER.md). Run by `research/aftertax.py`; tax by
`core/tax.py` from `core/tax_rates.json`, every rule dated and quoted from
the Income Tax Department's own section pages.

**Strategy.** `momentum_quality_top20` exactly as validated: top 20,
month-end rebalance, next-open fills, 15 bps slippage, charges from
`core/charges.json`, Rs 10,00,000 at the start. Added for this check
only: dividends credited on the ex-date (rupees per share from the
store's corporate actions, on the shares held the session before); each
financial year's tax paid from the portfolio at the year's last close;
everything sold at the window's last close (less slippage and charges)
and that year's tax paid. Gains are on fill prices; charges are not
deducted from gains (slightly more tax than a filed return).

**Fund.** Bought at the window's first close, held, sold at its last; a
growth plan, so its dividends stay inside the fund untaxed. Its value is
the stored price index plus the index's own daily dividend yield (no
total return index is stored), less an expense ratio of 1.0% a year,
fixed here. The fund's own factsheet was not read; direct plans of
momentum index funds are quoted around 0.4 to 0.5% by aggregator sites,
so 1.0% handicaps the fund. Rerun with the factsheet figure for the
card. Purchase stamp duty and redemption STT on fund units are left out.

**Windows.** `dev_2016` (2016 to 2020) against a Nifty 50 fund, since
Nifty200 Momentum 30 is stored only from August 2020; `validation`
(2021 to 2023) against a Nifty200 Momentum 30 fund. One run each. The
holdout stays closed.

**Reported.** Per financial year: return before and after tax, short and
long gains, dividends, tax paid, losses carried. Overall: CAGR before
tax, after gains tax only, and after all tax at a slab rate typed in for
the run (dividends); the end value of each after tax. The slab rate is
never written down: the run log keeps the result with dividends
untaxed. Tax is before surcharge (it depends on total income).

**The rule.** If the fund's after-tax end value beats the strategy's on
a window, the trade finder's low profile should lead with the fund for
that kind of market; the owner decides.

## 2026-10-29: the after-tax check on dev_2016 and validation (owner's machine)

Settings as pre-registered above, one run each, both in `runs.jsonl`
(`profile: after_tax`). Rs 10,00,000; tax before surcharge; the fund at a
1.0% expense ratio; the strategy's dividends taxed at the slab rate typed
for the printout only (the log keeps them untaxed). The dev_2016 run
crashed on its last two print lines after it was logged (a formatting
bug, fixed); its numbers below come from the run's own output and log.

| window | | CAGR before tax | after gains tax | end value after gains tax |
| --- | --- | --- | --- | --- |
| dev_2016 (2016 to 2020) | `momentum_quality_top20` | 20.5% | 17.7% | Rs 22.6 lakh |
| | Nifty 50 fund | 12.2% | 11.8% | Rs 17.5 lakh |
| validation (2021 to 2023) | `momentum_quality_top20` | 47.2% | 40.4% | Rs 27.5 lakh |
| | Nifty200 Momentum 30 fund | 26.2% | 24.2% | Rs 19.1 lakh |

**What tax does to each.**

- The strategy's gains are almost all short term: no long-term gain at
  all in validation, and at most Rs 18,527 in a dev year. It paid 15% (plus
  cess) every year it gained: Rs 72,780 for FY2017-18, Rs 1.43 lakh for
  April to December 2020, Rs 1.53 lakh for FY2021-22 and Rs 1.80 lakh for
  FY2023-24. Tax took about 2.8 points a year off its CAGR on dev and 6.8 on
  validation.
- Losses carried forward did their job: the FY2015-16 short-term loss
  (Rs 1.11 lakh) cut FY2017-18's bill, FY2018-19's loss cut FY2019-20's,
  and FY2022-23's Rs 1.49 lakh loss cut FY2023-24's.
- The fund paid once, at the end, at the long-term rate above the Rs 1
  lakh limit; on dev its cost was the 31 January 2018 NAV
  (grandfathering). Tax took 0.4 points a year off on dev and 2.0 on
  validation.
- Dividends barely matter at this size: Rs 58,719 over dev and Rs 29,415
  over validation, untaxed before April 2020.

**Verdict.** The strategy stays ahead of the fund after tax on both
windows, by a wide margin, so the trade finder need not lead with the
fund on this evidence. Limits of the check: the momentum index is stored
only from August 2020, so dev compares against a Nifty 50 fund; the fund
is a price index plus its dividend yield, not a total return index; the
expense ratio was not read from a factsheet; surcharge is left out, and
it would hit the strategy's yearly gains harder than the fund's single
sale at high incomes. With Rs 50,000 instead of Rs 10 lakh the Rs 1 to
1.25 lakh long-term limit would cover far more of the fund's gain.

## 2026-10-29: risk levels v2 on dev (owner's machine)

Settings as fixed above, the index part in fund 118581, stock dividends
credited. Dev 2013 to 2020, Rs 1,00,000 start.

| run | CAGR | Sharpe | worst drawdown | worst month | months losing | in stocks | in the fund | passes dev |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| equal-weight universe | 8.8% | 0.20 | -58.2% | | | | | |
| the index fund alone | 12.0% | 0.37 | -37.6% | | | | | |
| low v2 (20 stocks, 10%) | 15.5% | 0.57 | -29.5% | -17.9% | 37.5% | 37.9% | 51.5% | yes |
| medium v2 (5 stocks, 20%) | 23.3% | 0.79 | -32.9% | -15.8% | 34.4% | 56.5% | 30.7% | yes |
| high v2 (3 stocks, 30%) | 28.7% | 0.84 | -46.3% | -28.6% | 40.6% | 66.7% | 17.2% | yes |

The volatility target set the stock share at 48% (low), 69% (medium)
and 83% (high) on average. Low had three months with fewer than 20
stocks (whole shares on a small amount). Unfilled limit buys stay common
(279 in 95 months for low).

Beside the first design's runs (no dividends there, so not like for
like): medium v2 earned less than the plain top 5 (26.9%) with a
slightly smaller worst drawdown (-32.9% against -35.9%); high v2 earned
about what the top 3 did (27.3%) with a much smaller worst drawdown
(-46.3% against -61.6%). Every v2 level beat the index fund on both CAGR
and Sharpe.

Next: one validation look for the three levels, settings unchanged.

## 2026-10-29: risk levels v2 on validation, one look (owner's machine)

Settings unchanged. Validation 2021 to 2023.

| run | CAGR | Sharpe | worst drawdown | worst month | months losing | in stocks | in the fund | passes validation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| equal-weight universe | 34.6% | 1.41 | -23.2% | | | | | |
| the index fund alone | 16.9% | 0.70 | -16.4% | | | | | |
| low v2 | 27.1% | 1.32 | -18.4% | -5.6% | 27.8% | 33.1% | 52.6% | no |
| medium v2 | 29.7% | 1.14 | -19.6% | -7.0% | 41.7% | 47.6% | 34.0% | no |
| high v2 | 33.8% | 1.11 | -26.5% | -10.9% | 44.4% | 59.5% | 18.4% | no |

**Verdict under the fixed rule: no v2 level passes.** Each needed a CAGR
above the equal-weight universe's 34.6% (and low a Sharpe above 1.41);
all three fell short, high by 0.8 point. The trade finder keeps answering
"no trade".

What the numbers also show, not judged:

- Every level beat the index fund in both periods, on CAGR and Sharpe:
  on validation 27.1% to 33.8% against 16.9%, Sharpe 1.11 to 1.32
  against 0.70.
- Low kept its worst drawdown (-18.4%) close to the index fund's
  (-16.4%) while earning 10 points a year more.
- The bar the rule set is the equal-weight average of about 430 stocks,
  rebalanced monthly at no cost. No fund holds it, and in 2021 to 2023
  small and mid caps led it.

Changing the bar now, for example to the index fund, would be choosing
the rule after seeing the results. If the owner wants that, it is written
here first as a new rule, and the only clean test of it is the holdout
(2024 onward), opened once, when the owner asks.

## 2026-10-29: fundamentals batch F6, settings fixed before any run (owner agreed; second agent)

The NSE rows of the metrics catalogue in `docs/PROPOSAL.md` and its three
composites, each tested as its own signal. Built in
`research/fundamentals.py`, registered in `research/signals.py` with the
prefix `f_`, run with `research/evaluate.py` like every signal before it.
**20 dev runs, declared now**; nothing is added to the batch after a run.

**Inputs, point in time.** The `financials` table (F3 lines included). A
figure counts from the session after its `broadcast_at`; a revision
replaces the original from its own broadcast on. Per company and quarter,
consolidated figures when filed, else standalone. Lenders (a quarter
with interest earned and no revenue) are left out of every metric that
needs revenue or finance costs. A metric whose latest quarter ended more
than 365 days before the date is dropped (no score).

**Shares and prices.** Market cap = today's adjusted close times the
shares of the latest filing (paid-up capital over face value) restated
through the corporate action factors. EPS is restated the same way.

**Trailing four quarters (TTM)** = the sum of the four latest quarters,
only when they are four consecutive quarters.

| signal | definition | higher score |
| --- | --- | --- |
| `f_revenue_growth_q` | latest quarter's revenue over the same quarter a year earlier, less 1 | higher |
| `f_revenue_growth_ttm` | TTM revenue over the TTM a year earlier, less 1 | higher |
| `f_eps_growth_q` | change in restated EPS on the same quarter a year earlier, over its absolute value | higher |
| `f_eps_growth_ttm` | the same on TTM restated EPS | higher |
| `f_surprise` | the EPS change on a year earlier over the standard deviation of that change in up to 8 earlier quarters (at least 4: needs 9 quarters); dropped 120 days after its broadcast | higher |
| `f_operating_margin` | TTM (PBT + finance costs + depreciation - other income) over TTM revenue | higher |
| `f_net_margin` | TTM profit to owners (else profit) over TTM revenue | higher |
| `f_roe` | TTM profit to owners over the latest year-end net worth (paid-up capital + reserves) | higher |
| `f_interest_coverage` | (TTM PBT + TTM finance costs) over TTM finance costs; no finance costs ranks first | higher |
| `f_profit_share_8q` | share of the last 8 quarters with a profit | higher |
| `f_margin_stability` | minus the standard deviation of quarterly operating margin over the last 8 quarters | higher (steadier) |
| `f_other_income_share` | minus TTM other income over TTM PBT | higher (less) |
| `f_exceptional_share` | minus the absolute TTM exceptional items over TTM PBT | higher (less) |
| `f_earnings_yield` | TTM profit to owners over market cap | higher |
| `f_book_to_price` | latest year-end net worth over market cap | higher |
| `f_sales_to_price` | TTM revenue over market cap | higher |
| `f_small_size` | minus the log of market cap | higher (smaller) |
| `f_quality` | average percentile rank of `f_roe`, `f_interest_coverage`, `f_profit_share_8q` | higher |
| `f_value` | of `f_earnings_yield`, `f_book_to_price`, `f_sales_to_price` | higher |
| `f_growth` | of `f_revenue_growth_q`, `f_eps_growth_q`, `f_surprise` | higher |

Directions are the conventional ones, fixed here, never chosen from
results. Loss-makers and negative net worth sort last: a ratio whose
denominator is zero or negative (PBT, base-year EPS or revenue, net worth)
gives the worst score, not none. Composites rank within the eligible
universe each date and need at least two of their three parts. Earnings
yield uses profit over market cap rather than EPS over price: the same
ratio when the share count is unchanged, and it needs no EPS restatement.
The operating margin follows the proposal, so it excludes exceptional items
only when the company files them below PBT.

**Each run.** The eligible universe and month-end rebalance of
`momentum_quality_top20`; IC and deciles by `research/evaluate.py` (next
month's return); the top 20, equal weight, monthly, with the same costs.

**The bar (option B, owner 2026-10-29), on dev and then on validation:**

- the IC t-statistic above 3 (not 2: twenty signals are tested at once);
- the deciles orderly: `monotonic` above 0.5;
- the top 20's Sharpe above the equal-weight universe's.

A signal passes dev only with all three; only those get one validation
look, with the same bar. Combinations with `momentum_quality`, and a
quarterly-rebalance version, are later ideas with their own entries.
Ownership metrics (shareholding history from 2021 only) stay card
context, untested.

## 2026-11-04: multibagger misses, settings fixed before any run (owner agreed; owner's agent)

**Why.** The owner does not want to miss multibaggers such as APOLLO
(Apollo Micro Systems, INE713T01028). A one-stock look in chat on
2026-11-04, with no strategy run and nothing logged, used data to
2023-12-31 only (the holdout was not opened). It touched the validation
years 2021 to 2023, said here so the look is on record. What it showed,
for `momentum_quality_top20` as in `research/strategies.json`:

- 2020 to 2022 (about 7x, split-adjusted Rs 4.5 to Rs 30): outside the
  universe almost every month-end, because its 60-session median traded
  value was under Rs 2 crore (Rs 0.5 to 1.8 crore); BE series in early
  and late 2022.
- February and March 2023: ranks 7 and 11 of the eligible universe,
  bought. July to September 2023: ranks 6, 7 and 19, bought.
- November and December 2023: BE series again, outside the universe.

So the signal found it once it traded enough; the Rs 2 crore floor cost
the early run. One remembered winner says nothing about the many look-alikes
that failed, hence the audit below, on dev only.

### Step 1: the miss audit (dev only, descriptive, adopts nothing)

`research/misses.py`, numpy and pandas, reading the store through
`panel.load` from 2012 so the first scores have a year of history.

- **Dates:** month-ends from January 2013 to December 2018, so a 24-month
  window ends inside dev (December 2020). Nothing after 2020-12-31 is read.
- **A multibagger:** a company share (ISIN starting INE) whose
  split-adjusted close at a month-end t + 24 months is at least 3 times
  its close at month-end t. A stock counts once, with its earliest
  qualifying t (its window). Stocks with a failed corporate action check
  in the window are listed apart, not counted.
- **Caught:** in the top 20 of `momentum_quality` within the strategy's
  universe (`strategies.json`: min price Rs 20, Rs 2 crore median value
  over 60 sessions, 250 sessions of history, EQ series) at any month-end
  inside the window. Also reported: in the top tenth of the eligible
  ranking (the trade finder's candidates).
- **For each multibagger:** caught or not; months from window start to
  first catch; the share of the window's log return earned in the months
  it was held (top 20, held one month from each month-end); and, for each
  month-end in the window it was not held, the first filter that blocked
  it in this order: not EQ, price under Rs 20, history under 250
  sessions, failed corporate action check, median value under Rs 2 crore,
  ranked below the top 20.
- **Totals:** counts of multibaggers, caught and missed; misses by
  blocking filter; all of these split by price at window start (under Rs
  20, 20 to 100, over 100) and median traded value at window start (under
  Rs 50 lakh, 50 lakh to 2 crore, over 2 crore).
- **The other side (look-alikes):** at each month-end, stocks that are
  blocked only by the Rs 2 crore value floor, have at least Rs 50 lakh,
  and would rank in the top 20 if eligible. Their next 1-month and
  12-month returns against the actual top 20's and the equal-weight
  universe's: mean, median, share that tripled within 24 months, share
  that halved, share that later hit a circuit lock or moved to BE.

This is a description, not a strategy test: it adopts nothing, ranks
nothing, and is not a variant in the attempt count. It is logged once in
`research/runs.jsonl` with kind `audit` and a note, so the record exists.
Its results must not be used to pick the floor in step 2, which is fixed
here first.

### Step 2: a wider net, `momentum_quality_wide_top20` (a new idea)

Exactly `momentum_quality_top20` with one change: the universe's median
traded value floor is **Rs 50 lakh** over 60 sessions instead of Rs 2
crore. Every other setting is unchanged, including the per-order limit
of 1% of the stock's 60-session median value, which already caps the
order size at each capital level (at Rs 50 lakh that is Rs 50,000 a
stock). The floor is fixed now, before step 1 runs, and is never tuned.

- Tested through the engine's harness (`research/harness.py`, once
  `second-agent/engine-p1-p2` is merged) on dev: walk-forward, Rs 20,000
  and Rs 10 lakh, real charges, after-tax. Logged in `runs.jsonl`, one
  attempt.
- **It passes dev only if**, at both capital levels on the same test, its
  after-tax Sharpe is above `momentum_quality_top20`'s and its worst
  drawdown is no more than 5 percentage points deeper. The option B bar
  (IC t above 3) is reported but is not the test here: the current
  strategy's own dev IC t is 2.50, and the question is whether the wider
  universe beats it.
- If it passes dev: one validation look, the same rule. Only then may it
  replace or join `momentum_quality_top20` in `strategies.json`.

### Step 3: a radar in the app (untested context, after step 1)

A list on the Today page of stocks that would rank in the top tenth of
the tested signal but fail a universe filter, each with the filter that
blocks it (for example "median traded value Rs 1.2 crore, under Rs 2
crore", "BE series"). Labelled untested, never sized, never in the pick
list or order sheet. Built as a `jobs.py` task first, as every engine
feature is. BE stays a hard filter everywhere: it is NSE's
trade-for-trade surveillance series, not a choice of ours.


## 2026-11-04: multibagger miss audit, step 1 results (owner's machine; owner's agent)

Step 1 of the section above, run once as written: `research/misses.py`,
dev only, settings unchanged. The store was loaded from 2012-01-01 to
2020-12-31 and no later (the script refuses a panel with a later date); the
holdout was not opened and nothing after 2020-12-31 was read. Month-ends
2013-01 to 2018-12 (72), windows of 24 months, 3 times or more.
Descriptive: it adopts nothing, ranks nothing for trading and is not a
variant. Logged once in `runs.jsonl` with kind `audit`. Tested offline on a
made-up panel first (`tests/test_misses.py`).

Choices the section left open, fixed before the run and stated so they can
be checked: "inside the window" means the 24 month-ends t to t + 23 (a pick
at t + 24 earns nothing in the window). Forward prices are the adjusted
close, carried at most 20 sessions; a stock silent for longer is treated as
delisted and valued at its last close less the backtest's 50% haircut, so
dead stocks count against their group. "Would rank in the top 20 if
eligible" means a score at or above the 20th best score of the actual
universe. Tripled and halved are measured at 24 months, as the multibagger
test is. "Circuit lock or BE" is any session in the next 12 months with a
single-price day after a move of 2% or more (the backtest's rule), or a
stock that had a price but was not in the EQ series. A miss's "main"
blocker is the filter that blocked it in most of its window's months.

**Totals.** 821 multibaggers (2 more were listed apart for a failed
corporate action check). Caught (in the top 20 at some month-end of its
window): 104, 12.7%. Missed: 717. In the top tenth of the eligible ranking
at some point (the trade finder's candidates): 168, 20.5%. The caught ones
were first found a median 16 months into their 24-month window, and the
strategy held them for a median 8.7% of the window's log return, so even
the "caught" are mostly caught late.

**Misses by blocking filter** (717 missed stocks; a stock can be blocked by
several filters in different months):

| Filter | Stocks blocked by it at least once | Stocks it blocked most | Blocked stock-months |
|---|---|---|---|
| traded value under Rs 2 crore | 574 | 424 | 9,282 |
| price under Rs 20 | 271 | 138 | 3,102 |
| not EQ (BE or BZ) | 390 | 77 | 2,620 |
| ranked below the top 20 (passed every filter) | 179 | 66 | 1,895 |
| history under 250 sessions | 48 | 12 | 307 |
| no score | 2 | 0 | 2 |
| failed corporate action check | 0 | 0 | 0 |

Of 17,208 blocked stock-months, 54% were the value floor, 18% the price
floor, 15% not EQ, 11% rank. Only 66 of the 717 misses were mostly blocked
by rank alone, that is, were in the universe and simply not high enough.

**By price at window start** (caught of multibaggers):
under Rs 20 6 of 216 (2.8%); Rs 20 to 100 40 of 292 (13.7%); over Rs 100
58 of 255 (22.7%); no price that day 0 of 58 (carried from an earlier
session).

**By median traded value at window start:**
under Rs 50 lakh 36 of 537 (6.7%); Rs 50 lakh to 2 crore 30 of 86 (34.9%);
over Rs 2 crore 27 of 76 (35.5%); not enough history for a median 11 of 122
(9.0%). Top-tenth counts: 63, 44, 46 and 15.

**Look-alikes** (blocked only by the Rs 2 crore value floor, at least
Rs 50 lakh, score at or above the actual 20th; 723 stock-months, 10 a
month), against the actual top 20 (1,440 stock-months) and the whole
universe (28,456), pooled stock-months, before any costs:

| | Look-alikes | Actual top 20 | Whole universe |
|---|---|---|---|
| next 1 month, mean / median | +3.5% / +1.0% | +2.4% / +1.2% | +0.7% / +0.1% |
| next 12 months, mean / median | +22.5% / +7.0% | +17.4% / +5.5% | +8.2% / +0.8% |
| tripled within 24 months | 7.5% | 6.1% | 2.4% |
| halved within 24 months | 21.7% | 19.4% | 17.1% |
| circuit lock or BE within 12 months | 14.0% | 15.1% | 5.4% |

**Reading.** Most multibaggers were never reachable by this strategy:
657 of 821 (80%) began below Rs 2 crore a day or below Rs 20 a share (the
two floors together; 623 of them under the value floor alone), and the biggest single reason for a miss is the value floor,
not the ranking. But the look-alikes are not a free lunch on this evidence:
their mean returns are somewhat higher than the top 20's, their median
12-month return is higher by 1.5 points, and they halved more often (21.7%
against 19.4%); the differences rest on 723 overlapping stock-months, carry
no costs or slippage, and Rs 50 lakh a day is thin to trade (1% of it is
Rs 50,000 an order). Multibaggers are also chosen with hindsight, so a high
miss count does not by itself say the filters are wrong. Nothing here picks
or changes the Rs 50 lakh floor of step 2, which was fixed before the run
and is tested on its own through the harness.
## 2026-11-04: the Phase 2 harness checked on momentum_quality, dev (second agent; owner's store)

A check of the new machine, not a new idea: `research/harness.py` on
`momentum_quality`, monthly, top 20, 5 and 3, dev 2013 to 2020, under the
rules in `research/retail.json` (version 1). Three runs logged in batch
`phase2-check`. Different from the earlier engines on purpose: the
portfolio is invested from the first session (the decision is the close
before 1 January 2013), cash dividends are credited, and each capital level
starts from its own amount.

| run | IC (t) | fractional reference | Rs 20,000: CAGR, Sharpe, worst DD | Rs 10 lakh: CAGR, Sharpe, worst DD | after gains tax, 20k / 10 lakh | recorded earlier |
| --- | --- | --- | --- | --- | --- | --- |
| top 20 | 0.045 (2.50) | 26.1%, 0.82, -38.5% | 16.0%, 0.54, -36.5% | 22.8%, 0.77, -28.9% | 12.2% / 19.6% | 26.2%, 0.82 (backtest.py); 20.8%, 0.71 (profiles.py, Rs 1 lakh) |
| top 5 | same | 33.0%, 0.85, -56.0% | 26.4%, 0.78, -35.9% | 17.8%, 0.50, -39.7% | 23.0% / 15.9% | 26.9%, 0.77, -35.9% (Rs 1 lakh) |
| top 3 | same | 34.8%, 0.81, -81.6% | 26.9%, 0.71, -61.0% | 27.8%, 0.73, -46.9% | 22.6% / 23.5% | 27.3%, 0.71, -61.6% (Rs 1 lakh) |

The equal-weight universe made 8.8% (Sharpe 0.20). The IC, the deciles
(monotonic 0.92) and the fractional reference reproduce the recorded
numbers exactly, so the harness reads the same data and dates as before.

**Why the whole-share numbers move with capital.** A selection count on
the same rebalance dates (which stocks each run buys; no returns
computed, so not a new trial):

| run | the idea's own picks bought | passed over: liquidity (1% of median value) | passed over: one share costs more than the slot |
| --- | --- | --- | --- |
| top 20, Rs 20,000 | 84% | 0% | 15% |
| top 20, Rs 10 lakh | 99% | 0% | 0% |
| top 5, Rs 20,000 | 98% | 0% | 0% |
| top 5, Rs 10 lakh | 79% | 20% | 0% |
| top 3, Rs 20,000 | 98% | 0% | 0% |
| top 3, Rs 10 lakh | 48% | 51% | 0% |

- Top 20 at Rs 20,000 (Rs 1,000 a stock) cannot buy 15% of its picks, and
  the DP charge of Rs 15.34 is 1.5% of each sale: 16.0% against 22.8% at
  Rs 10 lakh. Unfilled limit buys (270 at Rs 10 lakh, 247 recorded at Rs 1
  lakh) take the rest of the gap to the fractional 26.1%, as recorded on
  2026-10-28.
- Top 5 and top 3 at Rs 10 lakh hold Rs 2 to 3 lakh a stock, more than 1%
  of the median traded value of many top-ranked stocks, so they buy more
  liquid substitutes from further down the top tenth. With so few stocks,
  which substitutes they get decides the result: top 3 came out about where
  it was recorded, top 5 nine points lower. This is the liquidity rule
  working, not a fault, and it says plainly that concentrated momentum has
  little capacity at Rs 10 lakh.

**Minimum capital (rule in retail.json: whole shares, DP charge at most
0.5% of a position, 90% of decision dates).** Top 20 needs Rs 3.18 lakh to
hold every one of its own picks (median Rs 1.04 lakh, at most Rs 13.8 lakh,
when one pick costs tens of thousands a share), top 5 Rs 28,559, top 3
Rs 16,454. Top 20 and top 5 are flagged at Rs 20,000. The simulator
instead passes over what does not fit, as the trade finder does, so its Rs
20,000 rows show what a small account actually got; the flag says the idea
as ranked cannot be followed there.

**Walk-forward (train 2013 to k, test k+1, 2016 to 2020).** The test-year IC
was negative in 2016 and 2020 and positive in 2017 to 2019; pooled over
the 59 test months it is 0.032 (t 1.56). Pooled test-year CAGR at Rs 10
lakh: top 20 13.8% (Sharpe 0.40), top 3 24.8% (0.63), against the
universe's 6.5%; top 20 beat the universe in 4 of 5 test years.

**Against the bar (option B).** All three fail it on dev, as
`momentum_quality` always has: the IC t is 2.50, below 3. Deciles and
Sharpe pass. `momentum_quality` stays the benchmark under the rule it was
validated by (t above 2); the higher bar applies to new batches from Phase 3.
No setting was changed after these runs.

## 2026-11-04: retail rules version 2, a maximum capital (owner chose 10%; settings fixed before any run)

The owner kept the minimum-capital rule of version 1 as it is: the DP
charge at most 0.5% of a position, 90% of decision dates covered, and the
minimum measured on the idea's own picks. Two additions, fixed now, before
any run:

- **Maximum capital.** The liquidity limit (an order at most 1% of the
  stock's 60-session median traded value) caps how much money an idea
  can take. With equal money per position, a pick is passed over when
  capital / positions is above 1% of its median traded value. An idea's
  maximum is the largest capital at which at most **10%** of its own picks,
  counted over all its decision dates, would be passed over for liquidity
  (owner, 2026-11-04). A capital level above it is flagged. Rupees at the
  time of each decision, not adjusted for growth.
- **The small account's own result beside the flag.** Each capital level
  reports the share of the idea's own picks its simulation actually bought,
  so a flagged level still shows what following the skip rule got, and
  how far it strayed from the tested ranking.

`research/retail.json` becomes version 2 with `max_capital.max_skipped_share`
0.10. Nothing else changes.

**Maximum capital of momentum_quality on dev, under version 2.** Selection
only, on the same decision dates as the 2026-11-04 check (no returns
computed, so no new trial):

| run | maximum capital | own picks passed over at Rs 20,000 / Rs 10 lakh | flagged |
| --- | --- | --- | --- |
| top 20 | Rs 55.1 lakh | 0% / 0% | no |
| top 5 | Rs 13.2 lakh | 0% / 0% | no |
| top 3 | Rs 7.3 lakh | 0% / 22.6% | at Rs 10 lakh |

The maximum is counted at a fixed capital. A compounding account crosses
it: the top 5 run started at Rs 10 lakh, grew past Rs 13.2 lakh within a
few years, and so passed over 20% of its own picks over the whole run (the
selection count above). The engine sizes to the capital typed on the day,
so the check applies to that day's amount.


## 2026-11-04: multibagger miss audit, step 2 results: `momentum_quality_wide_top20` on dev (owner's machine; owner's agent)

Step 2 of the section "2026-11-04: multibagger misses, settings fixed before
any run", run once as written. `momentum_quality_wide_top20` is exactly
`momentum_quality_top20` with the universe's median traded value floor at
Rs 50 lakh (5,000,000) over 60 sessions instead of Rs 2 crore; price floor
Rs 20, 250 sessions of history, EQ series, top 20, monthly, the signal, the
1% liquidity limit and every other setting unchanged. It is defined in code
(`research/harness.py` VARIANTS, `python3 research/harness.py --strategy
...`); `research/strategies.json` is not edited. The floor was not tuned and
step 1's results played no part in it.

Run through `research/harness.py` on dev only (2013-01-01 to 2020-12-31;
the panel is loaded to 2020-12-31 and no later; validation and the holdout
were not opened), walk-forward, at Rs 20,000 and Rs 10 lakh, real
Zerodha charges, whole shares, after gains tax. `momentum_quality_top20`
went through the same harness at the same settings as the comparison (it is
the Phase 2 check's idea under its strategies.json name and a fresh log
entry). Batch `mb-wide`, two logged attempts, ids 20261104-091647-0d02
(current) and 20261104-091752-c0d5 (wide).

**What retail rules version 2 changes here.** Nothing about how the two
capital levels work: both are still Rs 20,000 and Rs 10 lakh, both always
run, whatever the flags. Version 2 only adds a maximum-capital flag and the
share of the idea's own picks each level bought; both are reported below.

**What had to be added to the harness first** (the verdict rule needs an
after-tax Sharpe and drawdown, which it did not report): the after-tax
simulation's value series now has the last point replaced by the value after
the final sale, its charges and the last year's tax, and its Sharpe and worst
drawdown are reported for each level, each walk-forward year and the pooled
test years. The idea may carry its own universe (the base case uses the
panel's defaults, so older results are unchanged); the equal-weight universe
and the fractional reference follow the idea's own universe. The 464 earlier
tests still pass; 6 new offline tests cover the variant. The walk-forward
years, as before, do not credit dividends; the full-dev runs do.

**Which numbers are the verdict.** The walk-forward pooled test years
(2016 to 2020, each year from fresh capital, signal trained and read only on
earlier years), after tax, because the run was asked for walk-forward. The
full-dev run is shown beside it. They disagree at Rs 20,000, which is why
this is stated: the walk-forward is the verdict and was chosen before
either run was looked at.

| | current (Rs 2 crore) | wide (Rs 50 lakh) |
|---|---|---|
| **Walk-forward, pooled 2016 to 2020, after tax** | | |
| Rs 20,000: CAGR, Sharpe, worst drawdown | 1.6%, -0.16, -47.4% | 0.3%, -0.22, -53.1% |
| Rs 10 lakh: CAGR, Sharpe, worst drawdown | 10.3%, 0.26, -32.5% | 13.9%, 0.41, -31.2% |
| Walk-forward pooled, before tax: Rs 20,000 / Rs 10 lakh Sharpe | 0.03 / 0.40 | -0.04 / 0.55 |
| Equal-weight universe (own universe), Sharpe | 0.09 | 0.07 |
| **Full dev 2013 to 2020, after tax** | | |
| Rs 20,000: CAGR, Sharpe, worst drawdown | 12.2%, 0.37, -42.6% | 17.6%, 0.61, -36.2% |
| Rs 10 lakh: CAGR, Sharpe, worst drawdown | 19.6%, 0.66, -32.4% | 21.0%, 0.72, -32.3% |
| Full dev, before tax: Rs 20,000 / Rs 10 lakh Sharpe | 0.54 / 0.77 | 0.76 / 0.88 |
| **Ranking** | | |
| IC (t), full dev, 95 months | 0.045 (2.50) | 0.050 (3.01) |
| IC (t), walk-forward pooled test years, 59 months | 0.032 (1.56) | 0.039 (2.04) |
| Deciles monotonic | 0.92 | 0.98 |
| **Capital (retail rules version 2)** | | |
| Minimum capital (90% of dates) | Rs 3.18 lakh, flagged at Rs 20,000 | Rs 2.72 lakh, flagged at Rs 20,000 |
| Maximum capital | Rs 55.1 lakh, no flag | Rs 17.8 lakh, no flag |
| Own picks bought, Rs 20,000 / Rs 10 lakh | 82% / 100% | 89% / 85% |

Test years, before tax, Sharpe (Rs 20,000 / Rs 10 lakh): current 2016
-1.19 / -1.05, 2017 2.66 / 2.69, 2018 -1.54 / -0.87, 2019 -0.65 / 0.35, 2020
1.26 / 1.58; wide 2016 -0.60 / -0.40, 2017 2.04 / 2.57, 2018 -1.75 / -0.67,
2019 -0.98 / 0.17, 2020 1.52 / 1.68.

**The rule** (FINDINGS.md step 2): at both capital levels, after-tax Sharpe
above the current strategy's and worst drawdown no more than 5 points deeper.

- Rs 10 lakh: Sharpe 0.41 against 0.26 (above); drawdown -31.2% against
  -32.5% (not deeper). Passes.
- Rs 20,000: Sharpe -0.22 against -0.16 (not above); drawdown -53.1%
  against -47.4% (5.7 points deeper, over the 5 allowed). **Fails, on both
  counts.**

**Verdict: it does not pass dev.** The rule needs both levels. No validation
look is made, `strategies.json` stays as it is, and the wide strategy is not
adopted. (The option B bar, reported but not the test: IC t 3.01 on full dev
passes, 2.04 on the walk-forward years does not.)

**Reading.** On the whole of dev the wider net looks better at both levels,
and would pass the rule; the gain is larger at Rs 20,000. In the
walk-forward years, which are fewer (five) and read the signal only from the
past, it is better at Rs 10 lakh and slightly worse at Rs 20,000, where both
strategies lost money after tax over 2016 to 2020, the Rs 20,000 account
holding whole shares of a thinner set of stocks. The difference at Rs 20,000
is small against the noise of five test years and was not tested for
significance. Nothing here is tuned: the floor stays Rs 50 lakh and a failed
step 2 is not retried at another floor on these results. A new floor would be
a new idea with its own attempt.

## 2026-11-04: new direction, the multibagger protocol (owner agreed; fixed before any run)

The owner's goal is now to find stocks likely to return 3x or more within 3
years, equities only, NSE main board and SME, with no AI at run time and free
data only (HANDOVER.md, Direction 2026-11-04). This entry fixes the protocol
before any label or model is computed.

**Phase 0 check (not a run, not logged).** After removing the other asset
classes and the old skills, `momentum_quality` on dev through
`evaluate.ic_report` and `backtest.run` gave IC 0.045, t 2.50, monotonic
0.92, top 20 CAGR 26.16%, Sharpe 0.817, worst drawdown -38.5%: the numbers
of 2026-10-27 to the last digit. Nothing the research layer reads was
changed.

**Target.** For a stock and a month-end starting point t (the last stored
session of the month), the multiple is its close adjusted for corporate
actions at the month-end 36 months later over its adjusted close at t.
Dividends are not added. A hit is a multiple of at least 3. A stock with no
close in the 20 sessions up to the window's end is treated as delisted and
valued at its last close less 50% (the backtest's haircut), so dead stocks
count against their group.

**Splits by starting point.** Dev: 2013-01 to 2019-12 (windows end by
2022-12). Validation: 2020-01 to 2021-12 (by 2024-12). Holdout: 2022-01 to
2023-12, only starts whose window has ended in the store (by 2026-12; the
store ends 2026-10-30, so the last ten months of 2023 wait). Forward record:
2024 on. A model tested on starts from date T is trained only on starts
whose window ends on or before T (purged): validation trains on starts to
2016-12, the holdout on starts to 2018-12, walk-forward inside dev the same
way. One validation look per finished model; the holdout is opened once,
by the owner, in their own words.

**What this retires.** The momentum protocol's calendar holdout (2024 on):
validation labels here read 2023 and 2024 prices. `momentum_quality`
keeps its recorded results and stays the benchmark, compared on the new
splits.

### Phase 1: labels and base rates on dev (descriptive; settings fixed now)

`research/labels.py`. Descriptive: it ranks nothing, adopts nothing, and is
not a variant in the attempt count; logged once in `runs.jsonl` with kind
`audit`. Dev only: the store is read to 2022-12-31 and no further, and the
script refuses later starts.

- **Population at each t:** company equity shares (ISIN starting INE with
  security code 01 in characters 8 and 9; this drops rights entitlements,
  warrants and fund units), with a close in the 20 sessions up to t, last
  printed in series EQ, BE, BZ, SM, ST or SZ (SG, state government
  securities, and GB, gold bonds, are not equities). No liquidity or price
  floor: floors are a Phase 3 choice, made on tradability (order size
  against traded value), never on which bucket tripled most.
- **Listed apart, not counted:** a failed corporate action check at any
  session from t to the window's end (adjusted prices not trusted there).
- **Buckets at t:** series of the last print (main board EQ; main board BE
  or BZ; SME SM, ST or SZ); unadjusted close (under Rs 20, 20 to 100, 100 to
  500, 500 and over); traded value, the total over the 60 sessions to t over
  60, days without a trade counting as zero (under Rs 10 lakh, 10 to 50 lakh,
  50 lakh to 2 crore, 2 to 10 crore, 10 crore and over); listing age (first
  print in the store within 365 days before t, and after January 2012,
  against older); start year.
- **Reported per bucket:** stock-months, distinct stocks, share with a
  multiple of 3 or more (the base rate), 5 or more, 10 or more, 0.5 or less
  (halved), the median multiple, the share delisted, and the share whose
  adjusted close at some month-end in the window reached 3 times the start
  (touched, a secondary measure).
- **Caveat fixed now:** windows that start in neighbouring months overlap
  almost entirely, so stock-months are not independent. The real sample is
  about seven starting years, which is why the per-year table is shown and
  read first.

Market capitalisation is not a bucket here: shares outstanding are only in
the store from 2018 (results XBRL) and 2021 (shareholding). It is a Phase 2
check.

Rows per stock and start are written to `data/labels/dev.csv.gz` (local,
never committed) for Phase 4.

## 2026-11-04: Phase 1 results, labels and base rates on dev (owner's machine; owner's agent)

`research/labels.py`, run once as fixed above, logged in `runs.jsonl` with
kind `audit`. Starts 2013-01 to 2019-12 (84 month-ends), prices read to
2022-12-30 and no later, series EQ, BE, BZ, SM, ST and SZ. Tested offline
first on a made-up panel (`tests/test_labels.py`). `panel.load` gained a
`series` argument; its default (EQ, BE, BZ) is unchanged, so earlier runs
are unaffected.

**Totals.** 136,212 stock-months from 2,156 stocks; 36 stock-months (3
stocks) listed apart for a failed corporate action check. **13.0% of
stock-months tripled within 36 months**; 5.1% made 5x, 1.2% 10x; 23.0%
halved; median multiple 1.10; 9.2% delisted by the window's end (valued at
half their last close). 1,235 distinct stocks tripled from at least one
start. 21.1% touched 3x at some month-end in the window, so about two in five
of the stocks that reached 3x gave it back by the end.

| start year | stock-months | 3x | 5x | halved | median multiple | touched 3x |
| --- | --- | --- | --- | --- | --- | --- |
| 2013 | 18,157 | 26.0% | 11.3% | 12.0% | 1.70 | 37.6% |
| 2014 | 18,380 | 20.6% | 8.1% | 13.4% | 1.55 | 27.7% |
| 2015 | 18,120 | 11.7% | 4.2% | 18.7% | 1.18 | 22.5% |
| 2016 | 18,739 | 4.0% | 1.0% | 31.6% | 0.79 | 15.3% |
| 2017 | 19,807 | 1.5% | 0.3% | 49.6% | 0.51 | 5.2% |
| 2018 | 21,315 | 7.6% | 2.6% | 25.2% | 0.93 | 9.6% |
| 2019 | 21,694 | 20.6% | 8.6% | 10.3% | 1.60 | 31.5% |

| bucket at start | stock-months | stocks | 3x | halved | delisted |
| --- | --- | --- | --- | --- | --- |
| main board EQ | 119,415 | 1,946 | 12.8% | 21.3% | 6.8% |
| main board BE/BZ | 11,825 | 1,075 | 14.8% | 39.0% | 31.0% |
| SME | 4,972 | 209 | 13.5% | 27.1% | 14.9% |
| under Rs 20 | 30,663 | 922 | 16.1% | 36.1% | 23.7% |
| Rs 20 to 100 | 42,031 | 1,351 | 16.3% | 24.6% | 7.3% |
| Rs 100 to 500 | 41,678 | 1,248 | 10.7% | 18.5% | 3.8% |
| Rs 500 and over | 21,840 | 613 | 7.0% | 10.3% | 3.0% |
| traded value under Rs 10 lakh a day | 42,133 | 1,436 | 20.1% | 24.9% | 18.8% |
| Rs 10 to 50 lakh | 23,495 | 1,462 | 14.7% | 23.6% | 7.5% |
| Rs 50 lakh to 2 crore | 25,178 | 1,370 | 11.1% | 24.1% | 5.2% |
| Rs 2 to 10 crore | 24,061 | 1,132 | 8.6% | 23.8% | 4.0% |
| Rs 10 crore and over | 21,345 | 717 | 4.6% | 16.4% | 2.9% |
| listed under a year | 7,204 | 671 | 10.6% | 29.0% | 7.5% |
| listed a year or more | 129,008 | 2,074 | 13.2% | 22.7% | 9.3% |

**Reading.**

- **The market decides most of it.** The base rate runs from 1.5% (2017
  starts, windows ending in the 2020 crash) to 26% (2013 starts). Pooled
  rates mix regimes, so every model result must be judged against the base
  rate of its own start date (lift), and per start year, never pooled alone.
  Seven start years is a small sample of regimes.
- **Smaller, cheaper and thinner trade triples more often, and fails more
  often.** Under Rs 10 lakh a day: 20.1% tripled, 18.8% delisted, and an
  order of even Rs 20,000 would be days of volume. Rs 10 crore and over:
  4.6%. The gain from going small comes with more halving and delisting.
- **SME** tripled about as often as the main board (13.5%) but delisted
  twice as often and halved more; only 209 stocks, nearly all from 2017.
  Thin evidence.
- **BE/BZ** show the highest delisting (31%, which includes stocks
  suspended for over 20 sessions at the window's end).
- **Touched is not held.** 21.1% reached 3x, 13.0% were still there at 36
  months: the sell rule will matter as much as the pick (Phase 5).
- **Data caution.** The largest multiples include stocks under Rs 1 trading
  a few hundred rupees a day (DUCON 235x from Rs 0.25, ESSENTIA 112x,
  SCAPDVR 66x), which may be missed consolidations rather than real moves;
  not checked against the corporate action files yet. Well-known runs look
  right (ADANIGREEN 72x from April 2019, TANLA 62x and DEEPAKNTR 11x from
  December 2018). Phase 2 checks the top multiples against NSE's corporate
  action files. Few such rows exist, so the base rates barely move.

Nothing here chooses a floor: floors are fixed in Phase 3 on tradability.

## 2026-11-04: Phase 2 data checks, settings fixed before the run (owner's agent)

`research/coverage.py`. Descriptive: adopts nothing, ranks nothing, not a
variant; logged once with kind `audit`.

1. **Coverage** at each year-end session from 2013 and at the latest
   session: of company shares (ISIN INE, code 01) with a close in the 20
   sessions to the snapshot, by series group and by traded value (as Phase
   1), the share that had by then (filings broadcast before the snapshot
   day only) a results filing whose quarter ended within 400 days, a share
   count in it (paid-up capital over face value, both above zero), and a
   shareholding detail row with total shares for a quarter ended within 400
   days. No returns are read.
2. **SME history:** SME (SM, ST, SZ) securities trading per year, and how
   many have any results filing in the store.
3. **Extreme multiples:** a stock is flagged when its adjusted close moves
   by more than +50% or less than -33% (a ratio above 1.5 or below 1/3) in
   one session inside a window of its dev starts. NSE price bands are at
   most 20% for nearly every stock, so this points to a missed
   consolidation or split, an IPO or relisting day, or a real
   discontinuity. Reported: how many stocks are flagged, the 25 largest
   multiples with their flag, and the base rate without flagged stocks, as a
   sensitivity. The Phase 1 definition is not changed by it.
   Dev only: prices to 2022-12-30.

Decision rule fixed now: if the flagged stocks move the 3x base rate by
more than 1 percentage point, a corporate action cleaning step comes before
Phase 3. If not, Phase 1 stands.

## 2026-11-04: Phase 2 results, data checks (owner's machine; owner's agent)

`research/coverage.py`, three logged audit runs (the count is stated so none is hidden). Run 1 had a wrong SME
table (it counted a filing of any year, so an SME stock that later moved to the main board looked covered). Run 2
corrected that and added the promoter stake table and a trading-gap check. Run 3 changed the jump check to compare
each close with the previous close at any distance: runs 1 and 2 skipped gaps over 20 sessions, a hole between that
limit and the 60-session gap check. The note logged with run 3 says this "hid DUCON's chain switch"; that is wrong,
DUCON is not a chain switch (below). The code change stands; the note does not. Tests: `tests/test_coverage.py`.
Coverage is read from the store only, outcomes only for the multiples check (dev, prices to 2022-12-30).

**1. Fundamentals coverage** (company shares with a close in the 20 sessions to the snapshot; a results filing
broadcast before that day, quarter ended within 400 days; share count = paid-up capital over face value):

| year-end | all | main board EQ | main board BE/BZ | SME | promoter and public stake |
| --- | --- | --- | --- | --- | --- |
| 2013 to 2017 | 0% | 0% | 0% | 0% | 0% |
| 2018 | 84% | 95% | 81% | 0% | 0% |
| 2019 | 86% | 95% | 90% | 0% | 0% |
| 2020 | 89% | 96% | 90% | 0% | 0% |
| 2021 | 88% | 94% | 91% | 0% | 0% |
| 2022 | 89% | 96% | 87% | 3% | 73% |
| 2023 | 86% | 98% | 94% | 0% | 73% |
| 2024 | 81% | 98% | 96% | 0% | 70% |
| 2025 | 79% | 99% | 87% | 1% | 70% |
| 2026-10-30 | 73% | 91% | 75% | 0% | 65% |

- The share count is present wherever a filing is (within a point). Detailed holdings (FII, mutual funds,
  pledges) exist only for the latest quarter (56% of stocks on 2026-10-30, 0% before).
- The latest snapshot is lower because the September 2026 results season is not complete, and because the
  population now includes 571 SME stocks.
- By traded value on 2026-10-30, filing coverage is 41% (under Rs 10 lakh a day), 55%, 67%, 87% and 98% (Rs 10
  crore and over): the thinnest stocks, where most multibaggers start, have the least fundamentals.
- **Stake history.** NSE's shareholding feed stamps every filing before 2022-01-06 with a broadcast time on or
  after that day. So by broadcast time the promoter and public stake is usable from the end of 2022 (73%).
  `submitted_on` holds the original filing day and would date them from late 2021 (1,661 rows for the September
  2021 quarter; 63 and 84 for the March and June 2021 quarters), at the cost of trusting that field.
  Either way nothing before late 2021.

**2. SME.** 2 to 608 SME securities traded per year (109 in 2017, 284 in 2023, 608 in 2026). The store holds
results filings in the same year for 0 to 27 of them a year (16 in 2019, 22 in 2020, 27 in 2022, 3 in 2024). **SME
fundamentals are effectively absent**: SME companies file half-yearly in a different NSE feed that is not
collected. For SME stocks the model has prices, volume, delivery and age only, until that feed is found.

**3. The largest multiples.**

- Flag: an adjusted close above 1.5 or below one third of the previous close (at any distance). 57 of 2,156 stocks
  are flagged in their windows; the 3x base rate is 13.0% as fixed and 13.1% without them (1,205 of 1,235
  multibaggers remain). **Under the 1 point rule fixed before the run: Phase 1 stands, no cleaning step.**
- Trading gaps over 60 sessions inside a window: 3,207 stock-months from 162 stocks, with a 3x rate of 7.7%
  against 13.2% without them (sensitivity only, not adopted).
- **Correction to the Phase 1 entry.** It said the largest multiples (DUCON, ESSENTIA, SCAPDVR) "may be missed
  consolidations". DUCON was checked: one ISIN since 2012 (INE741L01018, formerly DYNATECH), Rs 0.25 in February
  2014, Rs 15.8 by September 2015, Rs 56 by December 2016. A real penny-stock run with turnover of about Rs 500
  a day, not a data error. The other very large multiples are the same kind: prices under Rs 1 (a Rs 0.05 tick
  is 20%) and turnover of hundreds to tens of thousands of rupees a day. Only one of the top 25 (ICIL) is flagged.
  Ordinary-price large winners (TANLA 62x, ADANIGREEN 72x, BCG 84x, TTML 55x) are not.
- Lesson for Phase 3: these multiples are real in the data and unobtainable in practice. They are handled by a
  tradability floor, not by cleaning.

**What this means for the plan (decisions in HANDOVER.md).** Dev starts 2013 to 2019 carry price, volume,
delivery, size and age features for every stock. Results fundamentals exist from 2018 starts and ownership from
late 2021. Under the purge rule (training windows must end before the first test start) a model cannot be trained
on fundamentals and then validated on a later regime: starts from 2018 resolve in 2021 or later, which is the
validation period. So fundamentals and ownership can be tested only forward, or on one regime with two training
start years.

## 2026-11-04: Phase 3, the multibagger model pre-registration (owner chose Option A, a Rs 25 lakh floor; fixed before any model run)

Nothing below has been run. Every setting is fixed now; a change after seeing any result is a new attempt, logged
as one. Phase 4 implements exactly this.

**Design (owner, Option A).** The validated core model uses only price, volume, delivery, size and age features,
which exist for every stock from 2013. Fundamentals (from 2018, main board only) and ownership (from late 2021)
cannot be trained and validated under the purge rule (Phase 2), so they are shown on stock pages as untested
context and scored on the forward record only, to be promoted by the rule of the themes layer (at least 12 months
and 30 scored lists, then a weight fixed in advance). SME results are not collected; SME stocks are scored on
price features alone.

**Universe at a start t** (point in time, to t's close):

- company equity share (ISIN INE, code 01), series EQ, BE, BZ, SM, ST or SZ at t;
- mean traded value over the 60 sessions to t of at least **Rs 25 lakh** (days without a trade count as zero),
  and a trade on at least 40 of those 60 sessions (so a few spike days cannot lift a thin stock over the floor);
- at least 252 sessions of history (the features need a year);
- no failed corporate action check in the past year.

No price floor: the value floor handles penny stocks (owner). On dev starts the floor leaves a median of 969
stocks a month (563 in 2013 to 1,360), of which 516 stock-months are SME (98 stocks) and 1,494 are BE or BZ; the
floor keeps 81,606 of the 136,212 stock-months that Phase 1 counted (60%; 10% of the SME ones), counts only, no outcomes. Phase 5 sizing: SME
shares trade in exchange-set market lots, and the Rs 20,000 account may not afford one; lot sizes come from the
Kite instruments file and are checked then.

**Features** (22; adjusted close unless stated; a year is 252 sessions; all to t's close; a stock short of any
window is outside the universe):

| group | features |
| --- | --- |
| momentum | `ret_1m` (21 sessions), `ret_3m` (63), `ret_6m` (126), `ret_12m` (252), `mom_12_1` (close 21 sessions ago over close 252 ago, less 1) |
| trend | `prox_52w_high` (close over its 252-session maximum), `prox_52w_low` (over its minimum), `ma200_ratio` (close over its 200-session mean), `ma50_over_ma200` |
| risk | `vol_1y` and `vol_3m` (standard deviation of daily returns over 252 and 63 sessions), `downside_vol_1y` (root mean square of negative daily returns), `max_drawdown_1y` |
| size and liquidity | `log_value_60` (log of mean daily traded value, 60 sessions), `value_trend` (60-session mean over the 250-session mean), `log_price` (unadjusted close) |
| delivery | `delivery_60` (mean delivery share, 60 sessions, days without it ignored), `delivery_trend` (over the 250-session mean) |
| age and series | `age_months` (months since the first close in the store, capped at 120), `pre_store` (1 when the first close is the store's first session), `lock_days_60` (sessions in the last 60 locked at a circuit: high equal to low after a move of 2% or more), `be_share_252` (share of the last 252 sessions in BE or BZ), `is_sme` (1 for SM, ST, SZ) |

Each feature except the two flags is replaced by its percentile rank among the universe on that start (0 to 1),
so regimes are normalised and no market-level feature enters (seven start years cannot support one). A missing
value becomes 0.5.

**Target.** The Phase 1 label: adjusted multiple at the month-end 36 months later, 3x or more; delisting valued at
half the last close. Starts and splits as in CLAUDE.md: dev 2013-01 to 2019-12, validation 2020-01 to 2021-12,
holdout sealed. Training starts must satisfy start + 36 months <= the first test start (purged).

**Candidates** (each one attempt, batch `mb-phase4`; hyperparameters fixed, no search):

| id | model |
| --- | --- |
| M1 | reference only: `momentum_quality` score through the same machinery |
| M2 | scorecard: mean of the percentile ranks of `mom_12_1` (+), `prox_52w_high` (+), `ma200_ratio` (+), `delivery_60` (+), `vol_1y` (-), `log_value_60` (-), equal weights, no training. The signs come from priors and from the Phase 1 base rates by size; the size sign is the one informed by data already seen |
| M3 | logistic regression, all 22 features, L2, C = 1.0, lbfgs, max_iter 1000 (scikit-learn) |
| M4 | gradient boosting (scikit-learn `HistGradientBoostingClassifier`): max_depth 3, learning_rate 0.05, max_iter 200, min_samples_leaf 500, l2_regularization 1.0, early stopping off, random_state 0 |

scikit-learn is added to `requirements.txt` in Phase 4 (BSD licence, runs offline on the owner's Mac).

**Scoring one start.** Rank the universe by the model's score (M3 and M4: predicted probability of 3x). Take the
top K, K = 5% of the universe rounded up (about 30 to 70). The measures: the top K's 3x rate against the
universe's 3x rate on the same start (the lift), plus their median multiple, share halved and share delisted.
Pooled over a period: sum of top-K hits over sum of top-K picks, over the universe's rate over the same starts.
K is a measuring cut for comparing models; it is not a basket size (Phase 5).

**Folds.**

- Dev walk-forward, trained only on starts whose windows end before the test year: test 2017 starts (train 2013-01
  to 2014-01), test 2018 (to 2015-01), test 2019 (to 2016-01). Each model runs once per fold.
- Validation: test starts 2020-01 to 2021-12, trained on starts 2013-01 to 2016-12. One look per finished model.
- Holdout: sealed. The model published for live use is trained on every start whose window has ended.

**Pass bar.** A candidate passes only if all hold:

1. on dev walk-forward, its lift is above 1.0 in at least two of the three test years (a model that fails gets no
   validation look);
2. on validation, its pooled lift is at least 1.5 and above 1.0 in each of 2020 and 2021;
3. its pooled validation lift is above M1's on the same starts (otherwise momentum alone does the job);
4. its top K's share halved is not more than 5 percentage points above the universe's, on validation.

If several pass, the simplest is kept (M2, then M3, then M4), not the best, to limit selection among three tries.
If none passes, no model is called predictive: the product shows base rates and the scorecard only as a
description, labelled "no validated edge", and the forward record decides.

Reported, not part of the bar: calibration by decile of predicted probability (M3, M4), a bootstrap 90% interval
over stocks, lift by series group and by price bucket, the top K's overlap with M1.

**What the evidence can and cannot be.** Validation has 24 starts in two start years whose windows overlap almost
entirely, so about two independent regimes. A lift of 1.5 can arise by chance. Passing licenses forward tracking
and nothing more; the frozen monthly lists (Phase 7) are the test that cannot be fooled.

## 2026-11-04: Phase 4 implementation notes, written before the first model run (owner's agent)

Choices the Phase 3 entry left open, and one slip in it. Fixed now; no model has been run.

- **Slip: the feature count.** The entry's text says 22 features, its table lists **23** (5 momentum, 4 trend, 4
  risk, 3 size and liquidity, 2 delivery, 5 age and series). The table is the definition; M3 and M4 use all 23.
- **Company shares only.** The universe applies the Phase 1 rule (ISIN INE, security code 01): ETFs and fund units
  (INF), which trade in EQ, are out. The first draft of `features.py` missed this; a test found it before any run.
- **Windows.** Returns and rolling statistics use the adjusted close carried forward at most 20 sessions (as the
  labels do), so a stock that did not trade on a day has a zero return that day. A rolling window needs at least
  80% of its sessions (252: 200; 200: 160; 63: 50; 60: 10 for delivery, 50 of 250 for its mean; 50: 40); below
  that the feature is missing and becomes 0.5. "At least 252 sessions of history" counts sessions with a close.
  "A trade on 40 of the last 60 sessions" counts sessions with a close.
- **Ranks** are percentile ranks with ties averaged, within each start's universe; `age_months` is ranked, the two
  flags are not.
- **Dropped rows.** A (start, stock) whose 36-month window holds a failed corporate action check is dropped from
  training and test tables (Phase 1's "listed apart"). A stock without a momentum_quality score is last in M1.
- **Ties** in a score are broken by security id. K is the top 5% rounded up per start.
- **Prices read.** Dev reads to 2022-12-31 (the last session is 2022-12-30); validation to 2024-12-31. The runner
  refuses a panel past those dates and refuses validation for a model that has not passed dev or has had its look.
- **Dependency.** scikit-learn 1.9.1 installed in the project's `.venv`; `requirements.txt` states `scikit-learn>=1.5`.
- **Dev walk-forward, once per model.** The runner logs one `mb_model` entry per model per stage in `runs.jsonl`.
  If a bug is found after a run, the fix is a new attempt and both runs stay logged.
