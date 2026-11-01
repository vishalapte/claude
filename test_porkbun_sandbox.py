"""Live tests for dot-claude/skills/enine-porkbun against Porkbun's sandbox. Run:
python3 -m unittest test_porkbun_sandbox

The other Porkbun tests use a fake; these run porkbun.py itself against Porkbun's test system,
which simulates domains and DNS and changes nothing real. They need a sandbox key pair
(pk1_sb_, sk1_sb_) in ~/.config/porkbun/credentials.d/sandbox, mode 600, and skip without it.
The client refuses any answer not marked sandbox, so a live key there fails the run instead
of writing real DNS.
"""

import subprocess
import sys
import unittest
from pathlib import Path

from test_configure_domain_email import pc

SCRIPT = Path(__file__).resolve().parent / "dot-claude" / "skills" / "enine-porkbun" / "porkbun.py"
ACCOUNT = "sandbox"


def porkbun(*argv):
    """(exit code, output) of porkbun.py run on the sandbox account."""
    done = subprocess.run([sys.executable, str(SCRIPT), "--account", ACCOUNT, *argv],
                          capture_output=True, text=True, timeout=300, check=False)
    return done.returncode, done.stdout + done.stderr


@unittest.skipUnless((pc.PORKBUN_ACCOUNTS / ACCOUNT).is_file(), f"no {pc.PORKBUN_ACCOUNTS / ACCOUNT}")
class Sandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key, secret = pc.porkbun_credentials(ACCOUNT)
        if not key.startswith(pc.SANDBOX_KEY):
            raise unittest.SkipTest(f"the {ACCOUNT} account's key is not a sandbox key ({pc.SANDBOX_KEY})")
        domains = pc.Porkbun(key, secret, "").call("POST", "/domain/listAll", {"start": 0}).get("domains") or []
        if not domains:
            raise unittest.SkipTest("the sandbox account holds no domain; register one there first")
        cls.domain = domains[0]["domain"].lower()
        cls.name = f"_enine-sandbox-test.{cls.domain}"

    def run_ok(self, *argv, codes=(0,)):
        code, out = porkbun(*argv)
        self.assertIn(code, codes, out)
        self.assertTrue(out.startswith("SANDBOX:"), out)
        return out

    def test_every_verb_on_the_sandbox(self):
        self.run_ok("list")
        self.run_ok("domain", self.domain)
        out = self.run_ok("create", "TXT", self.name, "sandbox test", codes=(1,))
        self.assertIn("Porkbun dry run:", out)
        self.run_ok("create", "TXT", self.name, "sandbox test", "--apply")
        self.run_ok("update", "TXT", self.name, "sandbox test 2", "--apply")
        self.run_ok("history", self.domain)
        self.run_ok("check", self.domain, "--intent", "move-nameservers", codes=(0, 1))
        self.run_ok("delete", "TXT", self.name, "--apply")
        self.run_ok("delete", "TXT", self.name)  # gone: ok, nothing to do


if __name__ == "__main__":
    unittest.main()
