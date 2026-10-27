import json
import os
import re
import unittest

from _load import ROOT

SKILLS = ["trader-copycat", "trader-news", "trader-budget", "trader-pelosi", "trader-analyst",
          "trader-screener", "trader-risk", "trader-broker", "trader-tax", "trader-alerts",
          "trader-rebalance"]
CORE = ["nse_calendar.py", "nse_holidays.json", "money.py", "charges.py", "charges.json", "instruments.py", "zerodha_console.py", "cas.py", "lots.py", "bhavcopy.py", "corporate_actions.py"]
# skills that can lead to an order carry the safety block
ORDER_PATH = ["trader-copycat", "trader-budget", "trader-pelosi", "trader-risk", "trader-broker",
              "trader-tax", "trader-alerts", "trader-rebalance"]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_matches_the_reel(self):
        local = (".env", "private", "trader", "data", "__pycache__")  # git-ignored, may exist locally
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git") and d not in local)
        self.assertEqual(root, sorted([".claude", ".claude-plugin", "CLAUDE.md", "LICENSE", "README.md",
                                       "core", "dashboard", "docs", "research", "tests"] + SKILLS))

    def test_core_modules_present(self):
        for f in CORE:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "core", f)), f)

    def test_personal_data_folders_are_ignored(self):
        with open(os.path.join(ROOT, ".gitignore")) as fh:
            ignored = fh.read().split()
        for d in ("private/", "trader/", "data/", ".env"):
            self.assertIn(d, ignored)

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("yes", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "india-trader")
        self.assertEqual(plugin["skills"], "./")
        self.assertIn("never places orders", plugin["description"].lower())
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "india-trader")
        self.assertEqual(market["plugins"][0]["name"], "india-trader")

    def test_readme_credits_upstream_and_carries_the_fine_print(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# India Trader"))
        for line in ("https://github.com/Jakeschincariol/trader-skill", "## Fine print",
                     "Not financial advice", "Nothing here places an order"):
            self.assertIn(line, readme)
        with open(os.path.join(ROOT, "docs", "trader-skill-README.md"), encoding="utf-8") as fh:
            upstream = fh.read()
        for s in SKILLS:
            self.assertIn("/" + s, upstream, s)

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

    def test_paper_trading_is_the_default(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        self.assertIs(cfg["paper"], True)
        self.assertIs(cfg.get("allow_options", False), False)


if __name__ == "__main__":
    unittest.main()
