"""Tests for dot-claude/skills/enine-aws-ses. Run: python3 -m unittest test_aws_ses

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude. Replaces the script's one `aws()` call with a fake SES and points ENINE_MAIL_SENDERS at a
temporary file, so it never runs the aws CLI or reads the real senders.toml.
"""

import io
import os
import shlex
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from test_configure_domain_email import load
from test_porkbun import Account, Porkbun

ses = load("enine-aws-ses", "ses_identity.py")

D = "example.com"
TOKENS = ["aaaa1111", "bbbb2222", "cccc3333"]


class FakeSes:
    """SESv2 as the aws CLI answers it: one identity, or none (NotFoundException)."""

    def __init__(self, identity=None, refuse=None):
        self.identity = identity
        self.refuse = refuse
        self.calls = []

    def __call__(self, *argv):
        self.calls.append(argv)
        if self.refuse:
            return 254, None, f"\naws: [ERROR]: An error occurred ({self.refuse}) when calling the operation: no"
        verb = argv[1]
        if verb == "get-email-identity":
            if self.identity is None:
                return 254, None, "An error occurred (NotFoundException) when calling the GetEmailIdentity operation"
            return 0, self.identity, ""
        if verb == "create-email-identity":
            self.identity = {"DkimAttributes": {"SigningAttributesOrigin": "AWS_SES", "Tokens": TOKENS,
                                                "NextSigningKeyLength": value(argv, "--dkim-signing-attributes")}}
        elif verb == "put-email-identity-dkim-signing-attributes":
            self.identity["DkimAttributes"]["NextSigningKeyLength"] = value(argv, "--signing-attributes")
        elif verb == "put-email-identity-mail-from-attributes":
            self.identity["MailFromAttributes"] = {"MailFromDomain": argv[argv.index("--mail-from-domain") + 1]}
        return 0, {}, ""

    def writes(self):
        return [c[1] for c in self.calls if c[1] != "get-email-identity"]


def value(argv, flag):
    return argv[argv.index(flag) + 1].split("=", 1)[1]


def identity(length="RSA_2048_BIT", mail_from=f"hermes.{D}", origin="AWS_SES"):
    ident = {"DkimAttributes": {"SigningAttributesOrigin": origin, "Tokens": list(TOKENS),
                                "CurrentSigningKeyLength": length, "NextSigningKeyLength": length}}
    if mail_from:
        ident["MailFromAttributes"] = {"MailFromDomain": mail_from}
    return ident


