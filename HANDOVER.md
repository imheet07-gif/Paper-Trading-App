# Handover

For anyone, person or agent, joining the work. Read this first, then
`CLAUDE.md` (the rules, which bind everyone), `research/FINDINGS.md` (every
experiment so far, failures included), `CONCEPTS.md` (the vocabulary) and
`docs/RUNBOOK.md` (how the owner runs it).

Last updated 2026-11-04. Earlier directions (the monthly momentum scan, the
trade finder, risk levels, options, metals, funds, themes, the twelve
`trader-*` skills) are superseded; they are in git history before branch
`multibagger/phase0`, and their experiments stay in FINDINGS.md.

## Direction (owner, 2026-11-04): find multibaggers, equities only

- **Goal:** rank Indian stocks by how likely they are, on tested evidence,
  to return **3x or more within 3 years**, with every answer showing the
  base rate and saying plainly that most picks will not triple.
- **Equities only,** NSE main board and **SME included**. No funds, ETFs,
  metals, F&O or commodities. One small-cap index is kept only as the
  benchmark.
- **No Claude at run time.** Everything runs as commands, the local app and
  macOS `launchd`; a repo test fails on any AI SDK import or API host.
  Claude sessions only build.
- **Free data only.** No point-in-time vendor exists for free (checked
  2026-11-04: exchange XBRL for results starts in 2017 on both NSE and BSE;
  Screener.in is restated and not point in time; MCA filings are paid). So
  price, volume, delivery, size and ownership carry the history from 2012,
  and fundamentals are tested on starting points from 2018. Parsing older
  result PDFs is a possible later phase.
- **Zerodha Kite, read-only:** live quotes on the watchlist, holdings sync,
  instruments. Never an input to the model (Kite's history carries
  survivorship bias and no delivery data). The daily login is the one manual
  step, and predictions never depend on it.
- **Protocol:** splits by starting point, purged training, one validation
  look, the holdout opened only by the owner (CLAUDE.md, "The multibagger
  protocol"; FINDINGS.md 2026-11-04).

## Where things stand

- **Data:** `data/market.db` on the owner's Mac. NSE prices and delivery
  2012-01-02 to 2026-10-30 (8,359 ISINs; 2026 alone has 3,001 EQ, 652 BE,
  582 SM and 372 ST), corporate actions, results XBRL (106,682 quarters,
  from December 2017), shareholding (from 2021), SAST (from 2015), insider
  trades (to May 2026), deals (since collection started).
- **Machinery kept:** panel loader, the momentum backtester and harness
  (whole shares, charges, tax, capital levels, walk-forward), the miss
  audit. Phase 0 check: `momentum_quality` on dev reproduces exactly (IC
  0.045, t 2.50, CAGR 26.16%, Sharpe 0.817, worst drawdown -38.5%).
- **What we learned already** (FINDINGS.md 2026-11-04): the old strategy
  caught 12.7% of dev multibaggers, late; 80% started below its liquidity
  or price floors. Widening the floor alone failed dev.

## Phases

| # | phase | output | pass condition | status |
| --- | --- | --- | --- | --- |
| 0 | Clean slate | other assets and skills removed, no-AI and no-order tests, runbook | tests green, known run reproduces | done 2026-11-04 |
| 1 | Labels and base rates | `research/labels.py`: 3x in 36 months per stock and month-end, SME included; base rates by size, price, liquidity and series, dev only | table in FINDINGS.md | done 2026-11-04: 13.0% of dev stock-months tripled; 1.5% to 26% by start year |
| 2 | Data checks | fundamentals coverage per year and series; how far SME history goes | coverage report | next |
| 3 | Pre-registration | feature list, universe floors (set on tradability, never on which bucket tripled), pass bar | committed to FINDINGS.md before any model run | |
| 4 | Models | scorecard, then logistic, then gradient boosting; one logged attempt each | beats the base rate and plain momentum on precision in the top K, calibrated, in most starting years | |
| 5 | Basket simulation | 15 to 30 names, long holds, costs, tax, capacity at Rs 20,000 and Rs 10 lakh | after tax, beats the small-cap index | |
| 6 | App and Kite | watchlist, stock page, track record, live quotes, holdings sync, nightly health check and notification | read-only, runs with no Claude | |
| 7 | Forward record | each month's list frozen, scored at 12, 24 and 36 months | runs 12 months or more before real money relies on it | |

## Rules that matter most

1. **No order tools, ever.** Never remove the denials in
   `.claude/settings.json`, never call an order tool.
2. **No AI at run time.** Nothing in `core/`, `research/` or `app/` calls
   Claude or any model API.
3. **The holdout belongs to the owner.** Never read holdout starting points
   unless the owner asks in their own words. Validation: one look per
   finished model, stated in the run's note.
4. **Every run is logged** in `research/runs.jsonl`, append-only; on a merge
   conflict keep every line. FINDINGS.md is append-only too.
5. **Pre-register before running.** Settings fixed in FINDINGS.md first; a
   change after seeing results is a new attempt.
6. **Facts from the publisher's own file**
   (`docs/solutions/conventions/verify-exchange-and-broker-facts-against-the-source-file.md`).
7. No em dashes in any file; a test checks.

## How to divide the work

Work branches from `multibagger/phase0` (or its successor once merged),
named `<owner>/<task>`, with a pull request back into it. Claim a task in
the table below in a small commit before starting.

## Tasks

| task | files | owner | status |
| --- | --- | --- | --- |
| Phase 1: labels and base rates | `research/labels.py`, `research/panel.py` (`series`), tests | owner's agent | done 2026-11-04 (FINDINGS.md) |
| Phase 2: data coverage report: fundamentals and shares outstanding per year and series, SME history, the top multiples checked against NSE's corporate action files | `research/coverage.py` | unassigned | next |
| Older fundamentals from result PDFs (optional, after Phase 4) | `core/` | unassigned | parked |
| BSE-only companies (BSE's API refuses scripts) | | unassigned | open: a known blind spot, stated on every list |

## Known data facts that cost time to learn

- NSE does not adjust `prev_close` on ex-dates; factors come from
  corporate action subjects and are checked against the price move.
- A split usually creates a new ISIN; ISINs are chained by symbol.
- Insider feed: `pid` is the company, `did` the transaction, and `did`
  repeats; empty after 2026-05-02.
- Results feed ends February 2025; the announcements feed carries results
  since, with a gap in the March 2025 quarter (FINDINGS.md).
- Results XBRL from April 2025 (the March 2025 quarter) is in
  `api/integrated-filing-results`
  (`xbrl` link per filing; pages of 20 unless `size` is given, so check
  `totalCount`). No ISIN in the rows. `broadcast_Date` is null on
  revisions, and is a median of two hours after the results PDF's
  announcement. A revision leaves its original in the feed. At half-year
  ends some files carry the half-year and no quarter (84 companies in
  March 2025, 31 in September 2025, 9 in March 2026). Most March 2025
  main-board ones are the March quarter dated from October (Coforge,
  CG Power), so a March quarter is taken only when the year confirms which
  it is; September ones have no year and are not derived. About 70 companies type a
  wrong ISIN in their own file (letter O for zero, swapped digits, the
  pre-split ISIN): kept when the file's Symbol is the feed's.
- 16% of results rows name a retired ISIN; SAST spells modes in two cases.
- `nseindia.com`'s homepage and `api.bseindia.com` answer 403 to scripts;
  NSE's API and file archive work.
