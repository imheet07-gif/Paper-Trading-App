---
name: trader-screener
description: >-
  The Stock Screener, India edition. Checks a list of NSE companies against
  the user's own rules (revenue growth, margins, EPS, P/E, earnings yield,
  sector, anything in the metrics file), says exactly which rule each
  company passes or fails and by how much, and ranks the ones that pass into
  a shortlist. Reads a metrics JSON or any CSV keyed by NSE symbol, with
  rupee amounts written as ₹, Rs, Rs. or INR and Indian digit grouping. Use
  when the user says "screen for stocks", "find me companies that", "filter
  my watchlist", "which of these pass my rules", "shortlist candidates",
  "rank these stocks", or "growing companies under 30 times earnings".
argument-hint: "[metrics file] [--rules rules.json]"
---

# trader-screener

A screen turns "I want profitable companies that are still growing and not
too expensive" into rules a computer can check, and shows its work: every
pass and every fail comes with the number and the threshold.

Tool in this folder (standard library Python, no network):

```bash
python3 "${CLAUDE_SKILL_DIR}/screen.py" --help
```

## What it reads and writes

- Metrics: a JSON list (or `{"companies": [...]}`) or a CSV with a header
  row. Each company needs a `symbol` (the NSE symbol; a `ticker` column is
  read as the symbol) and may carry an `isin`, which is passed through to
  the shortlist. Every other column is a metric. CSV numbers may carry
  `₹`, `Rs`, `Rs.` or `INR`, `%`, and commas in Indian (`1,23,456`) or
  western grouping. Units are never converted: a column in crores stays in
  crores, so write rules in the column's own unit.
- Prices (optional): `trader/prices.json` (`{"prices": {"TCS": "3000"}}`).
  With prices it adds `price`, `pe` (price / trailing EPS, only when EPS is
  positive) and `earnings_yield`.
- Rules: a JSON file, `rules.example.json` in this folder to start from.
- Writes `trader/shortlist.json` with `--out`: who passed, their score and
  why, who failed and why.

## Where the metrics come from

There is no India results reader yet: the XBRL financials task
(`core/financials.py`) and the Screener.in export importer
(`core/screener_export.py`) are open in HANDOVER.md. Until they land, the
metrics are the user's own: a CSV they export or type in. Say where the
numbers came from and for which quarter. Numbers from a website export are
for manual review only; they are not point-in-time and are never used in a
backtest.

## The rules file

```json
{"name": "Growing, profitable, not priced for perfection",
 "rules": [{"metric": "revenue_growth_yoy", "op": ">=", "value": 10},
           {"metric": "operating_margin", "op": ">=", "value": 25},
           {"metric": "pe", "op": "<=", "value": 35}],
 "rank":  [{"metric": "revenue_growth_yoy", "order": "desc", "weight": 2},
           {"metric": "pe", "order": "asc", "weight": 1}],
 "missing": "fail"}
```

Ops: `>`, `>=`, `<`, `<=`, `==`, `!=`, `between` ([low, high]), `in` and
`not in` (a list, for symbols or sectors to leave out). `"missing": "fail"`
fails a company that lacks a metric a rule needs (banks and NBFCs report
no operating margin in the usual sense); `"skip"` ignores that rule for it
and says so. Ranking scores each passing company 0 to 1 per rank metric and
takes the weighted average.

## Steps

1. Turn the user's words into rules. Ask when a threshold is vague ("cheap"
   means what P/E?). Write them to `trader/rules.json`.
2. Get metrics for the candidates from the user's own CSV, and prices in
   `trader/prices.json` if P/E matters.
3. Run the screen:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/screen.py" trader/metrics.csv --rules trader/rules.json \
     --prices trader/prices.json --out trader/shortlist.json
   ```

   Exit code 1 means nothing passed: say so and show the closest misses.
4. Report the shortlist with the reasons, the misses with the numbers that
   failed, and the dates the numbers are from (the quarter, the price date).
   Suggest reading each top name's own results filing on NSE before anything
   else.

## Example

The test fixtures hold five made-up companies (ALPHA to ECHO, not real
data). With made-up prices and `rules.example.json`:

```
Growing, profitable, not priced for perfection: 2 of 5 companies pass.
 1. PASS CHARLIE    Charlie Systems  score 0.75
 2. PASS DELTA      Delta Consumer   score 0.25
    FAIL ALPHA      pe 39.2, needs <= 35
    FAIL ECHO       operating_margin is missing
    FAIL BRAVO      revenue_growth_yoy 8.6, needs >= 10; operating_margin is missing
```

ECHO is a bank: with `"missing": "skip"` it passes and the report says the
operating margin rule was skipped for it.

## What it never does

- Never calls a passing company a buy. A screen is a filter on reported
  numbers, not a recommendation.
- Never fills in a missing number, converts units, or guesses a metric:
  missing means missing.
- Never sends anything to `/trader-budget` or an order sheet by itself.

## Safety

Education and tooling, not financial advice. This skill only filters
numbers. Nothing in this project places an order; the user enters any
order by hand in their broker after trader-risk has checked it.
