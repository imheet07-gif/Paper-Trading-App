#!/usr/bin/env python3
"""Build a personal portfolio dashboard: one self-contained HTML file.

The page has no external scripts, fonts or requests, so it opens offline in
any browser and the data in it goes nowhere. It is written to private/
(git-ignored) by default; keep it there or anywhere private.

  python3 dashboard/build.py --holdings private/holdings.xlsx \
      --tradebook private/tradebook-*.xlsx --pnl private/pnl.xlsx

Sections: headline numbers, allocation, holdings with their checks,
realized gains by financial year (FIFO, unmatched sells shown apart),
modelled charges by year, and every data check that needs a person.
"""

import argparse
import datetime as dt
import html
import json
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import charges as charges_mod  # noqa: E402
import lots  # noqa: E402
import zerodha_console as zc  # noqa: E402
from money import inr, inr_short  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "private", "dashboard.html")
CHARGE_HEADS = ("brokerage", "stt", "exchange_txn", "sebi_fee", "stamp_duty", "gst", "dp_charge")


# ---------------------------------------------------------------- data

def category(h):
    if h["kind"] == "equity":
        return "Direct stocks"
    c = h.get("category") or "Mutual funds"
    return c.replace("Equity - ", "") + " funds" if c.startswith("Equity - ") else c


def merge_trades(*tradebooks):
    """Fills from several tradebook exports, once each: overlapping date
    ranges must not double count. A fill is (exchange, trade ID, date)."""
    seen, out = set(), []
    for book in tradebooks:
        for t in book:
            k = (t["exchange"], t["trade_id"], t["date"])
            if k not in seen:
                seen.add(k)
                out.append(t)
    return out


def build_model(holdings, trades, pnl=None, broker="zerodha"):
    rows, checks = [], []
    for h in holdings["equity"] + holdings["mutual_funds"]:
        cost, value = zc.value(h)
        rows.append({
            "symbol": h["symbol"], "isin": h["isin"], "kind": h["kind"], "category": category(h),
            "quantity": h["quantity"], "avg": h["average_price"], "price": h["previous_close"],
            "cost": cost, "value": value or Decimal(0),
            "pnl": (value - cost) if (cost is not None and value is not None) else None,
            "flags": [],
        })
    total = sum(r["value"] for r in rows) or Decimal(1)
    for r in rows:
        r["weight"] = r["value"] / total * 100
    for w in holdings["warnings"]:
        checks.append(("holding", w))
        sym = w.split(":", 1)[0]
        for r in rows:
            if r["symbol"] == sym:
                r["flags"].append("cost missing" if "zero average price" in w else
                                  "discrepant" if "discrepant" in w else "pledged" if "pledged" in w else "check")

    alloc = {}
    for r in rows:
        alloc[r["category"]] = alloc.get(r["category"], Decimal(0)) + r["value"]
    alloc = sorted(alloc.items(), key=lambda kv: -kv[1])

    known = [r for r in rows if r["cost"] is not None]
    cost_known = sum(r["cost"] for r in known)
    value_known = sum(r["value"] for r in known)
    missing_cost_value = sum(r["value"] for r in rows if r["cost"] is None)

    m = lots.match(trades) if trades else {"open": {}, "realized": [], "symbols": {}}
    years = lots.by_year(m["realized"])
    for y in years:
        if y["unmatched_qty"]:
            checks.append(("history", "%s: %d shares of %s sold with no recorded buy (bought before the tradebooks "
                                      "start, transferred in, or received in a corporate action); their gain is not "
                                      "counted." % (y["fy"], y["unmatched_qty"], ", ".join(y["unmatched_symbols"]))))
    if trades:
        for line in lots.reconcile(m["open"], holdings["equity"]):
            if line["status"] != "match":
                checks.append(("reconcile", "%s: tradebooks leave %s shares open, holdings show %s (%s)." % (
                    line["symbol"] or line["isin"], line["lots"], line["held"] if line["held"] is not None else 0,
                    line["status"])))

    fees = {}
    if trades:
        notes = charges_mod.contract_note(
            [{"date": t["date"], "symbol": t["isin"], "side": t["side"], "qty": t["qty"], "price": t["price"],
              "exchange": t["exchange"]} for t in trades], broker)
        for day, n in notes.items():
            fy = lots.financial_year(dt.date.fromisoformat(day))
            f = fees.setdefault(fy, {k: Decimal(0) for k in CHARGE_HEADS})
            for k in CHARGE_HEADS:
                f[k] += n[k]
    ledger = None
    if pnl:
        ledger = {"period": "%s to %s" % (pnl["from"], pnl["to"]),
                  "total": sum(pnl["charges"].values(), Decimal(0)) + sum((d["amount"] for d in pnl["dp"]), Decimal(0))}
        if trades:
            v = zc.verify_charges(trades, pnl, broker)
            worst = max((abs(x["diff"]) for x in v["lines"]), default=Decimal(0))
            checks.append(("charges", "Modelled charges vs the broker ledger for %s: largest difference %s on any head."
                           % (ledger["period"], inr(worst, 4))))

    return {
        "as_of": holdings["as_of"], "rows": sorted(rows, key=lambda r: -r["value"]), "alloc": alloc,
        "total": sum(r["value"] for r in rows), "cost_known": cost_known, "value_known": value_known,
        "missing_cost_value": missing_cost_value, "years": years, "fees": fees, "ledger": ledger,
        "checks": checks, "trade_span": (min(t["date"] for t in trades), max(t["date"] for t in trades)) if trades else None,
        "fills": len(trades),
    }


