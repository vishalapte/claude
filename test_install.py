"""Tests for ./install: the agent name in settings.json. Run: python3 -m unittest test_install

Every run installs into a temporary CLAUDE_CONFIG_DIR, so ~/.claude is never touched.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATHS = ("ENINE_MAIL_IDENTITY", "ENINE_MAIL_SENDERS", "ENINE_MAIL_DRAFTS", "ENINE_MAIL_SENT")


class Install(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = Path(tmp.name) / "claude"
        self.settings = self.dest / "settings.json"

    def run_install(self, *args):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.dest))
        return subprocess.run([str(ROOT / "install"), *args], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, check=False)

    def test_the_shipped_settings_hold_the_placeholder(self):
        env = json.loads((ROOT / "dot-claude" / "settings.json").read_text())["env"]
        for key in PATHS:
            self.assertIn("/<agent>/", env[key])

    def test_the_agent_name_replaces_the_placeholder(self):
        result = self.run_install("--agent", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("<agent>", self.settings.read_text())
        env = json.loads(self.settings.read_text())["env"]
        self.assertEqual(env["ENINE_MAIL_IDENTITY"], "~/.config/alice/identity.json")
        self.assertEqual(env["ENINE_MAIL_SENT"], "~/.local/share/alice/sent.jsonl")

    def test_a_second_run_with_the_same_name_changes_nothing(self):
        self.run_install("--agent=alice")
        result = self.run_install("--agent", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already matches", result.stdout)

    def test_another_name_replaces_only_the_settings(self):
        self.run_install("--agent", "alice")
        result = self.run_install("--agent", "bob", "--dry-run")
        self.assertEqual(result.stdout.count("would replace"), 1)
        self.assertIn("settings.json", result.stdout)

    def test_no_name_without_a_terminal_is_refused(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("--agent NAME", result.stderr)
        self.assertFalse(self.dest.exists())

    def test_a_bad_name_is_refused(self):
        for name in ("../x", "a/b", "A", "a b", "-x", ""):
            with self.subTest(name=name):
                self.assertEqual(self.run_install("--agent", name).returncode, 2)
                self.assertFalse(self.dest.exists())

    def test_a_dry_run_writes_nothing(self):
        result = self.run_install("--agent", "alice", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.dest.exists())

    def test_a_pycache_directory_is_not_installed(self):
        repo = self.dest.parent / "repo"
        skill = repo / "dot-claude" / "skills" / "demo"
        (skill / "__pycache__").mkdir(parents=True)
        shutil.copy(ROOT / "install", repo)
        (repo / "dot-claude" / "settings.json").write_text("{}\n")
        (skill / "demo.py").write_text("")
        (skill / "__pycache__" / "demo.pyc").write_text("")
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.dest))

        def run(*args):
            return subprocess.run([str(repo / "install"), "--agent", "alice", *args], env=env,
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  check=False)

        self.assertNotIn("__pycache__", run("--dry-run").stdout)
        self.assertEqual(run().returncode, 0)
        self.assertTrue((self.dest / "skills" / "demo" / "demo.py").exists())
        self.assertFalse((self.dest / "skills" / "demo" / "__pycache__").exists())
        self.assertIn("already matches", run().stdout)


if __name__ == "__main__":
    unittest.main()
