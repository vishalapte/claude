"""DMARC reports in a mail store: find every one, read it, and rank what it shows.

Two kinds of report reach a domain's owner. An aggregate report (RFC 7489 section 7.2, the
`rua` address) is an XML file, zipped or gzipped or bare, with one row per sending IP: how many
messages it sent as the domain, and whether DKIM and SPF passed and aligned. A failure report
(RFC 6591, the `ruf` address) is one message that failed, as a multipart/report with a
message/feedback-report part. A message is screened by its subject, sender and content type, not
by the address it was sent to, because a domain's `rua` has pointed at different mailboxes over
the years; what passes is read for what it holds, and what does not is never opened.

The analysis puts threats first: senders that failed DMARC while using the domain, newest and
delivered first. Then holes: a published policy that lets spoofed mail through, and legitimate
senders whose pass rests on one mechanism. Then the statistics.

Every report is untrusted input: decompressed size is capped and a part that will not parse is
counted, never fatal.
"""

from __future__ import annotations

import gzip
import io
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser, HeaderParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from mail_eml import decode_hdr, header_values, read_head
from mail_search import _accounts, _walk, parse_day
from mail_store import Refusal

MAX_XML = 50 << 20  # bytes one decompressed report may reach; a larger one is skipped as unparseable
RECENT_DAYS = 30  # a failure this many days before today, or fewer, is recent
QUIET_DAYS = 60  # a domain with no report for longer than this has lost its monitoring, and
# a hole whose newest evidence is older than this is not reported: it may long since be fixed

_SCREEN_SUBJECT = re.compile(rb"report domain|(?<![a-z])dmarc|report-id")  # not Landmarc
_DMARC_WORD = re.compile(r"(?<![a-z])dmarc", re.I)
_FEEDBACK_CT = re.compile(rb"multipart/report[^\n]*feedback-report|report-type=\"?feedback-report")


# --------------------------------------------------------------------------- #
# finding reports
# --------------------------------------------------------------------------- #
def may_be_report(head: bytes) -> bool:
    """Whether a message's header block can belong to a DMARC report.

    An aggregate report's subject carries "Report Domain:" (RFC 7489 section 7.2.1.1) and the
    reporters that do not follow it still name DMARC; a failure report is a multipart/report of
    report-type feedback-report. Anything else is skipped without a full parse.
    """
    low = head.lower()
    subject = b" ".join(header_values(low, (b"subject",)))
    sender = b" ".join(header_values(low, (b"from",)))
    ctype = b" ".join(header_values(low, (b"content-type",)))
    return bool(_SCREEN_SUBJECT.search(subject) or b"dmarc" in sender or _FEEDBACK_CT.search(ctype))


def _bounded(stream) -> bytes:
    data = stream.read(MAX_XML + 1)
    if len(data) > MAX_XML:
        raise ValueError(f"report larger than {MAX_XML} bytes decompressed")
    return data


_XML_START = re.compile(rb"^(?:\xef\xbb\xbf)?\s*<(?:\?xml|(?:\w+:)?feedback\b)")


def _part_xml(data: bytes) -> list[bytes]:
    """The XML documents one attachment holds: its zip members, its gunzipped bytes, or itself.

    The format is read from the bytes, not the label: Mimecast sends a zip as application/gzip.
    """
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            found = []
            for member in zf.namelist():
                if member.lower().endswith(".xml"):
                    with zf.open(member) as fh:
                        found.append(_bounded(fh))
            return found
    if data[:2] == b"\x1f\x8b":
        return [_bounded(gzip.GzipFile(fileobj=io.BytesIO(data)))]
    if _XML_START.match(data) and b"feedback" in data[:4096]:
        return [data]
    return []


def _xml_blobs(msg):
    """Each leaf part's payload, for _part_xml; a body part is not XML and yields nothing."""
    for part in msg.walk():
        if not part.is_multipart():
            data = part.get_payload(decode=True)
            if data:
                yield data


def _strip_ns(root):
    for node in root.iter():
        if isinstance(node.tag, str) and "}" in node.tag:
            node.tag = node.tag.rsplit("}", 1)[1]
    return root


