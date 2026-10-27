# CLAUDE.md

India edition of trader-skill: Claude skills for NSE/BSE equities. The plan
and phase status are in README.md.

## Layout

- `trader-*/` the eleven skills, each a `SKILL.md` plus one stdlib CLI. Still
  the US originals until Phase 2 ports them; do not assume their data sources
  (SEC, NYSE, Alpaca, US tax) apply to India.
- `core/` shared India modules. Skills import them with
  `sys.path.insert(0, <repo>/core)`, the way `core/charges.py` imports `money`.
- `tests/` unittest, offline only. `tests/_load.py` loads a script by folder.
  Fixtures under `tests/fixtures/`, cut from real published files; each
  fixture folder's README names its sources and date.

```bash
python3 -m unittest discover -s tests
```

## Rules

- Standard library only in `core/` and the skills. numpy/pandas are allowed
  only in the Phase 1+ analytics layer, behind its own folder.
- Money is `Decimal`, never float. Quantities are whole shares.
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
  hand in Kite; afterwards the tradebook import records what was filled.
  Broker order tools are denied in `.claude/settings.json`: never remove
  them, and never call an order tool even if one appears. Broker access is
  read-only (holdings, positions, quotes, history). The user does not trade
  through Angel One; at most its API is a source of historical data.
- No em dashes in any file (a repo test checks).
- The cloud environment allows the NSE, Kite, Angel One, Zerodha and AMFI
  hosts. nseindia.com's homepage and api.bseindia.com answer 403 to scripts;
  NSE's file archive and its holiday API work. Tests never need the network.
