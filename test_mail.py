"""Tests for dot-claude/skills/enine-mail: the store, ingest, resolve, pin and the command line.
Run: python3 -m unittest test_mail test_mail_search test_mail_send

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude. The scripts hold no path: every path they read or write (the mail store,
the owner-address file, the sender config, the drafts folder, the sent log) comes from a flag
or an ENINE_MAIL_* variable, and MailCase sets only temp-dir values after removing any the
environment had. Every address, name and message-id here is made up.
"""

import contextlib
import email
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from email.policy import default as default_policy
from pathlib import Path
from unittest import mock

from test_configure_domain_email import load

mail = load("enine-mail", "mail.py")
store = sys.modules["mail_store"]
search_module = sys.modules["mail_search"]
send_module = sys.modules["mail_send"]
paths_module = sys.modules["mail_paths"]
MODULES = (mail, store, search_module, send_module, sys.modules["mail_eml"], paths_module)

OWNER = "owner@example.com"
MSGID = "test-0001@mail.example.com"
STEM = "b42413bb525fddda"  # sha1(MSGID)[:16]: wicket's archive name for it


class MailCase(unittest.TestCase):
    """A temp dir holding every path the scripts touch, and the owner's identity in it.

    The five paths are given the way a consumer gives them, by ENINE_MAIL_* variables, and
    any such variable already in the environment is removed first, so no test reads a real path.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove)
        self.root = self.tmp / "mail"
        self.identity = self.tmp / "identity.json"
        self.senders = self.tmp / "senders.toml"
        self.drafts = self.tmp / "out" / "drafts"
        self.sent = self.tmp / "out" / "sent.jsonl"
        environ = {key: value for key, value in os.environ.items() if not key.startswith("ENINE_MAIL_")}
        environ.update(ENINE_MAIL_STORE=str(self.root), ENINE_MAIL_IDENTITY=str(self.identity),
                       ENINE_MAIL_SENDERS=str(self.senders), ENINE_MAIL_DRAFTS=str(self.drafts),
                       ENINE_MAIL_SENT=str(self.sent))
        patcher = mock.patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.identity.write_text(json.dumps({"owner_addresses": [OWNER]}))

    def owners(self):
        return store.owner_addresses(self.identity)

    def ingest(self, src, **kwargs):
        return store.ingest(src, store=self.root, identity=self.identity, **kwargs)

    def resolve(self, message_id):
        return store.resolve(message_id, self.root)

    def resolve_many(self, message_ids):
        return store.resolve_many(message_ids, self.root)

    def search(self, **kwargs):
        return search_module.search(store=self.root, **kwargs)

    def _remove(self):
        """pin sets the user-immutable flag; clear it so the temp dir can be removed."""
        if sys.platform == "darwin":
            for path in self.tmp.rglob("*"):
                with contextlib.suppress(OSError):
                    os.chflags(path, 0)  # pylint: disable=no-member
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        """(exit code, stdout, stderr) of mail.py with these arguments."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mail.main(list(argv))
        return code, out.getvalue(), err.getvalue()


def eml(from_addr, to_addr, *, msgid=MSGID, date="Wed, 08 Jul 2026 09:56:11 -0400"):
    return (f"From: {from_addr}\r\nTo: {to_addr}\r\nDate: {date}\r\n"
            f"Message-ID: <{msgid}>\r\nSubject: hi\r\n\r\nbody\r\n").encode()


def parsed(raw):
    return email.message_from_bytes(raw, policy=default_policy)


class Isolation(MailCase):
    def test_no_module_holds_a_path_of_its_own(self):
        for module in MODULES:
            for name, value in vars(module).items():
                self.assertNotIsInstance(value, Path, f"{module.__name__}.{name} = {value}")
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("Path.home", source, module.__name__)
            self.assertNotIn("expanduser(\"~", source, module.__name__)


VARIABLES = {"store": "ENINE_MAIL_STORE", "identity": "ENINE_MAIL_IDENTITY", "senders": "ENINE_MAIL_SENDERS",
             "drafts": "ENINE_MAIL_DRAFTS", "sent": "ENINE_MAIL_SENT"}