# ---------------------------------------------------------------- rendering

def e(s):
    return html.escape("" if s is None else str(s))


def pct(x):
    return "%.1f%%" % x


def signed(x):
    if x is None:
        return "-"
    return ("+" if x > 0 else "") + inr(x, 0)


def bars_allocation(alloc, total):
    """Horizontal bars, one hue: how much sits in each bucket. HTML, so text
    stays readable at any width."""
    top = max(v for _, v in alloc) or Decimal(1)
    rows = []
    for name, v in alloc:
        tip = "%s: %s, %s of the portfolio" % (name, inr(v, 0), pct(v / total * 100))
        rows.append('<div class="hb"><div class="hb-l">%s</div><div class="hb-t"><div class="hb-b" data-tip="%s" '
                    'style="width:%.2f%%"></div></div><div class="hb-v">%s <span class="muted">%s</span></div></div>'
                    % (e(name), e(tip), max(float(v / top) * 100, 0.6), e(inr_short(v)), e(pct(v / total * 100))))
    return '<div class="hbars" role="img" aria-label="Allocation by category">%s</div>' % "".join(rows)


def all_years(years):
    """Fill in financial years with no sells, so the axis has no gaps."""
    if not years:
        return []
    have = {y["fy"]: y for y in years}
    first, last = int(years[0]["fy"][2:6]), int(years[-1]["fy"][2:6])
    out = []
    for start in range(first, last + 1):
        fy = "FY%d-%02d" % (start, (start + 1) % 100)
        out.append(have.get(fy) or {"fy": fy, "gain": Decimal(0), "short": Decimal(0), "long": Decimal(0),
                                    "unmatched_qty": 0, "unmatched_symbols": [], "empty": True})
    return out


