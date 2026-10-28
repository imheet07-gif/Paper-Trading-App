# funds fixtures

`navall_sample.txt` is cut from the real list, downloaded on the owner's
machine on 2026-10-28 (`core/funds.py list`): the header, a category line
and five scheme rows. It has separate Plan and Option columns.

`history_sample.txt` has the real history report's header (seen on the
owner's machine 2026-10-29: `Scheme Code;NAV Name;Plan;Option;...`) but
made-up rows; replace the rows with a real cut from `data/funds/`. Its
scheme codes and names are invented.

| file | stands for |
| --- | --- |
| `navall_sample.txt` | today's list, `https://www.amfiindia.com/spages/NAVAll.txt` |
| `history_sample.txt` | a history report, `DownloadNAVHistoryReport_Po.aspx` |
