"""Tests for dot-claude/skills/enine-aws-ses. Run: python3 -m unittest test_aws_ses

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude. Replaces the script's one `aws()` call with a fake SES, reads the zone from
test_porkbun's Porkbun fake with credentials in a temporary home, and points ENINE_MAIL_SENDERS at
a temporary file, so it never runs the aws CLI, calls Porkbun or reads the real senders.toml.
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
TOKENS = ["a" * 32, "b" * 32, "c" * 32]
OTHER = ["d" * 32, "e" * 32, "f" * 32]
MX = "feedback-smtp.us-east-1.amazonses.com"
SPF = "v=spf1 include:amazonses.com -all"
DMARC = f"v=DMARC1; p=reject; pct=100; rua=mailto:dmarc@{D}"


def dkim(tokens):
    return [{"name": f"{t}._domainkey", "type": "CNAME", "content": f"{t}.dkim.amazonses.com"} for t in tokens]


def configured(tokens=TOKENS):
    """A zone already set up for SES: the three DKIM CNAMEs, the MAIL FROM MX and SPF, DMARC."""
    return dkim(tokens) + [
        {"name": "hermes", "type": "MX", "content": MX, "prio": 10},
        {"name": "hermes", "type": "TXT", "content": SPF},
        {"name": "_dmarc", "type": "TXT", "content": DMARC},
    ]


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


class Run(Porkbun):
    module = ses
    argv0 = "ses_identity.py"

    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp())
        self.saved_aws = ses.aws
        self.senders = self.dir / "senders.toml"
        environ = mock.patch.dict(os.environ, {"ENINE_MAIL_SENDERS": str(self.senders)})
        environ.start()
        self.addCleanup(environ.stop)

    def tearDown(self):
        ses.aws = self.saved_aws
        super().tearDown()

    def argv(self, *argv):
        return [self.argv0, D, *argv]

    def run_ses(self, fake, *argv, zone=()):
        """Run ses_identity.py against a fake SES and a Porkbun zone holding `zone`."""
        ses.aws = fake or FakeSes()
        self.apis = Account(zone)
        return self.run_script(self.apis, *argv)

    def commands(self, out, verb="create"):
        """The printed porkbun.py commands of one verb, each as (TYPE, name, target, *flags)."""
        return [tuple(shlex.split(line)[3:]) for line in out.splitlines()
                if f" {verb} " in line and "porkbun.py" in line]

    def row(self, out, kind):
        """A Porkbun row's status, by its kind (DKIM, MX, SPF, DMARC); the SES rows come before them."""
        zone = out[out.index("Porkbun: the zone"):]
        return next(line.split()[0] for line in zone.splitlines() if line.split()[1:2] == [kind])