class Run(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.saved = ses.aws
        self.senders = self.dir / "senders.toml"
        environ = mock.patch.dict(os.environ, {"ENINE_MAIL_SENDERS": str(self.senders)})
        environ.start()
        self.addCleanup(environ.stop)

    def tearDown(self):
        ses.aws = self.saved

    def run_script(self, fake, *argv):
        ses.aws = fake or FakeSes()
        out, err = io.StringIO(), io.StringIO()
        saved_argv, sys.argv = sys.argv, ["ses_identity.py", D, *argv]
        try:
            with redirect_stdout(out), redirect_stderr(err):
                ses.main()
        except SystemExit as stop:
            code = stop.code
        finally:
            sys.argv = saved_argv
        return code, out.getvalue() + err.getvalue()

    def commands(self, out):
        """The printed porkbun.py create commands, each as (TYPE, name, target, *flags)."""
        return [tuple(shlex.split(line)[3:]) for line in out.splitlines() if " create " in line and "porkbun.py" in line]


class WithoutAws(Run):
    def test_no_profile_and_no_tokens_prints_three_records_and_exits_1(self):
        code, out = self.run_script(None)
        self.assertEqual(code, 1)
        self.assertIn("SES: skipped (no --profile", out)
        self.assertIn("DKIM tokens: no tokens", out)
        self.assertEqual([c[0] for c in self.commands(out)], ["MX", "TXT", "TXT"])

    def test_no_profile_with_dkim_prints_the_six_records(self):
        code, out = self.run_script(None, "--dkim", *TOKENS)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.commands(out), [
            ("CNAME", f"aaaa1111._domainkey.{D}", "aaaa1111.dkim.amazonses.com"),
            ("CNAME", f"bbbb2222._domainkey.{D}", "bbbb2222.dkim.amazonses.com"),
            ("CNAME", f"cccc3333._domainkey.{D}", "cccc3333.dkim.amazonses.com"),
            ("MX", f"hermes.{D}", "feedback-smtp.us-east-1.amazonses.com", "--prio", "10"),
            ("TXT", f"hermes.{D}", "v=spf1 include:amazonses.com -all"),
            ("TXT", f"_dmarc.{D}", f"v=DMARC1; p=reject; pct=100; rua=mailto:dmarc@{D}"),
        ])

    def test_account_is_passed_to_each_command(self):
        _, out = self.run_script(None, "--dkim", *TOKENS, "--account", "acme")
        self.assertTrue(all(c[-2:] == ("--account", "acme") for c in self.commands(out)))

    def test_overrides_replace_single_records(self):
        _, out = self.run_script(None, "--mail-from", "mail", "--region", "us-east-2", "--dmarc", "v=DMARC1; p=none")
        self.assertEqual(self.commands(out), [
            ("MX", f"mail.{D}", "feedback-smtp.us-east-2.amazonses.com", "--prio", "10"),
            ("TXT", f"mail.{D}", "v=spf1 include:amazonses.com -all"),
            ("TXT", f"_dmarc.{D}", "v=DMARC1; p=none"),
        ])

    def test_a_dmarc_override_that_is_not_dmarc_is_refused(self):
        code, out = self.run_script(None, "--dmarc", "p=reject")
        self.assertEqual(code, 2)
        self.assertIn("must start v=DMARC1", out)

    def test_two_tokens_are_refused(self):
        code, out = self.run_script(None, "--dkim", "a", "b")
        self.assertEqual(code, 2)