FLAGS = {"store": "--store", "identity": "--identity", "senders": "--senders", "drafts": "--drafts", "sent": "--sent"}
SENDER_CONFIG = '[defaults]\nregion = "us-east-1"\n[domains."example.org"]\nprofile = "p"\n'


class PathsFromTheConsumer(MailCase):
    """No path has a default: each comes from its flag or its variable, and a verb needs only its own."""

    def setUp(self):
        super().setUp()
        self.root.mkdir()
        self.senders.write_text(SENDER_CONFIG)
        self.body = self.tmp / "body.txt"
        self.body.write_text("hello\n")
        self.message = ["--from", "desk@example.org", "--to", "bob@example.net", "--subject", "S",
                        "--body", str(self.body)]

    def only(self, *names):
        """The environment with only these paths' variables set."""
        environ = {k: v for k, v in os.environ.items() if not k.startswith("ENINE_MAIL_")}
        environ.update({VARIABLES[n]: os.environ[VARIABLES[n]] for n in names})
        return mock.patch.dict(os.environ, environ, clear=True)

    def test_a_missing_path_refuses_and_names_its_flag_and_variable(self):
        with self.only():
            code, out, err = self.run_cli("search", "--subject", "x")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("search needs the mail store: pass --store DIR or set ENINE_MAIL_STORE", err)
        with self.only():
            code, _, err = self.run_cli("ingest", str(self.body))
        self.assertIn("--store DIR or set ENINE_MAIL_STORE", err)
        self.assertIn("--identity FILE or set ENINE_MAIL_IDENTITY", err)

    def test_each_path_from_its_flag_before_or_after_the_verb(self):
        with self.only():
            self.assertEqual(self.run_cli("search", "--subject", "x", "--store", str(self.root))[0], 1)
            self.assertEqual(self.run_cli("--store", str(self.root), "search", "--subject", "x")[0], 1)
            argv = ["draft", *self.message, "--senders", str(self.senders), "--drafts", str(self.drafts)]
            self.assertEqual(self.run_cli(*argv)[0], 0)
            self.assertEqual(self.run_cli("--drafts", str(self.drafts), "--sent", str(self.sent), "draft")[0], 0)
            src = self.tmp / "m.eml"
            src.write_bytes(eml(OWNER, "bob@example.org"))
            code, out, _ = self.run_cli("ingest", str(src), "--store", str(self.root), "--identity", str(self.identity))
            self.assertEqual(code, 1)
            self.assertIn(f"{OWNER}/archive/example.org/", out)  # the identity file made it outbound

    def test_a_flag_beats_its_variable(self):
        wrong = self.tmp / "wrong-store"
        with mock.patch.dict(os.environ, {"ENINE_MAIL_STORE": str(wrong)}):
            self.assertEqual(self.run_cli("search", "--subject", "x")[0], 2)  # no store at the variable's path
            self.assertEqual(self.run_cli("search", "--subject", "x", "--store", str(self.root))[0], 1)
        self.assertEqual(mail.mail_paths.resolve(mail.parser().parse_args(["--drafts", "/a", "draft"]),
                                                 {"ENINE_MAIL_DRAFTS": "/b", "ENINE_MAIL_SENT": ""}).drafts, Path("/a"))
        self.assertIsNone(mail.mail_paths.resolve(mail.parser().parse_args(["draft"]), {"ENINE_MAIL_SENT": ""}).sent)

    def test_a_verb_takes_only_the_path_flags_it_uses(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mail.parser().parse_args(["search", "--subject", "x", "--senders", "f"])

    def test_each_verb_needs_only_its_own_paths(self):
        self.run_cli("draft", *self.message)  # draft 1, with every path set
        src = self.tmp / "m.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        cases = [
            (("search", "--subject", "x"), ("store",)),
            (("resolve", MSGID), ("store",)),
            (("ingest", str(src)), ("store", "identity")),
            (("draft",), ("drafts", "sent")),
            (("draft", "1"), ("drafts",)),
            (("send", "1"), ("drafts",)),
            (("draft", *self.message), ("senders", "drafts")),
            (("send", *self.message), ("senders", "drafts")),
            (("draft", *self.message, "--in-reply-to", "p@example.net"), ("senders", "drafts", "sent", "store")),
        ]
        if hasattr(os, "chflags"):
            cases.append((("pin", MSGID, "--check"), ("store",)))
        for argv, needed in cases:
            with self.only(*needed):
                code, _, err = self.run_cli(*argv)
            self.assertNotEqual(code, 2, (argv, err))
            for missing in needed:
                with self.only(*(n for n in needed if n != missing)):
                    code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 2, (argv, missing))
                self.assertIn(f"{FLAGS[missing]} ", err)
                self.assertIn(VARIABLES[missing], err)

    def test_send_apply_needs_the_drafts_and_the_sent_log(self):
        self.run_cli("draft", *self.message)
        with self.only("drafts"):
            code, _, err = self.run_cli("send", "1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("--sent FILE or set ENINE_MAIL_SENT", err)


class Store(MailCase):
    def test_normalize_message_id_strips_and_lowercases(self):
        self.assertEqual(store.normalize_message_id("<ABC@Host.Example>"), "abc@host.example")
        self.assertIsNone(store.normalize_message_id("  "))
        self.assertIsNone(store.normalize_message_id(None))

    def test_local_msgid_is_wicket_parity_sha1_16(self):
        key = store.message_key(message_id=f"<{MSGID}>")
        self.assertEqual(key, MSGID)
        self.assertEqual(store.local_msgid(key), hashlib.sha1(MSGID.encode()).hexdigest()[:16])
        self.assertEqual(store.local_msgid(key), STEM)

    def test_message_key_falls_back_to_provider_native(self):
        self.assertEqual(store.message_key(message_id=None, provider="delphi", native_id="deadbeef"),
                         "delphi:deadbeef")

    def test_counterparty_outbound_uses_single_external_recipient(self):
        self.assertEqual(store.counterparty_domain(parsed(eml(OWNER, "bob@example.org")), self.owners()), "example.org")

    def test_counterparty_inbound_uses_sender_domain(self):
        self.assertEqual(store.counterparty_domain(parsed(eml("bob@example.org", OWNER)), self.owners()), "example.org")

    def test_counterparty_unfiled_when_ambiguous(self):
        msg = parsed(eml(OWNER, "alice@example.net, bob@example.org"))
        self.assertEqual(store.counterparty_domain(msg, self.owners()), store.UNFILED)

    def test_account_outbound_is_owner_from_not_counterparty(self):
        self.assertEqual(store.account_of(parsed(eml(OWNER, "bob@example.org")), self.owners()), OWNER)

    def test_account_inbound_prefers_delivery_header_alias(self):
        raw = (b"From: noreply@bookings.example.net\r\nTo: svc@example.org\r\n"
               b"Delivered-To: svc@example.org\r\nDate: Thu, 30 Apr 2026 11:59:47 +0000\r\n"
               b"Message-ID: <x@y.example>\r\nSubject: hi\r\n\r\nbody\r\n")
        self.assertEqual(store.account_of(parsed(raw), self.owners()), "svc@example.org")

    def test_account_canonicalizes_plus_tag(self):
        raw = (b"From: shop@store.example\r\nDelivered-To: owner+deals@example.com\r\n"
               b"Date: Thu, 30 Apr 2026 11:59:47 +0000\r\nMessage-ID: <z@y.example>\r\nSubject: hi\r\n\r\nbody\r\n")
        self.assertEqual(store.account_of(parsed(raw), self.owners()), OWNER)

    def test_no_identity_file_reads_as_no_owner(self):
        absent = self.tmp / "absent.json"
        self.assertEqual(store.owner_addresses(absent), frozenset())
        # With no owner known, outbound mail files under the sender's own domain.
        owners = store.owner_addresses(absent)
        self.assertEqual(store.counterparty_domain(parsed(eml(OWNER, "bob@example.org")), owners), "example.com")


class Ingest(MailCase):
    RELPATH = f"{OWNER}/archive/example.org/2026-07/{STEM}.eml"

    def source(self, name="msg.eml"):
        src = self.tmp / name
        src.write_bytes(eml(OWNER, "bob@example.org"))
        return src

    def test_a_dry_run_prints_the_target_and_writes_nothing(self):
        src = self.source()
        code, out, _ = self.run_cli("ingest", str(src))
        self.assertEqual(code, 1)
        self.assertIn(f"plan    {self.RELPATH}  <-  msg.eml", out)
        self.assertIn("Dry run: nothing written", out)
        self.assertTrue(src.exists())
        self.assertFalse((self.tmp / "mail").exists())

    def test_apply_moves_it_and_is_idempotent(self):
        src = self.source()
        code, out, _ = self.run_cli("ingest", str(src), "--apply")
        self.assertEqual(code, 0)
        self.assertIn(f"filed   {self.RELPATH}", out)
        self.assertTrue((self.tmp / "mail" / self.RELPATH).exists())
        self.assertFalse(src.exists())
        # Filing an identical copy again changes nothing and consumes the duplicate.
        again = self.source("again.eml")
        code, out, _ = self.run_cli("ingest", str(again), "--apply")
        self.assertEqual(code, 0)
        self.assertIn("exists", out)
        self.assertFalse(again.exists())

    def test_copy_keeps_the_source(self):
        src = self.source()
        self.assertEqual(self.run_cli("ingest", str(src), "--apply", "--copy")[0], 0)
        self.assertTrue(src.exists())
        self.assertEqual((self.tmp / "mail" / self.RELPATH).read_bytes(), src.read_bytes())

    def test_a_message_wicket_already_stored_is_exists_and_nothing_is_written(self):
        folder = self.tmp / "mail" / OWNER / "archive" / "example.org" / "2026-07"
        folder.mkdir(parents=True)
        harvested = folder / "18f2aa00bb11cc22.eml"
        harvested.write_bytes(eml(OWNER, "bob@example.org").replace(b"\r\nSubject", b"\r\nX-Harvested: 1\r\nSubject"))
        write_manifest(self.tmp / "mail" / OWNER, [{"id": MSGID, "downloaded": True,
                                                     "path": f"example.org/2026-07/{harvested.name}"}])
        src = self.source()
        for flags in ((), ("--apply",)):
            code, out, _ = self.run_cli("ingest", str(src), *flags)
            self.assertEqual(code, 0, out)
            self.assertIn(f"exists  {OWNER}/archive/example.org/2026-07/{harvested.name}", out)
        self.assertFalse((self.tmp / "mail" / self.RELPATH).exists())
        self.assertTrue(src.exists())  # not byte-identical, so the source is kept

    def test_a_stored_copy_with_other_bytes_is_never_overwritten(self):
        target = self.tmp / "mail" / self.RELPATH
        target.parent.mkdir(parents=True)
        target.write_bytes(b"the stored copy")
        src = self.source()
        code, out, _ = self.run_cli("ingest", str(src), "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("exists", out)
        self.assertEqual(target.read_bytes(), b"the stored copy")
        self.assertTrue(src.exists())

    def test_a_missing_file_is_skipped_and_exits_1(self):
        code, _, err = self.run_cli("ingest", str(self.tmp / "nope.eml"), "--apply")
        self.assertEqual(code, 1)
        self.assertIn("skip (not a file)", err)

    def test_json(self):
        code, out, _ = self.run_cli("ingest", str(self.source()), "--json")
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["files"][0]["relpath"], self.RELPATH)


class Resolve(MailCase):
    def test_resolve_finds_by_message_id(self):
        src = self.tmp / "msg.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        self.ingest(src, move=True)
        hit = self.resolve(MSGID)
        self.assertEqual(hit.name, f"{STEM}.eml")
        self.assertIsNone(self.resolve("no-such-id@nowhere.example"))
        code, out, _ = self.run_cli("resolve", f"<{MSGID}>")
        self.assertEqual((code, out.strip()), (0, str(hit)))
        code, out, err = self.run_cli("resolve", "no-such-id@nowhere.example")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("not found", err)


class ResolveByHeader(MailCase):
    """The harvest names files another way: resolve finds a message by the Message-ID inside it."""

    def harvested(self, stem, msgid_header, *, account=OWNER, extra=b"", body=b"body"):
        folder = self.tmp / "mail" / account / "archive" / "example.org" / "2026-08"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stem}.eml"
        path.write_bytes(b"From: alice@example.org\r\nTo: owner@example.com\r\n" + extra
                         + b"Message-ID:" + msgid_header + b"\r\nSubject: hi\r\n\r\n" + body + b"\r\n")
        return path

    def test_a_harvested_file_with_an_unrelated_name_resolves(self):
        path = self.harvested("18c2f0a1b2c3d4e5", b" <harvest-0001@mail.example.org>")
        self.assertEqual(self.resolve("<harvest-0001@mail.example.org>"), path)
        code, out, _ = self.run_cli("resolve", "harvest-0001@mail.example.org")
        self.assertEqual((code, out.strip()), (0, str(path)))

    def test_case_folding_and_a_comment_still_match(self):
        path = self.harvested("aaaa000000000001", b"\r\n <Harvest-0002@Mail.Example.ORG> (sent by the list)")
        self.assertEqual(self.resolve("harvest-0002@mail.example.org"), path)
        commented = self.harvested("aaaa000000000002", b" <part(x)one-0003@mail.example.org>")
        self.assertEqual(self.resolve("<partone-0003@mail.example.org>"), commented)

    def test_an_id_only_in_a_body_or_references_does_not_resolve(self):
        self.harvested("bbbb000000000001", b" <other-0001@mail.example.org>",
                       extra=b"References: <wanted-0009@mail.example.org>\r\n",
                       body=b"see <wanted-0009@mail.example.org>")
        self.assertIsNone(self.resolve("wanted-0009@mail.example.org"))
        self.assertEqual(self.run_cli("resolve", "wanted-0009@mail.example.org")[0], 1)

    def test_several_ids_are_answered_in_one_walk(self):
        first = self.harvested("cccc000000000001", b" <many-0001@mail.example.org>")
        second = self.harvested("cccc000000000002", b" <many-0002@mail.example.org>")
        self.harvested("cccc000000000003", b" <unrelated-0003@mail.example.org>")
        reads = []
        real_read = store.read_head

        def read(path):
            reads.append(path.name)
            return real_read(path)

        with mock.patch.object(store, "read_head", read), \
                mock.patch.object(Path, "rglob", autospec=True, side_effect=Path.rglob) as rglob:
            found = self.resolve_many(["many-0001@mail.example.org", "<many-0002@mail.example.org>",
                                        "missing-0004@mail.example.org"])
        self.assertEqual(found, {"many-0001@mail.example.org": first, "<many-0002@mail.example.org>": second,
                                 "missing-0004@mail.example.org": None})
        self.assertEqual(rglob.call_count, 1)
        self.assertEqual(sorted(reads), sorted(set(reads)))  # each file's header read at most once

    def test_the_computed_name_wins_then_the_first_in_path_order(self):
        msgid = b" <" + MSGID.encode() + b">"
        later = self.harvested("ffff000000000001", msgid, account="z@example.com")
        earlier = self.harvested("ffff000000000002", msgid, account="a@example.com")
        self.assertEqual(self.resolve(MSGID), earlier)
        src = self.tmp / "msg.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        filed = Path(self.ingest(src, move=True)["path"])
        self.assertEqual(filed.stem, STEM)
        self.assertEqual(self.resolve(MSGID), filed)
        self.assertTrue(later.exists())


