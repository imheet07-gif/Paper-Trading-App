# deals fixtures

Cut from NSE's files for the session of 27 October 2026, downloaded that
evening on the owner's machine with core/bhavcopy.py's fetcher:

| file | source |
| --- | --- |
| `bulk_2026-10-27.csv` | `nsearchives.nseindia.com/content/equities/bulk.csv`: header and 9 of the 70 rows, buys and sells. The three clients who are private individuals are replaced with INDIVIDUAL A, B and C; companies and funds are as published. |
| `block_2026-10-27.csv` | `nsearchives.nseindia.com/content/equities/block.csv`: as published, a day with no block deals (one NO RECORDS row). |
