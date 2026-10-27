---
name: trader-rebalance
description: >-
  Rebalancer, India edition. Brings the portfolio back to its target weights
  when it drifts: values every holding at current prices in rupees, works out
  each stock's drift from its target in percentage points, trades only the
  stocks outside a band (default 5 points), keeps the cash buffer and the
  per-stock cap, works in whole shares, counts estimated charges (STT, DP
  charge on sells, stamp duty, GST, from core/charges.py), skips tiny lines,
  lists sells before buys, and writes an order sheet to trader/orders.json
  for you to enter by hand. Use when the user says "rebalance", "my
  portfolio drifted", "bring it back to the targets", "I added money", "the
  new targets changed the weights", "am I overweight", or on a schedule
  (monthly or quarterly).
---

# trader-rebalance

The Rebalancer keeps the portfolio looking like the plan. Prices move, new
money arrives and the targets change; this skill finds the stocks that
drifted too far and writes the order sheet that brings them back. It
writes proposals only: trader-risk checks each line and the user enters the
ones they choose by hand in their broker. Nothing in this project places an
order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/rebalance.py"                    # the drift table, no files written
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --band 5 --write   # order sheet -> trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --sell-untracked   # also sell holdings not in the targets
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --json
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/portfolio.json` | `{"cash": "...", "positions": [{"symbol", "isin"?, "qty", "lots"?}]}`, whole shares |
| `trader/prices.json` | a price for every holding and every target, with its date |
| `trader/targets.json` | the target weights (`trader-budget/targets.example.json` shows the shape) |
| `trader/config.json` | cash buffer, cap, `min_order_inr`, `max_order_inr`, `rebalance_band_pct`, `broker` |
| `trader/orders.json` | the order sheet it writes with `--write`, sells first |

The portfolio can come from the user's broker holdings (the Zerodha Console
import in `core/zerodha_console.py`, or a read-only holdings tool); it stays
in `trader/` on the user's machine and never goes in the repo.

## How it decides

- **Account value** = cash + every holding at its price.
- **Targets**: the weights renormalised over the stock sleeve (account minus
  the cash buffer), with no stock above `max_position_pct` of the account. The
  excess is shared out, the same way trader-budget does it.
- **Drift** = current percent of the account minus target percent.
- **Band**: only stocks drifting MORE than the band trade (default 5 points).
  A trade takes the stock back to its target, rounded to whole shares (a sell
  to the nearest share, never more than held; a buy down to the share).
- **Sells first**, then buys. Sale proceeds less estimated charges count as
  cash for the buys. Buys, with their charges, only spend cash above the
  buffer; if there is not enough, all buys are scaled down by the same share.
- Lines under `min_order_inr` are skipped. A line over `max_order_inr` is cut
  to the max with a note to run again after it fills.
- **Untracked** holdings (not in the targets) are left alone and listed,
  unless the user asks to sell them with `--sell-untracked`.

## Steps

1. Refresh `trader/prices.json` and say its date.
2. Run `rebalance.py` and show the drift table in plain words: which stocks
   are over or under, by how many points, and what it would trade.
3. Before any sell, say that selling a winner can realise a taxable capital
   gain (short or long term by holding period). The Indian tax module is
   parked, so no tax figure is given; the FIFO lots in `core/lots.py` show
   which purchases a sale would consume.
4. If the user wants the lines: `rebalance.py --write`, then `/trader-risk`.
   The user enters each line they choose by hand.

## Example

The account trader-budget's example would hold once its sheet filled
(RELIANCE 17, TCS 8, HDFCBANK 20, ITC 34, INFY 6, Rs 10,153.44 cash) at the
same illustrative round prices sits on its targets: every drift is inside
the band, so `rebalance.py` says "Nothing is outside the band" and exits 1.
Add Rs 20,000 of cash and RELIANCE is 5.2 points under its target, outside
the 5-point band, so it proposes buying 4 RELIANCE (about Rs 5,600 plus
Rs 6.65 estimated charges). TCS, HDFCBANK, ITC and INFY are under target
too, but inside the band, so they wait.

## Left out on purpose

These are exchange or broker facts that are not yet in `core/` data read
from the publisher's own file. The user must check them in their broker:

- **Tick sizes.** Lines are market orders; no limit price is computed.
- **Price bands and circuit limits.** A line for a stock at its band may be
  rejected or not fill.
- **Same-day use of sale proceeds.** Settlement is T+1; whether the broker
  lets the user spend today's sale proceeds today is the broker's rule. If
  not, enter the sells today and the buys after they settle.
- **Tax.** No capital gains figure; the tax module is parked.
- **ISINs without a registry.** With `data/instruments.json` (built by
  `core/instruments.py build` from NSE's own equity list), each line gets
  its ISIN and a holding whose ISIN disagrees with NSE's is an error.
  Without it, the report notes that ISINs were not checked.

## What it never does

It never places an order, never trades inside the band to chase small
differences, never sells holdings outside the targets unless asked, and
never says a rebalance will improve returns.

## Safety

- Education and tooling, not financial advice. A rebalance follows the user's own targets, not a forecast.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this project places, modifies or cancels an order. The user enters each line by hand in
  their broker only after they say yes to it themselves. Broker order tools are denied in
  `.claude/settings.json` and stay denied.
- No options, margin, short selling or leverage.
- Broker keys live in environment variables or the MCP client config, never in the chat or a file here.