class EmptyZone(Run):
    def test_no_tokens_and_none_in_the_zone_is_an_error_and_the_rest_are_created(self):
        code, out = self.run_ses(None)
        self.assertEqual(code, 2)
        self.assertIn("SES: skipped (no --profile", out)
        self.assertEqual(self.row(out, "DKIM"), "ERROR")
        self.assertIn("no tokens provided (no SES identity read, no --dkim) and none in the zone", out)
        self.assertEqual([c[0] for c in self.commands(out)], ["MX", "TXT", "TXT"])

    def test_dkim_given_creates_the_six_records_and_writes_nothing(self):
        code, out = self.run_ses(None, "--dkim", *TOKENS)
        self.assertEqual(code, 1, out)
        self.assertEqual({self.row(out, k) for k in ("DKIM", "MX", "SPF", "DMARC")}, {"CREATE"})
        self.assertEqual(self.commands(out), [
            ("CNAME", f"{TOKENS[0]}._domainkey.{D}", f"{TOKENS[0]}.dkim.amazonses.com"),
            ("CNAME", f"{TOKENS[1]}._domainkey.{D}", f"{TOKENS[1]}.dkim.amazonses.com"),
            ("CNAME", f"{TOKENS[2]}._domainkey.{D}", f"{TOKENS[2]}.dkim.amazonses.com"),
            ("MX", f"hermes.{D}", MX, "--prio", "10"),
            ("TXT", f"hermes.{D}", SPF),
            ("TXT", f"_dmarc.{D}", DMARC),
        ])
        self.assertEqual(self.apis.writes, [])

    def test_account_is_passed_to_each_command(self):
        self.write_account("acme", "pk1_acme")
        _, out = self.run_ses(None, "--dkim", *TOKENS, "--account", "acme")
        self.assertTrue(all(c[-2:] == ("--account", "acme") for c in self.commands(out)))
        self.assertIn("pk1_acme", self.apis.keys)

    def test_overrides_replace_single_records(self):
        _, out = self.run_ses(None, "--dkim", *TOKENS, "--mail-from", "mail", "--region", "us-east-2",
                              "--dmarc", "v=DMARC1; p=none")
        self.assertEqual(self.commands(out)[3:], [
            ("MX", f"mail.{D}", "feedback-smtp.us-east-2.amazonses.com", "--prio", "10"),
            ("TXT", f"mail.{D}", SPF),
            ("TXT", f"_dmarc.{D}", "v=DMARC1; p=none"),
        ])

    def test_a_dmarc_override_that_is_not_dmarc_is_refused(self):
        code, out = self.run_ses(None, "--dmarc", "p=reject")
        self.assertEqual(code, 2)
        self.assertIn("must start v=DMARC1", out)

    def test_two_tokens_are_refused(self):
        code, out = self.run_ses(None, "--dkim", "a", "b")
        self.assertEqual(code, 2)

    def test_a_porkbun_refusal_stops_the_run(self):
        self.files["porkbun"].unlink()
        code, out = self.run_ses(None, "--dkim", *TOKENS)
        self.assertEqual(code, 2)
        self.assertIn("must set PORKBUN_API_KEY", out)


