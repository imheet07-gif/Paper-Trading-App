---
name: trader-broker
description: >-
  Paper Trader, India edition. Takes the order sheet that trader-budget or
  trader-rebalance wrote and trader-risk checked, shows each line as a plain
  ticket, records the user's explicit yes or no for each one, and fills the
  approved lines into a separate PAPER portfolio at the price the user gives
  (or the line's reference price), with the charges a real fill would cost.
  It never connects to a broker and never moves money. Use when the user says
  "paper trade these", "fill the orders on paper", "show me the tickets",
  "I approve line 2", "what did I paper-trade today", or after /trader-risk
  passes an order sheet.
---

# trader-broker

The last step of the flow, on paper. trader-budget or trader-rebalance
writes an order sheet, trader-risk checks every line, and this skill turns
each line the user approves into a paper fill. Nothing here reaches a
broker. When the user decides to trade for real, they enter the lines by
hand in their own broker app; this project never places an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                    # every line, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --index 1          # at the line's reference price
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --index 1 --price 1398.5
python3 "${CLAUDE_SKILL_DIR}/ticket.py" decline --index 2 --reply "no"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the order sheet (trader-budget or trader-rebalance) |
| `trader/state/risk.json` | trader-risk's verdicts; a line is READY only if this exact line passed today |
| `trader/config.json` | the budget that starts a new paper portfolio, and `broker` for the charge rates |
| `trader/paper_portfolio.json` | the paper account it fills: `{"paper": true, "cash", "positions": [{"symbol", "isin"?, "qty", "lots"}]}` |
| `trader/state/paper_broker.json` | every yes, no and fill, append only |

The paper portfolio is a separate file from the user's real holdings
(`trader/portfolio.json`), and the tool refuses to fill any file not marked
`"paper": true`. To check or rebalance the paper account, pass
`--portfolio trader/paper_portfolio.json` to trader-risk and trader-rebalance.

## Steps

1. Run `show`. Read each READY line to the user in plain words: side,
   shares, stock, the reference price and the rupee amount, and why it is on
   the sheet. Say any trader-risk notes.
2. Ask for a yes or no for each line, one at a time. Record it word for word
   with `approve --reply "..."` or `decline`. Only a reply that starts with
   "yes" and names no other stock or quantity counts; "ok", "sure", "go",
   "yes to all" and a yes with a condition do not. Never answer for the user.
3. `fill` each approved line. Ask the user for the price they would have
   got; without one, the line's reference price is used and the output says
   so. Charges come from `core/charges.py` and are added to a buy's cost.
4. Report the paper cash and position after each fill.

## Example

The sheet trader-budget writes for its example (illustrative round prices),
checked by trader-risk on 30 October 2026. Line 1 is READY: "BUY 17 shares
of RELIANCE on NSE, delivery, market order, reference price 1,400, about
23,800". The user replies "yes 17 RELIANCE"; `fill --index 1` records
"PAPER FILL: BUY 17 RELIANCE at 1,400.00 = 23,800.00, charges 28.26", the
paper cash drops from 1,00,000 to 76,171.74, and the lot's cost is 23,828.26
including charges.

## What it never does

It never connects to a broker, never calls an order tool, never moves
money, never fills a line without the user's own yes for that line, never
fills the same line twice in a day, and never writes to the user's real
portfolio file.

## Safety

- Education and tooling, not financial advice. A paper fill is a simulation, not a trade.
- Paper only: there is no live mode. Going live means the user entering lines by hand in their own
  broker, after their own decision.
- Nothing in this project places, modifies or cancels an order. Broker order tools are denied in
  `.claude/settings.json` and stay denied.
- Broker keys are never needed here and never belong in the chat or a file in this repo.
