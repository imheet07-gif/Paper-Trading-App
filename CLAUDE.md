# CLAUDE.md

A multibagger finder for Indian equities (NSE main board and SME). It ranks
stocks by how likely they are, on tested evidence, to return 3x or more
within 3 years, and says plainly that most picks will not. Equities only:
funds, metals, ETFs, F&O and commodities were dropped on 2026-11-04. It must
run with no Claude and no AI service at run time; Claude sessions are for
building only. Read HANDOVER.md (direction, phases, tasks) and
research/FINDINGS.md (every experiment, failures included) before starting.
docs/RUNBOOK.md is how the owner runs it day to day.

## Layout

- `core/` data ingest and accounting, standard library. Modules import one
  another with `sys.path.insert(0, <repo>/core)`, the way `core/charges.py`
  imports `money`. `bhavcopy.py` (NSE daily prices and delivery from 2012,
  every series, delisted stocks included), `corporate_actions.py` (factors,
  checked against the price move), `instruments.py` (ISIN registry joined
  with Kite tokens), `nse_calendar.py`, `money.py`, `charges.py`,
  `zerodha_console.py` and `lots.py` (the owner's Zerodha exports), and:
- `core/events.py` company events from NSE into the store, keyed on
  publication time (raw months cached in `data/events_raw/`): insider
  trades (feed empty after May 2026), results (results feed to February
  2025, then the announcements feed) and SAST Regulation 29 holdings
  changes. The app's update refreshes them. `research/event_signals.py`
  turns them into signals.
- `core/shareholding.py` quarterly shareholding patterns from NSE into the
  store (`shareholding` table: promoter, public and employee trust stakes
  per quarter, keyed on broadcast time; raw answers cached in
  `data/shareholding_raw/`). A revised filing replaces the original in
  NSE's list: `submitted_on` keeps the original filing day, the numbers are
  the revised ones. `shareholding_detail` adds, from each filing's XBRL,
  FII (FPI I and II), domestic institutions, mutual funds, insurance,
  retail, total shares and NSE's yes/no promoter pledge answers.
  `shareholding_holders` keeps each filing's named
  holders (promoters, public holders above 1%); names
  include private individuals and stay in the local store only. Pledged
  quantities are not read yet (no sample with pledges seen).
- `core/financials.py` quarterly results from the XBRL linked in NSE's
  results feed (2013 to March 2025) and, from April 2025 (the March 2025
  quarter), in its Integrated Filing (Financials) feed (`financials` table:
  revenue, profit, EPS, and for banks interest and provisions, for the
  quarter only, in rupees, keyed on the feed's broadcast time; parsed files cached in
  `data/financials_raw/`). A revision is stored beside its original under
  its own time; readers take the latest published by their day. A March
  file with no quarter context gives the quarter only when the year
  confirms it (half-year less December, no EPS, or a quarter dated as the
  half-year), flagged in `derived`. The nightly update fetches new
  filings. Since F3 it also reads the expense lines, finance costs,
  depreciation, exceptional items, face value, paid-up capital and, from
  the year's column of March files, reserves; each cached file keeps every
  plain figure of its quarter and year (`facts`, `FACTS_VERSION`).
- `core/deals.py` NSE bulk and block deals (`deals` table), collected by
  the scan's evening update. NSE publishes only the latest session, so a
  missed evening is lost; raw files cached in `data/deals_raw/`. Client
  names include private individuals: they stay in the local store only.
- `core/tax.py` income tax per financial year on listed equity gains
  and dividends for a resident individual: sells matched oldest first by
  `core/lots.py`, sections 111A and 112A rates by transfer date, the 1
  April 2018 end of the 10(38) exemption, 31 January 2018 grandfathering,
  set-off and eight-year carry-forward, dividends at the slab rate from
  April 2020 (typed in, never stored), cess. Every rule is in
  `core/tax_rates.json`, dated and quoted from incometaxindia.gov.in's
  section pages, under the Income-tax Act, 1961 to March 2026 and the
  Income-tax Act, 2025 from April 2026 (sections 196, 198, 90, 108, 111,
  92). Dates it does not cover (before FY2012-13, or from 1 April 2027,
  which needs the Finance Act, 2027) are refused. Surcharge is
  left out. `research/aftertax.py` runs a strategy with each year's tax
  paid from the portfolio (a hook in `backtest.run`) against an index
  fund, dev_2016 and validation only.
- `core/intraday.py` intraday candles (5-minute by default) for every
  registry stock from Kite Connect into `data/intraday.db`, keyed on the
  Kite token with an ISIN table; unadjusted, paise, UTC seconds. It calls
  only Kite's session and historical-candle addresses (checked in code and
  tests). The key and secret come from the git-ignored `.env`; the day's
  session token is kept in `private/` (mode 600) until 06:00 IST the next
  morning. Kite lists only stocks listed today: intraday research carries
  survivorship bias and must say so.
