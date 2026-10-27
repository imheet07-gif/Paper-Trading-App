#!/usr/bin/env python3
"""News monitor for trader-news, India edition. Standard library only.

Watches NSE corporate announcements for the stocks you hold (or plan to
buy) and prints only what is new since the last check, newest first, with
a label on what kind of filing it is and a flag on the ones that usually
move a price.

    python3 news.py                                   # symbols from trader/portfolio.json or targets.json
    python3 news.py --symbols TCS ITC INFY
    python3 news.py --symbols TCS --all --days 7      # everything in the last 7 days, seen or not
    python3 news.py --offline announcements.json      # a saved NSE answer instead of the network

Source: NSE's corporate announcements API, the same endpoint and date-range
query core/events.py uses,
    https://www.nseindia.com/api/corporate-announcements?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
one request per check, filtered to your symbols here. Each announcement
carries the moment NSE disseminated it (IST), the heading NSE filed it
under, the company's own one-line summary and a link to the PDF.

State: trader/state/news.json remembers what you have seen (30 days, by
NSE's seq_id), so an announcement shows once.

An announcement is the company's own filing, not a story about it: open the
PDF before acting, and treat nothing here as advice.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import nse_calendar  # noqa: E402

API = ("https://www.nseindia.com/api/corporate-announcements?index=equities"
       "&from_date=%s&to_date=%s")
KEEP_DAYS = 30
IST = nse_calendar.IST

# What a filing is, from NSE's heading and the company's one-line summary together.
# Our own labels, not NSE's taxonomy; the first kind that matches wins.
KINDS = [
    ("results", r"financial result|integrated filing- financial|results? for the (quarter|year|half)"),
    ("dividend", r"\bdividend\b"),
    ("buyback", r"\bbuy ?back\b"),
    ("bonus or split", r"\bbonus\b|\bsplit\b|sub-division"),
    ("fund raising", r"\bqip\b|preferential|rights issue|fund rais|\bncd\b|debentures"),
    ("credit rating", r"credit rating"),
    ("deal", r"acqui(re|sition)|amalgamation|merger|scheme of arrangement|demerger|joint venture"),
    ("people", r"resignation|appointment|cessation|change in (director|management|kmp)|\bceo\b|\bcfo\b|auditor"),
    ("promoter or insider", r"sast|regulation 29|regulation 31|insider trading|\bpledge\b|encumbrance"),
    ("legal or regulatory", r"litigation|penalt|\bsebi order\b|show cause|search|raid|\bgst\b demand|tax demand|default"),
    ("board meeting", r"board meeting"),
    ("investor meeting", r"analyst|investor (meet|call|presentation)|earnings call|con\.? call|conference call"),
    ("press release", r"press release"),
]
# kinds that usually move a price, flagged in the report
IMPORTANT = {"results", "dividend", "buyback", "bonus or split", "fund raising", "credit rating", "deal",
             "people", "promoter or insider", "legal or regulatory"}


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def load_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise NewsError("%s is not valid JSON: %s" % (path, e))


def watch_from_files(portfolio="trader/portfolio.json", targets="trader/targets.json"):
    """Symbols from the portfolio's positions, else the targets' weights."""
    for path, key in ((portfolio, "positions"), (targets, "weights")):
        doc = load_json(path)
        rows = (doc or {}).get(key, []) if isinstance(doc, dict) else []
        syms = [str(r.get("symbol") or r.get("ticker") or "").strip().upper() for r in rows]
        syms = [s for s in syms if s]
        if syms:
            return syms
    return []


# ---------------------------------------------------------------- parse

def parse_time(text):
    """'05-Aug-2024 16:58:46' (IST) -> aware datetime, or None."""
    try:
        return dt.datetime.strptime((text or "").strip().title(), "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


def kind_of(heading, summary):
    """The first kind in KINDS whose pattern matches the heading or the summary,
    so a board meeting outcome that carries results is filed as results."""
    low = ("%s\n%s" % (heading or "", summary or "")).lower()
    for name, pattern in KINDS:
        if re.search(pattern, low):
            return name
    return "other"


def parse(rows):
    """NSE's announcement rows -> items: {id, symbol, isin, company, time, heading, summary, link, kind}."""
    if isinstance(rows, dict):
        rows = rows.get("data", [])
    if not isinstance(rows, list):
        raise NewsError("expected a list of announcements")
    out = []
    for r in rows:
        when = parse_time(r.get("an_dt") or r.get("exchdisstime"))
        sym = str(r.get("symbol") or "").strip().upper()
        if not when or not sym or not r.get("seq_id"):
            continue
        heading = str(r.get("desc") or "").strip()
        summary = " ".join(str(r.get("attchmntText") or "").split())
        link = r.get("attchmntFile") if str(r.get("attchmntFile") or "").startswith("https://") else ""
        out.append({"id": "nse:%s" % r["seq_id"], "symbol": sym, "isin": r.get("sm_isin"),
                     "company": r.get("sm_name"), "time": when, "heading": heading, "summary": summary,
                     "link": link, "kind": kind_of(heading, summary),
                     "xbrl": str(r.get("hasXbrl")) == "True"})
    return out


