---
name: trader-pelosi
description: >-
  The Pelosi Tracker, India edition. India has no Congress trades; what it
  publishes is who inside a company trades its shares. Reads, from the
  local market store, NSE's insider trading disclosures (promoters,
  directors, key managerial people and their relatives, under SEBI's PIT
  rules), SAST Regulation 29 changes in large and promoter stakes, and
  named bulk and block deals; shows what was published since the last
  check, once each, with who, role, buy or sell, shares, rupees, the trade
  dates and how many days the disclosure took. Flags stocks you hold and
  pledges. Use when the user says "what are promoters buying", "insider
  buying", "did the promoter sell", "insider trades in X", "who bought X in
  a block deal", "track this promoter", "alert me on insider filings", or
  "run the Pelosi skill".
argument-hint: "[new|company SYMBOL|person NAME|status]"
---

# trader-pelosi (India)

The US skill reads what members of Congress must disclose. The Indian
equivalent is what company insiders and large holders must disclose to
the exchange. This skill reads those disclosures from the store the
user's machine already builds, and says what is new.

It is fast. It is not early. Every row is a public disclosure, read after
it was published, often days after the trade. There is no inside
information here. Say that every time.

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py" --help
```

Run from the repository root on the user's machine, where `data/market.db`
is. It reads only; it fetches nothing and sends nothing.

## What it reads

| source | what | filled by |
| --- | --- | --- |
| insider | trades by promoters, the promoter group, directors, key managerial people, employees and relatives (SEBI PIT disclosures on NSE) | `core/events.py fetch` (the app's update) |
| sast | a large holder's stake change under SAST Regulation 29: shares, % of the company, % held after; promoter or not | `core/events.py fetch` |
| deal | bulk and block deals with the client's name, price and quantity | `research/scan.py update` (each evening; a missed evening is lost) |

Writes `trader/state/pelosi.json`: the disclosures already shown, so each
alerts once. Reads `trader/portfolio.json` (if it exists) to flag stocks
you hold.

**A gap to say out loud:** NSE's insider feed has been empty since
2026-05-02. Until it returns, insider trades after that date are missing;
SAST changes and deals still arrive. `status` and every answer say when a
source has gone quiet.

## The rules

- **Quote the disclosure.** Each row comes from the exchange's own
  disclosure, with the company, the person or holder as filed, the role,
  and the dates. Never a news summary in its place.
- **Kinds are kinds.** A market purchase or sale is a trade. A pledge is
  a loan against shares, not a sale (an invoked pledge is the lender
  selling). ESOPs, gifts, transfers between promoters and allotments are
  not decisions to buy at the market: they are hidden unless `--all`.
- **Say the delay.** Each insider and SAST row shows the days between the
  last trade date and publication. Deals are published the same evening.
- **Rupees, not guesses.** Insider rows carry the filed value. SAST rows
  carry shares and % of the company, not a rupee value. A deal's value is
  its quantity times its price.
- **Names stay local.** Disclosures name private individuals. Show them to
  the user on their machine; never paste them anywhere else.

## Step 1: what is new

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py" new
python3 "${CLAUDE_SKILL_DIR}/insiders.py" new --held
python3 "${CLAUDE_SKILL_DIR}/insiders.py" new --company TATAMOTORS INFY
python3 "${CLAUDE_SKILL_DIR}/insiders.py" new --person "first last"
```

The first check shows the last 30 days; later checks only what was
published since. Exit code 1 means nothing new. The store changes when the
evening update runs (after about 7 pm IST), so once an evening is plenty:
a scheduled task or `/loop 24h`.

## Step 2: one company, or one name

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py" company TATAMOTORS --days 180
python3 "${CLAUDE_SKILL_DIR}/insiders.py" person "first last" --days 365
python3 "${CLAUDE_SKILL_DIR}/insiders.py" status
```

`person` matches every spelling that contains the words given (titles and
"Pvt Ltd" / "Private Limited" are evened out). Two people can share a
name: say so when the matches look like more than one person.

Add `--json` for the rows as data, and `--all` for ESOPs, gifts and
transfers.

## Example

```
New disclosures since 2026-10-21:
  - ALPHA insider: (name) (Promoter Group) bought 3,000 shares, worth ₹4.11 lakh,
    traded 2026-10-20, disclosed 2026-10-22, 2 days after the trade  [you hold this]
  - BETA SAST: (name) (promoter) sold 2,50,000 shares, 2.05% of the company, now
    holds 38.84%, traded 2026-10-19 to 2026-10-20, disclosed 2026-10-22, 2 days after the trade
  - ALPHA block deal: (name) (block deal) bought 2,00,000 shares at ₹150.50, worth ₹3.01 crore
```

Then: "These are public disclosures, read after they were published. A
promoter buying says what they did with their own money; it is not a
forecast."

## Untested, and said so

No signal built on these disclosures has passed this project's backtest:
insider buying and promoter buying were both tested and failed
(`research/FINDINGS.md`). Present them as context about a company, never
as a reason that a stock will rise. If the user wants a ranked trade, that
is `/trader-advisor`, which uses only the tested signal.

If the user wants to act on a disclosure anyway, it goes the long way:
shares only, sized by `/trader-budget`, checked by `/trader-risk`, and
paper-traded by `/trader-broker` after their yes; or an order sheet they
enter by hand in their own broker.

## What it never does

- Never says or implies that anyone traded on inside information, or
  that following insiders beats the market. No return claims.
- Never calls a pledge, an ESOP or a transfer a purchase or a sale.
- Never fetches, logs in or sends a name anywhere.
- Never places an order.

## Safety

- Education and tooling, not financial advice. Nothing in this pack is a
  recommendation to buy or sell anything.
- Paper trading by default. Going live is the user's own decision: they
  enter each line by hand in their own broker, after they say yes to it
  themselves.
- Nothing in this project places, modifies or cancels an order. Broker
  order tools are denied in `.claude/settings.json` and stay denied.
- Insider, SAST and deal disclosures are public and published after the
  trade. This skill reads them quickly; it has no inside information, and
  it says so.
