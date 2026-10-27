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
  Fixtures under `tests/fixtures/`; `tests/fixtures/core/` is synthetic and
  says so in its README.

```bash
python3 -m unittest discover -s tests
```

## Rules

- Standard library only in `core/` and the skills. numpy/pandas are allowed
  only in the Phase 1+ analytics layer, behind its own folder.
- Money is `Decimal`, never float. Quantities are whole shares.
- Rates, holidays and anything else published by an exchange, regulator or
  broker live in data (`core/charges.json`, `HOLIDAYS`) with a source line and
  a verified flag, never invented. Unknown means an error, not a default.
- ISIN is the key for a stock. Symbols and broker tokens are attributes.
- Times are timezone-aware; the market runs on IST (UTC+05:30, no DST).
- Never commit personal data: `private/`, `trader/` and `data/` are ignored.
  Build parsers from redacted samples.
- Nothing places an order except trader-broker, and only after trader-risk
  passed that exact order, the user typed yes for it, and the user clicked
  Allow once. Paper trading is the default. Never weaken these gates.
- No em dashes in any file (a repo test checks).
- Cloud sessions may not reach nseindia.com, api.kite.trade or
  angelbroking.com. Tests never need them; real downloads can run on the
  user's machine.
