---
name: trader-news
description: >-
  The News Monitor, India edition. Watches NSE corporate announcements for
  the stocks you hold or plan to buy: results, dividends, buybacks, bonuses
  and splits, fund raising, credit ratings, deals, director and KMP changes,
  promoter and insider filings, legal and regulatory matters. Shows only
  what is new since the last check, newest first, with NSE's dissemination
  time in IST, the company's own summary and the link to the filing, and
  flags the kinds that usually move a price. Never repeats an item. Use when
  the user says "any news on my stocks", "what's happening with TCS",
  "monitor the news", "watch my portfolio", "did anything break", "any
  announcements", or "morning briefing".
argument-hint: "[symbols]"
---

# trader-news

A position you hold can change in a minute: results, a dividend, a
resignation, a pledge, a regulator's order. In India the first public
record of each is the company's own announcement on NSE. This skill reads
those for your stocks and tells you only what is new.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py" --help
python3 "${CLAUDE_SKILL_DIR}/news.py"                          # symbols from trader/portfolio.json
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols TCS ITC INFY
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols TCS --all --days 7
```

## What it reads and writes

- Your symbols: `--symbols`, else `trader/portfolio.json` positions, else
  `trader/targets.json` weights.
- NSE's corporate announcements API, one request per check for the whole
  window (`--days`, default 2, at most 31), the same endpoint and query
  `core/events.py` uses. It runs where NSE answers scripts (the user's own
  machine); `--offline FILE` reads a saved answer instead.
- `trader/state/news.json`: what has been shown (30 days, by NSE's
  `seq_id`), so an item shows once. `--dry-run` leaves it alone.

## What each line says

```
! 06 Aug 19:42 IST  RPOWER       results              Outcome of Board Meeting
      Reliance Power Limited has submitted to the Exchange, the financial results for the quarter ended Jun 30, 2026.
      https://nsearchives.nseindia.com/corporate/RPOWER_...pdf
```

The time NSE disseminated it (IST), the symbol, a kind, NSE's heading, the
company's one-line summary and the PDF. The kind is this skill's own label
from the heading and the summary together, so a board meeting outcome that
carries results is filed as results. `!` marks the kinds that usually move
a price: results, dividend, buyback, bonus or split, fund raising, credit
rating, deal, people, promoter or insider, legal or regulatory.

## Steps

1. Run `news.py` for the user's stocks. Exit code 1 means nothing new.
2. Report the `!` items first, in plain words: what the company filed and
   when. Quote the company's summary; do not add to it.
3. For anything that matters, open the PDF before saying more. A results
   filing is numbers, not a verdict: say what changed only from the filing.
4. If the user wants to act, the normal path runs: size it, check it with
   trader-risk, and the user enters it by hand in their broker.

## Left out on purpose

- **Headlines and analyst views.** Only the company's own filings with NSE.
  No news site is read.
- **BSE-only companies.** BSE's API answers 403 to scripts.
- **Reading the PDFs.** The skill links them; it does not parse them. The
  results numbers are a separate task (results XBRL, HANDOVER.md).

## What it never does

- Never calls an announcement good or bad news for the price, and never
  turns one into an order.
- Never repeats an item it has shown, unless asked with `--all`.

## Safety

Education and tooling, not financial advice. An announcement is a fact
about a filing, not a recommendation. Nothing in this project places an
order; the user enters any order by hand in their broker after trader-risk
has checked it.