class Configured(Run):
    def test_a_configured_zone_without_tokens_is_confirmed_from_the_zone(self):
        code, out = self.run_ses(None, zone=configured())
        self.assertEqual(code, 0, out)
        self.assertEqual({self.row(out, k) for k in ("DKIM", "MX", "SPF", "DMARC")}, {"CONFIRM"})
        self.assertIn(f"CONFIRMED: {D} (DNS only; SES not read)", out)
        self.assertEqual(self.commands(out), [])

    def test_the_same_tokens_given_confirm(self):
        code, out = self.run_ses(None, "--dkim", *reversed(TOKENS), zone=configured())
        self.assertEqual(code, 0, out)
        self.assertIn(f"CONFIRMED: {D}", out)

    def test_other_sendings_records_beside_are_not_looked_at(self):
        zone = configured() + [
            {"name": "fm1._domainkey", "type": "CNAME", "content": f"fm1.{D}.dkim.fmhosted.com"},
            {"name": "hermes", "type": "TXT", "content": "google-site-verification=x"},
            {"name": "", "type": "TXT", "content": "v=spf1 include:spf.messagingengine.com -all"},
        ]
        code, out = self.run_ses(None, zone=zone)
        self.assertEqual(code, 0, out)

    def test_an_old_token_beside_the_given_three_is_not_looked_for(self):
        zone = configured()
        code, out = self.run_ses(None, "--dkim", *OTHER, zone=zone)
        self.assertEqual(self.row(out, "DKIM"), "PROMPT")
        self.assertEqual(self.commands(out, "delete"), [
            ("CNAME", f"{t}._domainkey.{D}", f"{t}.dkim.amazonses.com") for t in TOKENS])
        self.assertEqual(self.commands(out), [
            ("CNAME", f"{t}._domainkey.{D}", f"{t}.dkim.amazonses.com") for t in OTHER])
        self.assertEqual(code, 1)
        self.assertNotIn("CONFIRMED", out)
        self.assertEqual(self.apis.writes, [])

    def test_a_partly_set_zone_creates_only_what_is_absent(self):
        zone = [r for r in configured() if r["type"] != "MX"]
        code, out = self.run_ses(None, zone=zone)
        self.assertEqual(code, 1, out)
        self.assertEqual([self.row(out, k) for k in ("DKIM", "MX", "SPF", "DMARC")],
                         ["CONFIRM", "CREATE", "CONFIRM", "CONFIRM"])
        self.assertEqual(self.commands(out), [("MX", f"hermes.{D}", MX, "--prio", "10")])

    def test_a_different_dmarc_is_a_prompt_whose_update_deletes_then_creates(self):
        zone = [r for r in configured() if r["name"] != "_dmarc"] + [
            {"name": "_dmarc", "type": "TXT", "content": "v=DMARC1; p=none"}]
        code, out = self.run_ses(None, zone=zone)
        self.assertEqual(code, 1, out)
        self.assertEqual(self.row(out, "DMARC"), "PROMPT")
        self.assertIn("[zone holds v=DMARC1; p=none]", out)
        self.assertEqual(self.commands(out, "delete"), [("TXT", f"_dmarc.{D}", "v=DMARC1; p=none")])
        self.assertEqual(self.commands(out), [("TXT", f"_dmarc.{D}", DMARC)])

    def test_an_mx_with_another_priority_is_a_prompt(self):
        zone = [r for r in configured() if r["type"] != "MX"] + [
            {"name": "hermes", "type": "MX", "content": MX, "prio": 20}]
        _, out = self.run_ses(None, zone=zone)
        self.assertEqual(self.row(out, "MX"), "PROMPT")
        self.assertIn(f"[zone holds {MX} prio=20]", out)

    def test_a_second_mx_beside_the_right_one_is_a_prompt(self):
        zone = configured() + [{"name": "hermes", "type": "MX", "content": "mx.other.net", "prio": 10}]
        _, out = self.run_ses(None, zone=zone)
        self.assertEqual(self.row(out, "MX"), "PROMPT")

    def test_a_different_spf_is_a_prompt_not_a_merge(self):
        zone = [r for r in configured() if r["content"] != SPF] + [
            {"name": "hermes", "type": "TXT", "content": "v=spf1 include:mailgun.org -all"}]
        _, out = self.run_ses(None, zone=zone)
        self.assertEqual(self.row(out, "SPF"), "PROMPT")
        self.assertEqual(self.commands(out, "delete"), [("TXT", f"hermes.{D}", "v=spf1 include:mailgun.org -all")])

    def test_two_dkim_records_without_tokens_is_an_error(self):
        zone = configured()[1:]
        code, out = self.run_ses(None, zone=zone)
        self.assertEqual(code, 2)
        self.assertIn("the zone holds 2 SES DKIM records, not three in AWS form", out)

    def test_three_dkim_records_not_in_aws_form_without_tokens_is_an_error(self):
        zone = [r for r in configured() if r["type"] != "CNAME"] + dkim(["aaaa1111", "bbbb2222", "cccc3333"])
        code, out = self.run_ses(None, zone=zone)
        self.assertEqual(code, 2)
        self.assertIn("not three in AWS form", out)