def write_manifest(store_dir, rows, *, year=2026, tail=""):
    """A wicket manifest shard as wicket writes it: one sorted-key JSON row per line."""
    shard = store_dir / "manifest" / f"{year}.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows) + tail)
    return shard


class ResolveFromManifest(MailCase):
    """wicket's manifest answers resolve before any walk; the file still has the last word."""

    harvested = ResolveByHeader.harvested

    def setUp(self):
        super().setUp()
        self.reads = []
        real_read = store.read_head

        def read(path):
            self.reads.append(path.name)
            return real_read(path)

        for patcher in (mock.patch.object(store, "read_head", read),
                        mock.patch.object(Path, "rglob", autospec=True, side_effect=Path.rglob)):
            self.rglob = patcher.start()
            self.addCleanup(patcher.stop)

    def row(self, key, path, **extra):
        return {"id": key, "msgid": "18f2", "thread_id": "1", "downloaded": True,
                "path": f"example.org/2026-08/{path}", **extra}

    def test_a_manifest_row_answers_without_a_walk(self):
        target = self.harvested("1111000000000001", b" <man-0001@mail.example.org>")
        for i in range(2, 5):
            self.harvested(f"111100000000000{i}", f" <other-000{i}@mail.example.org>".encode())
        write_manifest(self.tmp / "mail" / OWNER, [self.row("man-0001@mail.example.org", target.name)],
                       tail='{"id": "torn-0001@mail.exa')  # a torn last line is skipped
        self.assertEqual(self.resolve("<MAN-0001@mail.example.org>"), target)
        self.assertEqual(self.reads, [target.name])  # the one file, to confirm the row
        self.assertEqual(self.rglob.call_count, 0)

    def test_a_second_copys_key_and_two_stores_take_the_first_path(self):
        first = self.harvested("2222000000000001", b" <dup-0001@mail.example.org>", account="a@example.com")
        later = self.harvested("2222000000000002", b" <dup-0001@mail.example.org>", account="z@example.com")
        write_manifest(self.tmp / "mail" / "z@example.com", [self.row("dup-0001@mail.example.org", later.name)])
        write_manifest(self.tmp / "mail" / "a@example.com",
                       [self.row("dup-0001@mail.example.org|gmail:19a0", first.name)])
        self.assertEqual(self.resolve("dup-0001@mail.example.org"), first)
        self.assertEqual(self.rglob.call_count, 0)

    def test_a_row_that_cannot_be_trusted_falls_back_to_the_walk(self):
        real = self.harvested("3333000000000001", b" <fall-0001@mail.example.org>")
        wrong = self.harvested("3333000000000002", b" <someone-else@mail.example.org>")
        write_manifest(self.tmp / "mail" / OWNER, [
            self.row("fall-0001@mail.example.org", "gone.eml"),  # stale: no such file
            self.row("fall-0001@mail.example.org", wrong.name),  # the file holds another id
            self.row("fall-0001@mail.example.org", real.name, excluded="duplicate of x"),
        ])
        self.assertEqual(self.resolve("fall-0001@mail.example.org"), real)
        self.assertEqual(self.rglob.call_count, 1)

    def test_several_ids_mix_manifest_computed_name_and_walk(self):
        from_row = self.harvested("4444000000000001", b" <mix-0001@mail.example.org>")
        walked = self.harvested("4444000000000002", b" <mix-0002@mail.example.org>")
        src = self.tmp / "msg.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        filed = Path(self.ingest(src, move=True)["path"])
        write_manifest(self.tmp / "mail" / OWNER, [self.row("mix-0001@mail.example.org", from_row.name)])
        found = self.resolve_many(["mix-0001@mail.example.org", MSGID, "mix-0002@mail.example.org"])
        self.assertEqual(found, {"mix-0001@mail.example.org": from_row, MSGID: filed,
                                 "mix-0002@mail.example.org": walked})
        self.assertEqual(self.rglob.call_count, 1)


