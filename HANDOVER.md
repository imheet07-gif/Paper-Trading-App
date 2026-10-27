# Handover

For anyone, person or agent, joining the work. Read this first, then
`CLAUDE.md` (the rules, which bind everyone), `README.md` (the plan and
phase status), `CONCEPTS.md` (the vocabulary) and `research/FINDINGS.md`
(every experiment so far, including the failures).

Last updated 2026-10-27.

## What this is

An India (NSE/BSE) research tool built from the trader-skill pack. It
produces signals and order sheets; it never places an order. The owner
enters every trade by hand in their broker. Personal use for now, built to
ship later as a plugin anyone can install, so nothing may assume one user
or one broker.

## Where things stand

- **Data:** NSE prices and delivery from 2012, corporate action factors
  checked against prices, ISINs joined across splits, index closes, and
  company events (insider trades to May 2026, results release times to
  today, SAST Regulation 29 holder changes to today). All in a local
  SQLite store, `data/market.db`, built by the commands below.
- **Research:** a backtester with the dev (2013 to 2020) / validation
  (2021 to 2023) / holdout (2024 on) protocol. One strategy has passed
  validation: `momentum_quality`, top 20, monthly rebalance. Timing
  filters, insider buying and results drift failed (FINDINGS.md).
- **Daily scan and paper ledger:** the scan runs only what is in
  `research/strategies.json`. Paper trading starts at the 2026-10-30
  rebalance and must run 3 to 6 months before any real money.
- **App:** `python3 app/server.py`, a local page with Update, Scan, a
  Strategy lab, the paper portfolio and the runs log.
- **Skills:** the eleven `trader-*` folders are still the US originals;
  porting them is Phase 2.
- **Tests:** `python3 -m unittest discover -s tests`, offline, all must
  pass before any push.

## Setting up

```bash
git clone <repo> && cd Paper-Trading-App && git checkout phase0-foundations
pip install numpy pandas            # research/ only; casparser only for CAS PDFs
python3 core/bhavcopy.py backfill --start 2012-01-01
python3 core/corporate_actions.py fetch --start 2012-01-01
python3 core/events.py fetch
python3 -m unittest discover -s tests
python3 app/server.py
```

Each person builds their own store; `data/`, `private/` and `trader/` are
git-ignored and never shared through the repo. The owner's holdings,
tradebooks and paper ledger stay on the owner's machine: do not ask for
them, and build any importer from a redacted sample.

## Rules that matter most when two agents share the work

1. **No order tools, ever.** Never remove the denials in
   `.claude/settings.json`, never call an order tool.
2. **The holdout belongs to the owner.** Never run `--open-holdout` or
   look at 2024-onward results unless the owner asks in their own words.
   Validation is also scarce: one look per finished idea, stated in the
   run's `--note`.
3. **Every run is logged** in `research/runs.jsonl`. It is append-only:
   on a merge conflict keep every line from both sides. The count of
   variants tried is what keeps the deflated Sharpe honest, so a run is
   never deleted.
4. **FINDINGS.md is append-only too:** add a dated section, do not rewrite
   another's.
5. **Facts from the publisher's own file** (CLAUDE.md, and
   `docs/solutions/conventions/verify-exchange-and-broker-facts-against-the-source-file.md`).
   Check that a key is unique and an ISIN current before keying on it.
6. **A strategy change is a new strategy:** validate it before it goes in
   `research/strategies.json`.
7. No em dashes in any file; a test checks.

## How to divide the work

- `main` is untouched; all work so far is on `phase0-foundations`.
- Each agent works on its own branch from `phase0-foundations`, named
  `<owner>/<task>` (for example `friend/shareholding`), and opens a pull
  request into `phase0-foundations`. The owner merges.
- Before starting a task, claim it in the table below in a small commit
  on your branch and push it, so the other side sees it. Keep a task to
  the files it lists; if it needs a shared file, keep that change small.
- Merge `phase0-foundations` into your branch before opening the PR and
  run the full tests.

**Shared files** (touch lightly, expect merges): `README.md` status
table, `CLAUDE.md` layout list, `research/signals.py` (`SIGNALS`,
`DESCRIPTIONS`), `app/server.py` (`SIGNALS`), `research/FINDINGS.md`,
`research/runs.jsonl`, `tests/test_repo.py` (root layout list).

## Tasks

Split as of 2026-10-27: the second agent takes the build and research
work; the owner's agent is kept for light upkeep (reviewing pull requests,
the paper test, small fixes), because its budget is low. Change the owner
column freely.

| task | files | owner | status |
| --- | --- | --- | --- |
| Position sizing and risk control (per-stock cap, cash buffer, volatility sizing) for `momentum_quality`, tested on dev only; one validation look for the finished version, recorded in FINDINGS.md | `research/backtest.py`, `research/paper.py` | second agent | dev runs done 2026-10-27: no variant beats equal weight on Sharpe, not adopted (FINDINGS.md); drawdown check open; branch `second-agent/position-sizing` |
| SAST Regulation 29 promoter open-market buying as a signal (`sast` table, `promoter=1 AND mode='Open market' AND side='Acquisition'`, usable the session after `disclosed_at`), settings fixed before testing, dev test | `research/event_signals.py`, `research/signals.py` | second agent | open |
| Shareholding patterns ingest: NSE `api/corporate-share-holdings-master` answers with `broadcastDate`, store promoter, FII, MF and public stakes and pledges per quarter, keyed on publication time | `core/shareholding.py`, tests, fixtures | second agent | open |
| Results XBRL financials: find where NSE serves the XBRL for each results filing (the announcement has `hasXbrl` but only the PDF link), parse with the standard library | `core/financials.py`, tests, fixtures | second agent | open |
| Bulk and block deals, collected daily from `nsearchives.nseindia.com/content/equities/bulk.csv` and `block.csv` (no history available) | `core/deals.py`, a line in `research/scan.py` update | second agent | open |
| Port `trader-news` and `trader-alerts` to India (NSE announcements, NSE calendar, read-only quotes) | `trader-news/`, `trader-alerts/` | second agent | open |
| Screener.in export importer: the owner's own Excel exports, read locally, for manual review only (not point-in-time, never for backtests) | `core/screener_export.py` | second agent | waiting for a sample file from the owner |
| Paper forward test from 2026-10-30, small fixes, reviewing the second agent's pull requests | `research/scan.py`, `research/paper.py`, `app/` | owner's agent | running |
| Mutual fund accumulation (AMC monthly portfolios; AMC sites are not on the cloud allow-list) | to be decided | unassigned | open |
| Indian tax module (FIFO lots exist in `core/lots.py`; TMCV/TMPV and ITC Hotels cost splits from the companies' notices; the owner's Zerodha tax P&L exports are the check) | `core/tax.py`, `trader-tax/` | unassigned | parked |

Suggested order for the second agent: position sizing, then the SAST
signal, then shareholding, then XBRL financials. The first two use data
already in the store.

## Known data facts that cost time to learn

- NSE does not adjust `prev_close` on ex-dates; factors come from
  corporate action subjects and are checked against the price move.
- A split usually creates a new ISIN; ISINs are chained by symbol.
- Insider feed: `pid` is the company, `did` the transaction, and `did`
  repeats; empty after 2026-05-02.
- Results feed ends February 2025; the announcements feed carries results
  since, with a gap in the March 2025 quarter (FINDINGS.md).
- 16% of results rows name a retired ISIN; SAST spells modes in two cases.
- `nseindia.com`'s homepage and `api.bseindia.com` answer 403 to scripts;
  NSE's API and file archive work.