class AtSes(Run):
    def test_a_profile_ses_refuses_is_skipped(self):
        fake = FakeSes(refuse="AccessDeniedException")
        code, out = self.run_ses(fake, "--profile", "send-only", "--dkim", *TOKENS, zone=configured())
        self.assertEqual(code, 0, out)
        self.assertIn("SES: skipped (profile send-only: An error occurred (AccessDeniedException)", out)
        self.assertIn(f"CONFIRMED: {D} (DNS only; SES not read)", out)
        self.assertEqual(fake.writes(), [])

    def test_a_missing_identity_is_planned_and_nothing_changes_in_a_dry_run(self):
        fake = FakeSes()
        code, out = self.run_ses(fake, "--profile", "admin")
        self.assertEqual(code, 2)
        self.assertIn("CREATE   identity", out)
        self.assertIn(f"CREATE   MAIL FROM  hermes.{D}", out)
        self.assertIn("SES creates the identity on --apply; run again after it", out)
        self.assertEqual(fake.writes(), [])

    def test_apply_creates_the_identity_with_2048_bit_dkim_and_mail_from(self):
        fake = FakeSes()
        code, out = self.run_ses(fake, "--profile", "admin", "--apply")
        self.assertEqual(code, 1, out)
        self.assertEqual(fake.writes(), ["create-email-identity", "put-email-identity-mail-from-attributes"])
        self.assertEqual(fake.identity["DkimAttributes"]["NextSigningKeyLength"], "RSA_2048_BIT")
        self.assertEqual(len(self.commands(out)), 6)

    def test_a_1024_bit_identity_without_mail_from_is_edited(self):
        fake = FakeSes(identity(length="RSA_1024_BIT", mail_from=None))
        code, out = self.run_ses(fake, "--profile", "admin", zone=configured())
        self.assertEqual(code, 1)
        self.assertIn("[was RSA_1024_BIT]", out)
        self.assertIn("[was none]", out)
        self.assertNotIn("CONFIRMED", out)
        code, out = self.run_ses(fake, "--profile", "admin", "--apply", zone=configured())
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.writes(), ["put-email-identity-dkim-signing-attributes", "put-email-identity-mail-from-attributes"])
        self.assertIn("After apply: 0 rows not ok", out)

    def test_a_conforming_identity_and_zone_is_confirmed(self):
        fake = FakeSes(identity())
        code, out = self.run_ses(fake, "--profile", "admin", zone=configured())
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.writes(), [])
        self.assertIn(f"CONFIRMED: {D}\n", out)

    def test_ses_tokens_against_other_tokens_in_the_zone_prompt(self):
        code, out = self.run_ses(FakeSes(identity()), "--profile", "admin", zone=configured(OTHER))
        self.assertEqual(code, 1)
        self.assertEqual(self.row(out, "DKIM"), "PROMPT")

    def test_dkim_that_is_not_easy_dkim_is_held(self):
        fake = FakeSes(identity(origin="EXTERNAL"))
        code, out = self.run_ses(fake, "--profile", "admin", "--apply", zone=configured())
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT DKIM", out)
        self.assertEqual(fake.writes(), [])

    def test_dkim_tokens_that_differ_from_ses_are_an_error(self):
        code, out = self.run_ses(FakeSes(identity()), "--profile", "admin", "--dkim", *OTHER)
        self.assertEqual(code, 2)
        self.assertEqual(self.row(out, "DKIM"), "ERROR")
        self.assertIn("--dkim differs from SES's tokens", out)
        self.assertNotIn("CNAME", " ".join(c[0] for c in self.commands(out)))


