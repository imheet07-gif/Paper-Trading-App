---
name: trader-alerts
description: >-
  Price Alerts, India edition. Rings when a price target, a stop or the NSE
  opening bell hits: above / below / stop rules in rupees, percent moves from
  the previous close, and the 09:15 IST opening bell, each ringing once so
  you are not spammed. Knows the NSE day (pre-open, normal market, closing
  session) and holidays from core/nse_calendar.py, reads last prices from
  your own market store or a prices file you supply (it never fetches a
  quote), and writes trader/prices.json for the other skills. Use when the
  user says "alert me when", "tell me if TCS hits", "set a stop", "price
  target", "watch my stocks", "is the market open", "when does the market
  open", "get me prices", or to refresh prices before sizing or a risk check.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to. An alert is
information, never an order: when a stop rings, nothing is sold. If the user
wants to act, the normal path runs: size it (trader-budget or
trader-rebalance), check it (trader-risk), and the user enters it by hand in
their broker. Nothing in this project places an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                         # NSE phase now, next open (IST)
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote TCS ITC --write         # last prices -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write      # portfolio and target symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --above 3200
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol ITC --stop 380 --note "my stop"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol INFY --pct 3 --direction down
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                         # exit 0 when something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset tcs-above-3200
```

## What it reads and writes

| file | what |
| --- | --- |
| `data/market.db` | the default price source: your own market store (core/bhavcopy.py), last close per symbol |
| a prices JSON | with `--source file --prices-file PATH`: `{"as_of": "...", "prices": {"TCS": "3210.25"}}`, for example from your broker's read-only quote tool |
| `trader/alerts.json` | the rules (start from `alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when |
| `trader/prices.json` | what `quote --write` and `check` save for trader-budget, trader-risk and trader-rebalance, prices as rupee strings |

## The clock

The NSE cash market in IST (UTC+05:30, no daylight saving): pre-open order
entry 09:00 to 09:08, order matching to 09:15, the normal market 09:15 to
15:30, the closing session 15:40 to 16:00. Holidays come from
`core/nse_holidays.json`, read from NSE's own holiday master. A year with no
list is an error, never a guess: refresh it with `core/nse_calendar.py
update-holidays`. Special sessions (Muhurat trading) are added to
`core/nse_calendar.py` when NSE publishes their timings.

## The rules

- **above** / **target**: rings when the price is at or above the level.
- **below** / **stop**: rings when the price is at or below the level.
- **pct_move**: rings when the price moved `pct` percent or more from the
  previous close (or from `ref`), optionally only `up` or `down`.
- **opening_bell**: rings once a trading day once the normal market is open.

An above, below or stop rule rings once and stays quiet until `reset`, or
re-arms by itself with `"rearm": true` when the price crosses back. A
pct_move or the bell rings at most once a day. Prices are rupees, kept as
exact decimals.

## Steps

1. `clock` when the user asks whether the market is open.
2. Make sure the prices are as fresh as the user needs: the store has the
   last close (end of day). For anything during the day, the user brings a
   prices file from their broker's read-only quote tool.
3. `add` the rules the user describes, then `check`. Say which rules rang,
   the price and its date, and what was missing.

## Left out on purpose

- **Live quotes.** This tool never fetches a price. The store gives the last
  close; intraday prices come from a file the user supplies. A read-only
  broker quote source can be added later as an importer.
- **Price bands and circuit limits.** Not yet in `core/` data read from
  NSE's own file, so an alert does not say whether a stock is locked.
- **Ex-dates.** NSE's previous close is not adjusted on an ex-date, so a
  pct_move that day can be the dividend, split or bonus, not the market. The
  message says so.

## What it never does

It never places, modifies or cancels an order, never treats an alert as a
decision, and never fetches anything from the network.

## Safety

- Education and tooling, not financial advice. An alert is information, not a recommendation.
- Paper trading by default. Going live is the user's own decision, in their own config.
- Nothing in this project places, modifies or cancels an order. The user enters each order by hand in
  their broker only after they say yes to it themselves. Broker order tools are denied in
  `.claude/settings.json` and stay denied.
- Broker keys live in environment variables or the MCP client config, never in the chat or a file here.
