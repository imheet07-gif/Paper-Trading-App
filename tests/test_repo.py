import json
import os
import re
import unittest

from _load import ROOT

CORE = ["nse_calendar.py", "nse_holidays.json", "money.py", "charges.py", "charges.json", "instruments.py",
        "zerodha_console.py", "lots.py", "bhavcopy.py", "corporate_actions.py", "events.py", "financials.py",
        "shareholding.py", "deals.py", "tax.py", "tax_rates.json", "intraday.py"]
CODE_DIRS = ("core", "research", "app")

# Run-time code must work with no AI service at all (owner, 2026-11-04): no SDK import, no API host.
AI_IMPORT = re.compile(r"^\s*(import|from)\s+(anthropic|openai|google\.generativeai|google\.genai|mistralai|"
                       r"cohere|ollama|langchain\w*|litellm)\b", re.M)
AI_HOST = re.compile(r"api\.anthropic\.com|api\.openai\.com|generativelanguage\.googleapis\.com")
# Nothing places, modifies or cancels an order, at any broker, ever.
ORDER_CALL = re.compile(r"kite\.trade[^\"'\s]*/(orders|gtt)|\bplace_order\b|\bmodify_order\b|\bcancel_order\b")


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv", "venv", "data", "private", "trader")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml", ".js", ".html")) or f == "LICENSE":
                yield os.path.join(base, f)


def code_files():
    for d in CODE_DIRS:
        for base, dirs, files in os.walk(os.path.join(ROOT, d)):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for f in files:
                if f.endswith((".py", ".js", ".html")):
                    yield os.path.join(base, f)


class Repo(unittest.TestCase):
    def test_root_layout(self):
        local = (".env", "private", "trader", "data", "__pycache__", ".venv", "venv", ".DS_Store")  # git-ignored
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git") and d not in local)
        self.assertEqual(root, sorted([".claude", "CLAUDE.md", "CONCEPTS.md", "HANDOVER.md", "LICENSE", "README.md",
                                       "app", "core", "docs", "research", "requirements.txt", "tests"]))

    def test_core_modules_present(self):
        for f in CORE:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "core", f)), f)

    def test_personal_data_folders_are_ignored(self):
        with open(os.path.join(ROOT, ".gitignore")) as fh:
            ignored = fh.read().split()
        for d in ("private/", "trader/", "data/", ".env"):
            self.assertIn(d, ignored)

    def test_runs_without_any_ai_service(self):
        for path in code_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            self.assertIsNone(AI_IMPORT.search(text), rel)
            self.assertIsNone(AI_HOST.search(text), rel)

    def test_no_order_path_in_code(self):
        for path in code_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            self.assertIsNone(ORDER_CALL.search(text), os.path.relpath(path, ROOT))

    def test_readme_carries_the_fine_print(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        for line in ("## Fine print", "Not financial advice", "Nothing here places an order",
                     "Most stocks on any list will not triple"):
            self.assertIn(line, readme)

    def test_runbook_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "RUNBOOK.md")))

    def test_broker_order_tools_are_denied(self):
        with open(os.path.join(ROOT, ".claude", "settings.json")) as fh:
            deny = json.load(fh)["permissions"]["deny"]
        for tool in ("place_order", "modify_order", "cancel_order",
                     "place_gtt_order", "modify_gtt_order", "delete_gtt_order"):
            self.assertIn("mcp__Zerodha_MCP__" + tool, deny)

    def test_license_keeps_the_original_copyright(self):
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            self.assertIn("Copyright (c) 2026 Jake Schincariol", fh.read())

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)  # built, so this file passes too


if __name__ == "__main__":
    unittest.main()
