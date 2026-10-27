import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from decimal import Decimal
from xml.sax.saxutils import escape

from _load import FIX, load

zc = load("core", "zerodha_console")
CORE = os.path.join(FIX, "core")


def _cell(ref, v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return '<c r="%s" t="b"><v>%d</v></c>' % (ref, v)
    if isinstance(v, (int, float)):
        return '<c r="%s"><v>%r</v></c>' % (ref, v)
    return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(str(v)))


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx(path, sheets):
    """A minimal .xlsx with inline strings, the shape Console's export takes."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        names = list(sheets)
        z.writestr("xl/workbook.xml",
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(n), i + 1, i + 1)
                             for i, n in enumerate(names)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join('<Relationship Id="rId%d" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1)
                             for i in range(len(names))) + "</Relationships>")
        for i, n in enumerate(names):
            rows = "".join('<row r="%d">%s</row>' % (ri + 1, "".join(_cell("%s%d" % (_col(ci), ri + 1), v)
                                                                       for ci, v in enumerate(r)))
                           for ri, r in enumerate(sheets[n]))
            z.writestr("xl/worksheets/sheet%d.xml" % (i + 1),
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       "<sheetData>%s</sheetData></worksheet>" % rows)


def fixture_xlsx(tmp, name):
    with open(os.path.join(CORE, name + ".json")) as fh:
        sheets = json.load(fh)["sheets"]
    path = os.path.join(tmp, name + ".xlsx")
    make_xlsx(path, sheets)
    return path


class Values(unittest.TestCase):
    def test_float_noise_is_cleaned(self):
        self.assertEqual(zc.num("178.54999999999998"), Decimal("178.55"))
        self.assertEqual(zc.num("0.30000000000000004"), Decimal("0.3"))
        self.assertEqual(zc.num("4525.710999999999"), Decimal("4525.711"))
        self.assertEqual(zc.num("1.5633"), Decimal("1.5633"))
        self.assertIsNone(zc.num("-"))
        self.assertEqual(zc.num("-0.0"), Decimal(0))

    def test_header_keys_match_across_xlsx_and_csv(self):
        self.assertEqual(zc.key("Quantity Pledged (Margin)"), "quantity_pledged_margin")
        self.assertEqual(zc.key("quantity_pledged_margin"), "quantity_pledged_margin")
        self.assertEqual(zc.key("Order Execution Time"), "order_execution_time")


class Holdings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = zc.parse_holdings(zc.read_any(fixture_xlsx(self.tmp.name, "console_holdings")))

    def tearDown(self):
        self.tmp.cleanup()

    def test_sections_and_date(self):
        self.assertEqual(self.h["as_of"], "2026-10-27")
        self.assertEqual([x["symbol"] for x in self.h["equity"]], ["INFY", "TMCV", "RELIANCE"])
        self.assertEqual(len(self.h["mutual_funds"]), 1)  # the Combined sheet is not double counted

    def test_values_are_decimals_without_noise(self):
        itc = self.h["equity"][0]
        self.assertEqual(itc["previous_close"], Decimal("285.3"))
        self.assertEqual(itc["average_price"], Decimal("250.125"))
        mf = self.h["mutual_funds"][0]
        self.assertEqual((mf["quantity"], mf["category"]), (Decimal("100.123"), "Equity - Flexi Cap"))

    def test_discrepant_units_are_inside_quantity_available(self):
        cost, val = zc.value(self.h["equity"][1])
        self.assertIsNone(cost)
        self.assertEqual(val, Decimal("3840.5"))  # 10 x 384.05, not 20 x

    def test_warnings_for_missing_cost_discrepant_and_pledged(self):
        w = "\n".join(self.h["warnings"])
        self.assertIn("TMCV: 10 held at zero average price", w)
        self.assertIn("TMCV: 10 of the 10 units are marked discrepant", w)
        self.assertIn("RELIANCE: 5 units pledged", w)
        self.assertNotIn("INFY", w)

    def test_numbers_file_is_refused_with_a_way_out(self):
        with self.assertRaisesRegex(ValueError, "download the .xlsx or .csv"):
            zc.read_any("holdings.numbers")

    def test_not_a_holdings_file(self):
        with self.assertRaises(ValueError):
            zc.parse_holdings({"x": [["hello"]]})


class Tradebook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.xlsx = fixture_xlsx(self.tmp.name, "console_tradebook")

    def tearDown(self):
        self.tmp.cleanup()

    def test_xlsx_fills(self):
        tb = zc.parse_tradebook(zc.read_any(self.xlsx))
        self.assertEqual((tb["from"], tb["to"]), ("2026-04-01", "2026-10-27"))
        self.assertEqual(len(tb["trades"]), 4)
        first = tb["trades"][0]
        self.assertEqual((first["symbol"], first["side"], first["qty"], first["price"]),
                         ("INFY", "buy", 40, Decimal("250.125")))
        self.assertIsInstance(first["qty"], int)

    def test_csv_reads_the_same_as_xlsx(self):
        a = zc.parse_tradebook(zc.read_any(self.xlsx))["trades"]
        b = zc.parse_tradebook(zc.read_any(os.path.join(CORE, "console_tradebook.sample.csv")))["trades"]
        drop = lambda ts: [{k: v for k, v in t.items()} for t in ts]
        self.assertEqual(drop(a), drop(b))

    def test_fills_group_into_orders(self):
        o = zc.orders(zc.parse_tradebook(zc.read_any(self.xlsx))["trades"])
        sail = [x for x in o if x["symbol"] == "RELIANCE"][0]
        self.assertEqual((sail["qty"], sail["fills"]), (10, 2))
        self.assertEqual(sail["price"], Decimal("178.5360"))  # (3 x 178.55 + 7 x 178.53) / 10

    def test_day_charges_one_dp_per_scrip(self):
        r = zc.day_charges(zc.parse_tradebook(zc.read_any(self.xlsx))["trades"], "2026-08-06")
        dp = sum(x["charges"]["dp_charge"] for x in r["rows"])
        self.assertEqual(dp, Decimal("30.68"))  # RELIANCE and TCS: 2 x 15.34
        bse = [x for x in r["rows"] if x["exchange"] == "BSE"][0]
        self.assertEqual(bse["charges"]["exchange_txn"], Decimal("0.38"))  # 10,006 x 0.00375%

    def test_fractional_quantity_refused(self):
        sheets = zc.read_any(os.path.join(CORE, "console_tradebook.sample.csv"))
        rows = list(sheets.values())[0]
        rows[1][8] = "1.5"
        with self.assertRaises(ValueError):
            zc.parse_tradebook(sheets)

    def test_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(zc.main(["tradebook", self.xlsx]), 0)
            self.assertEqual(zc.main(["charges", self.xlsx, "--date", "2026-08-06"]), 0)
        out = buf.getvalue()
        self.assertIn("4 fills in 3 orders, 1 buys, 2 sells", out)
        self.assertIn("dp_charge", out)


if __name__ == "__main__":
    unittest.main()
