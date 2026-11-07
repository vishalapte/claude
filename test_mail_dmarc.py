"""Tests for dot-claude/skills/enine-mail's dmarc verb: finding DMARC reports by content,
reading them, and ranking threats, holes and statistics.
Run: python3 -m unittest test_mail_dmarc

Every domain, address and IP here is made up (example.org, documentation ranges).
"""

import gzip
import io
import sys
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from unittest import mock

from test_mail import MailCase

dmarc = sys.modules["mail_dmarc"]

TODAY = date(2026, 11, 3)
D = "example.org"


def stamp(day):
    return int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp())


def report_xml(*, records, domain=D, p="reject", sp="", pct="", org="rcv.example", rid="r1", day=TODAY, ns=False):
    """An aggregate report; each record is (ip, count, disposition, dkim, spf, signer)."""
    rows = "".join(
        f"<record><row><source_ip>{ip}</source_ip><count>{count}</count><policy_evaluated>"
        f"<disposition>{disp}</disposition><dkim>{dkim}</dkim><spf>{spf}</spf></policy_evaluated></row>"
        f"<identifiers><header_from>{domain}</header_from></identifiers>"
        f"<auth_results><dkim><domain>{signer}</domain><result>{dkim}</result></dkim>"
        f"<spf><domain>{signer}</domain><result>{spf}</result></spf></auth_results></record>"
        for ip, count, disp, dkim, spf, signer in records)
    xmlns = ' xmlns="urn:ietf:params:xml:ns:dmarc-2.0"' if ns else ""
    extra = (f"<sp>{sp}</sp>" if sp else "") + (f"<pct>{pct}</pct>" if pct else "")
    return (f'<?xml version="1.0"?><feedback{xmlns}><report_metadata><org_name>{org}</org_name>'
            f"<report_id>{rid}</report_id><date_range><begin>{stamp(day)}</begin>"
            f"<end>{stamp(day) + 86399}</end></date_range></report_metadata><policy_published>"
            f"<domain>{domain}</domain><adkim>r</adkim><aspf>r</aspf><p>{p}</p>{extra}"
            f"</policy_published>{rows}</feedback>").encode()


