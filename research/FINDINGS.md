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