def _text(node, path):
    if node is None:
        return ""
    found = node.find(path)
    return found.text.strip() if found is not None and found.text else ""


def _utc_day(epoch: str) -> str:
    try:
        return datetime.fromtimestamp(int(epoch), timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def parse_aggregate(xml: bytes) -> dict | None:
    """One aggregate report as a dict, or None when the document is not a DMARC feedback."""
    root = _strip_ns(ET.fromstring(xml))
    if root.tag != "feedback":
        return None
    meta, pub = root.find("report_metadata"), root.find("policy_published")
    report = {
        "org": _text(meta, "org_name"),
        "report_id": _text(meta, "report_id"),
        "begin": _utc_day(_text(meta, "date_range/begin")),
        "end": _utc_day(_text(meta, "date_range/end")),
        "domain": _text(pub, "domain").lower(),
        "p": _text(pub, "p").lower(),
        "sp": _text(pub, "sp").lower(),
        "pct": _text(pub, "pct"),
        "adkim": _text(pub, "adkim").lower(),
        "aspf": _text(pub, "aspf").lower(),
        "records": [],
    }
    for rec in root.findall("record"):
        dkim = _text(rec, "row/policy_evaluated/dkim").lower()
        spf = _text(rec, "row/policy_evaluated/spf").lower()
        report["records"].append({
            "source_ip": _text(rec, "row/source_ip"),
            "count": int(_text(rec, "row/count") or 0),
            "disposition": _text(rec, "row/policy_evaluated/disposition").lower() or "none",
            "dkim": dkim,
            "spf": spf,
            "dmarc": "pass" if "pass" in (dkim, spf) else "fail",
            "reasons": sorted({_text(r, "type").lower() for r in rec.findall("row/policy_evaluated/reason")} - {""}),
            "header_from": _text(rec, "identifiers/header_from").lower(),
            "envelope_from": _text(rec, "identifiers/envelope_from").lower(),
            "dkim_domains": sorted({_text(a, "domain").lower() for a in rec.findall("auth_results/dkim")} - {""}),
            "spf_domains": sorted({_text(a, "domain").lower() for a in rec.findall("auth_results/spf")} - {""}),
        })
    return report


def parse_failure(msg) -> list[dict]:
    """Each message/feedback-report part of an auth-failure report, as a dict."""
    found = []
    for part in msg.walk():
        if part.get_content_type() != "message/feedback-report":
            continue
        payload = part.get_payload()
        if isinstance(payload, list):
            raw = "\n".join(str(p) for p in payload)
        else:
            data = part.get_payload(decode=True) or b""
            raw = data.decode("utf-8", errors="replace") if data else str(payload or "")
        fields = HeaderParser(policy=policy.compat32).parsestr(raw.strip() + "\n\n")
        kind = (fields.get("Feedback-Type") or "").strip().lower()
        if kind and kind != "auth-failure":
            continue
        found.append({
            "auth_failure": (fields.get("Auth-Failure") or "").strip().lower(),
            "source_ip": (fields.get("Source-IP") or "").strip(),
            "domain": (fields.get("Reported-Domain") or "").strip().lower(),
            "arrival": (fields.get("Arrival-Date") or "").strip(),
            "delivery": (fields.get("Delivery-Result") or "").strip().lower(),
            "mail_from": (fields.get("Original-Mail-From") or "").strip(),
        })
    return found


def collect(store: Path, account=None, lo: date | None = None,  # pylint: disable=too-many-locals
            hi: date | None = None) -> dict:
    """Every report in the store: aggregates deduplicated by (reporter, report id), failures."""
    accounts = _accounts(account, store)
    scanned = candidates = unparseable = duplicates = 0
    aggregates: dict[tuple, dict] = {}
    failures: list[dict] = []
    others: list[dict] = []
    for acct, path in _walk(accounts, lo, hi):
        scanned += 1
        try:
            head = read_head(path)
        except OSError:
            unparseable += 1
            continue
        if not may_be_report(head):
            continue
        candidates += 1
        src = str(path)
        try:
            with open(path, "rb") as fh:
                msg = BytesParser(policy=policy.compat32).parse(fh)
            held = 0
            for blob in _reports_in(msg):
                if blob is None:
                    unparseable += 1
                    continue
                try:
                    report = parse_aggregate(blob)
                except (ET.ParseError, ValueError):
                    unparseable += 1
                    continue
                if report is None:
                    continue
                held += 1
                key = (report["org"], report["report_id"] or (report["begin"], report["end"], report["domain"]))
                if key in aggregates:
                    duplicates += 1
                    continue
                report["account"], report["src"] = acct, src
                aggregates[key] = report
            for failure in parse_failure(msg):
                failure.update(account=acct, src=src, reporter=decode_hdr(msg.get("From", "")),
                               date=_day(msg, failure["arrival"]))
                failures.append(failure)
                held += 1
            subject = decode_hdr(msg.get("Subject", ""))
            if not held and _DMARC_WORD.search(subject):
                others.append({"date": _day(msg),
                               "from": " ".join(decode_hdr(msg.get("From", "")).split()),
                               "subject": " ".join(subject.split()), "account": acct, "src": src})
        except (zipfile.BadZipFile, OSError, EOFError, ET.ParseError, ValueError, LookupError):
            unparseable += 1  # a report that will not open is counted, never fatal
    reports = [r for r in aggregates.values() if _in_range(r["end"], lo, hi)]
    failures = [f for f in failures if _in_range(f["date"], lo, hi)]
    others = sorted((o for o in others if _in_range(o["date"], lo, hi)), key=lambda o: o["date"], reverse=True)
    return {
        "scanned": scanned, "candidates": candidates, "unparseable": unparseable,
        "duplicates": duplicates, "reports": reports, "failures": failures, "others": others,
    }


def _reports_in(msg):
    """Each XML document in the message, or None for an attachment that would not open.

    One bad attachment does not hide the good ones beside it.
    """
    for data in _xml_blobs(msg):
        try:
            yield from _part_xml(data)
        except (zipfile.BadZipFile, OSError, EOFError, ValueError):
            yield None


def _day(msg, arrival: str = "") -> str:
    """The UTC day of a failure report's Arrival-Date, else of the message's Date; "" for neither."""
    for raw in (arrival, msg.get("Date", "")):
        try:
            when = parsedate_to_datetime(str(raw))
            if when is not None:
                return when.astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError, IndexError):
            continue
    return ""


