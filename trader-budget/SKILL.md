---
name: trader-budget
description: >-
  Budget Manager, India edition. Sizes every position to your budget in
  rupees before anything is bought: keeps a cash buffer, caps any one stock
  (the excess is shared out to the others), buys whole shares only (India has
  no fractional shares), keeps the estimated buy charges (STT, exchange
  charge, SEBI fee, stamp duty, GST, from core/charges.py) inside the money,
  drops slices under your minimum order or under one share, and writes an
  order sheet to trader/orders.json for you to enter by hand in your broker.
  Nothing is placed. Use when the user says "I have Rs 1 lakh", "size my
  trades", "how many shares of each should I buy", "manage my budget",
  "split my money", "keep 10% in cash", "no stock over 25%", "set my rules",
  or after a scan or a target list is written to trader/targets.json.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns "I want these stocks at these weights" into "buy
exactly this many shares of each", inside rules the user sets once. It
writes an order sheet only. trader-risk checks each line, and the user
enters the ones they choose by hand in their own broker. Nothing in this
project places an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                          # trader/targets.json, config, prices
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 100000 --write  # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights RELIANCE=30,TCS=25,HDFCBANK=20 --prices trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --json
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/targets.json` | the weights, `{"weights": [{"symbol", "isin"?, "name"?, "weight"}]}` (`targets.example.json` here shows the shape) |
| `trader/prices.json` | a price for every symbol, `{"as_of": "...", "prices": {"RELIANCE": "1400.00"}}`; required, because whole shares need a price |
| `trader/orders.json` | the order sheet it writes with `--write` |

Prices come from the user: their broker's quote screen or Kite's read-only
quote tool, or the last close in their own market store. They are never
guessed. Say how old they are.

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how many rupees this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much stays in cash (default 10).
- **max_position_pct**: the most any one stock may be, as a percent of the budget (default 25).
- **max_order_inr** and **min_order_inr**: the biggest and smallest single order line in rupees.
- **broker**: whose charges to estimate. Only brokers with rates in `core/charges.json` work
  (Zerodha today); an unknown broker is an error, never a guess. Leave it out to skip charges.
- **exchange**: `NSE` (default) or `BSE`.
- **blocked**: symbols (or ISINs) they never want to own.
- Keep **paper** true until they have watched it work on paper.

## Step 2: size it

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

The maths, in order: the cash buffer comes off the top; the weights are
renormalised to 100 over the stocks that are left; any stock over the cap is
pinned at the cap and its excess is shared over the others by weight, again
until nobody is over; each slice buys the whole shares it can, then the
money left buys one more share at a time for the stock furthest below its
slice, while the cash lasts and the cap holds; with a broker set, the
estimated buy charges are counted in, and a share comes off if they do not
fit; a slice under the minimum order, or under the price of one share, is
dropped and its weight shared out. Money that cannot be invested is shown
as "left idle".

## Step 3: show the user

Show the table: symbol, weight, price, shares, rupees, which stock hit the
cap, the estimated charges (and whether the rates are verified against a
contract note), the cash buffer and what is left idle. Say where the weights
came from and the date of the prices. Then hand over to `/trader-risk`.

## Example

The shape of `targets.example.json` (five NSE symbols, made-up weights
30 / 25 / 20 / 15 / 10, not a recommendation) with illustrative round
prices, not quotes: RELIANCE 1,400, TCS 3,000, HDFCBANK 950, ITC 410,
INFY 1,500. A Rs 1,00,000 budget, a 10% buffer, a 25% cap, Zerodha rates:

| stock | shares | rupees | |
| --- | --- | --- | --- |
| RELIANCE | 17 | 23,800 | at the 25% cap (Rs 25,000); one more share would cross it |
| TCS | 8 | 24,000 | |
| HDFCBANK | 20 | 19,000 | |
| ITC | 34 | 13,940 | |
| INFY | 6 | 9,000 | |
| charges | | 106.56 | estimated |
| cash buffer | | 10,000 | |
| left idle | | 153.44 | whole shares do not divide evenly |

The first order line it writes is
`{"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": 17, "product": "delivery", "order_type": "market", "ref_price": "1400", "value": "23800.00", "est_charges": "28.26", ...}`.

## Left out on purpose

These are exchange or broker facts that are not yet in `core/` data read
from the publisher's own file, so this skill does not use or check them.
The user must check them in their broker before entering an order:

- **Tick sizes.** Orders are market orders; no limit price is computed. A
  limit price is the user's to set on the instrument's tick.
- **Price bands and circuit limits.** A stock locked at its upper or lower
  circuit may not fill at all, or the order may be rejected.
- **F&O lot sizes.** Only cash-market delivery equity is sized.
- **ISIN lookups.** A symbol is matched to an ISIN only if the targets carry
  one; this skill has no instrument registry of its own.
- **Live prices.** It reads `trader/prices.json` and never fetches a quote.

## Rules

- An order sheet only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget.
- Every number comes from the config, the targets, the prices or
  `core/charges.json`. Nothing is guessed. Money is Decimal rupees.
- No projections: never say what the positions will be worth or earn.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, and never sizes options, futures, margin, MTF or short positions.

## Safety

- Education and tooling, not financial advice. Nothing here predicts prices or promises returns.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this project places, modifies or cancels an order. The end of the flow is an order sheet;
  the user enters each line by hand in their broker only after they say yes to it themselves. Broker
  order tools are denied in `.claude/settings.json` and stay denied.
- No options, margin, short selling or leverage.
- Broker keys live in environment variables or the MCP client config, never in the chat or a file here.