@unittest.skipUnless(hasattr(os, "chflags"), "mail pin needs macOS chflags")
class PinHarvested(MailCase):
    harvested = ResolveByHeader.harvested

    def test_pin_locks_harvested_files_from_one_walk(self):
        one = self.harvested("dddd000000000001", b" <pin-0001@mail.example.org>")
        two = self.harvested("dddd000000000002", b" <pin-0002@mail.example.org>")
        with mock.patch.object(Path, "rglob", autospec=True, side_effect=Path.rglob) as rglob:
            code, out, _ = self.run_cli("pin", "pin-0001@mail.example.org", "pin-0002@mail.example.org", "--apply")
        self.assertEqual((code, rglob.call_count), (0, 1))
        self.assertIn("2 locked", out)
        self.assertTrue(store.is_locked(one) and store.is_locked(two))
        self.assertEqual(self.run_cli("pin", "--unpin", "pin-0001@mail.example.org", "--apply")[0], 0)
        self.assertFalse(store.is_locked(one))


@unittest.skipUnless(hasattr(os, "chflags"), "mail pin needs macOS chflags")
class Pin(MailCase):
    def setUp(self):
        super().setUp()
        src = self.tmp / "msg.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        self.path = Path(self.ingest(src, move=True)["path"])

    def test_a_dry_run_lists_what_would_lock_and_locks_nothing(self):
        code, out, _ = self.run_cli("pin", MSGID)
        self.assertEqual(code, 1)
        self.assertIn("would lock", out)
        self.assertFalse(store.is_locked(self.path))

    def test_apply_locks_and_check_then_passes(self):
        self.assertEqual(self.run_cli("pin", MSGID, "--check")[0], 1)
        self.assertEqual(self.run_cli("pin", MSGID, "--apply")[0], 0)
        self.assertTrue(store.is_locked(self.path))
        with self.assertRaises(PermissionError):
            self.path.unlink()
        # Idempotent: nothing is left to do, so the dry run and the check exit 0.
        self.assertEqual(self.run_cli("pin", MSGID, "--apply")[0], 0)
        self.assertEqual(self.run_cli("pin", MSGID)[0], 0)
        code, out, _ = self.run_cli("pin", MSGID, "--check")
        self.assertEqual(code, 0)
        self.assertIn("OK: all 1 .eml locked", out)

    def test_a_dangling_id_fails_every_mode(self):
        for mode in ((), ("--check",), ("--apply",)):
            code, out, err = self.run_cli("pin", MSGID, "missing@nowhere.example", *mode)
            self.assertEqual(code, 1, mode)
            self.assertIn("DANGLING  missing@nowhere.example", out + err)

    def test_unpin_is_a_dry_run_until_apply(self):
        self.run_cli("pin", MSGID, "--apply")
        code, out, _ = self.run_cli("pin", "--unpin", MSGID)
        self.assertEqual(code, 1)
        self.assertIn("would unlock", out)
        self.assertTrue(store.is_locked(self.path))
        self.assertEqual(self.run_cli("pin", "--unpin", MSGID, "--apply")[0], 0)
        self.assertFalse(store.is_locked(self.path))
        self.assertEqual(self.run_cli("pin", "--unpin", MSGID)[0], 0)  # nothing left to unlock

    def test_bad_combinations_are_refused(self):
        for argv in (("pin",), ("pin", MSGID, "--unpin", MSGID), ("pin", MSGID, "--check", "--apply"),
                     ("pin", "--unpin", MSGID, "--check")):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("refused:", err)
        self.assertFalse(store.is_locked(self.path))


class PinOffMacos(MailCase):
    def test_pin_refuses_without_chflags(self):
        saved = getattr(os, "chflags", None)
        if saved is not None:
            del os.chflags
        try:
            code, _, err = self.run_cli("pin", MSGID)
        finally:
            if saved is not None:
                os.chflags = saved
        self.assertEqual(code, 2)
        self.assertIn("macOS user-immutable flag", err)


class CommandLine(MailCase):
    def test_no_arguments_prints_the_help_and_exits_2(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("Usage", out)
        for verb in mail.VERBS:
            self.assertIn(f"mail.py {verb}", out)

    def test_every_verb_takes_json(self):
        for verb in mail.VERBS:
            argv = [verb, "x"] if verb in ("resolve", "ingest") else [verb]
            self.assertTrue(mail.parser().parse_args([*argv, "--json"]).json, verb)


if __name__ == "__main__":
    unittest.main()
