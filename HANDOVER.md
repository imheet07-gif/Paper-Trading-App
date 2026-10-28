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
- **Skills:** `trader-budget`, `trader-risk`, `trader-rebalance` and
  `trader-screener` are ported to India (rupees, whole shares, an order sheet
  for manual entry); the other seven are still the US originals; porting
  them is Phase 2. See "Gaps in the ported skills" below.
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

## Direction (owner, 2026-10-28): the trade finder

The product is no longer a monthly strategy list. It answers two questions:

- **"I have Rs 50,000 to invest today: what is the best trade?"**
- **"What about this stock?"**

Each answer comes with its evidence and rationale. This replaces the
six-tab plan in draft PR #12. Its after-tax check and its rule that trades
come only from tested evidence or the owner's own choice carry over.

What it must be honest about, shown in every answer:

- "Best" means highest ranked by evidence that passed the protocol, never
  a forecast. Today that evidence is `momentum_quality` alone. Every
  other source (copycat, deals, SAST, shareholding, results) is context on
  the card, labelled untested. None of them ranks a pick until it passes
  dev and validation.
- The evidence is for a one-month hold, entered at the next open from
  end-of-day data. A single stock swings far more than the tested
  basket of 20, and the card shows by how much (see the concentration
  test below).
- If the after-tax check shows a Nifty momentum index fund beats the
  strategy, the answer leads with the fund.
- Order sheet only. Nothing places an order.

### `research/advise.py --amount 50000` (and an Ask tab in the app)

1. **Candidates:** the tested signal's ranking on the latest stored
   session, through `backtest.select_top` as the scan does.
2. **Hard filters** (a pick failing any is dropped, and the card says why):
   - the universe filters;
   - EQ series only (BE is trade-for-trade);
   - not locked at a circuit on the last session;
   - no failed corporate action check;
   - the order is at most 1% of the stock's 60-day median traded value;
   - one share fits in the slice.
3. **Sizing for the amount:**
   - whole shares, at a limit price on the tick;
   - charges from `core/charges.py`, with the round trip shown as a
     percent of the money.
   - Before the first run, fix and write in FINDINGS.md:
     - the minimum slice per stock;
     - the most stocks the amount allows while the round trip stays under
       a fixed cost.
   - The answer gives the top pick and that basket side by side.
4. **Evidence card per pick:**
   - the signal's score, its rank and percentile, and its parts (12-1
     return, one-year volatility);
   - what this rank bucket returned over the next month on dev and on
     validation: mean, share of months positive, worst month. Read from
     the backtest store, never the holdout;
   - latest quarter from `core/financials.py` against the same quarter a
     year earlier;
   - shareholding changes, SAST and bulk or block deals in the last 90
     days, labelled untested context;
   - results or ex-dates due inside the holding month;
   - pledges and other red flags;
   - entry limit, quantity and charges;
   - the review date: the next rebalance day. No stop-loss or target,
     since none is tested; the card says so.
5. **Stock mode, `--stock SYMBOL`:**
   - the same card for any stock: its rank on the tested signal, its
     filters and red flags, and how it compares with the top pick;
   - when it ranks low it says plainly that there is no tested reason
     to prefer it.

### Research needed for honest cards (pre-register in FINDINGS.md first)

- **After-tax check:** `momentum_quality_top20` after short- and long-term
  capital gains tax, with dated rates from the Income Tax Department's own
  text in a data file, against a Nifty momentum index fund over 2013 to
  2023 (fund costs included). Do this first: it can change the answer.
- **Concentration:** dev and validation results for the top 1, 3 and 5
  against the top 20 (CAGR, Sharpe, worst drawdown, share of months
  losing), so a card can say what holding one stock has meant.
  Logged runs, no holdout.

Order: after-tax check, concentration test, `advise.py` with the card,
stock mode, the Ask tab. New tested signals (for example earnings surprise
once results XBRL since March 2025 is found) widen the candidates later.

## Tasks

Split as of 2026-10-27: the second agent takes the build and research
work; the owner's agent is kept for light upkeep (reviewing pull requests,
the paper test, small fixes), because its budget is low. Change the owner
column freely.