class AtSes(Run):
    def test_a_profile_ses_refuses_is_skipped(self):
        fake = FakeSes(refuse="AccessDeniedException")
        code, out = self.run_script(fake, "--profile", "send-only", "--dkim", *TOKENS)
        self.assertEqual(code, 0, out)
        self.assertIn("SES: skipped (profile send-only: An error occurred (AccessDeniedException)", out)
        self.assertEqual(fake.writes(), [])

    def test_a_missing_identity_is_planned_and_nothing_changes_in_a_dry_run(self):
        fake = FakeSes()
        code, out = self.run_script(fake, "--profile", "admin")
        self.assertEqual(code, 1)
        self.assertIn("CREATE   identity", out)
        self.assertIn(f"CREATE   MAIL FROM  hermes.{D}", out)
        self.assertEqual(fake.writes(), [])

    def test_apply_creates_the_identity_with_2048_bit_dkim_and_mail_from(self):
        fake = FakeSes()
        code, out = self.run_script(fake, "--profile", "admin", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.writes(), ["create-email-identity", "put-email-identity-mail-from-attributes"])
        self.assertEqual(fake.identity["DkimAttributes"]["NextSigningKeyLength"], "RSA_2048_BIT")
        self.assertIn("DKIM tokens: from SES", out)
        self.assertEqual(len(self.commands(out)), 6)

    def test_a_1024_bit_identity_without_mail_from_is_edited(self):
        fake = FakeSes(identity(length="RSA_1024_BIT", mail_from=None))
        code, out = self.run_script(fake, "--profile", "admin")
        self.assertEqual(code, 1)
        self.assertIn("[was RSA_1024_BIT]", out)
        self.assertIn("[was none]", out)
        code, out = self.run_script(fake, "--profile", "admin", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.writes(), ["put-email-identity-dkim-signing-attributes", "put-email-identity-mail-from-attributes"])
        self.assertIn("After apply: 0 rows not ok", out)

    def test_a_conforming_identity_is_ok_and_gives_its_tokens(self):
        fake = FakeSes(identity())
        code, out = self.run_script(fake, "--profile", "admin", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.writes(), [])
        self.assertIn("DKIM tokens: from SES", out)

    def test_dkim_that_is_not_easy_dkim_is_held(self):
        fake = FakeSes(identity(origin="EXTERNAL"))
        code, out = self.run_script(fake, "--profile", "admin", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT DKIM", out)
        self.assertEqual(fake.writes(), [])

    def test_dkim_tokens_that_differ_from_ses_print_no_dkim_commands(self):
        code, out = self.run_script(FakeSes(identity()), "--profile", "admin", "--dkim", "x1", "x2", "x3")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT: --dkim differs", out)
        self.assertNotIn("CNAME", " ".join(c[0] for c in self.commands(out)))


class Senders(Run):
    TEXT = ('# Sending domains\n[defaults]\nregion = "us-east-1"\n\n'
            f'[domains."{D}"]\nprofile = "ses-x"  # account 000\nregion = "us-east-2"\nmail_from = "mail.{D}"\n')

    def test_region_and_mail_from_come_from_senders_toml(self):
        self.senders.write_text(self.TEXT)
        _, out = self.run_script(None)
        self.assertEqual(self.commands(out)[0], ("MX", f"mail.{D}", "feedback-smtp.us-east-2.amazonses.com", "--prio", "10"))

    def test_a_listed_domain_is_ok_and_not_edited(self):
        self.senders.write_text(self.TEXT)
        _, out = self.run_script(None, "--dkim", *TOKENS, "--sender-profile", "ses-y", "--apply")
        self.assertIn("Sender config: ok", out)
        self.assertEqual(self.senders.read_text(), self.TEXT)

    def test_a_new_domain_is_shown_then_appended_keeping_the_comments(self):
        text = '# Sending domains\n[defaults]\nregion = "us-east-1"\n'
        self.senders.write_text(text)
        code, out = self.run_script(None, "--dkim", *TOKENS, "--sender-profile", "ses-y")
        self.assertEqual(code, 1)
        self.assertIn(f'[domains."{D}"]', out)
        self.assertEqual(self.senders.read_text(), text)
        code, out = self.run_script(None, "--dkim", *TOKENS, "--sender-profile", "ses-y", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.senders.read_text(),
                         text + f'\n[domains."{D}"]\nprofile   = "ses-y"\nmail_from = "hermes.{D}"\n')

    def test_an_unset_sender_config_is_refused_naming_flag_and_variable(self):
        with mock.patch.dict(os.environ, {"ENINE_MAIL_SENDERS": ""}):
            code, out = self.run_script(None, "--sender-profile", "ses-y")
        self.assertEqual(code, 2)
        self.assertIn("pass --senders FILE or set ENINE_MAIL_SENDERS", out)

    def test_the_flag_wins_over_the_variable(self):
        flagged = self.dir / "other.toml"
        flagged.write_text(self.TEXT)
        _, out = self.run_script(None, "--senders", str(flagged))
        self.assertEqual(self.commands(out)[0][1], f"mail.{D}")

    def test_no_senders_file_is_refused(self):
        code, out = self.run_script(None, "--sender-profile", "ses-y")
        self.assertEqual(code, 2)
        self.assertIn("no ", out)


class PorkbunSkips(Porkbun):
    """The two refusals the skill reads as "Porkbun: skipped" (SKILL.md step 8); the texts are pinned here."""

    def test_no_credentials_file(self):
        self.files["porkbun"].unlink()
        code, out = self.run_script(Account([]), "create", "TXT", f"_dmarc.{D}", "v=DMARC1; p=reject")
        self.assertEqual(code, 2)
        self.assertIn("must set PORKBUN_API_KEY", out)

    def test_no_such_account(self):
        code, out = self.run_script(Account([]), "create", "TXT", f"_dmarc.{D}", "v=DMARC1; p=reject", "--account", "nobody")
        self.assertEqual(code, 2)
        self.assertIn("no Porkbun account nobody", out)


if __name__ == "__main__":
    unittest.main()