def _in_range(day: str, lo: date | None, hi: date | None) -> bool:
    if not day:
        return lo is None and hi is None
    d = date.fromisoformat(day)
    return (lo is None or d >= lo) and (hi is None or d < hi)


# --------------------------------------------------------------------------- #
# the analysis
# --------------------------------------------------------------------------- #
def _sender(rec: dict) -> str:
    """Who sent it, as the report names it: the DKIM signer, else the SPF domain, else unknown."""
    for domain in rec["dkim_domains"] + rec["spf_domains"]:
        if domain:
            return domain
    return "(unnamed)"


def threats(reports: list[dict], today: date, recent_days: int) -> list[dict]:
    """One row per (domain, source IP) that failed DMARC, worst first.

    A source that also passed for the same domain is a legitimate sender failing some of the
    time (forwarding, a mailing list, a key rotation): low. One that never passed is a stranger
    using the domain: high when a receiver delivered its mail, medium when every message was
    quarantined or rejected.
    """
    passing = {(r["domain"], rec["source_ip"]) for r in reports for rec in r["records"] if rec["dmarc"] == "pass"}
    rows: dict[tuple, dict] = {}
    for report in reports:
        for rec in report["records"]:
            if rec["dmarc"] != "fail":
                continue
            key = (report["domain"], rec["source_ip"])
            row = rows.setdefault(key, {
                "domain": report["domain"], "source_ip": rec["source_ip"], "messages": 0,
                "delivered": 0, "quarantined": 0, "rejected": 0, "first": report["end"],
                "last": report["end"], "reporters": set(), "senders": set(), "reasons": set(),
                "header_from": set(), "seen_passing": key in passing,
            })
            row["messages"] += rec["count"]
            outcome = {"none": "delivered", "quarantine": "quarantined", "reject": "rejected"}
            row[outcome.get(rec["disposition"], "delivered")] += rec["count"]
            row["first"] = min(row["first"], report["end"])
            row["last"] = max(row["last"], report["end"])
            row["reporters"].add(report["org"])
            row["senders"].add(_sender(rec))
            row["reasons"].update(rec["reasons"])
            row["header_from"].add(rec["header_from"])
    out = []
    for row in rows.values():
        age = (today - date.fromisoformat(row["last"])).days if row["last"] else None
        row["recent"] = age is not None and age <= recent_days
        if row["seen_passing"]:
            row["severity"] = "low"
        elif row["delivered"]:
            row["severity"] = "high"
        else:
            row["severity"] = "medium"
        for key in ("reporters", "senders", "reasons", "header_from"):
            row[key] = sorted(row[key] - {""})
        out.append(row)
    rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda r: (not r["recent"], rank[r["severity"]], _neg_day(r["last"]), -r["messages"]))
    return out