class Senders(Run):
    TEXT = ('# Sending domains\n[defaults]\nregion = "us-east-1"\n\n'
            f'[domains."{D}"]\nprofile = "ses-x"  # account 000\nregion = "us-east-2"\nmail_from = "mail.{D}"\n')

    def test_region_and_mail_from_come_from_senders_toml(self):
        self.senders.write_text(self.TEXT)
        _, out = self.run_ses(None, "--dkim", *TOKENS)
        self.assertEqual(self.commands(out)[3], ("MX", f"mail.{D}", "feedback-smtp.us-east-2.amazonses.com", "--prio", "10"))

    def test_a_listed_domain_is_flagged_and_not_edited(self):
        self.senders.write_text(self.TEXT)
        _, out = self.run_ses(None, "--dkim", *TOKENS, "--apply")
        self.assertIn(f"Sender config: {self.senders} already lists {D}; not changed", out)
        self.assertEqual(self.senders.read_text(), self.TEXT)

    def test_add_sender_comments_out_the_prevailing_profile_and_keeps_the_comments(self):
        text = ('# Sending domains\n[defaults]\nregion = "us-east-1"\n\n'
                '[domains."a.org"]\nprofile = "ses-b"\n\n[domains."b.org"]\nprofile = "ses-c"\n\n'
                '[domains."c.org"]\nprofile = "ses-c"\n')
        self.senders.write_text(text)
        code, out = self.run_ses(None, zone=configured())
        self.assertEqual(code, 1)
        self.assertIn(f"Sender config: ADD to {self.senders}", out)
        self.assertNotIn("CONFIRMED", out)
        self.assertEqual(self.senders.read_text(), text)
        code, out = self.run_ses(None, "--apply", zone=configured())
        self.assertEqual(code, 0, out)
        self.assertIn(f"CONFIRMED: {D}", out)
        self.assertEqual(self.senders.read_text(), text + (
            f'\n[domains."{D}"]\n# profile = "ses-c"\nmail_from = "hermes.{D}"\n# sends_for = []\n'))

    def test_a_tie_takes_the_profile_listed_first(self):
        text = '[domains."a.org"]\nprofile = "ses-b"\n\n[domains."b.org"]\nprofile = "ses-c"\n'
        self.senders.write_text(text)
        self.run_ses(None, "--apply", zone=configured())
        self.assertIn('# profile = "ses-b"\n', self.senders.read_text())

    def test_no_profile_in_the_file_leaves_the_line_empty(self):
        self.senders.write_text('[defaults]\nregion = "us-east-1"\n')
        self.run_ses(None, "--apply", zone=configured())
        self.assertIn(f'[domains."{D}"]\n# profile = \nmail_from', self.senders.read_text())

    def test_a_region_off_the_default_is_written(self):
        text = '# Sending domains\n[defaults]\nregion = "us-east-1"\n'
        self.senders.write_text(text)
        self.run_ses(None, "--region", "us-east-2", "--apply", zone=configured())
        self.assertEqual(self.senders.read_text(), text + (
            f'\n[domains."{D}"]\n# profile = \nmail_from = "hermes.{D}"\nregion    = "us-east-2"\n# sends_for = []\n'))

    def test_the_cut_flags_are_refused(self):
        for flag in ("--mx", "--spf", "--sender-profile"):
            code, _ = self.run_ses(None, flag, "x", zone=configured())
            self.assertEqual(code, 2, flag)

    def test_the_entry_written_reads_back_as_toml(self):
        self.senders.write_text('[domains."a.org"]\nprofile = "ses-b"\n')
        self.run_ses(None, "--apply", zone=configured())
        entry = ses.senders(self.senders)["domains"][D]
        self.assertEqual(entry, {"mail_from": f"hermes.{D}"})

    def test_an_unset_sender_config_adds_nothing(self):
        with mock.patch.dict(os.environ, {"ENINE_MAIL_SENDERS": ""}):
            code, out = self.run_ses(None, "--apply", zone=configured())
        self.assertEqual(code, 0, out)
        self.assertIn("Sender config: not set (--senders FILE or ENINE_MAIL_SENDERS); nothing added", out)

    def test_the_flag_wins_over_the_variable(self):
        flagged = self.dir / "other.toml"
        flagged.write_text(self.TEXT)
        _, out = self.run_ses(None, "--senders", str(flagged), "--dkim", *TOKENS)
        self.assertEqual(self.commands(out)[3][1], f"mail.{D}")

    def test_a_set_path_with_no_file_adds_nothing_and_creates_no_file(self):
        code, out = self.run_ses(None, "--apply", zone=configured())
        self.assertEqual(code, 0, out)
        self.assertIn(f"Sender config: no {self.senders}; nothing added (the file is never created)", out)
        self.assertFalse(self.senders.exists())


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