def zipped(xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("rcv.example!example.org!1!2.xml", xml)
    return buf.getvalue()


def message(subject, *, attach=None, ctype=("application", "gzip"), name="r.xml.gz", html=None):
    msg = EmailMessage()
    msg["From"] = "noreply-dmarc@rcv.example"
    msg["To"] = "dmarc@example.org"
    msg["Subject"] = subject
    msg["Date"] = "Tue, 03 Nov 2026 01:00:00 +0000"
    msg.set_content("report attached")
    if html:
        msg.add_alternative(html, subtype="html")
    if attach is not None:
        msg.add_attachment(attach, maintype=ctype[0], subtype=ctype[1], filename=name)
    return msg.as_bytes()


def failure_report(feedback_type="auth-failure"):
    """An RFC 6591 report: multipart/report holding a message/feedback-report part."""
    b = "b0undary"
    return (f"From: ruf@rcv.example\r\nTo: dmarc@example.org\r\n"
            f"Subject: DMARC failure report for example.org\r\nDate: Mon, 02 Nov 2026 10:00:00 +0000\r\n"
            f'MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=feedback-report; boundary="{b}"\r\n\r\n'
            f"--{b}\r\nContent-Type: text/plain\r\n\r\nfailed\r\n"
            f"--{b}\r\nContent-Type: message/feedback-report\r\n\r\n"
            f"Feedback-Type: {feedback_type}\r\nVersion: 1\r\nAuth-Failure: dmarc\r\n"
            f"Source-IP: 203.0.113.9\r\nReported-Domain: example.org\r\n"
            f"Arrival-Date: Mon, 02 Nov 2026 09:59:00 +0000\r\nDelivery-Result: reject\r\n\r\n"
            f"--{b}--\r\n").encode()


class DmarcCase(MailCase):
    def put(self, raw, *, account="dmarc@example.org", name="a", month="2026-11"):
        folder = self.root / account / "archive" / "rcv.example" / month
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.eml").write_bytes(raw)

    def scan(self, **kwargs):
        return dmarc.dmarc(store=self.root, today=kwargs.pop("today", TODAY), **kwargs)


class FindingReports(DmarcCase):
    def setUp(self):
        super().setUp()
        self.root.mkdir()

    def test_a_zip_labelled_gzip_is_read_by_its_bytes(self):
        xml = report_xml(records=[("192.0.2.1", 3, "none", "pass", "pass", D)])
        self.put(message("Report domain: example.org Submitter: mimecast.org", attach=zipped(xml)))
        result = self.scan()
        self.assertEqual(result["stats"]["total"]["reports"], 1)
        self.assertEqual(result["stats"]["total"]["messages"], 3)
        self.assertEqual(result["unparseable"], 0)

    def test_an_html_body_naming_feedback_does_not_hide_the_gzip_beside_it(self):
        xml = report_xml(records=[("192.0.2.1", 2, "none", "pass", "pass", D)])
        self.put(message("[Preview] Report Domain: example.org", attach=gzip.compress(xml),
                         html="<div>Send feedback &nbsp; about this report</div>"))
        result = self.scan()
        self.assertEqual(result["stats"]["total"]["reports"], 1)
        self.assertEqual(result["unparseable"], 0)

    def test_bare_namespaced_xml_is_read(self):
        xml = report_xml(records=[("192.0.2.1", 1, "none", "pass", "pass", D)], ns=True)
        self.put(message("Report Domain: example.org", attach=xml, ctype=("text", "xml"), name="r.xml"))
        self.assertEqual(self.scan()["stats"]["total"]["reports"], 1)

    def test_a_report_stored_in_two_mailboxes_counts_once(self):
        raw = message("Report Domain: example.org", attach=gzip.compress(
            report_xml(records=[("192.0.2.1", 4, "none", "pass", "pass", D)])))
        self.put(raw, account="a@example.org")
        self.put(raw, account="b@example.org")
        result = self.scan()
        self.assertEqual(result["stats"]["total"]["messages"], 4)
        self.assertEqual(result["duplicates"], 1)

    def test_a_bad_attachment_is_counted_and_the_good_one_beside_it_read(self):
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = "r@rcv.example", "dmarc@example.org", "Report Domain: example.org"
        msg.set_content("two")
        msg.add_attachment(b"\x1f\x8bnot gzip", maintype="application", subtype="gzip", filename="bad.xml.gz")
        msg.add_attachment(gzip.compress(report_xml(records=[("192.0.2.1", 1, "none", "pass", "pass", D)])),
                           maintype="application", subtype="gzip", filename="good.xml.gz")
        self.put(msg.as_bytes())
        result = self.scan()
        self.assertEqual(result["unparseable"], 1)
        self.assertEqual(result["stats"]["total"]["reports"], 1)

    def test_a_report_past_the_size_cap_is_unparseable(self):
        self.put(message("Report Domain: example.org", attach=gzip.compress(
            report_xml(records=[("192.0.2.1", 1, "none", "pass", "pass", D)]))))
        with mock.patch.object(dmarc, "MAX_XML", 64):
            result = self.scan()
        self.assertEqual(result["unparseable"], 1)
        self.assertEqual(result["stats"]["total"]["reports"], 0)

    def test_mail_that_is_not_a_report_is_not_read_in_full(self):
        msg = EmailMessage()
        msg["From"], msg["Subject"] = "friend@example.net", "Brunch at Landmarc"
        msg.set_content("see you")
        self.put(msg.as_bytes())
        result = self.scan()
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["other_dmarc_mail"], [])

    def test_a_report_whose_headers_do_not_mark_it_is_not_read(self):
        msg = EmailMessage()
        msg["From"], msg["Subject"] = "reports@rcv.example", "Monthly summary"
        msg.set_content("attached")
        msg.add_attachment(gzip.compress(report_xml(records=[("192.0.2.1", 1, "none", "pass", "pass", D)])),
                           maintype="application", subtype="gzip", filename="r.xml.gz")
        self.put(msg.as_bytes())
        result = self.scan()
        self.assertEqual((result["candidates"], result["stats"]["total"]["reports"]), (0, 0))

    def test_mail_naming_dmarc_with_no_report_is_listed_for_the_owner(self):
        self.put(message("Weekly DMARC Report: Action Required"))
        rows = self.scan()["other_dmarc_mail"]
        self.assertEqual([r["subject"] for r in rows], ["Weekly DMARC Report: Action Required"])

    def test_a_failure_report_is_read_and_an_abuse_report_is_not(self):
        self.put(failure_report(), name="ruf")
        self.put(failure_report("abuse"), name="abuse")
        rows = self.scan()["failure_reports"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["source_ip"], rows[0]["domain"], rows[0]["auth_failure"], rows[0]["date"]),
                         ("203.0.113.9", D, "dmarc", "2026-11-02"))
        self.assertTrue(rows[0]["recent"])


