"""Tests for mail.py draft and send: build a draft, show it, send it through a stubbed SES.
Run: python3 -m unittest test_mail_send

The drafts folder, the sent log, the sender config and the mail store sit in MailCase's
temp dir, and subprocess.run is replaced, so no test reads the operator's config or reaches AWS.
"""

import base64
import json
import stat
import subprocess
import unittest
from unittest import mock

from test_mail import MailCase, send_module

SENDERS = """
[defaults]
region   = "us-east-1"
bcc      = "owner@{domain}"
reply_to = "owner@{domain}"
name     = "Mail Desk"

[domains."example.org"]
profile = "ses-test"

[domains."east2.example"]
profile = "ses-test"
region  = "us-east-2"

[domains."old.example"]
profile    = "ses-test"
deprecated = true
"""

# A Message-ID long enough that the default email policy would fold or encode it.
LONG_ID = "<test" + "x" * 90 + "@mail.example.com>"


class SendCase(MailCase):
    def setUp(self):
        super().setUp()
        self.senders.write_text(SENDERS, encoding="utf-8")
        self.body = self.tmp / "body.txt"
        self.body.write_text("Hello.\n", encoding="utf-8")
        self.calls = []
        self.ses = {"returncode": 0, "stdout": '{"MessageId": "0100abc-000000"}'}
        patcher = mock.patch.object(send_module.subprocess, "run", self.fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_run(self, command, **_):
        """SES as `aws sesv2 send-email` answers; each call's command and input are recorded."""
        source = command[command.index("--cli-input-json") + 1]
        with open(source.removeprefix("file://"), encoding="utf-8") as fh:
            self.calls.append({"command": command, "request": json.load(fh)})
        return subprocess.CompletedProcess(command, self.ses["returncode"], self.ses["stdout"], "denied")

    def build(self, **overrides):
        args = {"sender": "Mail Desk <desk@example.org>", "to": ["Bob Baker <bob@example.net>"], "cc": [],
                "bcc": [], "reply_to": [], "subject": "A subject", "body": self.body, "attach": [],
                "in_reply_to": None}
        args.update(overrides)
        return send_module.build(senders=self.senders, drafts=self.drafts, sent=self.sent, store=self.root, **args)

    def message_argv(self, *extra):
        return ["--from", "desk@example.org", "--to", "bob@example.net", "--subject", "S",
                "--body", str(self.body), *extra]

    def store_eml(self, message_id, references):
        stem = send_module.mail_store.local_msgid(send_module.mail_store.message_key(message_id=message_id))
        folder = self.tmp / "mail" / "acct" / "archive" / "example.net" / "2026-10"
        folder.mkdir(parents=True)
        (folder / f"{stem}.eml").write_bytes(
            (f"From: bob@example.net\r\nTo: desk@example.org\r\nMessage-ID: {message_id}\r\n"
             f"References: {references}\r\nSubject: hi\r\n\r\nbody\r\n").encode())


def head_lines(d):
    return dict(line.split(": ", 1) for line in d["head"].splitlines())


class Build(SendCase):
    def test_build_writes_draft_1_private_with_bcc_only_in_destinations(self):
        d = self.build()
        head = head_lines(d)
        self.assertEqual((d["number"], d["state"]), (1, "draft"))
        self.assertEqual(d["bcc"], ["owner@example.org"])
        self.assertNotIn("Bcc", head)
        self.assertNotIn("Message-ID", head)
        self.assertEqual(head["Reply-To"], "owner@example.org")
        self.assertEqual((d["profile"], d["region"]), ("ses-test", "us-east-1"))
        self.assertEqual(d["text"], "Hello.\n")
        for path in (self.drafts / "1.eml", self.drafts / "1.json"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for folder in (self.drafts, self.drafts.parent):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertFalse((self.tmp / "mail").exists())

    def test_a_bare_from_address_gets_the_default_name(self):
        self.assertEqual(head_lines(self.build(sender="desk@example.org"))["From"], "Mail Desk <desk@example.org>")

    def test_a_from_with_its_own_name_keeps_it(self):
        self.assertEqual(head_lines(self.build(sender="Other <desk@example.org>"))["From"],
                         "Other <desk@example.org>")

    def test_reply_to_flag_replaces_the_default(self):
        d = self.build(reply_to=["alice@example.org", "carol@example.net"])
        self.assertEqual(head_lines(d)["Reply-To"], "alice@example.org, carol@example.net")

    def test_region_falls_back_to_defaults_unless_the_domain_sets_one(self):
        self.assertEqual(self.build()["region"], "us-east-1")
        self.assertEqual(self.build(sender="desk@east2.example")["region"], "us-east-2")

    def test_deprecated_and_unknown_domains_are_refused(self):
        for sender in ("desk@old.example", "desk@unknown.example"):
            with self.assertRaises(send_module.Refusal, msg=sender):
                self.build(sender=sender)
        self.assertFalse(self.drafts.exists())

    def test_a_missing_sender_config_is_refused(self):
        (self.tmp / "senders.toml").unlink()
        with self.assertRaises(send_module.Refusal) as caught:
            self.build()
        self.assertIn("no sender config", str(caught.exception))

    def test_default_bcc_is_skipped_when_already_a_recipient(self):
        d = self.build(cc=["owner@example.org"], bcc=["dan@example.net"])
        self.assertEqual((d["bcc"], d["cc"]), (["dan@example.net"], ["owner@example.org"]))

    def test_no_header_is_folded_or_encoded(self):
        self.store_eml(LONG_ID, "<a" + "y" * 80 + "@example.net>")
        subject = ("Re: " + "a long subject line that runs well past seventy-eight " * 2).strip()
        d = self.build(subject=subject, in_reply_to=LONG_ID)
        self.assertNotIn("=?", d["head"])
        self.assertFalse(any(line[:1].isspace() for line in d["head"].splitlines()))
        self.assertEqual(head_lines(d)["Subject"], subject)


class Threading(SendCase):
    def test_threading_from_the_store(self):
        self.store_eml(LONG_ID, "<root@example.net>")
        head = head_lines(self.build(in_reply_to=LONG_ID.strip("<>")))
        self.assertEqual(head["In-Reply-To"], LONG_ID)
        self.assertEqual(head["References"], f"<root@example.net> {LONG_ID}")

    def test_threading_from_the_manifest_before_any_harvest(self):
        self.sent.parent.mkdir(parents=True)
        line = {"message_id": "<abc@email.amazonses.com>", "references": "<root@example.org>"}
        self.sent.write_text(json.dumps(line) + "\n", encoding="utf-8")
        head = head_lines(self.build(in_reply_to="abc@email.amazonses.com"))
        self.assertEqual(head["In-Reply-To"], "<abc@email.amazonses.com>")
        self.assertEqual(head["References"], "<root@example.org> <abc@email.amazonses.com>")

    def test_threading_from_the_bare_id(self):
        head = head_lines(self.build(in_reply_to="nowhere@example.net"))
        self.assertEqual(head["In-Reply-To"], "<nowhere@example.net>")
        self.assertEqual(head["References"], "<nowhere@example.net>")


class Apply(SendCase):
    def test_apply_sends_the_draft_bytes_records_one_line_and_removes_it(self):
        attachment = self.tmp / "a.pdf"
        attachment.write_bytes(b"%PDF-1.4\n")
        self.build(cc=["carol@example.net"], attach=[attachment])
        raw = (self.drafts / "1.eml").read_bytes()
        code, out, err = self.run_cli("send", "1", "--apply")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("sent  <0100abc-000000@email.amazonses.com>  A subject", out)
        (call,) = self.calls
        request = call["request"]
        self.assertEqual(call["command"][:7],
                         ["aws", "sesv2", "send-email", "--profile", "ses-test", "--region", "us-east-1"])
        self.assertNotIn("FromEmailAddress", request)  # SES would drop From's display name
        self.assertEqual(request["Destination"], {"ToAddresses": ["bob@example.net"],
                                                  "CcAddresses": ["carol@example.net"],
                                                  "BccAddresses": ["owner@example.org"]})
        self.assertEqual(base64.b64decode(request["Content"]["Raw"]["Data"]), raw)
        lines = self.sent.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        line = json.loads(lines[0])
        self.assertEqual(line["message_id"], "<0100abc-000000@email.amazonses.com>")
        self.assertEqual(line["attachments"], ["a.pdf"])
        self.assertEqual(line["reply_to"], ["owner@example.org"])
        self.assertEqual(stat.S_IMODE(self.sent.stat().st_mode), 0o600)
        self.assertEqual(list(self.drafts.iterdir()), [])
        self.assertFalse((self.tmp / "mail").exists())

    def test_ses_failure_returns_the_draft_to_draft(self):
        self.build()
        self.ses["returncode"] = 254
        code, _, err = self.run_cli("send", "1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("SES refused draft 1 (aws exit 254): denied", err)
        self.assertEqual(json.loads((self.drafts / "1.json").read_text())["state"], "draft")
        self.assertFalse(self.sent.exists())
        self.assertEqual(sorted(p.name for p in self.drafts.iterdir()), ["1.eml", "1.json"])

    def test_no_message_id_leaves_the_draft_sending_so_it_cannot_go_twice(self):
        self.build()
        self.ses["stdout"] = "not json"
        self.assertEqual(self.run_cli("send", "1", "--apply")[0], 2)
        self.assertEqual(json.loads((self.drafts / "1.json").read_text())["state"], "sending")
        code, _, err = self.run_cli("send", "1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("marked 'sending'", err)
        self.assertEqual(len(self.calls), 1)

    def test_apply_refuses_a_missing_or_sending_draft(self):
        code, _, err = self.run_cli("send", "7", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("no draft 7", err)
        self.build()
        meta_path = self.drafts / "1.json"
        meta = json.loads(meta_path.read_text())
        meta["state"] = "sending"
        meta_path.write_text(json.dumps(meta))
        code, _, err = self.run_cli("send", "1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("sending", err)
        self.assertEqual(self.calls, [])


class DraftVerb(SendCase):
    def test_draft_builds_then_shows_the_destinations_and_never_sends(self):
        code, out, err = self.run_cli("draft", *self.message_argv())
        self.assertEqual((code, err), (0, ""))
        self.assertIn("draft 1 (draft)", out)
        self.assertIn("send it with: python3 ~/.claude/skills/enine-mail/mail.py send 1 --apply", out)
        code, out, _ = self.run_cli("draft", "1")
        self.assertEqual(code, 0)
        self.assertIn("bcc:  owner@example.org", out)
        self.assertIn("profile ses-test, region us-east-1", out)
        self.assertEqual(self.calls, [])

    def test_draft_lists_drafts_and_sent_lines_newest_first(self):
        self.assertIn("no drafts and nothing sent", self.run_cli("draft")[1])
        self.build(subject="first")
        self.build(subject="second")
        self.sent.write_text(json.dumps({"message_id": "<old@x.example>", "sent_at": "t1", "subject": "s1",
                                         "to": ["bob@example.net"]}) + "\n"
                             + json.dumps({"message_id": "<new@x.example>", "sent_at": "t2", "subject": "s2",
                                           "to": ["bob@example.net"]}) + "\n", encoding="utf-8")
        code, out, _ = self.run_cli("draft", "--json")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["mode"], "list")
        self.assertEqual([d["subject"] for d in result["drafts"]], ["second", "first"])
        self.assertEqual([s["message_id"] for s in result["sent"]], ["<new@x.example>", "<old@x.example>"])
        text = self.run_cli("draft")[1].splitlines()
        self.assertTrue(text[0].startswith("draft 2"))
        self.assertTrue(text[2].startswith("sent  t2  s2"))

    def test_a_number_and_message_args_do_not_mix(self):
        self.build()
        for verb in ("draft", "send"):
            code, _, err = self.run_cli(verb, "1", "--subject", "again")
            self.assertEqual(code, 2, verb)
            self.assertIn("already built", err)
        self.assertEqual(send_module.numbers(self.drafts), [1])


class SendVerb(SendCase):
    def test_send_n_is_a_dry_run(self):
        self.build()
        code, out, _ = self.run_cli("send", "1")
        self.assertEqual(code, 1)
        self.assertIn("draft 1 (draft)", out)
        self.assertIn("Dry run: nothing sent.", out)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.sent.exists())

    def test_send_with_message_args_drafts_first_and_sends_nothing(self):
        code, out, _ = self.run_cli("send", *self.message_argv())
        self.assertEqual(code, 1)
        self.assertIn("draft 1 (draft)", out)
        self.assertIn("send 1 --apply", out)
        self.assertEqual(send_module.numbers(self.drafts), [1])
        self.assertEqual(self.calls, [])

    def test_apply_takes_only_a_draft_number(self):
        code, _, err = self.run_cli("send", *self.message_argv("--apply"))
        self.assertEqual(code, 2)
        self.assertIn("--apply needs a draft number", err)
        self.assertFalse(self.drafts.exists())
        self.assertEqual(self.calls, [])

    def test_send_with_nothing_is_refused(self):
        code, _, err = self.run_cli("send")
        self.assertEqual(code, 2)
        self.assertIn("draft lists the drafts", err)


if __name__ == "__main__":
    unittest.main()
