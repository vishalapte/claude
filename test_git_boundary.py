"""Tests for dot-claude/git-hooks. Run: python3 -m unittest test_git_boundary

Lives at the repo root on purpose: the install extracts with --strip-components=2, so a
top-level file never reaches ~/.claude, while a tests/ directory would.

Each test gets a scratch HOME whose ~/.claude/git-hooks links to this checkout's hooks, and
git is pointed at it exactly the way settings.json does, `~` included.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent / "dot-claude" / "git-hooks"


class GitBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude" / "git-hooks").symlink_to(HOOKS)
        self.orgs = self.tmp / ".claude" / "orgs.json"
        self.orgs.write_text('["My-Org", "me"]')
        self.env = {
            "HOME": str(self.tmp), "PATH": os.environ["PATH"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "~/.claude/git-hooks",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        self.git("init", "-q", "--bare", "remote.git", cwd=self.tmp)
        self.git("init", "-q", "repo", cwd=self.tmp)
        self.repo = self.tmp / "repo"
        self.git("commit", "-q", "--allow-empty", "-m", "x")

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env,
                              capture_output=True, text=True, check=check)

    def repo_hook(self, name, marker):
        hook = self.repo / ".git" / "hooks" / name
        hook.write_text(f"#!/bin/sh\ncat > /dev/null\ntouch {self.tmp / marker}\n")
        hook.chmod(0o755)

    def boundary(self, url):
        return subprocess.run([str(HOOKS / "_boundary.py"), url], env=self.env,
                              capture_output=True, text=True)

    # --- the owner check

    def test_listed_owner_passes_case_insensitively(self):
        for url in ("https://github.com/my-org/app.git", "git@github.com:ME/app.git",
                    "ssh://git@ssh.github.com:443/me/app.git"):
            with self.subTest(url=url):
                self.assertEqual(self.boundary(url).returncode, 0)

    def test_unlisted_owner_refused(self):
        out = self.boundary("https://github.com/stranger/app.git")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("'stranger' is not in", out.stderr)

    def test_non_github_remote_refused(self):
        for url in ("https://gitlab.com/me/app.git", str(self.tmp / "remote.git")):
            with self.subTest(url=url):
                self.assertNotEqual(self.boundary(url).returncode, 0)

    # --- the absence of the config protects rather than exposes

    def test_missing_orgs_refuses(self):
        self.orgs.unlink()
        self.assertNotEqual(self.boundary("https://github.com/me/app.git").returncode, 0)

    def test_malformed_orgs_refuses(self):
        for bad in ("not json", "[]", '{"a": 1}', '["ok", 3]', '[""]'):
            with self.subTest(bad=bad):
                self.orgs.write_text(bad)
                self.assertNotEqual(self.boundary("https://github.com/me/app.git").returncode, 0)

    # --- through real git: every way of starting a push reaches the hook

    def test_push_refused_however_started(self):
        remote = str(self.tmp / "remote.git")
        self.git("config", "alias.p", "push")
        for argv in (["git", "push", remote, "HEAD:refs/heads/main"],
                     ["git", "p", remote, "HEAD:refs/heads/main"],
                     ["bash", "-c", f"git push {remote} HEAD:refs/heads/main"]):
            with self.subTest(argv=argv):
                out = subprocess.run(argv, cwd=self.repo, env=self.env, capture_output=True, text=True)
                self.assertNotEqual(out.returncode, 0)
                self.assertIn("org_boundary", out.stderr)
        self.assertEqual(self.git("branch", "-r", cwd=self.tmp / "remote.git").stdout, "")

    def test_allowed_push_runs_repo_pre_push_with_stdin(self):
        self.repo_hook("pre-push", "repo-pre-push-ran")
        out = subprocess.run([str(HOOKS / "pre-push"), "origin", "https://github.com/me/app.git"],
                             cwd=self.repo, env=self.env, input="refs/heads/x abc refs/heads/x def\n",
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue((self.tmp / "repo-pre-push-ran").exists())

    def test_refused_push_skips_repo_pre_push(self):
        self.repo_hook("pre-push", "repo-pre-push-ran")
        out = subprocess.run([str(HOOKS / "pre-push"), "origin", "https://github.com/stranger/app.git"],
                             cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse((self.tmp / "repo-pre-push-ran").exists())

    # --- the repo's own hooks keep working under core.hooksPath

    def test_repo_hooks_still_run(self):
        self.repo_hook("pre-commit", "repo-pre-commit-ran")
        self.git("commit", "-q", "--allow-empty", "-m", "y")
        self.assertTrue((self.tmp / "repo-pre-commit-ran").exists())

    def test_repo_hook_failure_still_blocks(self):
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.assertNotEqual(self.git("commit", "-q", "--allow-empty", "-m", "z", check=False).returncode, 0)

    def test_repo_without_hooks_is_unaffected(self):
        self.git("commit", "-q", "--allow-empty", "-m", "w")


if __name__ == "__main__":
    unittest.main()