# ---------------------------------------------------------------- fetch

def fetch(start, end, get=None):
    """One NSE request for every announcement from `start` to `end` (dates)."""
    if get is None:
        import bhavcopy
        get = bhavcopy.http_get
    url = API % (start.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y"))
    blob = get(url)
    if blob is None:
        return []
    try:
        return json.loads(blob)
    except ValueError:
        raise NewsError("NSE answered something that is not JSON")


# ---------------------------------------------------------------- select and report

def select(items, watch, state, now, days=2, show_all=False):
    """Items for the watched symbols, inside the window, unseen unless show_all; newest first."""
    seen = state.get("seen", {})
    cutoff = now - dt.timedelta(days=days)
    watch = set(watch)
    out = [it for it in items if it["symbol"] in watch and it["time"] >= cutoff
           and (show_all or it["id"] not in seen)]
    out.sort(key=lambda it: it["time"], reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat(timespec="seconds")
    for it in items:
        seen.setdefault(it["id"], stamp)
    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render(items, watch, now, days):
    lines = []
    if not items:
        lines.append("Nothing new from NSE for %s in the last %d day(s)." % (", ".join(watch), days))
    for it in items:
        flag = "!" if it["kind"] in IMPORTANT else " "
        lines.append("%s %s IST  %-12s %-20s %s" % (flag, it["time"].strftime("%d %b %H:%M"), it["symbol"],
                                                   it["kind"], it["heading"]))
        if it["summary"]:
            lines.append("      %s" % (it["summary"][:220] + ("..." if len(it["summary"]) > 220 else "")))
        if it["link"]:
            lines.append("      %s" % it["link"])
    lines.append("")
    lines.append("! marks the kinds of filing that usually move a price. An announcement is the company's own "
                 "filing: read the PDF before acting. Not financial advice.")
    return "\n".join(lines)


def _json_ready(items):
    return [dict(it, time=it["time"].isoformat()) for it in items]


def main(argv=None, get=None):
    ap = argparse.ArgumentParser(description="NSE announcements for your stocks (trader-news).")
    ap.add_argument("--symbols", nargs="*", help="NSE symbols (default: portfolio, else targets)")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--targets", default="trader/targets.json")
    ap.add_argument("--days", type=int, default=2, help="look back this many days (default 2)")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--offline", metavar="FILE", help="a saved NSE announcements answer, no network")
    ap.add_argument("--now", help="pretend it is this time (ISO; no offset = IST)")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        watch = [s.upper() for s in (a.symbols or [])] or watch_from_files(a.portfolio, a.targets)
        if not watch:
            raise NewsError("which stocks? pass --symbols TCS ITC, or keep a trader/portfolio.json")
        if a.days < 1 or a.days > 31:
            raise NewsError("--days must be between 1 and 31 (one request covers the window)")
        now = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.now(IST)
        now = now if now.tzinfo else now.replace(tzinfo=IST)
        now = now.astimezone(IST)
        if a.offline:
            rows = load_json(a.offline)
            if rows is None:
                raise NewsError("no such file: %s" % a.offline)
        else:
            rows = fetch(now.date() - dt.timedelta(days=a.days), now.date(), get)
        items = parse(rows)
        state = load_json(a.state) or {}
        shown = select(items, watch, state, now, a.days, a.all)
    except NewsError as e:
        print("news.py: %s" % e, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"watch": watch, "items": _json_ready(shown)}, indent=2, ensure_ascii=False))
    else:
        print(render(shown, watch, now, a.days))
    if not a.dry_run:
        remember(shown, state, now)
        folder = os.path.dirname(a.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(a.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if shown else 1


if __name__ == "__main__":
    sys.exit(main())