- `research/` the research engine (numpy and pandas). `panel.py` loads the
  market store; `misses.py` the multibagger miss audit of the old strategy
  (it becomes the label generator); `fundamentals.py` point-in-time
  fundamentals from the `financials` table; `event_signals.py`. The
  momentum machinery stays as the benchmark any new model must beat:
  `signals.py`, `backtest.py`, `evaluate.py`, `regimes.py`, `profiles.py`,
  `exits.py`, `aftertax.py`, `retail.json` / `retail.py` (the retail rules:
  capital levels, liquidity, charges, tax, minimum and maximum capital),
  `harness.py` (one testing machine: whole shares, real charges, after-tax,
  both capital levels, walk-forward, attempts counted), `scan.py` (its
  `update` is the nightly data update) and `paper.py`. `strategies.json`
  lists what the scan may run; changing a strategy means validating the new
  version first. Every run is logged to `research/runs.jsonl`, which is
  committed so the count of variants tried survives.
- `app/` the local app (`server.py`, standard library; `static/` in plain
  JavaScript, nothing loaded from outside). It runs only `research/jobs.py`
  tasks with arguments checked against fixed lists; never add a task that
  places orders or opens the holdout. Rebuilt around the watchlist in Phase 6.
- `docs/RUNBOOK.md` setup, the nightly and monthly jobs, and what to do when
  a source breaks, written for a person with no Claude session.
- `docs/solutions/` documented solutions to past problems, with YAML
  frontmatter (`module`, `tags`, `problem_type`).
- `CONCEPTS.md` shared domain vocabulary (ISIN chains, corporate actions).
- `tests/` unittest, offline only. `tests/_load.py` loads a script by folder.
  Fixtures under `tests/fixtures/`, cut from real published files; each
  fixture folder's README names its sources and date.

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests
```

## The multibagger protocol (owner, 2026-11-04)

- **Target:** a stock's return (close adjusted for splits, bonuses and other
  corporate actions; dividends not added) over the 36 months from a month-end starting point is at least
  3x. Base rates by size, price and series are shown beside every result.
- **Universe:** INE company shares in EQ, BE and the SME series (SM, ST),
  with liquidity floors fixed in FINDINGS.md before any model run.
- **Splits by starting point** (a label needs 36 months of later prices):
  dev starts 2013 to 2019, validation starts 2020 and 2021, holdout starts
  2022 and 2023 (only those whose window has ended in the store), and
  everything after is the forward record. A model is trained only on
  starting points whose 36-month window ends on or before the first test
  starting point (purged). Validation gets one look per finished model; the
  holdout is opened once, only by the owner, in their own words.
- This replaces the old momentum protocol's calendar holdout (2024 onward):
  validation labels read 2023 and 2024 prices. The momentum strategy keeps
  its recorded results but its holdout is retired.
- Pre-register (features, settings, pass bar) in FINDINGS.md before a run;
  never tune on validation; every attempt is logged.

## Rules

- No AI at run time: no Claude API, no other LLM API, no AI SDK import in
  `core/`, `research/` or `app/` (a repo test checks). Every feature is a
  command first, then an app task, then scheduled by macOS `launchd`.
- `core/` stays standard library. `research/` may use numpy, pandas and
  libraries pinned in `requirements.txt` that run offline on the owner's Mac;
  a new one needs a line of justification in its commit.
- Personal details in imported statements (name, PAN, email, address,
  phone, owners) are dropped at read time; account and folio numbers are
  masked. Passwords come from a prompt or an environment variable, never
  the command line, and are never stored or printed.
- Money is `Decimal`, never float, in everything that touches accounts,
  charges, tax and order sheets; the market store keeps integer paise.
  `research/` panels use float64 for speed: their numbers are estimates,
  never written back into accounting. Quantities are whole shares.
- Rates, holidays and anything else published by an exchange, regulator or
  broker live in data (`core/charges.json`, `core/nse_holidays.json`) with a
  source line, read from the publisher's own page or file, never invented.
  Unknown means an error, not a default.
- ISIN is the key for a stock. Symbols and broker tokens are attributes.
- Times are timezone-aware; the market runs on IST (UTC+05:30, no DST).
- Never commit personal data: `private/`, `trader/` and `data/` are ignored.
  Build parsers from redacted samples.
- Nothing in this project places, modifies or cancels an order, at any
  broker, ever. The end of every flow is an order sheet the user enters by
  hand in their broker; afterwards the tradebook import records what was
  filled. Broker order tools are denied in `.claude/settings.json`: never remove
  them, and never call an order tool even if one appears. Broker access is
  read-only (holdings, positions, quotes, history). The user does not trade
  through Angel One; at most its API is a source of historical data.
- No em dashes in any file (a repo test checks).
- The cloud environment allows the NSE, Kite, Angel One, Zerodha and AMFI
  hosts. nseindia.com's homepage and api.bseindia.com answer 403 to scripts;
  NSE's file archive and its holiday API work. Tests never need the network.
