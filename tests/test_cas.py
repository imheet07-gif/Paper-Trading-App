import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock
from decimal import Decimal

from _load import FIX, load
from test_zerodha_console import fixture_xlsx

cas = load("core", "cas")
zc = load("core", "zerodha_console")
CORE = os.path.join(FIX, "core")
PERSONAL = ("AAAAA0000A", "TEST INVESTOR", "test@example.com", "1 Test Road", "0000000000", "00000001", "12345678")


def fixture(name):
    with open(os.path.join(CORE, name + ".json")) as fh:
        return json.load(fh)["data"]


class Depository(unittest.TestCase):
    def setUp(self):
        self.n = cas.normalise(fixture("cas_cdsl"))

    def test_shape(self):
        self.assertEqual(self.n["issuer"], "CDSL")
        self.assertEqual(self.n["period"], {"from": "01-Sep-2026", "to": "30-Sep-2026"})
        self.assertEqual([e["isin"] for e in self.n["equities"]], ["INE009A01021", "INE1TAE01010"])
        infy = self.n["equities"][0]
        self.assertEqual((infy["quantity"], infy["price"], infy["dp"]),
                         (Decimal("10"), Decimal("1500.05"), "ZERODHA BROKING LIMITED"))
        mf = self.n["mutual_funds"][0]
        self.assertEqual((mf["units"], mf["value"]), (Decimal("100.123"), Decimal("8940.98")))

    def test_personal_details_are_dropped_and_numbers_masked(self):
        text = json.dumps(self.n, default=str)
        for p in PERSONAL:
            self.assertNotIn(p, text)
        self.assertEqual(self.n["equities"][0]["account"], "************0001")
        self.assertEqual(self.n["mutual_funds"][0]["folio"], "****5678")

    def test_value_mismatch_and_fractional_shares_warn(self):
        data = fixture("cas_cdsl")
        e = data["accounts"][0]["equities"][0]
        e["value"], e["num_shares"] = "99999", "10.5"
        w = "\n".join(cas.normalise(data)["warnings"])
        self.assertIn("INE009A01021: fractional share quantity 10.5", w)
        self.assertIn("INE009A01021: quantity x price does not match", w)


class FundHouse(unittest.TestCase):
    def test_cams_folio(self):
        n = cas.normalise(fixture("cas_cams"))
        self.assertEqual(n["issuer"], "CAMS")
        self.assertEqual(n["equities"], [])
        mf = n["mutual_funds"][0]
        self.assertEqual((mf["isin"], mf["units"], mf["total_cost"], mf["dp"]),
                         ("INF879O01027", Decimal("50.5"), Decimal("4000"), "PPFAS Mutual Fund"))
        self.assertNotIn("9876543", json.dumps(n, default=str))

    def test_units_not_matching_transactions_warn(self):
        data = fixture("cas_cams")
        data["folios"][0]["schemes"][0]["close_calculated"] = "49.5"
        self.assertIn("closing units differ", cas.normalise(data)["warnings"][0])


class Reconcile(unittest.TestCase):
    def test_cas_against_console_holdings(self):
        with tempfile.TemporaryDirectory() as d:
            broker = zc.parse_holdings(zc.read_any(fixture_xlsx(d, "console_holdings")))
        lines = {x["isin"]: x for x in cas.reconcile(cas.normalise(fixture("cas_cdsl")), broker)}
        self.assertEqual(lines["INE1TAE01010"]["status"], "match")
        self.assertEqual(lines["INF879O01027"]["status"], "match")
        self.assertEqual((lines["INE009A01021"]["status"], lines["INE009A01021"]["cas"], lines["INE009A01021"]["broker"]),
                         ("quantity differs", Decimal("10"), Decimal("40")))
        self.assertEqual(lines["INE002A01018"]["status"], "missing from CAS")


class Safety(unittest.TestCase):
    def test_password_is_never_a_command_line_option(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            cas.main(["holdings", "x.pdf", "--password", "secret"])

    @unittest.skipIf(importlib.util.find_spec("casparser"), "casparser is installed here")
    def test_missing_library_says_how_to_install(self):
        with self.assertRaisesRegex(RuntimeError, "pip install casparser"):
            cas.read_pdf("x.pdf", "pw")

    def test_a_missing_file_names_the_pdfs_that_are_there_without_asking_for_the_password(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "cas.pdf.pdf"), "wb").close()  # Finder hides the doubled extension
            err = io.StringIO()
            with redirect_stderr(err), mock.patch.object(cas, "_password", side_effect=AssertionError):
                self.assertEqual(cas.main(["holdings", os.path.join(d, "cas.pdf")]), 2)
            self.assertIn("no file at", err.getvalue())
            self.assertIn("cas.pdf.pdf", err.getvalue())

    def test_mask(self):
        self.assertEqual(cas.mask("1208160000000001"), "************0001")
        self.assertEqual(cas.mask(None), "")
        self.assertEqual(cas.mask("12"), "12")


if __name__ == "__main__":
    unittest.main()
