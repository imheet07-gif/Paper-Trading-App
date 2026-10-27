# financials fixtures

Three results XBRL files as published by NSE, downloaded 2026-10-27 on the
owner's machine with core/bhavcopy.py's fetcher, from the `xbrl` links in
NSE's results feed (`www.nseindia.com/api/corporates-financial-results`):

| file | source |
| --- | --- |
| `INDAS_TCS_2020-03_consolidated.xml` | `nsearchives.nseindia.com/corporate/xbrl/INDAS_54905_240070_16042020082728_WEB.xml`: TCS, quarter and year ended 31 March 2020, consolidated. The older format: facts point at contexts the file never declares (OneD, FourD); each carries its own reporting period dates. |
| `INDAS_INFY_2022-06_standalone.xml` | `nsearchives.nseindia.com/corporate/xbrl/INDAS_600774_2315_24072022182100_WEB.xml`: Infosys, quarter ended 30 June 2022, standalone, declared contexts. |
| `BANKING_HDFCBANK_2020-06_consolidated.xml` | `nsearchives.nseindia.com/corporate/xbrl/BANKING_57945_291264_19072020084008_WEB.xml`: HDFC Bank, quarter ended 30 June 2020, consolidated, the banking format (interest earned, provisions; no start date). |

Unchanged. Company-level figures only; no personal data.
