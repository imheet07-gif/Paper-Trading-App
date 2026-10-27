# event fixtures

Rows cut from public NSE answers fetched 2026-10-27:

| file | source |
| --- | --- |
| `insider_2024-01.json` | `www.nseindia.com/api/corporates-pit?index=equities&from_date=01-01-2024&to_date=31-01-2024`: one promoter market purchase, one director market sale, one ESOP, one pledge and one employee purchase. The person's name (`acqName`) is replaced with a placeholder; everything else is as published. |
| `results_2024-01.json` | `www.nseindia.com/api/corporates-financial-results?index=equities&from_date=01-01-2024&to_date=31-01-2024&period=Quarterly`: three filings as published. |
| `announcements_2024-2026.json` | `www.nseindia.com/api/corporate-announcements?index=equities&from_date=...&to_date=...` for August 2024 and August 2026: a "Financial Result Updates" release (2024 heading), a results "Outcome of Board Meeting" (2026 heading), a board meeting outcome that is not results, an "Integrated Filing- Financial" and a press release, as published. |
| `sast_2026-09.json` | `www.nseindia.com/api/corporate-sast-reg29?index=equities&from_date=01-09-2026&to_date=30-09-2026`: a promoter open-market acquisition, a non-promoter sale and a promoter inter-se transfer. The holder's name (`acquirerName`) is replaced with a placeholder and `remarks` cleared; everything else is as published. |
