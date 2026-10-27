# core fixtures

Rows cut from the real public files, downloaded 2026-10-27:

| file | source |
| --- | --- |
| `EQUITY_L.sample.csv` | https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv (header and 7 rows: EQ, BE, BZ and a rights entitlement) |
| `kite_instruments.sample.csv` | https://api.kite.trade/instruments (the same stocks on NSE, plus a BSE row, an index and a future that must be ignored) |
| `angel_scrips.sample.json` | https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json (same, tick size in paise) |
| `nse_holiday_master.json` | https://www.nseindia.com/api/holiday-master?type=trading, capital market (CM) segment only |

Tests that need a disagreement between sources (a tick-size mismatch) edit a
copy of these rows in the test itself, so the files stay as published.

## Zerodha Console exports (personal data, so synthetic)

`console_holdings.json`, `console_tradebook.json` and
`console_tradebook.sample.csv` copy the exact layout of Console's holdings
and equity tradebook exports as checked against a real export on
2026-10-27: title rows, header row position, column names (Title Case in
XLSX, snake_case in CSV) and spreadsheet float noise such as
178.54999999999998. Every value, client ID and trade ID is made up. The CSV
column names follow Console's CSV download and should be re-checked against
a real one.
