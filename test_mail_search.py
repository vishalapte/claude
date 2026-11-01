"""Tests for mail.py search: the flag and Gmail parsers, the matcher, and the store walk.
Run: python3 -m unittest test_mail_search

A small store of made-up .eml under MailCase's temp dir; nothing outside it is read.
"""

import base64
import unittest
from datetime import date
from email.header import Header
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

from test_mail import OWNER, MailCase, search_module, write_manifest

And, Before, Contains, Not, OnOrAfter, Or = (search_module.And, search_module.Before, search_module.Contains,
                                             search_module.Not, search_module.OnOrAfter, search_module.Or)
Refusal = search_module.Refusal
build_term, search = search_module.build_term, search_module.search

DUES = "Annual dues invoice for ACME42"
FILING = "Tax filing for ACME42"


def write(root, name, *, sender, subject, when, to=f"Owner <{OWNER}>", cc=None, bcc=None, body="hello",
          attach=None, month="2026-08", domain="example.org"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg["Date"] = when
    msg.set_content(body)
    if attach:
        msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename=attach)
    path = root / OWNER / "archive" / domain / month / f"{name}.eml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msg.as_bytes())
    return path


class SearchCase(MailCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "mail"
        write(self.root, "alice", sender="Alice Archer <alice@alpha.example.com>", subject=DUES,
              when="Mon, 10 Aug 2026 10:00:00 +0530", domain="alpha.example.com", attach="invoice.pdf")
        write(self.root, "bob", sender="Bob Baker <bob@beta.example.net>", subject=FILING,
              when="Tue, 11 Aug 2026 10:00:00 +0530", cc="Carol Cole <carol@example.net>",
              domain="beta.example.net")
        write(self.root, "pnl", sender="Accounts <accounts@example.org>", subject="Profit and Loss statement",
              when="Wed, 12 Aug 2026 10:00:00 +0530", body="Total debited INR 5900.00 on your card")
        write(self.root, "split", sender="Accounts <accounts@example.org>", subject="Profit statement, Loss carried",
              when="Thu, 13 Aug 2026 10:00:00 +0530")
        write(self.root, "sent", sender=f"Owner <{OWNER}>", subject="Note to self",
              when="Fri, 14 Aug 2026 10:00:00 +0530", bcc="Dan Hidden <dan@example.org>")

    def subjects(self, **kwargs):
        return [row["subject"] for row in self.search(**kwargs)["rows"]]


class Flags(SearchCase):
    def test_flag_matches_display_name_and_address_case_insensitively(self):
        self.assertEqual(self.subjects(sender="ALPHA.EXAMPLE.COM"), [DUES])
        self.assertEqual(self.subjects(sender="alice archer"), [DUES])

    def test_lowercase_or_inside_a_flag_unions(self):
        self.assertEqual(self.subjects(sender="alice or bob"), [FILING, DUES])

    def test_and_binds_tighter_than_or(self):
        self.assertEqual(build_term({"subject": "a and b or c"}),
                         Or((And((Contains("subject", "a"), Contains("subject", "b"))), Contains("subject", "c"))))

    def test_or_and_and_are_operators_in_any_case_and_quotes_make_the_word(self):
        union = Or((Contains("from", "alice"), Contains("from", "bob")))
        for value in ("alice OR bob", "alice or bob", "alice Or bob"):
            self.assertEqual(build_term({"from": value}), union, value)
        self.assertEqual(build_term({"from": 'alice "or" bob'}),
                         And((Contains("from", "alice"), Contains("from", "or"), Contains("from", "bob"))))

    def test_quoted_term_keeps_a_literal_and(self):
        self.assertEqual(self.subjects(subject='"profit and loss"'), ["Profit and Loss statement"])
        self.assertEqual(self.subjects(subject="profit and loss"),
                         ["Profit statement, Loss carried", "Profit and Loss statement"])

    def test_parentheses_inside_a_flag_and_separate_flags_and(self):
        self.assertEqual(self.subjects(sender="(alice or bob)", cc="carol"), [FILING])
        self.assertEqual(self.subjects(sender="(alice or bob)", subject="dues"), [DUES])

    def test_bad_flag_values_are_refused(self):
        for value in ("(alice or bob", "alice or", 'say "hi', "alice (and) x"):
            with self.assertRaises(Refusal, msg=value):
                build_term({"from": value})


class OneGrammar(SearchCase):
    """A field flag is its field's query; --after is after:; one tokenizer for both."""

    def test_a_flag_is_its_fields_query(self):
        for value in ("alice or bob", "(alice or bob) and archer", "alice -bob", '"alice archer"',
                      "art*", "Re: hello", "alice to:carol"):
            for field in ("from", "subject"):
                self.assertEqual(build_term({field: value}), build_term({}, query=f"{field}:({value})"),
                                 (field, value))
        self.assertEqual(build_term({}, after="2026-08-01", before="2026/09/01"),
                         build_term({}, query="after:2026-08-01 before:2026/09/01"))

    def test_two_bare_words_in_a_flag_are_anded_and_quotes_make_the_phrase(self):
        self.assertEqual(build_term({"subject": "annual dues"}),
                         And((Contains("subject", "annual"), Contains("subject", "dues"))))
        self.assertEqual(build_term({"subject": '"annual dues"'}), Contains("subject", "annual dues"))
        self.assertEqual(self.subjects(subject="dues annual"), [DUES])
        self.assertEqual(self.subjects(subject='"dues annual"'), [])

    def test_lowercase_operators_in_the_query(self):
        self.assertEqual(self.subjects(query="from:alice or from:bob"), [FILING, DUES])
        self.assertEqual(self.subjects(query="subject:tax and subject:acme42"), [FILING])
        self.assertEqual(build_term({}, query="from:alice or from:bob"),
                         Or((Contains("from", "alice"), Contains("from", "bob"))))

    def test_a_quoted_or_is_the_word(self):
        write(self.root, "either", sender="Accounts <accounts@example.org>", subject="Either this or that",
              when="Sat, 15 Aug 2026 10:00:00 +0530")
        self.assertEqual(self.subjects(subject='"or"'), ["Either this or that"])
        self.assertEqual(self.subjects(query='subject:"or"'), ["Either this or that"])

    def test_a_flag_takes_minus_words_and_a_word_with_a_colon(self):
        self.assertEqual(self.subjects(subject="acme42 -tax"), [DUES])
        write(self.root, "re", sender="Accounts <accounts@example.org>", subject="Re: hello there",
              when="Sat, 15 Aug 2026 10:00:00 +0530")
        self.assertEqual(self.subjects(subject="Re: hello"), ["Re: hello there"])
        self.assertEqual(self.subjects(query="Re: hello"), ["Re: hello there"])

    def test_a_flag_refusal_names_the_flag(self):
        with self.assertRaises(Refusal) as caught:
            build_term({"from": "(alice or bob"})
        self.assertTrue(str(caught.exception).startswith("--from: unbalanced '('"))


class Query(SearchCase):
    def test_gmail_query_operators_or_groups_and_negation(self):
        self.assertEqual(self.subjects(query="from:alpha.example.com subject:(dues OR invoice)"), [DUES])
        self.assertEqual(self.subjects(query="subject:ACME42 -from:alice"), [FILING])
        self.assertEqual(self.subjects(query="(from:alice OR from:bob) has:attachment"), [DUES])
        self.assertEqual(self.subjects(query="filename:invoice"), [DUES])

    def test_gmail_bare_phrase_matches_the_body(self):
        self.assertEqual(self.subjects(query='from:accounts "5900.00"'), ["Profit and Loss statement"])

    def test_query_is_anded_with_the_flags(self):
        self.assertEqual(self.subjects(sender="alice or bob", query="has:attachment"), [DUES])

    def test_unknown_operator_and_gmail_folders_are_refused(self):
        for query in ("in:inbox", "label:work", "larger:5M", "has:drive"):
            with self.assertRaises(Refusal, msg=query):
                build_term({}, query=query)

    def test_relative_ages_count_back_from_today(self):
        today = date(2026, 3, 31)
        self.assertEqual(build_term({}, query="older_than:1m", today=today), Before(date(2026, 2, 28)))
        self.assertEqual(build_term({}, query="newer_than:2d", today=today), OnOrAfter(date(2026, 3, 29)))
        self.assertEqual(build_term({}, query="-older_than:1y", today=today), Not(Before(date(2025, 3, 31))))

    def test_nothing_to_search_for_is_refused(self):
        with self.assertRaises(Refusal):
            build_term({})


class Dates(MailCase):
    def test_ist_date_boundary_across_a_month_edge(self):
        """20:00 UTC on 31 July is 1 August in IST, and it is filed in the July folder."""
        write(self.tmp / "mail", "edge", sender="a@example.org", subject="edge",
              when="Fri, 31 Jul 2026 20:00:00 +0000", month="2026-07")
        subjects = lambda **kw: [row["subject"] for row in self.search(**kw)["rows"]]  # noqa: E731
        self.assertEqual(subjects(after="2026-08-01"), ["edge"])
        self.assertEqual(subjects(before="2026/08/01"), [])
        self.assertEqual(subjects(query="after:2026/08/01 before:2026/08/02"), ["edge"])
        self.assertEqual(self.search(after="2026-08-01")["rows"][0]["date"], "2026-08-01T01:30:00+05:30")

    def test_bad_dates_are_refused(self):
        for value in ("2026-13-01", "01/08/2026", "yesterday"):
            with self.assertRaises(Refusal, msg=value):
                build_term({}, after=value)


class Walk(SearchCase):
    def test_months_outside_the_widened_range_are_not_opened(self):
        write(self.root, "old", sender="a@example.org", subject="old", when="Mon, 05 Jan 2026 10:00:00 +0530",
              month="2026-01")
        everything = self.search(subject="o")
        pruned = self.search(subject="o", after="2026-08-01")
        self.assertEqual(pruned["scanned"], everything["scanned"] - 1)

    def test_rfc2047_headers_are_decoded(self):
        name, subject = "Ålice Ändersson", "Rechnung für Juli"
        path = self.root / OWNER / "archive" / "example.de" / "2026-08" / "x.eml"
        path.parent.mkdir(parents=True)
        path.write_bytes((f"From: {Header(name, 'utf-8').encode()} <ae@example.de>\r\nTo: {OWNER}\r\n"
                          f"Subject: {Header(subject, 'utf-8').encode()}\r\n"
                          "Date: Mon, 10 Aug 2026 10:00:00 +0200\r\n\r\nbody\r\n").encode())
        result = self.search(sender="ålice ändersson", subject="FÜR JULI")
        self.assertEqual([row["subject"] for row in result["rows"]], [subject])
        self.assertEqual(result["rows"][0]["from"], f"{name} <ae@example.de>")

    def test_unparseable_file_is_counted_and_skipped(self):
        bad = self.root / OWNER / "archive" / "example.org" / "2026-08" / "bad.eml"
        bad.write_bytes(b"")
        result = self.search(subject="o")
        self.assertEqual(result["unparseable"], 1)
        self.assertNotIn("bad.eml", {Path(row["path"]).name for row in result["rows"]})

    def test_rows_carry_attachments_newest_first_and_limit(self):
        result = self.search(subject="ACME42", limit=1)
        self.assertEqual(result["matched"], 2)
        self.assertEqual([row["subject"] for row in result["rows"]], [FILING])
        full = self.search(subject="dues")["rows"][0]
        self.assertEqual((full["attachments"], full["account"]), (1, OWNER))

    def test_unknown_account_is_refused(self):
        with self.assertRaises(Refusal):
            self.search(subject="x", account=["nobody@example.com"])
        self.assertEqual(self.search(subject="dues", account=[OWNER])["matched"], 1)


class WholeWords(MailCase):
    """A term matches whole words, as Gmail's do; `word*` is a prefix."""

    SUBJECTS = ("Smart meters", "Partners meeting", "Arts fair", "ART grant", "Re: ART-review")

    def setUp(self):
        super().setUp()
        for i, subject in enumerate(self.SUBJECTS, 1):
            write(self.tmp / "mail", f"w{i}", sender=f"A. Archer <alice{i}@alpha.example.com>", subject=subject,
                  when=f"Mon, 1{i} Aug 2026 10:00:00 +0530", attach="invoice_2026.pdf" if i == 4 else None)
        write(self.tmp / "mail", "sub", sender="Billing <billing@mail.example.org>", subject="Statement",
              when="Thu, 20 Aug 2026 10:00:00 +0530")

    def subjects(self, **kwargs):
        return sorted(row["subject"] for row in self.search(**kwargs)["rows"])

    def test_a_term_inside_a_longer_word_does_not_match(self):
        want = ["ART grant", "Re: ART-review"]
        self.assertEqual(self.subjects(subject="art"), want)
        self.assertEqual(self.subjects(query="subject:ART"), want)
        self.assertEqual(self.subjects(query="ART"), want)
        self.assertEqual(self.subjects(query='"art grant"'), ["ART grant"])
        self.assertEqual(self.subjects(subject="meter"), [])

    def test_a_trailing_star_is_a_prefix(self):
        want = ["ART grant", "Arts fair", "Re: ART-review"]
        self.assertEqual(self.subjects(subject="art*"), want)
        self.assertEqual(self.subjects(query="subject:art*"), want)
        self.assertEqual(self.subjects(sender="alph*", subject="partner*"), ["Partners meeting"])

    def test_addresses_and_domains_still_match(self):
        self.assertEqual(self.subjects(sender="alice4@alpha.example.com"), ["ART grant"])
        self.assertEqual(len(self.subjects(sender="alpha.example.com")), 5)
        self.assertEqual(self.subjects(query="from:example.org"), ["Statement"])
        self.assertEqual(self.subjects(query="from:billing"), ["Statement"])
        self.assertEqual(self.subjects(sender="alice"), [])  # alice1..alice5 are other words

    def test_filename_matches_whole_words(self):
        self.assertEqual(self.subjects(query="filename:invoice"), ["ART grant"])
        self.assertEqual(self.subjects(query="filename:pdf"), ["ART grant"])
        self.assertEqual(self.subjects(query="filename:voice"), [])

    def test_a_lone_star_is_refused(self):
        for kwargs in ({"subject": "*"}, {"query": "*"}, {"query": "subject:*"}):
            with self.assertRaises(Refusal, msg=kwargs):
                self.search(**kwargs)
        code, _, err = self.run_cli("search", "--subject", "*")
        self.assertEqual(code, 2)
        self.assertIn("'*' needs a word before it", err)


def b64(text):
    return base64.b64encode(text.encode()).decode()


# One attached part per case: its header lines exactly as stored, and the name the parser finds.
ATTACHED = {
    "rfc2231": ("Content-Type: application/pdf\r\n"
                "Content-Disposition: inline; filename*=utf-8''r%C3%A9sum%C3%A9-plan.pdf", "résumé-plan.pdf"),
    "continued": ("Content-Type: application/pdf\r\n"
                  "Content-Disposition: inline;\r\n filename*0*=utf-8''long%C3%A9;\r\n filename*1*=report.pdf",
                  "longéreport.pdf"),
    "rfc2047": (f"Content-Type: application/pdf; name=\"=?utf-8?b?{b64('facture-été.pdf')}?=\"", "facture-été.pdf"),
    "folded": ("Content-Type: application/pdf\r\nContent-Disposition: inline;\r\n\tfilename\r\n =\r\n"
               " \"folded-ledger.pdf\"", "folded-ledger.pdf"),
    "type-name-only": ("Content-Type: application/pdf; name=\"typeonly-sheet.pdf\"", "typeonly-sheet.pdf"),
    "encoded-header": ("Content-Type: application/pdf\r\n"
                       f"Content-Disposition: =?utf-8?b?{b64('attachment; filename=hidden-memo.pdf')}?=",
                       "hidden-memo.pdf"),
    "no-name": ("Content-Type: application/octet-stream\r\nContent-Disposition: attachment", ""),
    "escaped": ('Content-Type: application/pdf; name="bill\\board-plan.pdf"', "billboard-plan.pdf"),
    "ligature": ('Content-Type: application/pdf; name="\ufb01ling-notes.pdf"', "\ufb01ling-notes.pdf"),
}
# The cases whose name the raw bytes cannot show, which every filename: term must parse.
ENCODED = {"rfc2231", "continued", "rfc2047", "encoded-header", "escaped", "ligature"}


def multipart(subject, part_headers):
    """A two-part message: a text part and one base64 part under the given header lines."""
    return (f"From: alice@example.org\r\nTo: {OWNER}\r\nSubject: {subject}\r\n"
            "Date: Mon, 10 Aug 2026 10:00:00 +0530\r\nMIME-Version: 1.0\r\n"
            "Content-Type: multipart/mixed; boundary=XYZ\r\n\r\n"
            "--XYZ\r\nContent-Type: text/plain\r\n\r\nsee the file\r\n"
            f"--XYZ\r\n{part_headers}\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQK\r\n"
            "--XYZ--\r\n").encode()


class AttachmentPrefilter(MailCase):
    """filename: and has:attachment scan raw bytes first; the scan never drops a real match."""

    def setUp(self):
        super().setUp()
        self.folder = self.tmp / "mail" / OWNER / "archive" / "example.org" / "2026-08"
        self.folder.mkdir(parents=True)
        self.paths = {}
        for case, (headers, _) in ATTACHED.items():
            self.paths[case] = self.folder / f"{case}.eml"
            self.paths[case].write_bytes(multipart(case, headers))
        write(self.tmp / "mail", "plain", sender="bob@example.org", subject="plain",
              when="Mon, 10 Aug 2026 10:00:00 +0530", body="please rename the file name later")
        self.parsed = []
        real = self.full_parse = search_module.load_body

        def recording(path, view):
            self.parsed.append(path.stem)
            return real(path, view)

        patcher = mock.patch.object(search_module, "load_body", recording)
        patcher.start()
        self.addCleanup(patcher.stop)

    def subjects(self, query):
        return sorted(row["subject"] for row in self.search(query=query)["rows"])

    def test_the_parser_finds_each_name(self):
        for case, (_, name) in ATTACHED.items():
            view = search_module.read_headers(self.paths[case])
            self.assertTrue(self.full_parse(self.paths[case], view), case)
            self.assertEqual(view.filenames, [name], case)

    def test_each_encoding_is_found_by_filename(self):
        for case, word in (("rfc2231", "résumé"), ("continued", "longéreport"), ("rfc2047", "facture"),
                           ("folded", "ledger"), ("type-name-only", "typeonly"), ("encoded-header", "memo")):
            self.assertEqual(self.subjects(f"filename:{word}"), [case], case)

    def test_has_attachment_finds_every_case_and_not_the_plain_message(self):
        self.assertEqual(self.subjects("has:attachment"), sorted(ATTACHED))
        self.assertEqual(self.subjects("-has:attachment"), ["plain"])

    def test_a_file_without_the_header_text_is_not_fully_parsed(self):
        self.assertTrue(all(search_module.may_attach(path) for path in self.paths.values()))
        self.assertFalse(search_module.may_attach(self.folder / "plain.eml"))
        for query in ("filename:ledger", "has:attachment", "-has:attachment", "subject:plain has:attachment"):
            self.parsed.clear()
            self.search(query=query, limit=1)
            self.assertNotIn("plain", self.parsed, query)

    def test_name_in_a_body_does_not_make_a_candidate(self):
        bodies = {
            "qp": ("Content-Type: text/plain\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n"
                   "the new username=\r\n is set; filename =\r\n later\r\n"),
            "html": ("Content-Type: text/html\r\n\r\n<meta name=\"viewport\"><a name=3Dtop>"
                     "content-disposition: attachment</a>\r\n"),
        }
        for case, part in bodies.items():
            path = self.folder / f"body-{case}.eml"
            path.write_bytes((f"From: bob@example.org\r\nTo: {OWNER}\r\nSubject: {case}\r\n"
                              f"Date: Mon, 10 Aug 2026 10:00:00 +0530\r\nMIME-Version: 1.0\r\n{part}").encode())
            self.assertFalse(search_module.may_attach(path), case)

    def test_a_file_with_bare_cr_line_ends_is_still_a_candidate(self):
        path = self.folder / "bare-cr.eml"
        path.write_bytes(multipart("bare-cr", "Content-Type: application/pdf;\r\n name=\"cr-only.pdf\"")
                         .replace(b"\r\n", b"\r"))
        self.assertTrue(search_module.may_attach(path))
        self.assertEqual(self.subjects("filename:cr-only"), ["bare-cr"])

    def test_escaped_and_non_ascii_names_are_found(self):
        self.assertEqual(self.subjects("filename:billboard"), ["escaped"])
        self.assertEqual(self.subjects("filename:filing"), ["ligature"])  # the ligature folds to fi

    def test_filename_parses_only_files_whose_name_can_hold_the_term(self):
        self.parsed.clear()
        self.assertEqual(self.subjects("filename:ledger"), ["folded"])
        self.assertEqual(set(self.parsed), ENCODED | {"folded"})

    def test_a_term_only_in_the_body_is_not_a_candidate(self):
        body = self.folder / "body-only.eml"
        body.write_bytes(multipart("body-only", 'Content-Type: application/pdf; name="scan.pdf"')
                         .replace(b"see the file", b"the ledger is attached"))
        self.parsed.clear()
        self.assertEqual(self.subjects("filename:ledger"), ["folded"])
        self.assertNotIn("body-only", self.parsed)
        self.assertEqual(self.subjects("filename:scan"), ["body-only"])

    def test_an_encoded_name_stays_a_candidate_for_any_term(self):
        self.parsed.clear()
        self.assertEqual(self.subjects("filename:nowhere"), [])
        self.assertEqual(set(self.parsed), ENCODED)

    def test_has_attachment_is_not_narrowed_by_a_filename_term(self):
        self.parsed.clear()
        self.assertEqual(self.subjects("has:attachment -filename:nowhere"), sorted(ATTACHED))
        self.assertEqual(set(self.parsed), set(ATTACHED))

    def test_a_row_decided_without_a_parse_still_counts_its_attachments(self):
        rows = self.search(query="subject:type-name-only -filename:nowhere")["rows"]
        self.assertEqual([(row["subject"], row["attachments"]) for row in rows], [("type-name-only", 1)])

    def test_a_term_without_an_attachment_part_parses_as_before(self):
        self.parsed.clear()
        self.assertEqual(self.subjects('"rename the file"'), ["plain"])
        self.assertIn("plain", self.parsed)


class SearchManifest(MailCase):
    """A subject term rules files out by their manifest row, unread; results stay the walk's."""

    FILES = {  # name: (From header, Subject header, the row's subject, or None for no row)
        "a1": ("Alice Archer <alice@alpha.example.com>", "ART grant", "ART grant"),
        "a2": ("Bob Baker <bob@beta.example.net>", "Smart meters", "Smart meters"),
        "a3": ("Carol Cole <carol@example.net>", "Partners meeting", "Partners meeting"),
        "a4": ("Dan Dell <dan@example.org>", "Re: ART review", None),
        "a5": ("Erin <erin@example.org>", "=?utf-8?q?ART_plan?=", "=?utf-8?q?ART_plan?="),
    }

    def setUp(self):
        super().setUp()
        rows = []
        for i, (name, (sender, subject, row_subject)) in enumerate(self.FILES.items()):
            path = write(self.tmp / "mail", name, sender=sender, subject=subject,
                         when=f"Mon, 1{i} Aug 2026 10:00:00 +0530")
            if row_subject is not None:
                rows.append({"id": f"{name}@mail.example.org", "subject": row_subject,
                             "from": sender.split("<")[1].rstrip(">"), "to": [OWNER],
                             "date": "2026-08-10T04:30:00+00:00", "downloaded": True,
                             "path": path.relative_to(self.tmp / "mail" / OWNER / "archive").as_posix()})
        self.shard = write_manifest(self.tmp / "mail" / OWNER, rows)
        self.read = []
        real = search_module.read_headers

        def counting(path):
            self.read.append(path.stem)
            return real(path)

        patcher = mock.patch.object(search_module, "read_headers", counting)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_both(self, **kwargs):
        """(the result with the manifest, the stems read, the result without it)."""
        self.read.clear()
        with_manifest = self.search(**kwargs)
        read = sorted(self.read)
        self.shard.rename(self.shard.with_suffix(".off"))
        try:
            walked = self.search(**kwargs)
        finally:
            self.shard.with_suffix(".off").rename(self.shard)
        return with_manifest, read, walked

    def test_a_subject_term_reads_only_files_its_rows_cannot_rule_out(self):
        result, read, walked = self.run_both(query="subject:art")
        self.assertEqual(result, walked)
        self.assertEqual(sorted(r["subject"] for r in result["rows"]), ["ART grant", "ART plan", "Re: ART review"])
        self.assertEqual(read, ["a1", "a4", "a5"])  # a4 has no row; a5's row still holds "=?"

    def test_terms_the_rows_cannot_decide_read_every_file(self):
        for kwargs in ({"sender": "alice archer"}, {"query": "subject:meters OR from:dell"},
                       {"query": "subject:nothing-here OR after:2026-08-01"}):
            result, read, walked = self.run_both(**kwargs)
            self.assertEqual(result, walked, kwargs)
            self.assertEqual(read, sorted(self.FILES), kwargs)

    def test_a_subject_flag_uses_the_manifest_too(self):
        result, read, walked = self.run_both(subject="art")
        self.assertEqual(result, walked)
        self.assertEqual(read, ["a1", "a4", "a5"])

    def test_negation_and_dates_combine_with_the_subject(self):
        for kwargs in ({"query": "-subject:art"}, {"subject": "art", "after": "2026-08-12"},
                       {"subject": "art", "sender": "alice"}):
            result, read, walked = self.run_both(**kwargs)
            self.assertEqual(result, walked, kwargs)
        self.assertEqual(self.run_both(query="-subject:art")[1], ["a2", "a3", "a4", "a5"])


class SearchCommand(SearchCase):
    def test_bcc_flag_and_its_single_dash_alias(self):
        self.assertEqual(self.subjects(bcc="dan hidden"), ["Note to self"])
        code, out, _ = self.run_cli("search", "-bcc", "dan@example.org", "--json")
        self.assertEqual(code, 0)
        self.assertIn('"Note to self"', out)

    def test_no_match_exits_1(self):
        code, out, _ = self.run_cli("search", "--from", "nobody-at-all")
        self.assertEqual(code, 1)
        self.assertIn("0 matched", out)

    def test_a_bad_query_is_refused_with_exit_2(self):
        code, out, err = self.run_cli("search", "--query", "in:inbox")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("refused: --query: unknown operator in:", err)

    def test_text_rows(self):
        code, out, _ = self.run_cli("search", "--subject", "dues")
        self.assertEqual(code, 0)
        self.assertIn("2026-08-10 10:00  owner@example.com  Alice Archer <alice@alpha.example.com>", out)
        self.assertIn(f"    {DUES}  [1 attached]", out)


if __name__ == "__main__":
    unittest.main()