def bars_years(years):
    """Columns around a zero line: gains up in blue, losses down in red,
    each with its value directly labelled."""
    if not years:
        return '<p class="muted">No trades loaded.</p>'
    vals = [y["gain"] for y in years]
    hi, lo = max(max(vals), Decimal(0)), min(min(vals), Decimal(0))
    span = (hi - lo) or Decimal(1)
    zero = float(hi / span) * 100  # zero line, percent from the top of the plot
    cols = []
    for y in years:
        frac = float(abs(y["gain"]) / span) * 100
        if y.get("empty"):
            bar, label = "", '<div class="vb-n muted" style="bottom:calc(%.2f%% + 4px)">no sells</div>' % (100 - zero)
        elif y["gain"] >= 0:
            bar = '<div class="vb-b gain" style="bottom:%.2f%%;height:%.2f%%"></div>' % (100 - zero, max(frac, 0.8))
            label = '<div class="vb-n" style="bottom:calc(%.2f%% + 4px)">%s</div>' % (100 - zero + frac, e(signed(y["gain"])))
        else:
            bar = '<div class="vb-b loss" style="top:%.2f%%;height:%.2f%%"></div>' % (zero, max(frac, 0.8))
            label = '<div class="vb-n" style="top:calc(%.2f%% + 4px)">%s</div>' % (zero + frac, e(signed(y["gain"])))
        tip = "%s: %s (short term %s, long term %s)%s" % (
            y["fy"], signed(y["gain"]), signed(y["short"]), signed(y["long"]),
            "; %d shares sold with no recorded buy are not counted" % y["unmatched_qty"] if y["unmatched_qty"] else "")
        cols.append('<div class="vb-c" data-tip="%s">%s%s</div>' % (e(tip), bar, label))
    xs = "".join('<div>%s%s</div>' % (e(y["fy"]), " *" if y["unmatched_qty"] else "") for y in years)
    return ('<div class="vbars" role="img" aria-label="Realized gains by financial year">'
            '<div class="vb-plot"><div class="vb-zero" style="top:%.2f%%"></div>%s</div>'
            '<div class="vb-x">%s</div></div>' % (zero, "".join(cols), xs))


CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--base:#c3c2b7;--ring:rgba(11,11,11,.10);--accent:#2a78d6;--gain:#2a78d6;--loss:#e34948;
--good:#006300;--warn-bg:#fff6df;--warn-ink:#6b4a00}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--base:#383835;--ring:rgba(255,255,255,.10);
--accent:#3987e5;--gain:#3987e5;--loss:#e66767;--good:#0ca30c;--warn-bg:#2e2611;--warn-ink:#f2cf7a}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--grid:#2c2c2a;--base:#383835;--ring:rgba(255,255,255,.10);--accent:#3987e5;--gain:#3987e5;--loss:#e66767;
--good:#0ca30c;--warn-bg:#2e2611;--warn-ink:#f2cf7a}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
h1{font-size:22px;margin:0}h2{font-size:16px;margin:0 0 12px}.muted{color:var(--ink2)}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:18px;margin-top:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:16px}
.kpi{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px}
.kpi .k{font-size:13px;color:var(--ink2)}.kpi .v{font-size:28px;font-weight:600;margin-top:2px}
.kpi .s{font-size:13px;color:var(--ink2)}
.hbars{display:grid;gap:10px;margin-bottom:8px}
.hb{display:grid;grid-template-columns:minmax(96px,150px) 1fr minmax(120px,auto);align-items:center;gap:10px}
.hb-l{font-size:13px;color:var(--ink2);text-align:right}.hb-t{height:20px}
.hb-b{height:20px;background:var(--accent);border-radius:0 4px 4px 0}.hb-b:hover{opacity:.85}
.hb-v{font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.vbars{margin:8px 0 16px}.vb-plot{position:relative;height:220px;margin:24px 0;display:flex;gap:8px}
.vb-zero{position:absolute;left:0;right:0;border-top:1px solid var(--base)}
.vb-c{position:relative;flex:1;min-width:0}
.vb-b{position:absolute;left:50%;transform:translateX(-50%);width:min(56px,60%)}
.vb-b.gain{background:var(--gain);border-radius:4px 4px 0 0}.vb-b.loss{background:var(--loss);border-radius:0 0 4px 4px}
.vb-c:hover .vb-b{opacity:.85}
.vb-n{position:absolute;left:0;right:0;text-align:center;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.vb-x{display:flex;gap:8px}.vb-x div{flex:1;min-width:0;text-align:center;font-size:12px;color:var(--muted)}
@media (max-width:560px){.hb{grid-template-columns:1fr auto}.hb-l{grid-column:1/-1;text-align:left}
.hb-t{grid-column:1}.vb-n{font-size:11px}.vb-x div{font-size:11px}}
.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
th{font-weight:600;color:var(--ink2);font-size:13px}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.up{color:var(--good)}.down{color:var(--loss)}
.flag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--warn-bg);
color:var(--warn-ink);margin-left:6px}
ul.checks{margin:0;padding-left:20px}ul.checks li{margin:6px 0}
.tag{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-right:6px}
button{font:inherit;font-size:13px;background:var(--surface);color:var(--ink);border:1px solid var(--ring);
border-radius:8px;padding:6px 10px;cursor:pointer}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);font-size:13px;
padding:6px 10px;border-radius:8px;max-width:320px;display:none;z-index:9}
footer{margin-top:24px;font-size:13px;color:var(--ink2)}
"""

JS = """
(function(){var t=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(function(el){
el.addEventListener('mousemove',function(ev){t.textContent=el.getAttribute('data-tip');t.style.display='block';
var x=Math.min(ev.clientX+14,window.innerWidth-t.offsetWidth-8);t.style.left=x+'px';t.style.top=(ev.clientY+14)+'px';});
el.addEventListener('mouseleave',function(){t.style.display='none';});});
var b=document.getElementById('theme');b.addEventListener('click',function(){var r=document.documentElement;
var dark=r.getAttribute('data-theme')?r.getAttribute('data-theme')==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
r.setAttribute('data-theme',dark?'light':'dark');try{localStorage.setItem('theme',r.getAttribute('data-theme'))}catch(e){}});
try{var s=localStorage.getItem('theme');if(s)document.documentElement.setAttribute('data-theme',s)}catch(e){}})();
"""


def render(m):
    total, rows = m["total"], m["rows"]
    unreal_known = m["value_known"] - m["cost_known"]
    mf_value = sum(r["value"] for r in rows if r["kind"] == "mutual_fund")
    realized_total = sum(y["gain"] for y in m["years"])
    k = []
    k.append(('Portfolio value', inr(total, 0), "as on %s, at the previous close" % m["as_of"]))
    k.append(('Unrealized P&amp;L', signed(unreal_known),
              "%s on %s invested%s" % (pct(unreal_known / m["cost_known"] * 100) if m["cost_known"] else "-",
                                     inr(m["cost_known"], 0),
                                     "; excludes %s with no cost on record" % inr(m["missing_cost_value"], 0)
                                     if m["missing_cost_value"] else "")))
    k.append(('In mutual funds', pct(mf_value / total * 100) if total else "-", "%s in funds, %s in stocks" % (
        inr_short(mf_value), inr_short(total - mf_value))))
    if m["years"]:
        k.append(('Realized, matched', signed(realized_total), "%s to %s, FIFO, before charges" % m["trade_span"]))
    kpis = "".join('<div class="kpi"><div class="k">%s</div><div class="v">%s</div><div class="s">%s</div></div>'
                   % (a, e(b), e(c)) for a, b, c in k)

    trs = []
    for r in rows:
        flags = "".join('<span class="flag">%s</span>' % e(f) for f in r["flags"])
        cls = "" if r["pnl"] is None else ("up" if r["pnl"] >= 0 else "down")
        trs.append("<tr><td>%s%s</td><td>%s</td><td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td>"
                   "<td class='n'>%s</td><td class='n %s'>%s</td><td class='n'>%s</td></tr>" % (
                       e(r["symbol"]), flags, e(r["category"]), e(r["quantity"]),
                       e(inr(r["avg"], 2) if r["avg"] else "-"), e(inr(r["price"], 2) if r["price"] is not None else "-"),
                       e(inr(r["value"], 0)), cls, e(signed(r["pnl"])), e(pct(r["weight"]))))
    holdings_table = ("<div class='tablewrap'><table><thead><tr><th>Holding</th><th>Category</th><th class='n'>Qty</th>"
                      "<th class='n'>Avg cost</th><th class='n'>Last close</th><th class='n'>Value</th>"
                      "<th class='n'>Unrealized</th><th class='n'>Weight</th></tr></thead><tbody>%s</tbody></table></div>"
                      % "".join(trs))

    alloc_rows = "".join("<tr><td>%s</td><td class='n'>%s</td><td class='n'>%s</td></tr>"
                         % (e(n), e(inr(v, 0)), e(pct(v / total * 100))) for n, v in m["alloc"])

    yr_rows = []
    for y in m["years"]:
        f = m["fees"].get(y["fy"])
        yr_rows.append("<tr><td>%s</td><td class='n %s'>%s</td><td class='n'>%s</td><td class='n'>%s</td>"
                       "<td class='n'>%s</td><td class='n'>%s</td></tr>" % (
                           e(y["fy"]), "up" if y["gain"] >= 0 else "down", e(signed(y["gain"])), e(signed(y["short"])),
                           e(signed(y["long"])), e("%d (%s)" % (y["unmatched_qty"], ", ".join(y["unmatched_symbols"]))
                                                    if y["unmatched_qty"] else "-"),
                           e(inr(sum(f.values()), 2) if f else "-")))
    years_table = ("<div class='tablewrap'><table><thead><tr><th>Year</th><th class='n'>Matched gain</th>"
                   "<th class='n'>Short term</th><th class='n'>Long term</th><th class='n'>Sold, no recorded buy</th>"
                   "<th class='n'>Charges (modelled)</th></tr></thead><tbody>%s</tbody></table></div>" % "".join(yr_rows))

    checks = "".join("<li><span class='tag'>%s</span>%s</li>" % (e(kind), e(text)) for kind, text in m["checks"])
    ledger = ""
    if m["ledger"]:
        ledger = "<p class='muted'>Broker ledger charges for %s: %s (including DP charges).</p>" % (
            e(m["ledger"]["period"]), e(inr(m["ledger"]["total"], 2)))

    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Portfolio</title>
<style>%(css)s</style></head><body><main>
<header><div><h1>Portfolio</h1><div class="muted">Holdings as on %(as_of)s. Built %(built)s from your broker exports, on this device.</div></div>
<button id="theme" type="button">Light / dark</button></header>
<section class="kpis">%(kpis)s</section>
<section class="card"><h2>Where the money sits</h2>%(alloc_chart)s
<details><summary class="muted">Table</summary><div class="tablewrap"><table><thead><tr><th>Category</th>
<th class="n">Value</th><th class="n">Share</th></tr></thead><tbody>%(alloc_rows)s</tbody></table></div></details></section>
<section class="card"><h2>Holdings</h2>%(holdings)s</section>
<section class="card"><h2>Realized gains by financial year</h2>
<p class="muted">FIFO matching of your tradebooks, price difference before charges. * marks a year with sells that have no recorded buy; those are not counted.</p>
%(years_chart)s%(years_table)s%(ledger)s</section>
<section class="card"><h2>Needs a look</h2><ul class="checks">%(checks)s</ul></section>
<footer>Not financial or tax advice. Values use the previous close from the export. Charges are modelled from the
tradebook with rates in core/charges.json. This file makes no network requests.</footer>
</main><div id="tip" role="tooltip"></div><script>%(js)s</script></body></html>
""" % {"css": CSS, "js": JS, "as_of": e(m["as_of"]), "built": e(dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
       "kpis": kpis, "alloc_chart": bars_allocation(m["alloc"], total) if m["alloc"] else "",
       "alloc_rows": alloc_rows, "holdings": holdings_table, "years_chart": bars_years(all_years(m["years"])),
       "years_table": years_table if m["years"] else "", "ledger": ledger,
       "checks": checks or "<li>Nothing flagged.</li>"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the offline portfolio dashboard (HTML).")
    ap.add_argument("--holdings", required=True, help="Console holdings export (.xlsx or .csv)")
    ap.add_argument("--tradebook", nargs="*", default=[], help="Console equity tradebook exports, any number")
    ap.add_argument("--pnl", help="Console P&L export, to check modelled charges against the ledger")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    try:
        holdings = zc.parse_holdings(zc.read_any(a.holdings))
        trades = merge_trades(*(zc.parse_tradebook(zc.read_any(p))["trades"] for p in a.tradebook))
        pnl = zc.parse_pnl(zc.read_any(a.pnl)) if a.pnl else None
    except (ValueError, KeyError) as err:
        print("error:", err, file=sys.stderr)
        return 2
    page = render(build_model(holdings, trades, pnl))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