class Ranking(DmarcCase):
    def setUp(self):
        super().setUp()
        self.root.mkdir()

    def add(self, name, **kwargs):
        month = kwargs.get("day", TODAY).strftime("%Y-%m")  # the archive files a report by when it came
        self.put(message(f"Report Domain: {kwargs.get('domain', D)}",
                         attach=gzip.compress(report_xml(rid=name, **kwargs))), name=name, month=month)

    def test_threats_rank_recent_then_delivered_then_blocked_then_legitimate(self):
        old = TODAY - timedelta(days=200)
        self.add("legit", records=[("192.0.2.1", 5, "none", "pass", "pass", D),
                                   ("192.0.2.1", 1, "none", "fail", "fail", D)])
        self.add("blocked", records=[("198.51.100.7", 2, "reject", "fail", "fail", "spoof.example")])
        self.add("delivered", p="none", records=[("203.0.113.5", 3, "none", "fail", "fail", "spoof.example")])
        self.add("old", day=old, p="none", records=[("203.0.113.99", 9, "none", "fail", "fail", "spoof.example")])
        threats = self.scan()["threats"]
        self.assertEqual([(t["source_ip"], t["severity"], t["recent"]) for t in threats],
                         [("203.0.113.5", "high", True), ("198.51.100.7", "medium", True),
                          ("192.0.2.1", "low", True), ("203.0.113.99", "high", False)])
        self.assertEqual((threats[1]["rejected"], threats[1]["delivered"]), (2, 0))

    def test_holes_name_a_weak_policy_and_a_sender_passing_on_spf_alone(self):
        self.add("none", records=[("192.0.2.1", 1, "none", "pass", "pass", D)], p="none")
        self.add("pct", domain="pct.example", p="quarantine", pct="50",
                 records=[("192.0.2.2", 1, "none", "pass", "pass", "pct.example")])
        self.add("sp", domain="sp.example", p="reject", sp="none",
                 records=[("192.0.2.3", 1, "none", "pass", "pass", "sp.example")])
        self.add("spf", domain="spf.example", p="reject",
                 records=[("192.0.2.4", 3, "none", "fail", "pass", "mailer.example")])
        self.add("quiet", domain="quiet.example", p="reject", day=TODAY - timedelta(days=90),
                 records=[("192.0.2.5", 1, "none", "pass", "pass", "quiet.example")])
        holes = [(h["severity"], h["domain"], h["kind"]) for h in self.scan()["holes"]]
        self.assertEqual(holes, [("high", D, "p=none"),
                                 ("medium", "pct.example", "pct=50"),
                                 ("medium", "sp.example", "sp=none"),
                                 ("medium", "spf.example", "DKIM not aligned"),
                                 ("low", "quiet.example", "no recent reports")])

    def test_a_policy_hole_on_a_report_past_the_quiet_window_is_not_reported(self):
        self.add("stale", p="none", day=TODAY - timedelta(days=90),
                 records=[("192.0.2.1", 1, "none", "pass", "pass", D)])
        result = self.scan()
        self.assertEqual([(h["severity"], h["kind"]) for h in result["holes"]], [("low", "no recent reports")])
        self.assertFalse(dmarc.alarming(result))

    def test_a_sender_last_on_spf_alone_past_the_quiet_window_is_not_a_hole(self):
        self.add("old", day=TODAY - timedelta(days=90), records=[("192.0.2.1", 3, "none", "fail", "pass", "old.example")])
        self.add("now", records=[("192.0.2.2", 1, "none", "pass", "pass", D)])
        self.assertEqual(self.scan()["holes"], [])

    def test_a_sender_whose_newest_mail_passed_dkim_is_not_a_hole(self):
        self.add("before", day=TODAY - timedelta(days=20), records=[("192.0.2.1", 3, "none", "fail", "pass", D)])
        self.add("after", day=TODAY - timedelta(days=5), records=[("192.0.2.1", 2, "none", "pass", "pass", D)])
        self.assertEqual(self.scan()["holes"], [])

    def test_a_kept_spf_hole_is_dated_by_its_last_spf_only_message(self):
        spf_day = TODAY - timedelta(days=5)
        self.add("signed", day=TODAY - timedelta(days=20), records=[("192.0.2.1", 2, "none", "pass", "pass", D)])
        self.add("unsigned", day=spf_day, records=[("192.0.2.1", 3, "none", "fail", "pass", D)])
        holes = self.scan()["holes"]
        self.assertEqual([h["kind"] for h in holes], ["DKIM not aligned"])
        self.assertIn(f"3 of 5 passing messages passed on SPF alone (last {spf_day.isoformat()})", holes[0]["detail"])

    def test_statistics_count_by_domain_reporter_and_month(self):
        self.add("a", records=[("192.0.2.1", 4, "none", "pass", "pass", D),
                               ("198.51.100.7", 1, "reject", "fail", "fail", "spoof.example")])
        self.add("b", org="other.example", day=TODAY - timedelta(days=40),
                 records=[("192.0.2.1", 2, "none", "pass", "pass", D)])
        stats = self.scan()["stats"]
        self.assertEqual(stats["total"]["messages"], 7)
        self.assertEqual(stats["total"]["failing"], 1)
        self.assertEqual([(d["domain"], d["reports"], d["messages"], d["failing"], d["p"]) for d in stats["domains"]],
                         [(D, 2, 7, 1, "reject")])
        self.assertEqual([r["reporter"] for r in stats["reporters"]], ["other.example", "rcv.example"])
        self.assertEqual([m["month"] for m in stats["months"]], ["2026-09", "2026-11"])

    def test_domain_and_date_filters(self):
        self.add("a", records=[("192.0.2.1", 1, "none", "pass", "pass", D)])
        self.add("b", domain="other.example", records=[("192.0.2.2", 1, "none", "pass", "pass", "other.example")])
        self.add("c", day=date(2026, 1, 5), records=[("192.0.2.3", 1, "none", "pass", "pass", D)])
        self.assertEqual(self.scan(domain=["other.example"])["stats"]["total"]["reports"], 1)
        self.assertEqual(self.scan(after="2026-02-01")["stats"]["total"]["reports"], 2)
        self.assertEqual(self.scan(before="2026-02-01")["stats"]["total"]["reports"], 1)