def _neg_day(day: str) -> int:
    return -date.fromisoformat(day).toordinal() if day else 0


def holes(reports: list[dict], today: date, quiet_days: int) -> list[dict]:
    """Weaknesses in what each domain publishes and in how its own senders authenticate.

    Only a hole with recent evidence is reported: a report describes the day it covers, and a
    hole fixed since then would otherwise stay listed for as long as the store keeps the report.
    A domain whose newest report is quiet shows as "no recent reports" and nothing else.
    """
    latest: dict[str, dict] = {}
    for report in reports:
        if report["domain"] and (report["domain"] not in latest or report["end"] >= latest[report["domain"]]["end"]):
            latest[report["domain"]] = report
    found = []

    def add(domain, severity, kind, detail):
        found.append({"domain": domain, "severity": severity, "kind": kind, "detail": detail})

    for domain, report in sorted(latest.items()):
        quiet = (today - date.fromisoformat(report["end"])).days if report["end"] else None
        if quiet is not None and quiet > quiet_days:
            add(domain, "low", "no recent reports",
                f"the last report covers {report['end']}, {quiet} days ago: the rua address may have changed "
                "or reports are not reaching this store")
            continue
        on = f"as published on {report['end']}"
        if report["p"] in ("none", ""):
            add(domain, "high", "p=none",
                f"policy is none {on}: receivers deliver mail that fails DMARC, so the domain can be spoofed")
        if report["pct"] and report["pct"] != "100" and report["p"] not in ("none", ""):
            add(domain, "medium", f"pct={report['pct']}",
                f"p={report['p']} applies to {report['pct']}% of failing mail {on}; the rest is delivered")
        if report["sp"] == "none" and report["p"] not in ("none", ""):
            add(domain, "medium", "sp=none",
                f"subdomain policy is none {on}: any subdomain can be spoofed though p={report['p']}")

    # A legitimate sender: a (domain, sender) pair with passing mail. Its pass is fragile when it
    # rests on one mechanism; DKIM survives forwarding and SPF does not. A sender whose newest
    # passing mail passed DKIM has fixed it, and one last seen on SPF alone long ago is old news.
    by_sender: dict[tuple, dict] = defaultdict(lambda: {"messages": 0, "dkim_fail": 0, "dkim_last": "", "spf_last": ""})
    for report in reports:
        for rec in report["records"]:
            if rec["dmarc"] != "pass":
                continue
            agg = by_sender[(report["domain"], _sender(rec))]
            agg["messages"] += rec["count"]
            if rec["dkim"] == "pass":
                agg["dkim_last"] = max(agg["dkim_last"], report["end"])
            else:
                agg["dkim_fail"] += rec["count"]
                agg["spf_last"] = max(agg["spf_last"], report["end"])
    for (domain, sender), agg in sorted(by_sender.items()):
        last = agg["spf_last"]
        stale = last and (today - date.fromisoformat(last)).days > quiet_days
        if agg["dkim_fail"] and not stale and agg["dkim_last"] <= last:
            add(domain, "medium", "DKIM not aligned",
                f"{sender}: {agg['dkim_fail']} of {agg['messages']} passing messages passed on SPF alone "
                f"(last {last}); a forward of them fails DMARC")
    rank = {"high": 0, "medium": 1, "low": 2}
    found.sort(key=lambda h: (rank[h["severity"]], h["domain"], h["kind"]))
    return found


