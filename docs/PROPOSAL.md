# Proposal: fundamentals for the trade finder

A suggestion from the owner, 2026-10-28, for the agents to discuss. Nothing
here is claimed, built or tested, and no shared file is changed. Once
agreed, tasks are claimed in HANDOVER.md the usual way, and any signal's
settings go into `research/FINDINGS.md` as a pre-registration before any
run.

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
| F3 | Net worth and finance costs from the results XBRL (see below) | `core/financials.py`, fixtures | both |
| F4 | Screener.in export importer (existing task). Parked: the owner has no Screener.in subscription | `core/screener_export.py` | context |
| F5 | Evaluate one vendor API (checklist below); approved by the owner 2026-10-28 | none until chosen | context |
| F6 | Fundamental signals, pre-registered (below) | `research/signals.py`, `research/event_signals.py` | ranking |

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

F3 is therefore: add `FinanceCosts` (every quarter) and paid-up capital
plus reserves (year-end files) to `core/financials.py`, from files
already cached.

### F5: before paying for any API

1. Does it give each figure as first reported, with its publication date?
2. How far back does it go, and does it include smaller NSE companies?
3. Licence: the project is meant to ship as a plugin, so each user would
   need their own key, and the terms must allow that.
4. Does it add anything over F2 and F3, which are free? Debt and
   borrowings are the obvious gap.

Vendors found so far, such as FinancialFilings.com, are unverified. Try a
sample before trusting any claim.

### F6: fundamental signals, in the protocol

Each signal is a new idea:

- pre-registered in FINDINGS.md;
- tested on dev;
- given one validation look;
- added to the trade finder's ranking only after it passes.

Until then fundamentals are context on the card, labelled untested.

- **`earnings_surprise`** (needs F2 for live use).
  - **Score:** latest quarter's diluted EPS minus the same quarter a year
    earlier, divided by the standard deviation of that change over the
    previous 8 quarters.
  - **Needs:** at least 9 quarters.
  - **Usable:** from the session after `broadcast_at`, and stale after 120
    days.
- **`quality`** (needs F3).
  - **Score:** the average percentile rank of:
    - ROE: trailing four quarters of profit attributable to owners,
      divided by the net worth from the latest year-end results;
    - interest coverage: trailing four quarters;
    - earnings stability: the share of the last 8 quarters with a
      profit.
  - **Excluded:** loss-makers over the trailing four quarters.
  - **Banks and NBFCs:** excluded, since interest coverage means nothing
    for a lender. A lender quality score is a separate idea.
  - **Usable:** from the session after the latest input's `broadcast_at`.
- - **Combinations with `momentum_quality`**, for example 50/50 ranks, are a
  separate pre-registration, made only after each part has been tested on
  its own.

**What still stands between `quality` and its dev test:**

1. F3's open checks:
   - reserves present in the 2013 to 2016 files;
   - how often reserves are missing;
   - the September file.
2. F3's parser change, with fixtures and tests.
3. The definition above written into FINDINGS.md unchanged, with its
   pass marks.

ROE needs a year-end net worth, so the first usable month is after the
March 2014 results (about May 2014). Dev then runs from mid-2014 to 2020.
Live use after testing also needs F2.

## 5. Fit with the trade finder order

- **Data tasks:** F2 to F4 are `core/` work. They touch no app or advise
  file and can run alongside the trade finder steps.
- **F1:** a few lines in `advise.py`. Best done by whoever owns that file
  now.
- **F6:** waits for F2 or F3 and joins the queue of "shorter-horizon
  candidates": a new candidate ranks only after it passes.

## 6. Decisions

Made by the owner, 2026-10-28:

- No Screener.in subscription: F4 is parked.
- Evaluate a vendor API: F5 is approved.

Still open:

1. Approve F1 to F3 as tasks to claim.
2. Pre-register `earnings_surprise` now (it can be tested on 2013 to 2020
   today), or wait for F3 and test it alongside `quality`.
3. Approve the `quality` definition in F6, which replaces debt to equity
   with interest coverage, or change it before pre-registration.
