# shareholding fixtures

Rows cut from public NSE answers fetched 2026-10-27 on the owner's machine
with core/bhavcopy.py's fetcher:

| file | source |
| --- | --- |
| `master_2026-09.json` | `www.nseindia.com/api/corporate-share-holdings-master?index=equities`: 3 of the 2,367 rows (all for the quarter ended 30 September 2026): an ordinary filing (DONEAR), a revised one (EPL) and one with shares held by employee trusts (SPENCERS). |
| `history_TCS.json` | `www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol=TCS`: all 21 rows, September 2021 to September 2026, including a revised March 2026 filing. |

| `shp_xbrl_TCS_2026-09.xml`, `shp_xbrl_DONEAR_2026-09.xml` | the two filings' XBRL from the `xbrl` links above (`nsearchives.nseindia.com/corporate/xbrl/SHP_...xml`), cut to the category-level contexts and the facts the parser reads (percent of shares, number of shares, number of holders, the encumbrance answers, symbol, ISIN, date). Every per-shareholder context and fact (holder names; NSE masks the PANs) is removed. |

The company's free-text notes (`remarksWeb`, `shareholdingPatternNotes`,
`promoterDematNotes`, `publicShareholdingNotes`) are replaced with
"(cleared)" where they had text, to keep the files small; everything else
is as published.
