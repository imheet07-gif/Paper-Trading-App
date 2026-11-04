# Multibagger Finder (India)

A research tool for Indian equities (NSE main board and SME) that ranks
stocks by how likely they are, on tested evidence, to return **3x or more
within 3 years**. It runs on your own computer from public exchange data,
needs no Claude and no AI service to run, and never places an order.

**Status (2026-11-04): Phase 0 done, the clean slate.** The data store and
the testing machinery are kept; other asset classes and the old trading
skills are removed. Nothing predicts multibaggers yet. The plan and phases
are in [HANDOVER.md](HANDOVER.md); every experiment, failures included, is
in [research/FINDINGS.md](research/FINDINGS.md); day-to-day use is in
[docs/RUNBOOK.md](docs/RUNBOOK.md).

## What it will answer

Each month, a ranked watchlist. For every stock: its score, the
historical share of stocks with a score like it that tripled within 36
months against the base rate for all stocks, the median outcome, the share
that halved, and the evidence behind the score (growth, profitability,
ownership changes, price behaviour). Each list is frozen when published and
scored as it matures, so the tool keeps its own honest track record.

## Phases

| # | phase | status |
| --- | --- | --- |
| 0 | Clean slate: equities only, no AI at run time, runbook | done |
| 1 | Labels and base rates: 3x in 36 months for every stock and month-end, SME included | next |
| 2 | Data checks: fundamentals coverage per year, SME price history | |
| 3 | Pre-register features and the pass bar in FINDINGS.md | |
| 4 | Models: transparent scorecard, then logistic, then gradient boosting | |
| 5 | Basket simulation with real costs, tax and capacity | |
| 6 | App and Kite: watchlist, stock pages, live quotes, holdings sync (read-only) | |
| 7 | Forward record: frozen monthly lists, scored as they mature | |

## Data

All from the publishers' own files, into a local SQLite store
(`data/market.db`, never committed): NSE daily prices and delivery from
2012 for every series including SME, with delisted stocks (no survivorship
bias); corporate action factors checked against prices; ISINs joined across
splits; quarterly results from the exchange XBRL (December 2017 onward);
shareholding patterns; SAST Regulation 29 holder changes; insider trades
(to May 2026); bulk and block deals. Exchange XBRL for results only exists
from 2017, so fundamentals are tested on starting points from 2018. BSE-only
companies are not covered (BSE's API refuses scripts).

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic on it. A multibagger forecast is a probability, not a promise:
**Most stocks on any list will not triple**, many will fall, and small and
SME companies can lose most of their value or stop trading. Past test
results do not guarantee future ones. Decide for yourself, and talk to a
SEBI-registered adviser if you need advice. Sharing these lists with others
as recommendations would need SEBI Research Analyst registration; this
project is for personal use.

**Nothing here places an order.** Broker access is read-only (quotes,
holdings). Broker order tools are denied in `.claude/settings.json`, and
should be set to Blocked in your Claude connector settings too.

Charges and tax figures are estimates until checked against your own
contract notes and a chartered accountant.

MIT. Parts derive from the trader-skill pack by Jake Schincariol; India
edition by imheet07-gif.
