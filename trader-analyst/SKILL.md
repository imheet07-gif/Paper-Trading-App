---
name: trader-analyst
description: >-
  The Earnings Analyst, India edition. Reads the quarterly results Indian
  companies filed with NSE (the XBRL in the local market store) and sums up
  what changed: revenue, profit, net margin, profit before tax as a share
  of revenue and basic EPS, quarter by quarter, each against the same
  quarter a year earlier, with the last four quarters added up; banks on
  their own lines (interest earned and expended, net interest income,
  provisions). Points out profit that leans on other income, and says when
  newer results were published than the store has read. Writes a metrics
  file the screener reads. Use when the user says "how were TCS's
  results", "analyse the quarter", "what changed this quarter", "is
  revenue growing", "compare margins", or "run the analyst on my holdings".
argument-hint: "[symbols]"
---

# trader-analyst (India)

Results day moves stocks, and the press release puts the best number
first. This skill reads the numbers the company actually filed with the
exchange, lines them up quarter by quarter, and says plainly what changed.

```bash
python3 "${CLAUDE_SKILL_DIR}/results.py" --help
```

Run from the repository root on the user's machine, where `data/market.db`
is. It reads only; the numbers come from `core/financials.py`, which
downloads each filing's XBRL from NSE on the user's machine.

## What it reads and writes

- Reads the `financials` table: per quarter and per filing (consolidated
  or standalone), revenue from operations, other income, profit before
  tax, tax, profit (attributable to the owners when filed), basic and
  diluted EPS; for banks interest earned and expended, operating profit
  and provisions. Rupees, as filed.
- Reads the `results` table (`core/events.py`) only to notice results
  published after the newest quarter the store has numbers for.
- Writes `trader/metrics.json` with `--metrics`: one row per company for
  `/trader-screener` (money in crore, percentages as numbers):
  `revenue_ttm_cr`, `net_profit_ttm_cr`, `eps_ttm`, `net_margin`,
  `pbt_margin`, `revenue_growth_yoy`, `profit_growth_yoy`,
  `eps_growth_yoy`, the quarter and the day it was published. There is no
  operating margin in the filed lines read today; screen on `pbt_margin`
  or `net_margin`.

## The rules

- **Filed numbers only.** Every number comes from the company's filing.
  Nothing is estimated, and a missing line is "n/a", never zero.
- **Compare like with like.** Growth is against the same quarter a year
  earlier. The change on the quarter before is in `--json` too, but many
  businesses are seasonal.
- **One kind of filing at a time.** Consolidated when the newest quarter
  has it, else standalone; never mixed. `--standalone` asks for it.
- **Say when the data lags.** The store's results XBRL ends with filings
  published in February 2025. When later results were published, the
  tool prints a NOTE with the quarter and the day: read those results
  from the exchange before summarising, and say so.
- **Flag one-offs.** When other income is more than a quarter of profit
  before tax, part of the profit is outside the business. The tool says
  so. A loss is flagged too.
- **Banks are banks.** No revenue line: the top line is interest earned,
  and net interest income and provisions are shown.
- **EPS over four quarters is a sum** of the quarterly basic EPS: close,
  not exact, when the share count changed. Say so when it matters.

## Steps

1. Run it for the companies in question (or the symbols in
   `trader/portfolio.json`):

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/results.py" TCS
   python3 "${CLAUDE_SKILL_DIR}/results.py" TCS INFY HDFCBANK --quarters 8 --metrics trader/metrics.json
   ```

   `--json` gives everything machine-readable; ISINs work in place of
   symbols.
2. If a NOTE says newer results exist, read them from the exchange (the
   user can paste the results or the press release) and say which numbers
   came from where.
3. Write the summary: one line on the quarter (revenue, profit, EPS,
   growth against a year earlier), one on margins, one on anything
   unusual (other income, a loss, a lag), with the quarter and the day it
   was published. Plain words, real numbers, no adjectives the numbers do
   not support.

## Example (made-up company)

```
ALPHA (INE001A01011), consolidated results, as filed
  quarter ending 2024-12-31 (published 2024-12-31, unaudited): revenue ₹1,200 crore
  (+20.0% on a year earlier), profit ₹190 crore (+26.7%), EPS ₹14.00 (+40.0%)
    net margin 15.8%, profit before tax 20.0% of revenue
    flag: other income is 42% of profit before tax: part of the profit is outside the business
  last four quarters (2024-03-31 to 2024-12-31): revenue ₹4,500 crore, profit ₹700 crore, EPS ₹50.00
  NOTE: results for the quarter ending 2025-03-31 were published on 2025-04-20 and are not in the
  store's numbers yet
```

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what
  the company filed.
- Never invents a number the filings do not have, or fills a quarter it
  does not hold.
- Never fetches anything itself; never reads a news summary in place of a
  filing.

## Safety

Education and tooling, not financial advice. This skill only reads
filings. Nothing in this project places, modifies or cancels an order;
any trade is an order sheet the user enters by hand in their own broker,
after they say yes to it themselves.
