#!/usr/bin/env python3
"""Read a Consolidated Account Statement (CAS) into one broker-neutral shape.

A CAS lists everything a person holds, whichever broker or fund house they
used: NSDL and CDSL send one for demat accounts (stocks, and mutual fund
units held in demat), CAMS and KFintech for mutual fund folios. It is the
one import that works for every investor in India.

The PDF is parsed by casparser (MIT, github.com/codereverser/casparser),
which runs entirely offline. It is the one third-party library in core/, and
it is imported only when a PDF is read:

  pip install casparser

The PDF password (usually the PAN) is asked for at a prompt or read from
the CAS_PASSWORD environment variable. It is never taken on the command
line, never stored and never printed. Personal details in the statement
(name, PAN, email, address, phone, owners) are dropped at read time, and
account and folio numbers are masked to their last four characters.

  python3 core/cas.py holdings statement.pdf
  python3 core/cas.py holdings statement.pdf --reconcile holdings-EQ.xlsx
  python3 core/cas.py holdings statement.pdf --json
"""

import argparse
import getpass
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from money import D  # noqa: E402

VALUE_TOLERANCE = Decimal("1")  # rupees; statements round prices and values separately


def _d(v):
    if v is None or v == "":
        return None
    return D(str(v).replace(",", ""))


def mask(s):
    s = "" if s is None else str(s)
    return ("*" * max(len(s) - 4, 0)) + s[-4:] if s else ""


def _enum(v):
    return getattr(v, "name", v) if v is not None else None


def read_pdf(path, password):
    """casparser's parse of the PDF as a plain dict (Decimals kept)."""
    try:
        import casparser
    except ImportError:
        raise RuntimeError("reading a CAS PDF needs casparser: pip install casparser")
    data = casparser.read_cas_pdf(path, password)
    return data.model_dump(by_alias=True) if hasattr(data, "model_dump") else data


def normalise(data):
    """casparser output (depository or RTA statement) -> broker-neutral holdings.

    Returns {"source", "issuer", "period", "equities", "mutual_funds", "warnings"}.
    Each holding carries the account it sits in (masked), never its owner.
    """
    period = data.get("statement_period") or {}
    out = {
        "source": "cas",
        "issuer": _enum(data.get("file_type")),
        "period": {"from": str(period.get("from") or period.get("from_") or ""), "to": str(period.get("to") or "")},
        "equities": [],
        "mutual_funds": [],
        "warnings": list(data.get("parse_warnings") or []),
    }
    # NSDL / CDSL: demat accounts
    for acct in data.get("accounts") or []:
        where = {"depository": acct.get("type"), "dp": acct.get("name"),
                 "account": mask("%s%s" % (acct.get("dp_id") or "", acct.get("client_id") or ""))}
        for e in acct.get("equities") or []:
            h = dict(where, isin=e["isin"], name=e.get("name"), symbol=e.get("symbol"),
                     quantity=_d(e["num_shares"]), price=_d(e["price"]), value=_d(e["value"]))
            out["equities"].append(h)
            if h["quantity"] != h["quantity"].to_integral_value():
                out["warnings"].append("%s: fractional share quantity %s" % (h["isin"], h["quantity"]))
            if h["price"] and abs(h["quantity"] * h["price"] - h["value"]) > VALUE_TOLERANCE:
                out["warnings"].append("%s: quantity x price does not match the stated value" % h["isin"])
        for m in acct.get("mutual_funds") or []:
            out["mutual_funds"].append(dict(where, isin=m["isin"], name=m.get("name"), folio=mask(m.get("folio")),
                                            units=_d(m["balance"]), nav=_d(m["nav"]), value=_d(m["value"]),
                                            total_cost=_d(m.get("total_cost")), avg_cost=_d(m.get("avg_cost"))))
    # CAMS / KFintech: mutual fund folios
    for folio in data.get("folios") or []:
        for s in folio.get("schemes") or []:
            val = s.get("valuation") or {}
            out["mutual_funds"].append({
                "depository": None, "dp": folio.get("amc"), "account": mask(folio.get("folio")),
                "isin": s.get("isin"), "name": s.get("scheme"), "folio": mask(folio.get("folio")),
                "units": _d(s.get("close")), "nav": _d(val.get("nav")), "value": _d(val.get("value")),
                "total_cost": _d(val.get("cost")), "avg_cost": None,
            })
            if s.get("close") is not None and s.get("close_calculated") is not None and \
                    _d(s["close"]) != _d(s["close_calculated"]):
                out["warnings"].append("%s: closing units differ from the sum of transactions" % s.get("scheme"))
    return out


def reconcile(cas, broker):
    """Compare CAS holdings with a broker's holdings (zerodha_console.parse_holdings output).

    Matched by ISIN. A CAS covers every account, so a holding missing from
    the broker can be at another broker; a holding missing from the CAS is
    a red flag.
    """
    def qty_by_isin(rows, qkey):
        out = {}
        for r in rows:
            out[r["isin"]] = out.get(r["isin"], Decimal(0)) + (r[qkey] or 0)
        return out

    lines = []
    for kind, ckey, bkey, ck, bk in (("equity", "equities", "equity", "quantity", "quantity"),
                                     ("mutual_fund", "mutual_funds", "mutual_funds", "units", "quantity")):
        c, b = qty_by_isin(cas[ckey], ck), qty_by_isin(broker[bkey], bk)
        for isin in sorted(set(c) | set(b)):
            cq, bq = c.get(isin), b.get(isin)
            if cq is None:
                status = "missing from CAS"
            elif bq is None:
                status = "only in CAS (another broker or folio?)"
            elif cq == bq:
                status = "match"
            else:
                status = "quantity differs"
            lines.append({"kind": kind, "isin": isin, "cas": cq, "broker": bq, "status": status})
    return lines


def _password():
    pw = os.environ.get("CAS_PASSWORD")
    return pw if pw else getpass.getpass("CAS password (not shown, not stored): ")


def _js(o):
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read a CAS PDF (NSDL, CDSL, CAMS, KFintech) into holdings.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("holdings")
    h.add_argument("pdf")
    h.add_argument("--reconcile", metavar="BROKER_EXPORT", help="a Zerodha Console holdings export to compare with")
    h.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cas = normalise(read_pdf(a.pdf, _password()))
    except Exception as e:  # casparser raises its own types for a wrong password or an unknown layout
        print("error: could not read the CAS: %s" % e, file=sys.stderr)
        return 2
    if a.json and not a.reconcile:
        print(json.dumps(cas, default=_js, indent=1))
        return 0
    print("%s CAS %s to %s: %d stock holdings, %d mutual fund holdings" % (
        cas["issuer"], cas["period"]["from"], cas["period"]["to"], len(cas["equities"]), len(cas["mutual_funds"])))
    for w in cas["warnings"]:
        print("  check:", w)
    if a.reconcile:
        import zerodha_console
        lines = reconcile(cas, zerodha_console.parse_holdings(zerodha_console.read_any(a.reconcile)))
        if a.json:
            print(json.dumps(lines, default=_js, indent=1))
            return 0
        for x in lines:
            if x["status"] != "match":
                print("  %-12s %-13s CAS %-12s broker %-12s %s" % (x["isin"], x["kind"], x["cas"], x["broker"], x["status"]))
        print("  %d of %d holdings match" % (sum(x["status"] == "match" for x in lines), len(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