class DmarcCommandLine(DmarcCase):
    def test_exit_1_on_a_recent_threat_and_0_when_clean(self):
        self.root.mkdir()
        today = date.today()
        self.put(message("Report Domain: example.org", attach=gzip.compress(
            report_xml(day=today, records=[("192.0.2.1", 1, "none", "pass", "pass", D)]))), name="ok")
        code, out, _ = self.run_cli("dmarc")
        self.assertEqual(code, 0, out)
        self.assertIn("THREATS", out)
        self.put(message("Report Domain: example.org", attach=gzip.compress(
            report_xml(rid="r2", day=today, records=[("198.51.100.7", 1, "reject", "fail", "fail", "x.example")]))),
            name="bad")
        code, out, _ = self.run_cli("dmarc")
        self.assertEqual(code, 1)
        self.assertLess(out.index("198.51.100.7"), out.index("BY DOMAIN"))

    def test_refuses_without_a_store(self):
        with mock.patch.dict("os.environ", {"ENINE_MAIL_STORE": ""}):
            code, _, err = self.run_cli("dmarc")
        self.assertEqual(code, 2)
        self.assertIn("ENINE_MAIL_STORE", err)

    def test_json(self):
        self.root.mkdir()
        code, out, _ = self.run_cli("dmarc", "--json")
        self.assertEqual(code, 0)
        self.assertIn('"threats": []', out)


if __name__ == "__main__":
    unittest.main()
