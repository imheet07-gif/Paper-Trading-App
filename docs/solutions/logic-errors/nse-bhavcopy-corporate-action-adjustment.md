---
title: NSE bhavcopy prices are unadjusted, and corporate action factors fail in three non-obvious ways
date: 2026-10-27
category: logic-errors
module: market data (core/bhavcopy.py, core/corporate_actions.py, research/panel.py)
problem_type: logic_error
component: data_model
symptoms:
  - Bonus and split ex-dates show as 50-90% one-day crashes in back-adjusted price series
  - "Corporate action check reports adjusted ex-date moves like -88.6% or -47.5% for bonuses"
  - All face value splits reported as having no trade on the ex-date
  - Momentum and volatility signals would rank stocks on fake crashes
root_cause: data_integrity
resolution_type: code_fix
severity: critical
tags: [nse, bhavcopy, corporate-actions, bonus, split, isin, adjustment, backtest]
---

# NSE bhavcopy prices are unadjusted, and corporate action factors fail in three non-obvious ways

## Problem

Every return-based signal (momentum, volatility, drawdown) needs prices
adjusted for bonuses, splits and rights. NSE's daily bhavcopy is
unadjusted, and the obvious ways of adjusting it from NSE's own data are
wrong in ways that pass a casual look and only show up across the full
2012-2026 history.

## Symptoms

- A 1:1 bonus showed as a -51% day (Surya Roshni, ex 2025-01-01: close
  Rs 594.65 then Rs 289.20).
- After adding factors, the first full run flagged 111 of 1,466 bonus,
  split, rights and restructure events, many with adjusted ex-date moves of
  -47.5% to -89.5% (Vakrangee 2012-04-12, Indoco, CCL, Deepak Nitrite,
  Minda Corp).
- Every face value split in a 22-day sample was reported as "no trade on
  the ex-date".

## What Didn't Work

- **Reading the factor from the file's previous close.** The first design
  assumed NSE adjusts `PREVCLOSE` on ex-dates, so `prev_close(t) /
  close(t-1)` would give the factor. It does not: on Surya Roshni's ex-date
  the file's previous close is still Rs 594.65. The approach was removed;
  `core/bhavcopy.py:20` now states prices and previous close are exactly as
  published.
- **One regex per event kind.** A first match on "Bonus a:b" returned
  before the split part of "Bonus 1:1 And Face Value Split From Rs.10/- To
  Re.1/-" was read, so the factor was 0.5 instead of 0.05.
- **Checking each corporate action row on its own.** NSE often lists a
  bonus and a split as two separate rows on the same ex-date (Vertoz,
  2024-07-05). Each row alone fails the price check even though their
  product is right.
- **Querying prices by the ISIN in the action row.** A face value split
  usually issues a new ISIN, so the old ISIN has no row on the ex-date and
  the new ISIN has no row before it. NSE's action feed also sometimes
  carries a stale ISIN (Surya Roshni reported as INE335A01012, traded as
  INE335A01020).

## Solution

Take factors from NSE's corporate action feed
(`www.nseindia.com/api/corporates-corporateActions`), never from the
bhavcopy, and handle the three failure modes explicitly:

1. **Parse bonus and split together.** `parse_subject` searches for both
   parts and multiplies them (`core/corporate_actions.py:63-69`):
   bonus a:b gives b / (a + b), face value X to Y gives Y / X, and NSE's
   spellings ("Split From Rs.10/- To Re.1/-", "Split Rs.10/- To Rs.5/-",
   "From 10/- To Face Value 2/-") all match.
2. **Verify per security-day, not per row.** `verify()`
   (`core/corporate_actions.py:256`) multiplies every stated factor on one
   security's ex-date and checks the product against the actual move:
   close(ex) / close(before) / factor must be within 25%
   (`NORMAL_DAY`, line 49). A price-implied restructure factor is dropped
   when the same day also has a stated factor (line 270), so the move is not
   counted twice.
3. **Join ISINs into one security.** `links()` (line 160) links an ISIN
   that stops trading to one that starts under the same symbol within 10
   calendar days (`LINK_GAP_DAYS`, line 157); `chain()` (line 179) gives all
   ISINs of a security. Factors are stored on the latest ISIN, and checks,
   adjustment and research panels run over the whole chain. Stale ISINs in
   the feed are resolved through prices by symbol.

What still fails the check (5 securities over 2012-2026, where NSE's feed
is missing or wrong, for example Dharan 2021: split listed 2 to 1, price
fell tenfold) is listed by `failed_checks()` (line 294) and kept out of
the research universe for a year (`ISSUE_DAYS`, `research/panel.py:26`).
A wrong number is never traded on.

## Why This Works

The bhavcopy is a trade record, not an adjusted series, so the only
source of truth for a factor is the corporate action announcement, and
the only check on the announcement is the price itself. Checking the
combined factor per security-day matches how the exchange applies events
(all of one day's events act at once), and joining ISINs matches how
depositories issue new ISINs on face value changes. Excluding what still
fails turns a silent data error into a visible, bounded gap.

## Prevention

- Never derive adjustment from `prev_close`; the tests pin that NSE does
  not adjust it (`tests/test_market.py`, `test_udiff_keeps_stocks`).
- Run `python3 core/corporate_actions.py check` after any backfill or
  parser change and read the counts: failed checks should stay in single
  digits, and unexplained jumps among liquid stocks (price at least Rs 20,
  Rs 2 crore median traded value) should all be real events.
- Raw feed rows are cached in `data/corporate_actions_raw.json`; after a
  parser change run `rebuild`, not a refetch.
- Tests cover each failure mode: combined subjects (`test_subjects`), same
  day rows checked together (`test_two_events_on_one_day_are_checked_together`),
  a split across an ISIN change (`test_split_checked_across_the_isin_change`,
  `test_links_and_chain`), a deliberately wrong factor
  (`test_wrong_factor_is_flagged`) and the exclusion list
  (`test_failed_check_is_listed`).

## Related Issues

- `research/FINDINGS.md` records the signal results that depend on this
  adjustment.
