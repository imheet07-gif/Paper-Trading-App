# Proposal: fundamentals for the trade finder

From the owner, 2026-10-28. Approved by the owner the same day (sections
6 and 7). Nothing here is claimed, built or tested yet. The tasks are in
HANDOVER.md to claim the usual way, and the catalogue's settings go into
`research/FINDINGS.md` as a pre-registration before any run.

This replaces this file's first version, the six-tab desk. The owner's
trade finder direction in HANDOVER.md superseded that plan the same day.
The earlier text is in this branch's history.

## 1. Why

The trade finder's evidence card shows a stock's latest quarter. The owner
also wants fundamentals more broadly: the numbers a company reports
(growth, margins, ROE, debt), both on the card and, once tested, in the
ranking. Third-party APIs for Indian filings exist. The question is where
they help and where they would mislead.

## 2. Two jobs, two kinds of source

| job | example | needs | source |
| --- | --- | --- | --- |
| **Context** on a card | "Revenue up 18% on a year earlier, ROE 21%, debt to equity 0.4" | Correct and current | Any reliable source, labelled with its source and date |
| **Ranking**, a tested signal | "Buy the top tenth by earnings surprise" | Point in time: each number as first published, with the moment it became public | NSE's own filings only |

Why vendor data cannot rank:

- **Restated history.** Vendors usually store figures as later restated,
  after mergers, accounting changes and corrections. A backtest on them
  uses numbers nobody had at the time.
- **No publication time.** Vendors rarely give the time each figure became
  public, and the backtester trades only after publication
  (`broadcast_at`).
- **Project rule.** CLAUDE.md already requires facts from the publisher's
  own file.

## 3. What exists, and the gaps

- **Quarterly results** (`core/financials.py`): revenue, profit and EPS,
  plus banks' interest and provisions. They come from NSE's results XBRL,
  keyed on publication time, from 2013 **to February 2025 only**. Where NSE
  serves the XBRL since then is an open task in HANDOVER.md.
- **No balance sheet.** ROE, debt and asset figures cannot be computed.
- **The card is silently stale.** `research/advise.py` `results_context`
  shows the latest stored quarter. For every stock today that is December
  2024 or earlier, and the card does not say the quarter is old.
- **Screener.in export importer:** waiting for a sample file from the
  owner (HANDOVER.md). It is for display only, never for backtests.

## 4. Proposed work

Small first. Each item lists the files it touches so it can be claimed
without conflicts.

| id | work | touches | for |
| --- | --- | --- | --- |
| F1 | Staleness label on the card: when the latest stored results are older than the last quarter that should have reported, say "no recent results stored (latest: <quarter>)" and do not show growth as current | `research/advise.py` (the owner of that file decides) | context |
| F2 | Results XBRL since March 2025 (existing open task) | `core/financials.py` | both |
| F3 | Every metric line the results XBRL carries (see below and the catalogue) | `core/financials.py`, fixtures | both |
| F4 | Screener.in export importer (existing task). Parked: the owner has no Screener.in subscription | `core/screener_export.py` | context |
| F5 | Evaluate one vendor API (checklist below); approved by the owner 2026-10-28 | none until chosen | context |
| F6 | The metrics catalogue: every metric on the card, and each one tested as its own pre-registered signal (below) | `research/signals.py`, `research/event_signals.py` | ranking |

### F3: what the results XBRL holds for quality (checked 2026-10-28)

Checked against the three fixtures in `tests/fixtures/financials/`. These
are unchanged files as NSE published them. The TCS file is a March quarter
with the full year.

- **No statement of assets and liabilities.** The March 2020 TCS file has
  no borrowings, total assets or cash. It does carry segment assets and
  liabilities, but only multi-segment companies file those, so they
  cannot stand in for a balance sheet.
- **Net worth, once a year.** The March (year-end) file carries
  `PaidUpValueOfEquityShareCapital` and
  `ReserveExcludingRevaluationReserves`. Their sum for TCS is Rs 84,749
  crore at March 2020. That gives an annual, point-in-time net worth, and
  so ROE.
- **Interest burden, every quarter.** Every file carries `FinanceCosts`
  and `ProfitBeforeTax`. Interest coverage is (PBT plus finance costs)
  divided by finance costs. It is a quarterly, point-in-time stand-in for
  leverage.
- **Debt to equity is not available** from these files. Borrowings would
  need the annual report XBRL filed with the Ministry of Corporate Affairs
  (not on the allow-list), or a vendor that passes the F5 checklist.
