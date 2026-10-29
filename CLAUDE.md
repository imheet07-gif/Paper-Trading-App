# CLAUDE.md

India edition of trader-skill: Claude skills for NSE/BSE equities. The plan
and phase status are in README.md. Two agents share the work: read
HANDOVER.md for the task split and how to coordinate before starting. Personal use for now, but built to ship
later as a Claude plugin anyone can install: nothing may assume one user or
one broker. Brokers plug in through importers that produce the same
broker-neutral holdings, trades and charges.

## Layout

- `trader-*/` the twelve skills, each a `SKILL.md` plus one stdlib CLI
  (`trader-advisor`, the trade finder's chat front, runs
  `research/advise.py` instead).
  `trader-budget`, `trader-risk`, `trader-rebalance`, `trader-screener`,
  `trader-alerts`, `trader-news`, `trader-advisor`, `trader-broker` (a paper trader only:
  no broker connection, no live mode), `trader-copycat` (named holders
  from shareholding filings, and bulk and block deals) and `trader-pelosi`
  (insider, promoter SAST and bulk and block deal disclosures, from the
  store; `insiders.py`) and `trader-analyst` (quarterly results from the
  store's `financials` table; `results.py`) are ported to India (rupees, whole
  shares, an order sheet for manual entry; gaps in HANDOVER.md). The rest
  are still the US originals until
  Phase 2 ports them; do not assume their data sources (SEC, NYSE, Alpaca,
  US tax) apply to India.
- `core/` shared India modules. Skills import them with
  `sys.path.insert(0, <repo>/core)`, the way `core/charges.py` imports `money`.
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
  holders (promoters, public holders above 1%) for trader-copycat; names
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
  half-year), flagged in `derived`. The scan's evening update fetches new
  filings.
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
- `core/metals.py` gold and silver: Sovereign Gold Bonds (series GB) from
  the store, gold and silver ETFs picked from NSE's ETF list file, and the
  owner's physical metal ledger (`trader/metals.json`), valued only at
  rates the owner records with a date and source (`trader/metal_rates.json`).
- `core/funds.py` AMFI mutual fund NAVs (`fund_nav` table: scheme code,
  date, NAV as published; raw windows cached in `data/funds/`). This cloud
  session cannot reach AMFI: downloads run on the owner's machine, and the
  test samples are made up in AMFI's layout until a real cut replaces them.
- `research/` the signals engine (numpy and pandas allowed here only):
  `panel.py` loads the market store, `signals.py` holds candidates,
  `backtest.py` simulates and enforces the dev / validation / holdout
  protocol, `scan.py` is the daily scan and `paper.py` its paper ledger
  (`trader/paper.json`), `exits.py` stops, targets and sizing,
  `advise.py` the trade finder (only the top tenth of the tested ranking, hard filters,
  evidence cards), `profiles.py` its concentration and risk-level backtests
  (dev and validation only). The scan runs only strategies in
  `research/strategies.json` and selects with `backtest.select_top`, so it
  trades exactly what was validated; changing a strategy means validating
  the new version first. Never open the holdout without the user asking; every run is
  logged to `research/runs.jsonl`, which is committed so the count of
  variants tried survives.
- `app/` the local app (`server.py`, standard library; `static/` page,
  styles and charts in plain JavaScript, nothing loaded from outside). It
  runs only `research/jobs.py` tasks with arguments checked against fixed
  lists (its Ask tab runs the trade finder, writing to `private/advice/`);
  never add a task that places orders or opens the holdout.
- `dashboard/` the personal dashboard: `build.py` writes one self-contained
  HTML file (no external scripts, fonts or requests) to `private/`.
- `docs/solutions/` documented solutions to past problems (bugs, best
  practices, workflow patterns), organized by category with YAML
  frontmatter (`module`, `tags`, `problem_type`); relevant when
  implementing or debugging in documented areas.
- `CONCEPTS.md` shared domain vocabulary (securities, ISIN chains,
  corporate actions and factors); relevant when orienting to the codebase
  or discussing domain concepts.
- `tests/` unittest, offline only. `tests/_load.py` loads a script by folder.
  Fixtures under `tests/fixtures/`, cut from real published files; each
  fixture folder's README names its sources and date.

```bash
python3 -m unittest discover -s tests
```

## Rules

- Standard library only in `core/` and the skills, with one exception:
  `core/cas.py` imports casparser (MIT, offline) lazily, only when a PDF is
  read. numpy/pandas are allowed only in `research/`. Any new dependency
  needs the same justification.
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
