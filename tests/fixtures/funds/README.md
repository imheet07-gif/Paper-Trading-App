# funds fixtures

`navall_sample.txt` is cut from the real list, downloaded on the owner's
machine on 2026-10-28 (`core/funds.py list`): the header, a category line
and five scheme rows. It has separate Plan and Option columns.

`history_sample.txt` is still made up, in the layout AMFI documents for
its history report; replace it with a real cut once `core/funds.py fetch`
has run on the owner's machine. Its scheme codes and names are invented.

| file | stands for |
| --- | --- |
| `navall_sample.txt` | today's list, `https://www.amfiindia.com/spages/NAVAll.txt` |
| `history_sample.txt` | a history report, `DownloadNAVHistoryReport_Po.aspx` |
