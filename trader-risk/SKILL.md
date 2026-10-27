---
name: trader-risk
description: >-
  Risk Manager, India edition. Checks every line of an order sheet against
  your limits before you enter anything in your broker, and blocks anything
  over them: blocked symbols or ISINs, max and min order in rupees, max
  position percent after the fill, the cash buffer (with estimated charges),
  margin, short sales, futures and options (off unless allowed), anything
  that is not a whole-share delivery line on NSE or BSE, duplicates and a
  per-sheet line limit. Prints PASS or BLOCK with the reason for each line,
  says plainly what it does not check (tick sizes, price bands and circuit
  limits, F&O lot sizes), and records the verdicts. Use when the user says
  "check my orders", "is this safe", "risk check", "does this break my
  rules", "can I place this", or before entering any order sheet.
---

# trader-risk

The Risk Manager is the gate between an order sheet and the user's own
hands on their broker. Every sheet written by trader-budget or
trader-rebalance passes through it. A BLOCK is final for that line: the
user should not enter it. A PASS is a rules check, not a yes; nothing in
this project places an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"             # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write  # look without recording
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the order sheet (whole shares, `exchange`, `side`, `qty`, `product`, `order_type`) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | `{"cash": "...", "positions": [{"symbol", "isin"?, "qty", "lots"?}]}`; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a price per symbol, to value each line and each holding |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact line |

## The checks

Lines are checked in sheet order, as if each passed line had filled, so a
sheet that is fine line by line but breaks a rule together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol; side not buy or sell; qty not a positive whole number; a rupee amount instead of shares; exchange not NSE or BSE; product not delivery (no intraday, MTF or margin); a limit order with no limit price |
| asset | an F&O contract while `allow_options` is false; crypto, or a symbol that does not look like a cash-market symbol |
| blocked | the symbol or ISIN is on the `blocked` list |
| size | under `min_order_inr` or over `max_order_inr` |
| position | the stock would be over `max_position_pct` of the account after the fill |
| cash | the buy plus its estimated charges would leave less than the cash buffer, or need margin (`allow_margin` false) |
| short | a sell of more than you hold (`allow_short` false) |
| duplicate | the same symbol and side twice in one sheet |
| sheet limit | more than `max_orders_per_day` lines passed in one sheet |

Charges come from `core/charges.py` for the config's `broker` (an unknown
broker is an error, never a default); without a broker they are not
counted, and it says so. Warnings do not block, but say them to the user:
LIVE money, unverified charge rates, a line valued at the sheet's own
`ref_price`, an unchecked limit price.

## Left out on purpose

These are exchange or broker facts that are not yet in `core/` data read
from the publisher's own file. The check does not use them and says so on
every run; the user must check them in their broker:

- **Tick sizes.** A limit price is not checked against the instrument's tick.
- **Price bands and circuit limits.** A line can PASS here and still be
  rejected or sit unfilled because the stock is at its band.
- **F&O lot sizes and margins.** F&O is off by default and not sized.
- **Same-day use of sale proceeds.** Sells are counted as freeing cash for
  later buys in the same sheet; whether the broker lets that money be spent
  the same day (settlement is T+1) is the broker's rule.
- **Orders already entered today.** The user enters orders by hand, so the
  sheet limit counts one sheet, not the day.

## Steps

1. Make sure `trader/prices.json` is fresh and says its date.
2. Run `check.py`. Exit code 0 means every line passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, line by line. For a BLOCK, give
   the reason and the fix (fewer shares, a different stock, or a rule the
   user may decide to change). Never change a limit yourself. Read out the
   "Not checked" line.
4. If the sheet changes in any way, run the check again: the fingerprint in
   `trader/state/risk.json` marks an edited line as unchecked.

## Example

The order sheet trader-budget writes for its example (illustrative round
prices, Rs 1,00,000, Zerodha rates) on a fresh paper account:

```
PASS  #1 buy RELIANCE 17 shares, about ₹23,800.00
PASS  #2 buy TCS 8 shares, about ₹24,000.00
PASS  #3 buy HDFCBANK 20 shares, about ₹19,000.00
PASS  #4 buy ITC 34 shares, about ₹13,940.00
PASS  #5 buy INFY 6 shares, about ₹9,000.00
5 passed, 0 blocked. Cash after the passed lines: ₹10,153.44 (paper).
```

A sixth line, one more RELIANCE share, is blocked three ways: it dips into
the Rs 10,000 cash buffer, takes RELIANCE over its 25% cap, and repeats
RELIANCE in the same sheet.

## What it never does

It never places an order, never edits a line to make it pass, never loosens
a limit, and never treats a PASS as the user's yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this project places, modifies or cancels an order. The user enters each line by hand in
  their broker only after they say yes to it themselves. Broker order tools are denied in
  `.claude/settings.json` and stay denied.
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the MCP client config, never in the chat or a file here.
