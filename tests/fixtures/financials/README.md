# financials fixtures

Three results XBRL files as published by NSE, downloaded 2026-10-27 on the
owner's machine with core/bhavcopy.py's fetcher, from the `xbrl` links in
NSE's results feed (`www.nseindia.com/api/corporates-financial-results`):

| file | source |
| --- | --- |
| `INDAS_TCS_2020-03_consolidated.xml` | `nsearchives.nseindia.com/corporate/xbrl/INDAS_54905_240070_16042020082728_WEB.xml`: TCS, quarter and year ended 31 March 2020, consolidated. The older format: facts point at contexts the file never declares (OneD, FourD); each carries its own reporting period dates. |
| `INDAS_INFY_2022-06_standalone.xml` | `nsearchives.nseindia.com/corporate/xbrl/INDAS_600774_2315_24072022182100_WEB.xml`: Infosys, quarter ended 30 June 2022, standalone, declared contexts. |
| `BANKING_HDFCBANK_2020-06_consolidated.xml` | `nsearchives.nseindia.com/corporate/xbrl/BANKING_57945_291264_19072020084008_WEB.xml`: HDFC Bank, quarter ended 30 June 2020, consolidated, the banking format (interest earned, provisions; no start date). |

Added 2026-10-29, downloaded on the owner's machine the same way, from the
`xbrl` links in NSE's Integrated Filing feed
(`www.nseindia.com/api/integrated-filing-results`):

| file | source |
| --- | --- |
| `INTEGRATED_INDAS_SANOFICONR_2026-09_standalone.xml` | `nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1748152_28102026071728_WEB.xml`: Sanofi Consumer Healthcare India, quarter ended 30 September 2026, standalone, the original filing. |
| `INTEGRATED_BANKING_CUB_2026-09_standalone.xml` | `nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_BANKING_1748004_28102026061141_WEB.xml`: City Union Bank, quarter ended 30 September 2026, standalone, the banking format with a start date. |
| `INTEGRATED_NONINDAS_SWARAJ_2025-03_consolidated.xml` | `nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_NONINDAS_1460174_30052025074441_WEB.xml`: Swaraj Suiting, an SME half-yearly filer, half year and year ended 31 March 2025: no quarter. |
| `integrated_feed.json` | five rows of the feed as published: Sanofi Consumer's original filing and one of its revisions (no `broadcast_Date`), its governance filing, City Union Bank's filing (`from_date=01-10-2026&to_date=28-10-2026`) and Swaraj Suiting's (`from_date=01-05-2025&to_date=31-05-2025`). |

Unchanged. Company-level figures only (auditor firm names included); no personal data.
