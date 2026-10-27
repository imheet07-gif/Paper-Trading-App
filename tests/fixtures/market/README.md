# market fixtures

Rows cut from public NSE files, downloaded 2026-10-27:

| file | source |
| --- | --- |
| `udiff_YYYYMMDD.csv` | UDiFF bhavcopy `content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`: header plus SURYAROSNI, GARFIBRES and one gold bond row, 2024-12-31 to 2025-01-03 (a 1:1 bonus ex 2025-01-01 and a 4:1 bonus ex 2025-01-03) |
| `mto_YYYYMMDD.dat` | delivery position `archives/equities/mto/MTO_DDMMYYYY.DAT`: header lines plus the same two stocks |
| `old_20150101.csv` | old-format bhavcopy `content/historical/EQUITIES/2015/JAN/cm01JAN2015bhav.csv.zip`: header and two rows |
| `old_20050103_no_isin.csv` | old-format bhavcopy for 2005-01-03: header and one row, from before NSE added the ISIN column |
| `nse_corporate_actions.json` | `www.nseindia.com/api/corporates-corporateActions?index=equities`: the two stocks' 2024-25 rows plus one each of a split, rights issue, demerger, AGM and special dividend |

Tests zip the CSVs again where the real file is a zip.