- **Not yet checked:**
  - a September-quarter file, where a half-yearly statement may appear;
  - files from 2013 to 2016, the older taxonomy, for the reserves element;
  - how often reserves are missing.

  Run these checks on the cached files before pre-registering.

F3 is therefore: widen `core/financials.py` to every line in the catalogue
below that the results XBRL carries:

- the expense lines (materials, employee costs, depreciation, finance
  costs, other expenses);
- exceptional items;
- face value and paid-up capital, every quarter;
- reserves, from year-end files.

All of it comes from files already cached.

### F5: before paying for any API

1. Does it give each figure as first reported, with its publication date?
2. How far back does it go, and does it include smaller NSE companies?
3. Licence: the project is meant to ship as a plugin, so each user would
   need their own key, and the terms must allow that.
4. Does it add anything over F2 and F3, which are free? Debt and
   borrowings are the obvious gap.

Vendors found so far, such as FinancialFilings.com, are unverified. Try a
sample before trusting any claim.

### F6: the metrics catalogue

The owner wants many metrics, not one chosen over another. So:

- **On the card:** every metric below that has data, each labelled with
  its source and date.
- **In research:** each metric is its own signal, tested on dev.

The whole list is pre-registered in one go, so the number of attempts is
known before the first run and the deflated Sharpe counts every one. A
metric joins the trade finder's ranking only after it passes dev and its
one validation look. Until then it is labelled untested context.

Sources:

- **NSE:** NSE's results XBRL. Point in time, usable for testing.
  Needs F3, and F2 for live use.
- **NSE + prices:** the same data combined with the stored daily closes.
  Point in time.
- **Vendor:** shown on the card only. Used for testing only if the vendor
  passes the F5 checklist, publication dates especially.

| family | metric | definition | source |
| --- | --- | --- | --- |
| Growth | Revenue growth | Latest quarter against the same quarter a year earlier; and trailing four quarters against the four before | NSE |
| Growth | EPS growth | The same, on diluted EPS restated to today's share count with the stored corporate action factors | NSE |
| Growth | Earnings surprise | EPS change on a year earlier, divided by the standard deviation of that change over the previous 8 quarters; needs 9 quarters; stale after 120 days | NSE |
| Profitability | Operating margin | (PBT + finance costs + depreciation - other income) over revenue, trailing four quarters | NSE |
| Profitability | Net margin | Profit to owners over revenue, trailing four quarters | NSE |
| Profitability | ROE | Profit to owners, trailing four quarters, over the latest year-end paid-up capital plus reserves | NSE |
| Profitability | ROCE | Operating profit over capital employed (equity plus borrowings) | Vendor |
| Safety | Interest coverage | (PBT + finance costs) over finance costs, trailing four quarters; not for lenders | NSE |
| Safety | Debt to equity | Borrowings over net worth | Vendor |
| Safety | Earnings stability | Share of the last 8 quarters with a profit; and the volatility of quarterly operating margin | NSE |
| Earnings quality | Other income share | Other income over PBT: profit that does not come from the business | NSE |
| Earnings quality | Exceptional items share | Exceptional items over PBT, trailing four quarters | NSE |
| Earnings quality | Cash conversion | Operating cash flow over net profit | Vendor (cash flow is not in the results XBRL) |
| Valuation | Earnings yield | Trailing four quarters' EPS over today's price (P/E inverted, so loss-makers sort last instead of breaking the scale) | NSE + prices |
| Valuation | Book to price | Year-end net worth per share over today's price (P/B inverted) | NSE + prices |
| Valuation | Sales to price | Trailing revenue per share over today's price | NSE + prices |
| Size | Market cap | Today's price times shares outstanding (paid-up capital over face value, from the latest results) | NSE + prices |
| Ownership | Promoter, FII, DII and MF stakes and changes; pledge flag | Already in `shareholding_detail`. History from September 2021 only, so context, not testable on dev | NSE |

**Lenders** (banks, NBFCs, insurers) get their own lines where the XBRL
carries them:

- net interest income growth;
- provisions over operating profit;
- ROE.

Interest coverage, operating margin and debt to equity are left blank
for lenders, never shown as zero.

**Composites, pre-registered with the list:**

- `quality`: equal-weighted average rank of ROE, interest coverage and
  earnings stability;
- `value`: of earnings yield, book to price and sales to price;
- `growth`: of revenue growth, EPS growth and earnings surprise.

Combinations with `momentum_quality` come only after each part has been
tested on its own.

**Rules for every metric:**

- Usable from the session after the latest input's `broadcast_at`.
- Loss-makers and negative net worth sort last instead of being dropped,
  so the card can still show them.
- Ranks are within the same eligible universe the trade finder uses.

