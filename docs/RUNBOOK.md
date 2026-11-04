# Runbook

How to run this project with no Claude session. Everything here is a plain
command on your Mac. Times are IST.

## Once: setup

```bash
git clone <repo> && cd Paper-Trading-App
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s tests          # must pass, offline

# build the store (hours the first time; resumable)
python3 core/bhavcopy.py backfill --start 2012-01-01
python3 core/corporate_actions.py fetch --start 2012-01-01
python3 core/events.py fetch
python3 core/financials.py fetch
python3 core/shareholding.py fetch
```

For Kite (optional; only live quotes and holdings use it), put the key and
secret of your Kite Connect app in a `.env` file at the repository root:

```
KITE_API_KEY=...
KITE_API_SECRET=...
```

Never type them on a command line or commit them. `.env`, `data/`,
`private/` and `trader/` are git-ignored.

## Every trading evening (after about 7 pm)

```bash
python3 research/scan.py update     # prices, delivery, corporate actions, indices, instruments, events, results, deals
```

Bulk and block deals are published for the latest session only: a missed
evening is lost for good, so this is the one job worth scheduling.

## Once a month (shareholding is quarterly; `update` does not fetch it)

```bash
python3 core/shareholding.py fetch
```

## Every morning, only if you want live quotes

```bash
python3 core/intraday.py login      # Zerodha requires a fresh login every day
```

Predictions never depend on this step; skip it and nothing else breaks.

## Scheduling with launchd (no Claude, no terminal left open)

Save as `~/Library/LaunchAgents/in.multibagger.update.plist`, with your own
paths, then `launchctl load ~/Library/LaunchAgents/in.multibagger.update.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>in.multibagger.update</string>
  <key>ProgramArguments</key><array>
    <string>/Users/YOU/Paper-Trading-App/.venv/bin/python3</string>
    <string>/Users/YOU/Paper-Trading-App/research/scan.py</string>
    <string>update</string>
  </array>
  <key>StartCalendarInterval</key><array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>15</integer></dict>
  </array>
  <key>StandardOutPath</key><string>/Users/YOU/Paper-Trading-App/private/update.log</string>
  <key>StandardErrorPath</key><string>/Users/YOU/Paper-Trading-App/private/update.log</string>
</dict></plist>
```

The Mac must be awake at that time (System Settings, Battery or Energy,
schedule a wake), or launchd runs the job when it next wakes.

## The app

```bash
python3 app/server.py               # then open http://127.0.0.1:8765
```

It answers on this computer only. Phase 6 rebuilds it around the
multibagger watchlist.

## Checking the store

```bash
python3 core/bhavcopy.py status
python3 core/events.py status
python3 core/financials.py coverage
python3 core/shareholding.py status
```

## When a source breaks

NSE changes endpoints and file layouts without notice. The symptoms and
what to do:

| symptom | likely cause | what to do |
| --- | --- | --- |
| `update` reports a file not published | NSE is late, or a holiday | run it again later; a day missing for more than two days is a real break |
| HTTP 401 or 403 from nseindia.com | NSE's cookie or bot check changed | try again later; if it persists, the fetcher needs a fix (open an issue) |
| a parser error naming a column | NSE changed a file's layout | do not edit the store by hand; the parser needs a fix, with a new fixture cut from the new file |
| Kite says the token is invalid | the daily session ended | `python3 core/intraday.py login` |
| tests fail after `git pull` | a change needs a newer library | `pip install -r requirements.txt` |

Nothing in the data layer is ever guessed: an unknown value is an error, so
a break shows up as a failed job, never as quietly wrong numbers.

Planned (Phase 6): a health check after each nightly job and a macOS
notification when one fails.
