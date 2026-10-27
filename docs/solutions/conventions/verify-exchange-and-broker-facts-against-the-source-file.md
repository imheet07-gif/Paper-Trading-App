---
title: Verify every exchange and broker fact against the publisher's own file before building on it
date: 2026-10-27
category: conventions
module: market data, charges and broker imports (core/, research/)
problem_type: convention
component: data_model
severity: high
applies_when:
  - Encoding a rate, holiday, field meaning or file layout published by NSE, BSE, SEBI or a broker
  - Writing a parser for an exchange or broker file from documentation, search results or memory
  - A number from a secondary source (blog, broker help page, search snippet) will feed charges, tax or signals
  - A new data source or file format is added to the market store
related_components: [testing_framework]
tags: [nse, zerodha, data-verification, fixtures, charges, holidays, parsers]
---

# Verify every exchange and broker fact against the publisher's own file before building on it

## Context

In one session, five facts that looked settled were wrong when checked
against the real file, and each would have flowed silently into charges,
tax lots or signals:

| assumed | source of the assumption | what the real file showed |
| --- | --- | --- |
| NSE had 15 weekday holidays in 2026 | NSE circular as summarised by broker sites | NSE's holiday master has 16: 2026-01-15 (Maharashtra civic elections) was missing |
| NSE transaction charge 0.00297% | search results quoting older Zerodha pages | Zerodha's live charges page says 0.00307%; a real P&L ledger confirmed it to 4 decimals |
| bhavcopy `PREVCLOSE` is adjusted on ex-dates | general knowledge of exchange data | it is the raw prior close, so a 1:1 bonus looks like a -51% day |
| Console "Quantity Discrepant" is extra to "Quantity Available" | column names | it is included: Console's own present value only adds up that way |
| the EQ series holds company shares | the series name | 446 ETFs and fund units (INF ISINs) trade in EQ and passed the liquidity filter |

Search results also disagreed with each other (three different NSE rates
on Zerodha's own pages, two Muhurat dates for Diwali 2026), which is a
sign the answer has to come from the publisher, not from consensus.

## Guidance

1. **Read the fact from the publisher's own file or page**, fetched
   directly, and record the source and date next to the value. Rates and
   holidays live in data files with a source line and a verified flag
   (`core/charges.json`, `core/nse_holidays.json`), never in code.
   Holidays are loaded from NSE's holiday master by `update-holidays`
   rather than typed in.
2. **Reconcile against a real document the user already has.** A broker
   ledger, contract note or holdings summary is the strongest check: the
   charges model only became `verified` after reproducing a real Console
   P&L ledger head by head, which also exposed STT rounding once per day
   and BSE's lower rate for exclusive scrips.
3. **Cut test fixtures from the real file**, not from a hand-written
   imitation, and name the source URL and download date in the fixture
   folder's README. Personal files are the exception: copy their exact
   layout with made-up values.
4. **Check derived totals against the publisher's own totals.** Console
   prints its present value; the parser's sum must match it. A mismatch
   is a misread column, not rounding.
5. **Make an unknown an error, not a default.** A year without a holiday
   list, a broker without rates or an unparseable date raises; it is never
   filled with a guess.
6. **When a check fails, record which fact was wrong and why** (commit
   message, `research/FINDINGS.md` for research results, or a learning
   here), so the next reader does not repeat the assumption.

## Why This Matters

Each of these errors was small at its source and large downstream: one
missing holiday shifts T+1 settlement dates, a fee off by 0.0001% breaks
reproducing a contract note to the paisa, an unadjusted previous close
poisons every momentum signal, and ETFs in the universe distorted the
low-volatility backtest from 8.1% to 13.2% CAGR. None of them raised an
error; the numbers simply looked plausible.

## When to Apply

- Before encoding any published rate, calendar, field meaning or layout.
- When a secondary source and the publisher could disagree, which is
  always: broker help pages and search snippets lag the exchange.
- When a parser's output will be summed, adjusted or traded on.

## Examples

- Holidays: typed list from broker sites (15 days) replaced by
  `python3 core/nse_calendar.py update-holidays <NSE holiday master JSON>`,
  which found 2026-01-15.
- Charges: `python3 core/zerodha_console.py verify-charges tradebook.xlsx
  pnl.xlsx` compares the model with the broker's ledger head by head
  before a block is marked `verified`.
- Universe: after reading the real ISIN prefixes in the store, the
  research universe takes INE (company) ISINs only, with a test that an
  INF ISIN is never eligible (`tests/test_research.py`,
  `test_funds_and_etfs_are_never_eligible`).

## Related

- `docs/solutions/logic-errors/nse-bhavcopy-corporate-action-adjustment.md`
  covers the corporate action case in depth.