def statistics(reports: list[dict]) -> dict:  # pylint: disable=too-many-locals
    """Counts by domain, reporter, month and sender."""
    def bucket():
        return {"reports": 0, "messages": 0, "failing": 0}

    domains, reporters, months, senders = (defaultdict(bucket) for _ in range(4))
    policy_of: dict[str, tuple] = {}
    total = bucket()
    first = last = ""
    for report in reports:
        tags = ((domains, report["domain"]), (reporters, report["org"]), (months, report["end"][:7]))
        for table, key in tags:
            table[key]["reports"] += 1
        total["reports"] += 1
        first = min(first or report["end"], report["end"])
        last = max(last, report["end"])
        if report["domain"] not in policy_of or report["end"] >= policy_of[report["domain"]][0]:
            policy_of[report["domain"]] = (report["end"], report["p"] or "(none)")
        for rec in report["records"]:
            fail = rec["count"] if rec["dmarc"] == "fail" else 0
            for table, key in (*tags, (senders, (report["domain"], _sender(rec)))):
                table[key]["messages"] += rec["count"]
                table[key]["failing"] += fail
            total["messages"] += rec["count"]
            total["failing"] += fail
    for key, row in domains.items():
        row["last"], row["p"] = policy_of.get(key, ("", ""))

    def rows(table, name):
        return [dict({name: k}, **v) for k, v in table.items()]

    return {
        "total": dict(total, first=first, last=last),
        "domains": sorted(rows(domains, "domain"), key=lambda r: (-r["messages"], r["domain"])),
        "reporters": sorted(rows(reporters, "reporter"), key=lambda r: (-r["reports"], r["reporter"])),
        "months": sorted(rows(months, "month"), key=lambda r: r["month"]),
        "senders": sorted(
            ({"domain": d, "sender": s, **v} for (d, s), v in senders.items()),
            key=lambda r: (-r["messages"], r["domain"], r["sender"]),
        ),
    }


def dmarc(*, store: Path, account=None, domain=None, after=None,  # pylint: disable=too-many-arguments
          before=None, days: int = RECENT_DAYS, today: date | None = None) -> dict:
    """Find, read and rank every DMARC report in the store; the result `mail.py dmarc` prints."""
    if days < 0:
        raise Refusal("--days must be 0 or more")
    lo = parse_day(after, "--after") if after else None
    hi = parse_day(before, "--before") if before else None
    today = today or date.today()
    found = collect(store, account, lo, hi)
    reports, failures = found["reports"], found["failures"]
    if domain:
        wanted = {d.lower() for d in domain}
        reports = [r for r in reports if r["domain"] in wanted]
        failures = [f for f in failures if f["domain"] in wanted]
    failures.sort(key=lambda f: f["date"], reverse=True)
    for failure in failures:
        failure["recent"] = (bool(failure["date"])
                             and (today - date.fromisoformat(failure["date"])) <= timedelta(days=days))
    return {
        "today": today.isoformat(),
        "recent_days": days,
        "recent_since": (today - timedelta(days=days)).isoformat(),
        "scanned": found["scanned"],
        "candidates": found["candidates"],
        "unparseable": found["unparseable"],
        "duplicates": found["duplicates"],
        "threats": threats(reports, today, days),
        "failure_reports": failures,
        "holes": holes(reports, today, QUIET_DAYS),
        "stats": statistics(reports),
        "other_dmarc_mail": found["others"],
    }


def alarming(result: dict) -> bool:
    """Whether the result holds a recent threat, a recent failure report, or a high hole."""
    return (any(t["recent"] and t["severity"] != "low" for t in result["threats"])
            or any(f["recent"] for f in result["failure_reports"])
            or any(h["severity"] == "high" for h in result["holes"]))