| task | files | owner | status |
| --- | --- | --- | --- |
| Position sizing and risk control (per-stock cap, cash buffer, volatility sizing) for `momentum_quality`, tested on dev only; one validation look for the finished version, recorded in FINDINGS.md | `research/backtest.py`, `research/paper.py` | second agent | dev runs done 2026-10-27: no variant beats equal weight on Sharpe, not adopted (FINDINGS.md); drawdown check open; branch `second-agent/position-sizing` |
| SAST Regulation 29 promoter open-market buying as a signal (`sast` table, `promoter=1 AND mode='Open market' AND side='Acquisition'`, usable the session after `disclosed_at`), settings fixed before testing, dev test | `research/event_signals.py`, `research/signals.py` | second agent | dev_2016 run done 2026-10-27: too sparse for an IC, backtest Sharpe -0.05, failed and not adopted (FINDINGS.md) |
| Shareholding patterns ingest: NSE `api/corporate-share-holdings-master` answers with `broadcastDate`, store promoter, FII, MF and public stakes and pledges per quarter, keyed on publication time | `core/shareholding.py`, tests, fixtures | second agent | done on branch `second-agent/shareholding`: promoter, public and employee trust stakes per quarter keyed on broadcast time (revisions flagged), plus FII, DII, MF, insurance, retail, total shares and pledge flags from each filing's XBRL; pledged quantities wait for a sample with pledges |
| Results XBRL financials: find where NSE serves the XBRL for each results filing (the announcement has `hasXbrl` but only the PDF link), parse with the standard library | `core/financials.py`, tests, fixtures | second agent | done to February 2025 on branch `second-agent/financials`: the quarter from each linked XBRL (Ind-AS old and new formats, banks), keyed on the results broadcast time; open: where NSE serves the XBRL for results since March 2025 (announcements flag `hasXbrl` without a link) |
| Bulk and block deals, collected daily from `nsearchives.nseindia.com/content/equities/bulk.csv` and `block.csv` (no history available) | `core/deals.py`, a line in `research/scan.py` update | second agent | done on branch `second-agent/deals`: `core/deals.py`, collected by `research/scan.py update` each evening |
| Port `trader-news` and `trader-alerts` to India (NSE announcements, NSE calendar, read-only quotes) | `trader-news/`, `trader-alerts/` | second agent | done on branch `second-agent/skill-ports-2` (on top of the first ports); alerts read prices from the store or a file, no live quotes yet |
| Port `trader-budget`, `trader-risk`, `trader-rebalance` and `trader-screener` to India (rupees, whole shares, an order sheet for manual entry, verified charges). Offline only: tick sizes, price bands and circuit limits, lot sizes and other NSE facts not yet in `core/` data are left out and listed in each SKILL.md | `trader-budget/`, `trader-risk/`, `trader-rebalance/`, `trader-screener/`, their tests | second agent | done on branch `second-agent/india-skill-ports`, PR into `phase0-foundations`; gaps listed below |
| Screener.in export importer: the owner's own Excel exports, read locally, for manual review only (not point-in-time, never for backtests) | `core/screener_export.py` | second agent | waiting for a sample file from the owner |
| Paper forward test from 2026-10-30, small fixes, reviewing the second agent's pull requests | `research/scan.py`, `research/paper.py`, `app/` | owner's agent | running |
| Port `trader-copycat` to India: discover, follow and copy named investors from the shareholding filings' named holders and bulk and block deals; mutual fund portfolios as a second source later | `trader-copycat/`, `core/shareholding.py` (holders) | second agent | done on branch `second-agent/copycat`; MF portfolios open |
| Mutual fund accumulation (AMC monthly portfolios; AMC sites are not on the cloud allow-list) | to be decided | unassigned | open |
| Indian tax module (FIFO lots exist in `core/lots.py`; TMCV/TMPV and ITC Hotels cost splits from the companies' notices; the owner's Zerodha tax P&L exports are the check) | `core/tax.py`, `trader-tax/` | unassigned | parked |

Next for the second agent: the trade finder above, in its stated order.

## Gaps in the ported skills

The four ported skills (`trader-budget`, `trader-risk`, `trader-rebalance`,
`trader-screener`) were ported offline, without NSE access, so every
exchange or broker fact that is not already in `core/` data, read from the
publisher's own file, was left out rather than guessed. Each SKILL.md has
a "Left out on purpose" section and the scripts print what they did not
check. Open items, each a task for whoever has the source file:

| gap | what it means today | what closes it |
| --- | --- | --- |
| Tick sizes | Order sheets are market orders; no limit price is computed or checked against the tick. `research/paper.py` has ticks from the market store, the skills do not | a tick per instrument in a `core/` data file or a read of the store, from NSE's own file |
| Price bands and circuit limits | A line can PASS trader-risk and still be rejected or sit unfilled at the band | NSE's daily price band file, stored with its source line, and a check in `trader-risk` |
| F&O lot sizes and margins | F&O lines are blocked unless `allow_options`, and never sized | NSE's lot size file; out of scope until someone wants F&O |
| Same-day use of sale proceeds | Sells are counted as freeing cash for later buys in the same sheet; settlement is T+1 and same-day credit is the broker's rule | the broker's own page, recorded in `core/charges.json` style with a source line |
| Symbol to ISIN | Closed on branch `second-agent/skill-ports-2`: budget, risk and rebalance read `data/instruments.json` (`core/instruments.py build` from NSE's equity list), fill ISINs, refuse a wrong one, and say when no registry was built | build the registry on the owner's machine |
| Orders entered today | The user enters orders by hand, so `max_orders_per_day` counts one sheet, not the day | read the day's tradebook import |
| Charges rates | Estimated with `core/charges.py`; shown UNVERIFIED until every block in `core/charges.json` is matched to a contract note | match a buy-day contract note |
| Metrics source for the screener | The user's own CSV only | the open XBRL financials and Screener.in export tasks |
| Example prices | The examples and tests use made-up round prices and made-up companies, labelled as such | nothing; they test arithmetic, not markets |

Knock-on effects on skills that are still US originals:

- `trader-broker`: ported on branch `second-agent/paper-broker` as a paper
  trader (owner's decision, 2026-10-27): tickets, an explicit yes per line,
  fills into `trader/paper_portfolio.json` with real charges. No broker
  connection and no live mode.
- `trader-alerts`: ported (`second-agent/skill-ports-2`).
- `trader-tax` and `trader-copycat` still write US-shaped portfolio and
  targets files.

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