**What still stands between these metrics and their dev tests:**

1. F3's open checks:
   - the older 2013 to 2016 files carry the same elements;
   - how often reserves and expense lines are missing;
   - the September file.
2. F3's parser change, with fixtures and tests.
3. The catalogue's NSE rows and the three composites written into
   FINDINGS.md unchanged, with their pass marks: about 16 dev runs,
   declared up front.
4. The vendor rows wait for F5. A vendor that passes the checklist adds
   its rows to the tests in a later, separate pre-registration.

ROE and book to price need a year-end net worth, so they start after the
March 2014 results (about May 2014). The quarterly metrics can start in
2015, once 8 or more quarters have built up. Live use of any NSE metric
needs F2.

## 5. Fit with the trade finder order

- **Data tasks:** F2 to F4 are `core/` work. They touch no app or advise
  file and can run alongside the trade finder steps.
- **F1:** a few lines in `advise.py`. Best done by whoever owns that file
  now.
- **F6:**
  - **The card:** can show the metrics as soon as F3 lands, labelled
    untested, through a small change in `advise.py` by its owner.
  - **The ranking:** each metric joins the queue of "shorter-horizon
    candidates" and ranks only after it passes.

## 6. Decisions

Made by the owner, 2026-10-28:

- No Screener.in subscription: F4 is parked.
- Evaluate a vendor API: F5 is approved.

- Many metrics rather than one chosen over another: the F6 catalogue, with
  each metric on the card and each tested as its own signal.

- F1 to F3 approved as tasks to claim.
- The F6 catalogue and its three composites approved for
  pre-registration, unchanged.

The tasks are listed in HANDOVER.md for claiming. F2 is the existing
results XBRL row there.

## 7. Broker reports and company communications (owner approved 2026-10-28)

The owner wants broker reports tracked. They are treated the same way as
themes, deals and copycat: context on the card and a forward scorecard,
never a reason to rank a pick until a properly dated history has been
tested.

**Why this shape:**

- **Estimate revisions** (analysts raising or cutting profit forecasts)
  are among the better-evidenced stock signals worldwide. Ratings and
  target prices are weaker: they lean optimistic and rarely say sell.
- **No free history.** NSE does not publish broker research. Dated
  histories of ratings, targets and estimates come from paid databases.
  Without one, nothing here can be backtested.
- **Copyright.** Broker reports are copyrighted. The owner may read and log
  the ones they receive. The project never scrapes broker sites and never
  stores or ships report text. A plugin user brings their own data.
- **Regulation.** Personal use is fine. Passing on signals built from
  reports needs SEBI Research Analyst registration (README fine print).

| id | work | touches | for |
| --- | --- | --- | --- |
| B1 | Add to the F5 vendor evaluation: does the vendor offer consensus estimates, estimate revisions, ratings and target prices, each with the date it changed? If yes, show them on the card, and pre-register an estimate-revisions signal for testing, separately from the F6 batch | none until a vendor is chosen | context, then ranking |
| B2 | Broker call log and scorecard | new `core/broker_calls.py`, tests | context, forward test |
| B3 | Company communications on the card: links to the latest earnings-call transcript, investor presentation and analyst meet, from the NSE announcements already fetched (`trader-news` classifies them as "investor meeting") | `research/advise.py` (its owner), `trader-news/` | context |

**B2 in detail.**

- **The log:** the owner logs each broker call they come across in
  `trader/broker_calls.jsonl`, which is local, append-only and never
  edited. Each record holds:
  - when it was logged (IST);
  - the report date;
  - the broker;
  - the stock's ISIN;
  - the rating (buy, hold or sell, mapped from the broker's own words);
  - the target price and horizon;
  - the source, as a link or "received by email".

  No report text is kept.
- **No backdating:** a call logged after its report date is scored from
  the logging time, not the report date.
- **Scoring:** each call is scored at 1, 3 and 12 months on the stored
  closes, against the Nifty 500. It is reported per broker:
  - hit rate;
  - average excess return;
  - how often the target was reached.

  Scores are shown only once a broker has at least 20 scored calls.
- **On the card:** the latest logged calls on the stock, labelled "broker
  view, untested", with each broker's scorecard so far.

**Order:**

- B3 is cheap and can start now.
- B2 can start now. Its scorecard needs months of calls before it says
  anything.
- B1 rides on F5.

### Decisions for section 7

Made by the owner, 2026-10-28: add broker reports to the plan.

Still open:

1. How the owner wants to log calls:
   - a short command;
   - a line in a chat with Claude;
   - forwarding report emails for Claude to read the rating and target
     from (the text is not kept).

