"""Tests for dot-claude/skills/enine-porkbun. Run: python3 -m unittest test_porkbun

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude. Uses the Porkbun fake from test_configure_domain_email, plus the account's
domain list, and never touches the network or the real credentials files.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from test_configure_domain_email import D, FakeApis, Run, load, pc, porkbun_new_domain

pb = load("enine-porkbun", "porkbun.py")


class Account(FakeApis):
    """FakeApis plus /domain/listAll: an exact `domain` match gives 0 or 1 domain; without one,
    every domain, a page of pb.LIST_PAGE at a time from `start`. /dns/scan answers `scanned`,
    the records the domain's live nameservers publish, in Porkbun's scan form."""

    domains = (D,)

    def __init__(self, records, scanned=(), **kwargs):
        super().__init__(records, **kwargs)
        self.lookups = []
        self.pages = []
        self.scanned = [dict(r) for r in scanned]
        self.scans = 0
        self.points = []  # /dns/history's restorePoints
        self.missing, self.extra = [], []  # /dns/diff against any point, fully qualified names
        self.restores = []  # (snapshotId, prune, dryRun) per /dns/restore
        self.intents = []
        self.saved_as = None  # what a dry-run restore answers as previousStateSavedAs

    def http(self, method, url, headers, body=None):
        if url == f"{pc.PB_API}/dns/history/{D}":
            return 200, {"status": "SUCCESS", "domain": D, "restorePoints": self.points}
        if url.startswith(f"{pc.PB_API}/dns/diff/{D}/"):
            snapshot = {"id": int(url.rsplit("/", 1)[1]), "takenAt": "2026-10-30 10:00:00", "reason": "before-delete"}
            return 200, {"status": "SUCCESS", "snapshot": snapshot, "missing": self.missing, "extra": self.extra}
        if url == f"{pc.PB_API}/dns/restore/{D}":
            self.restores.append((body["snapshotId"], body.get("prune", False), body.get("dryRun", False)))
            removed = len(self.extra) if body.get("prune") else 0
            if body.get("dryRun"):
                return 200, {"status": "SUCCESS", "restored": len(self.missing), "removed": removed,
                             "failed": [], "previousStateSavedAs": self.saved_as}
            for r in self.missing:
                sub = r["name"][: -len(D) - 1] if r["name"] != D else ""
                self.records[str(self.next_id)] = {"name": sub, "type": r["type"], "content": r["content"], "prio": r.get("prio") or 0}
                self.next_id += 1
            self.writes.append(("restore", body["snapshotId"]))
            return 200, {"status": "SUCCESS", "restoredFrom": body["snapshotId"], "previousStateSavedAs": 901,
                         "restored": len(self.missing), "removed": removed, "failed": []}
        if url.startswith(f"{pc.PB_API}/dns/preflight/{D}?intent="):
            self.intents.append(url.split("=", 1)[1])
            checks = [{"id": "nameservers-ours", "severity": "warning", "ok": True, "title": "Our nameservers answer"},
                      {"id": "dnssec-active", "severity": "blocker", "ok": False, "title": "DNSSEC is on",
                       "detail": "DS records are published", "next_action": "remove DNSSEC first"}]
            return 200, {"status": "SUCCESS", "intent": self.intents[-1], "blockers": ["dnssec-active"],
                         "warnings": [], "checks": checks}
        if url == f"{pc.PB_API}/dns/scan/{D}":
            self.scans += 1
            return 200, {"status": "SUCCESS", "domain": D, "recordCount": len(self.scanned), "records": self.scanned}
        if url == pc.PB_API + "/domain/listAll":
            self.keys.add(headers["X-API-Key"])
        if url == pc.PB_API + "/domain/listAll" and "domain" not in body:
            self.pages.append(body.get("start", 0))
            every = [{"domain": d, "tld": d.split(".", 1)[1]} for d in self.domains]
            page = every[body.get("start", 0):body.get("start", 0) + pb.LIST_PAGE]
            return 200, {"status": "SUCCESS", "count": len(page), "domains": page}
        if url == pc.PB_API + "/domain/listAll":
            self.lookups.append(body["domain"])
            held = [{"domain": d, "apiAccess": 1} for d in self.domains if d == body["domain"]]
            return 200, {"status": "SUCCESS", "count": len(held), "domains": held}
        return super().http(method, url, headers, body)

    def at(self, name, rtype):
        return sorted(r["content"] for r in self.records.values() if r["name"] == name and r["type"] == rtype)


class Porkbun(Run):
    module = pb
    argv0 = "porkbun.py"

    def argv(self, *argv):
        return [self.argv0, *argv]


class Create(Porkbun):
    def test_dry_run_shows_the_create_and_writes_nothing(self):
        apis = Account(porkbun_new_domain())
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1")
        self.assertEqual(code, 1)
        self.assertIn(f"CREATE   A     auth.{D}", out)
        self.assertIn("Dry run: nothing written", out)
        self.assertEqual(apis.writes, [])

    def test_apply_creates_the_record_and_verifies(self):
        apis = Account(porkbun_new_domain())
        code, out = self.run_script(apis, "create", "a", f"Auth.{D}.", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.at("auth", "A"), ["192.0.2.1"])
        self.assertIn("After apply: 0 rows not ok", out)

    def test_a_second_create_writes_nothing(self):
        apis = Account(porkbun_new_domain())
        self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        before = len(apis.writes)
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(apis.writes), before)

    def test_another_a_record_at_the_name_stays(self):
        apis = Account([{"name": "auth", "type": "A", "content": "192.0.2.1"}])
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.2", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.at("auth", "A"), ["192.0.2.1", "192.0.2.2"])

    def test_the_zone_is_the_longest_suffix_the_account_holds(self):
        apis = Account([])
        apis.domains = (D, f"sub.{D}")
        code, out = self.run_script(apis, "create", "A", f"a.b.sub.{D}", "192.0.2.1")
        self.assertIn(f"(zone sub.{D})", out)
        self.assertEqual(apis.lookups, [f"a.b.sub.{D}", f"b.sub.{D}", f"sub.{D}"])

    def test_the_apex_and_a_wildcard_are_names(self):
        apis = Account([])
        self.run_script(apis, "create", "A", D, "192.0.2.1", "--apply")
        self.run_script(apis, "create", "A", f"*.{D}", "192.0.2.2", "--apply")
        self.assertEqual(apis.at("", "A"), ["192.0.2.1"])
        self.assertEqual(apis.at("*", "A"), ["192.0.2.2"])

    def test_mx_takes_its_priority(self):
        apis = Account([])
        code, out = self.run_script(apis, "create", "MX", D, "mx1.example.net.", "--prio", "10", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual([(r["content"], r["prio"]) for r in apis.records.values()], [("mx1.example.net", 10)])

    def test_txt_content_with_spaces_and_a_dash_is_one_value(self):
        apis = Account([])
        code, out = self.run_script(apis, "create", "TXT", D, "--", "v=spf1", "-all", "--apply")
        self.assertEqual(code, 1, out)  # after `--`, "--apply" is content too: a dry run
        code, out = self.run_script(apis, "create", "TXT", D, "--apply", "--", "v=spf1", "-all")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.at("", "TXT"), ["v=spf1 -all"])

    def test_alias_content_is_compared_as_a_host(self):
        apis = Account([{"name": "", "type": "ALIAS", "content": "sites.example.net"}])
        code, out = self.run_script(apis, "create", "ALIAS", D, "Sites.Example.NET.", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.writes, [])

    def test_a_cname_beside_another_type_is_a_conflict(self):
        apis = Account([{"name": "auth", "type": "A", "content": "192.0.2.1"}])
        code, out = self.run_script(apis, "create", "CNAME", f"auth.{D}", "host.example.net", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT CNAME auth.example.com", out)
        self.assertIn("RFC 1034", out)
        self.assertEqual(apis.writes, [])

    def test_a_record_beside_a_cname_is_a_conflict(self):
        apis = Account([{"name": "auth", "type": "CNAME", "content": "host.example.net"}])
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("CNAME already at this name", out)
        self.assertEqual(apis.writes, [])

    def test_a_second_cname_is_a_conflict(self):
        apis = Account([{"name": "auth", "type": "CNAME", "content": "host.example.net"}])
        code, out = self.run_script(apis, "create", "CNAME", f"auth.{D}", "other.example.net", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("use update", out)
        self.assertEqual(apis.writes, [])


    def test_an_alias_below_the_apex_beside_another_type_is_a_conflict(self):
        apis = Account([{"name": "www", "type": "TXT", "content": "keep"}])
        code, out = self.run_script(apis, "create", "ALIAS", f"www.{D}", "sites.example.net", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("TXT already at this name (RFC 1034)", out)  # Porkbun serves it as a CNAME
        self.assertEqual(apis.writes, [])

    def test_an_identical_record_with_another_ttl_is_ok(self):
        apis = Account([{"name": "auth", "type": "A", "content": "192.0.2.1"}])
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--ttl", "300", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.writes, [])  # a ttl is changed with update, not by a second record


class Update(Porkbun):
    def test_update_edits_the_one_record_and_keeps_its_ttl(self):
        apis = Account([{"name": "auth", "type": "A", "content": "192.0.2.1"}])
        code, out = self.run_script(apis, "update", "A", f"auth.{D}", "192.0.2.2", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("was 192.0.2.1", out)
        self.assertEqual(apis.at("auth", "A"), ["192.0.2.2"])
        self.assertEqual([w[0] for w in apis.writes], ["edit"])

    def test_update_keeps_an_mx_priority_it_was_not_given(self):
        apis = Account([{"name": "", "type": "MX", "content": "mx1.example.net", "prio": 10}])
        code, out = self.run_script(apis, "update", "MX", D, "mx2.example.net", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual([(r["content"], r["prio"]) for r in apis.records.values()], [("mx2.example.net", 10)])

    def test_update_with_no_record_is_a_conflict(self):
        apis = Account([])
        code, out = self.run_script(apis, "update", "A", f"auth.{D}", "192.0.2.2", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("use create", out)
        self.assertEqual(apis.writes, [])

    def test_update_with_several_records_is_a_conflict(self):
        apis = Account([{"name": "auth", "type": "A", "content": "192.0.2.1"},
                        {"name": "auth", "type": "A", "content": "192.0.2.3"}])
        code, out = self.run_script(apis, "update", "A", f"auth.{D}", "192.0.2.2", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("2 A records at this name", out)
        self.assertEqual(apis.writes, [])

    def test_update_to_the_same_value_writes_nothing(self):
        apis = Account([{"name": "auth", "type": "A", "content": "192.0.2.1"}])
        code, out = self.run_script(apis, "update", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.writes, [])


    def test_match_edits_only_the_record_whose_value_starts_with_it(self):
        apis = Account([{"name": "", "type": "TXT", "content": "v=spf1 -all"},
                        {"name": "", "type": "TXT", "content": "site-verification=keep"}])
        code, out = self.run_script(apis, "update", "TXT", D, "v=spf1 include:_spf.example.net -all",
                                    "--match", "V=SPF1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.at("", "TXT"), ["site-verification=keep", "v=spf1 include:_spf.example.net -all"])
        self.assertEqual([w[0] for w in apis.writes], ["edit"])

    def test_without_match_several_records_name_the_option(self):
        apis = Account([{"name": "", "type": "TXT", "content": "v=spf1 -all"},
                        {"name": "", "type": "TXT", "content": "site-verification=keep"}])
        code, out = self.run_script(apis, "update", "TXT", D, "v=spf1 mx -all", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("pass --match", out)
        self.assertEqual(apis.writes, [])

    def test_a_match_no_record_starts_with_is_a_conflict(self):
        apis = Account([{"name": "", "type": "TXT", "content": "site-verification=keep"}])
        code, out = self.run_script(apis, "update", "TXT", D, "v=spf1 -all", "--match", "v=spf1", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("no TXT record starting v=spf1 at this name; use create", out)
        self.assertEqual(apis.writes, [])


class Delete(Porkbun):
    def setUp(self):
        super().setUp()
        self.apis = Account([{"name": "auth", "type": "A", "content": "192.0.2.1"},
                             {"name": "auth", "type": "A", "content": "192.0.2.2"},
                             {"name": "auth", "type": "TXT", "content": "keep"}])

    def test_delete_without_content_removes_every_record_of_the_type(self):
        code, out = self.run_script(self.apis, "delete", "A", f"auth.{D}", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.apis.at("auth", "A"), [])
        self.assertEqual(self.apis.at("auth", "TXT"), ["keep"])

    def test_delete_with_content_removes_only_that_record(self):
        code, out = self.run_script(self.apis, "delete", "A", f"auth.{D}", "192.0.2.2", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.apis.at("auth", "A"), ["192.0.2.1"])

    def test_delete_of_an_absent_record_is_ok(self):
        code, out = self.run_script(self.apis, "delete", "AAAA", f"auth.{D}", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("[absent]", out)
        self.assertEqual(self.apis.writes, [])

    def test_delete_dry_run_writes_nothing(self):
        code, out = self.run_script(self.apis, "delete", "A", f"auth.{D}")
        self.assertEqual(code, 1)
        self.assertEqual(out.count("DELETE"), 2)
        self.assertEqual(self.apis.writes, [])


class Domain(Porkbun):
    def setUp(self):
        super().setUp()
        self.apis = Account([{"name": "", "type": "A", "content": "192.0.2.1"},
                             {"name": "www", "type": "A", "content": "192.0.2.2"},
                             {"name": "www", "type": "TXT", "content": "keep"},
                             {"name": "api.www", "type": "A", "content": "192.0.2.3"},
                             {"name": "awww", "type": "A", "content": "192.0.2.4"}])

    def test_the_domain_lists_the_whole_zone_and_writes_nothing(self):
        code, out = self.run_script(self.apis, "domain", D)
        self.assertEqual(code, 0, out)
        self.assertIn(f"(zone {D}): 5", out)
        self.assertEqual(self.apis.writes, [])
        self.assertNotIn("preflight", out)

    def test_a_name_lists_itself_and_the_names_under_it_only(self):
        code, out = self.run_script(self.apis, "domain", f"www.{D}")
        self.assertEqual(code, 0, out)
        self.assertIn(f"api.www.{D}", out)
        self.assertNotIn("192.0.2.4", out)  # awww is not under www
        self.assertNotIn("192.0.2.1", out)  # nor is the apex
        self.assertIn(f"at www.{D} and under it (zone {D}): 3", out)

    def test_type_narrows_the_list(self):
        code, out = self.run_script(self.apis, "domain", f"www.{D}", "--type", "txt")
        self.assertEqual(code, 0, out)
        self.assertIn("TXT records", out)
        self.assertIn(": 1", out)

    def test_a_name_with_no_records_lists_none(self):
        code, out = self.run_script(self.apis, "domain", f"none.{D}")
        self.assertEqual(code, 0, out)
        self.assertIn(": 0", out)


class List(Porkbun):
    def test_every_domain_is_one_row_per_name_with_its_tlds(self):
        apis = Account([])
        apis.domains = ("example.net", "example.com", "other.org", "example.co.uk")
        code, out = self.run_script(apis, "list")
        self.assertEqual(code, 0, out)
        self.assertIn("Porkbun account: 4 domains under 2 names", out)
        self.assertIn("  example  co.uk, com, net", out)
        self.assertIn("  other    org", out)
        self.assertEqual(apis.writes, [])

    def test_every_page_is_read(self):
        apis = Account([])
        apis.domains = tuple(f"name{i}.example" for i in range(5))
        with mock.patch.object(pb, "LIST_PAGE", 2):
            code, out = self.run_script(apis, "list")
        self.assertEqual(apis.pages, [0, 2, 4])
        self.assertIn("5 domains under 5 names", out)

    def test_an_empty_account_lists_nothing(self):
        apis = Account([])
        apis.domains = ()
        code, out = self.run_script(apis, "list")
        self.assertEqual(code, 0, out)
        self.assertIn("0 domains under 0 names", out)


# What another DNS host publishes for D: a scan reports names as prefixes, "" for the apex.
ELSEWHERE = [
    {"name": "", "type": "A", "content": "192.0.2.10", "ttl": 300, "prio": None},
    {"name": "www", "type": "CNAME", "content": "example.com.", "ttl": 300, "prio": None},
    {"name": "", "type": "MX", "content": "mx1.example.net.", "ttl": 3600, "prio": 10},
    {"name": "", "type": "TXT", "content": '"v=spf1 include:_spf.example.net -all"', "ttl": 3600, "prio": None},
    {"name": "", "type": "NS", "content": "ns1.example.net.", "ttl": 3600, "prio": None},
    {"name": "", "type": "SOA", "content": "ns1.example.net. admin.example.net. 1 7200 900 1209600 300",
     "ttl": 3600, "prio": None},
]


class Capture(Porkbun):
    """A domain registered at Porkbun (parking placeholders, Porkbun's NS) served by another host."""

    def zone(self):
        return [r for r in porkbun_new_domain() if r["type"] in ("ALIAS", "CNAME", "NS")]

    def test_dry_run_lists_each_record_found_and_writes_nothing(self):
        apis = Account(self.zone(), scanned=ELSEWHERE)
        code, out = self.run_script(apis, "capture", D)
        self.assertEqual(code, 1, out)
        self.assertIn("4 records found at its live nameservers", out)
        for row in (f"CREATE   A     {D}", f"CREATE   CNAME www.{D}", f"CREATE   MX    {D}",
                    f"CREATE   TXT   {D}", f"DELETE   ALIAS {D}", f"DELETE   CNAME *.{D}"):
            self.assertIn(row, out)
        self.assertNotIn("ns1.example.net", out)  # NS and SOA describe the delegation
        self.assertIn("Dry run: nothing written", out)
        self.assertEqual(apis.writes, [])
        self.assertEqual(apis.scans, 1)

    def test_apply_copies_the_records_and_a_second_run_writes_nothing(self):
        apis = Account(self.zone(), scanned=ELSEWHERE)
        code, out = self.run_script(apis, "capture", D, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.at("", "A"), ["192.0.2.10"])
        self.assertEqual(apis.at("www", "CNAME"), ["example.com"])
        self.assertEqual(apis.at("", "MX"), ["mx1.example.net"])
        self.assertEqual(apis.at("", "TXT"), ["v=spf1 include:_spf.example.net -all"])
        self.assertEqual(apis.at("", "ALIAS"), [])  # the parking placeholders are gone
        self.assertEqual(apis.at("", "NS"), ["curitiba.porkbun.com", "salvador.porkbun.com"])
        self.assertEqual(apis.scans, 1)  # the re-read after apply re-plans the same scan
        writes = len(apis.writes)
        code, out = self.run_script(apis, "capture", D)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(apis.writes), writes)

    def test_a_record_porkbun_already_holds_is_ok(self):
        apis = Account([{"name": "", "type": "MX", "content": "mx1.example.net", "prio": 10}], scanned=ELSEWHERE)
        code, out = self.run_script(apis, "capture", D)
        self.assertIn(f"ok       MX    {D}", out)

    def test_another_spf_record_in_the_zone_is_a_conflict(self):
        zone = self.zone() + [{"name": "", "type": "TXT", "content": "v=spf1 -all"}]
        apis = Account(zone, scanned=ELSEWHERE)
        code, out = self.run_script(apis, "capture", D, "--apply")
        self.assertEqual(code, 1, out)
        self.assertIn("CONFLICT TXT", out)
        self.assertIn("another SPF record, v=spf1 -all", out)
        self.assertEqual(apis.at("", "A"), [])  # nothing is written at the apex
        self.assertEqual(apis.at("", "ALIAS"), ["pixie.porkbun.com"])
        self.assertEqual(apis.at("www", "CNAME"), ["example.com"])  # other names still are

    def test_a_record_found_pointing_at_the_parking_host_is_not_copied(self):
        apis = Account(self.zone(), scanned=[{"name": "*", "type": "CNAME", "content": "pixie.porkbun.com."},
                                             {"name": "", "type": "A", "content": "192.0.2.10"}])
        code, out = self.run_script(apis, "capture", D, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.at("*", "CNAME"), [])

    def test_a_record_the_scan_reports_twice_is_counted_once(self):
        apis = Account(self.zone(), scanned=ELSEWHERE + [dict(ELSEWHERE[0], ttl=600), ELSEWHERE[2]])
        code, out = self.run_script(apis, "capture", D)
        self.assertIn("4 records found", out)
        self.assertEqual(out.count(f"CREATE   A     {D}"), 1)

    def test_a_scan_that_finds_nothing_is_refused(self):
        apis = Account(self.zone(), scanned=[ELSEWHERE[4]])
        code, out = self.run_script(apis, "capture", D, "--apply")
        self.assertEqual(code, 2)
        self.assertIn("found no records", out)
        self.assertEqual(apis.writes, [])


LOST = "lost"  # an answer: Porkbun wrote, but the answer never came back


class Flaky(Account):
    """Account whose writes can be refused or go unanswered, and which replays an answer for a
    repeated Idempotency-Key as Porkbun does. `answers` maps (verb, content) to the answers a
    write gets, in order, before the fake's own; LOST writes and then raises Unreachable."""

    def __init__(self, records, answers=(), **kwargs):
        super().__init__(records, **kwargs)
        self.answers = {k: list(v) for k, v in dict(answers).items()}
        self.sent = []  # (method, path, Idempotency-Key or None), every request
        self.replies = {}

    def http(self, method, url, headers, body=None):
        path = url[len(pc.PB_API):]
        key = headers.get("Idempotency-Key")
        self.sent.append((method, path, key))
        if key in self.replies:
            return self.replies[key]
        parts = path.strip("/").split("/")
        if method == "POST" and parts[0] == "dns" and parts[1] in ("create", "edit", "delete"):
            content = body["content"] if parts[1] != "delete" else self.records.get(parts[3], {}).get("content")
            queue = self.answers.get((parts[1], content))
            if queue:
                answer = queue.pop(0)
                if answer == LOST:
                    self.replies[key] = super().http(method, url, headers, body)
                    raise pc.Unreachable("timed out")
                return answer
        reply = super().http(method, url, headers, body)
        if key:
            self.replies[key] = reply
        return reply

    def idempotency_keys(self, verb):
        return [k for m, path, k in self.sent if path.startswith(f"/dns/{verb}/")]


def error(status, code, message="refused", **fields):
    return status, {"status": "ERROR", "code": code, "message": message, **fields}


class Resends(Porkbun):
    def setUp(self):
        super().setUp()
        self.waits = []
        self.saved_sleep, pc.sleep = pc.sleep, self.waits.append

    def tearDown(self):
        pc.sleep = self.saved_sleep
        super().tearDown()

    def test_a_write_whose_answer_is_lost_is_resent_with_its_key_and_lands_once(self):
        apis = Flaky(porkbun_new_domain(), answers={("create", "192.0.2.1"): [LOST]})
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.at("auth", "A"), ["192.0.2.1"])
        keys = apis.idempotency_keys("create")
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(self.waits, [2])

    def test_each_write_gets_its_own_key_and_a_read_gets_none(self):
        apis = Flaky([{"name": "auth", "type": "A", "content": "192.0.2.9"}])
        code, out = self.run_script(apis, "update", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(set(apis.idempotency_keys("edit"))), 1)
        self.assertTrue(all(k is None for m, path, k in apis.sent if not path.startswith("/dns/edit/")))

    def test_a_rate_limit_waits_its_retry_after_and_resends(self):
        limited = error(429, "RATE_LIMITED", _retry_after="7")
        apis = Flaky(porkbun_new_domain(), answers={("create", "192.0.2.1"): [limited]})
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.waits, [7])

    def test_a_write_still_in_flight_or_marked_retryable_is_resent(self):
        apis = Flaky(porkbun_new_domain(), answers={("create", "192.0.2.1"): [
            error(409, "IDEMPOTENCY_KEY_IN_USE"), error(400, "DNS_READ_FAILED", next_action={"retryable": True})]})
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.waits, [2, 5])

    def test_a_write_is_given_up_after_three_resends(self):
        apis = Flaky(porkbun_new_domain(), answers={("create", "192.0.2.1"): [error(503, "UNAVAILABLE")] * 4})
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.waits, [2, 5, 10])
        self.assertIn("FAILED   A     auth.", out)
        self.assertEqual(apis.at("auth", "A"), [])

    def test_a_refusal_that_will_not_change_is_not_resent(self):
        apis = Flaky(porkbun_new_domain(), answers={("create", "192.0.2.1"): [error(400, "INVALID_TYPE")]})
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.waits, [])
        self.assertEqual(len(apis.idempotency_keys("create")), 1)


class RefusedWrites(Porkbun):
    def test_a_refusal_names_the_code_hint_records_in_the_way_and_request_id(self):
        conflict = error(400, "RECORD_CONFLICT", "a CNAME is exclusive", next_action={"hint": "delete the CNAME first"},
                         conflictingRecords=[{"id": "7", "name": f"auth.{D}", "type": "CNAME", "content": "x.example.net"}],
                         requestId="req-1")
        apis = Flaky(porkbun_new_domain(), answers={("create", "192.0.2.1"): [conflict]})
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 1, out)
        for part in ("RECORD_CONFLICT", "hint: delete the CNAME first", f"in the way: CNAME auth.{D} x.example.net",
                     "(request req-1)"):
            self.assertIn(part, out)

    def test_a_suggested_merge_is_named(self):
        spf = error(400, "SPF_CONFLICT", suggestedMerge={"content": "v=spf1 include:a include:b -all"})
        apis = Flaky(porkbun_new_domain(), answers={("create", "v=spf1 include:b -all"): [spf]})
        code, out = self.run_script(apis, "create", "TXT", D, "v=spf1 include:b -all", "--apply")
        self.assertIn("suggested: v=spf1 include:a include:b -all", out)

    def test_a_refused_delete_holds_its_name_and_the_other_names_are_written(self):
        old = {"name": "", "type": "MX", "content": "mx.old.example.net", "prio": 10}
        apis = Flaky([old], answers={("delete", "mx.old.example.net"): [error(400, "RECORD_LOCKED")]})
        pc.http = apis.http
        porkbun = pc.Porkbun("pk1_x", "sk1_x", D)
        new_mx = pc.Record("", "MX", "mx.new.example.net", 10, None)
        www = pc.Record("www", "A", "192.0.2.5", 0, None)
        requests = lambda zone: [*(pc.Request("delete", r, old=r) for r in zone if r.type == "MX"),
                                 pc.Request("create", new_mx), pc.Request("create", www)]
        with redirect_stdout(io.StringIO()) as out:
            drift = pc.converge(porkbun, "test", requests, True)
        self.assertTrue(drift)
        self.assertEqual(apis.at("", "MX"), ["mx.old.example.net"])  # the new MX is not added beside it
        self.assertEqual(apis.at("www", "A"), ["192.0.2.5"])
        self.assertIn("CREATE skipped: a write at this name was refused", out.getvalue())
        self.assertIn("DELETE refused: Porkbun", out.getvalue())


class DryRun(Porkbun):
    """A dry run sends each create to Porkbun with dryRun, which checks it and writes nothing."""

    def test_each_create_is_checked_by_porkbun_and_nothing_is_written(self):
        apis = Account(porkbun_new_domain())
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1")
        self.assertEqual(code, 1, out)
        self.assertIn("[Porkbun dry run: ok]", out)
        self.assertEqual(apis.rehearsals, [("auth", "A", "192.0.2.1")])
        self.assertEqual(apis.writes, [])

    def test_with_apply_no_dry_run_is_sent(self):
        apis = Account(porkbun_new_domain())
        self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(apis.rehearsals, [])

    def test_porkbuns_refusal_is_on_the_row(self):
        apis = Flaky(porkbun_new_domain(), answers={("create", "192.0.2.1"): [error(400, "ZONE_RECORD_LIMIT")]})
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1")
        self.assertEqual(code, 1, out)
        self.assertIn("Porkbun dry run: refuses: Porkbun /dns/create/example.com -> 400 ZONE_RECORD_LIMIT", out)
        self.assertEqual(apis.writes, [])

    def test_a_refusal_this_plans_delete_clears_says_so(self):
        apis = Flaky([{"name": "www", "type": "A", "content": "192.0.2.9"}],
                     answers={("create", "host.example.net"): [error(400, "RECORD_CONFLICT")]})
        pc.http = apis.http
        cname = pc.Record("www", "CNAME", "host.example.net", 0, None)
        requests = lambda zone: [*(pc.Request("delete", r, old=r) for r in zone), pc.Request("create", cname)]
        with redirect_stdout(io.StringIO()) as out:
            pc.converge(pc.Porkbun("pk1_x", "sk1_x", D), "test", requests, False)
        self.assertIn("this plan's delete or edit at the name runs first", out.getvalue())
        self.assertEqual(apis.writes, [])

    def test_an_answer_without_would_succeed_is_refused(self):
        answered = (200, {"status": "SUCCESS", "id": "900"})
        apis = Flaky(porkbun_new_domain(), answers={("create", "192.0.2.1"): [answered]})
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1")
        self.assertEqual(code, 2)
        self.assertIn("without wouldSucceed, so it may have written", out)


class Sandboxed(Account):
    """Account answering as Porkbun's sandbox does: every answer says sandbox: true."""

    marked = True

    def http(self, method, url, headers, body=None):
        status, parsed = super().http(method, url, headers, body)
        if isinstance(parsed, dict) and self.marked:
            parsed = {**parsed, "sandbox": True}
        return status, parsed


class Sandbox(Porkbun):
    def setUp(self):
        super().setUp()
        self.write_credentials("porkbun", "PORKBUN_API_KEY=pk1_sb_x\nPORKBUN_SECRET_API_KEY=sk1_sb_x\n")

    def test_a_sandbox_key_is_labelled_and_works(self):
        apis = Sandboxed(porkbun_new_domain())
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("SANDBOX: Porkbun's test system"), out)
        self.assertEqual(apis.at("auth", "A"), ["192.0.2.1"])

    def test_the_read_verbs_are_labelled_too(self):
        code, out = self.run_script(Sandboxed([]), "list")
        self.assertTrue(out.startswith("SANDBOX:"), out)

    def test_an_answer_not_marked_sandbox_is_refused(self):
        apis = Sandboxed(porkbun_new_domain())
        apis.marked = False
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("got an answer not marked sandbox", out)
        self.assertEqual(apis.writes, [])

    def test_a_live_key_is_not_labelled(self):
        self.write_credentials("porkbun", "PORKBUN_API_KEY=pk1_x\nPORKBUN_SECRET_API_KEY=sk1_x\n")
        code, out = self.run_script(Account([]), "list")
        self.assertNotIn("SANDBOX", out)


class RestorePoints(Porkbun):
    def setUp(self):
        super().setUp()
        self.apis = Account([{"name": "www", "type": "A", "content": "192.0.2.7"}])
        self.apis.points = [{"id": 1240, "takenAt": "2026-10-30 11:00:00", "reason": "before-create", "source": "auto",
                             "recordCount": 2, "matchesLive": True},
                            {"id": 1234, "takenAt": "2026-10-30 10:00:00", "reason": "before-delete", "source": "auto",
                             "recordCount": 1, "matchesLive": False}]
        self.apis.missing = [{"name": D, "type": "MX", "content": "mx.example.net", "ttl": 600, "prio": 10}]
        self.apis.extra = [{"name": f"www.{D}", "type": "A", "content": "192.0.2.7", "ttl": 600, "prio": None}]

    def test_history_lists_the_points_newest_first_and_writes_nothing(self):
        code, out = self.run_script(self.apis, "history", D)
        self.assertEqual(code, 0, out)
        self.assertIn(f"restore points for {D}, newest first: 2", out)
        self.assertLess(out.index("1240"), out.index("1234"))
        self.assertIn("(the live zone)", out)
        self.assertEqual(self.apis.writes, [])

    def test_a_restore_dry_run_shows_the_diff_and_porkbuns_dry_run_and_writes_nothing(self):
        code, out = self.run_script(self.apis, "restore", D, "1234")
        self.assertEqual(code, 1, out)
        self.assertIn(f"ADD      MX    {D}", out)
        self.assertIn("[added since; --prune deletes it]", out)
        self.assertIn("Porkbun dry run: adds 1, deletes 0", out)
        self.assertIn("Dry run: nothing written", out)
        self.assertEqual(self.apis.restores, [(1234, False, True)])
        self.assertEqual(self.apis.writes, [])

    def test_prune_deletes_what_was_added_since(self):
        code, out = self.run_script(self.apis, "restore", D, "1234", "--prune")
        self.assertIn(f"DELETE   A     www.{D}", out)
        self.assertIn("deletes 1", out)

    def test_apply_restores_and_names_the_point_that_undoes_it(self):
        code, out = self.run_script(self.apis, "restore", D, "1234", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.apis.restores, [(1234, False, False)])
        self.assertEqual(self.apis.at("", "MX"), ["mx.example.net"])
        self.assertIn(f"To undo this restore: porkbun.py restore {D} 901 --prune --apply", out)

    def test_a_dry_run_that_saved_the_zone_is_refused(self):
        self.apis.saved_as = 1250
        code, out = self.run_script(self.apis, "restore", D, "1234")
        self.assertEqual(code, 2)
        self.assertIn("may have restored", out)


class Check(Porkbun):
    def test_check_runs_the_preflight_for_the_intent_and_writes_nothing(self):
        apis = Account([])
        code, out = self.run_script(apis, "check", D, "--intent", "move-nameservers")
        self.assertEqual(code, 1, out)  # a blocker
        self.assertEqual(apis.intents, ["move-nameservers"])
        self.assertIn("blockers=dnssec-active", out)
        self.assertIn("BLOCKER  dnssec-active", out)
        self.assertIn("remove DNSSEC first", out)
        self.assertIn("ok       nameservers-ours", out)
        self.assertEqual(apis.writes, [])

    def test_the_intent_defaults_to_general(self):
        apis = Account([])
        self.run_script(apis, "check", D)
        self.assertEqual(apis.intents, ["general"])

    def test_an_unknown_intent_is_refused_before_any_call(self):
        apis = Account([])
        code, out = self.run_script(apis, "check", D, "--intent", "move")
        self.assertEqual(code, 2)
        self.assertEqual(apis.lookups, [])


class Help(Porkbun):
    def test_a_bare_call_prints_the_help_without_the_credentials(self):
        self.files["porkbun"].unlink()
        apis = Account([])
        code, out = self.run_script(apis)
        self.assertEqual(code, 2)
        for word in ("<verb> <TYPE> <name> [target]", "Examples", "update A", "delete A", " ".join(pb.TYPES)):
            self.assertIn(word, out)
        self.assertEqual(out, pb.help_text() + "\n")  # the help alone: no refusal, no credentials error
        self.assertEqual(apis.lookups, [])

    def test_a_bad_argument_is_named_before_the_credentials_are_read(self):
        self.files["porkbun"].unlink()
        code, out = self.run_script(Account([]), "create", "A", f"auth.{D}", "not-an-address")
        self.assertEqual(code, 2)
        self.assertIn("must be an IPv4 address", out)
        self.assertNotIn("credentials", out)


class Refusals(Porkbun):
    def test_a_name_the_account_does_not_hold_is_refused(self):
        apis = Account([])
        code, out = self.run_script(apis, "create", "A", "auth.other.org", "192.0.2.1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("no domain in Porkbun account default holds auth.other.org", out)

    def test_an_a_record_that_is_not_an_address_is_refused_before_any_call(self):
        apis = Account([])
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "3.222.107", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("must be an IPv4 address", out)
        self.assertEqual(apis.lookups, [])

    def test_create_without_content_is_refused(self):
        code, out = self.run_script(Account([]), "create", "A", f"auth.{D}")
        self.assertEqual(code, 2)
        self.assertIn("needs the record's content", out)

    def test_only_the_porkbun_credentials_are_needed(self):
        self.files["forwardemail"].unlink()
        code, out = self.run_script(Account([]), "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 0, out)

    def test_a_credentials_file_readable_by_others_is_refused(self):
        self.write_credentials("porkbun", "PORKBUN_API_KEY=a\nPORKBUN_SECRET_API_KEY=b\n", mode=0o644)
        code, out = self.run_script(Account([]), "create", "A", f"auth.{D}", "192.0.2.1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("readable by others", out)


class Accounts(Porkbun):
    """--account NAME reads ~/.config/porkbun/credentials.d/NAME instead of the default file."""

    def test_a_write_uses_the_accounts_keys_only(self):
        self.write_account("acme", "pk1_acme")
        apis = Account(porkbun_new_domain())
        code, out = self.run_script(apis, "create", "A", f"auth.{D}", "192.0.2.1", "--account", "acme", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.at("auth", "A"), ["192.0.2.1"])
        self.assertEqual(apis.keys, {"pk1_acme"})

    def test_the_account_may_come_before_or_after_the_verb(self):
        self.write_account("acme", "pk1_acme")
        for argv in (("--account", "acme", "list"), ("list", "--account", "acme")):
            apis = Account([])
            code, out = self.run_script(apis, *argv)
            self.assertEqual(code, 0, out)
            self.assertIn("Porkbun account acme: 1 domains under 1 names", out)
            self.assertEqual(apis.keys, {"pk1_acme"})

    def test_without_account_the_default_file_is_used(self):
        self.write_account("acme", "pk1_acme")
        apis = Account([])
        code, out = self.run_script(apis, "list")
        self.assertEqual(code, 0, out)
        self.assertIn("Porkbun account: 1 domains", out)
        self.assertEqual(apis.keys, {"pk1_x"})

    def test_a_name_the_default_does_not_hold_names_the_other_accounts(self):
        self.write_account("acme", "pk1_acme")
        self.write_account("beta", "pk1_beta")
        code, out = self.run_script(Account([]), "create", "A", "auth.other.org", "192.0.2.1")
        self.assertEqual(code, 2)
        self.assertIn("no domain in Porkbun account default holds auth.other.org; "
                      "other accounts: acme, beta (pass --account NAME)", out)

    def test_an_unknown_account_names_the_file_to_create_and_the_accounts(self):
        self.write_account("acme", "pk1_acme")
        apis = Account([])
        code, out = self.run_script(apis, "list", "--account", "nope")
        self.assertEqual(code, 2)
        self.assertIn(f"no Porkbun account nope: create {pc.PORKBUN_ACCOUNTS / 'nope'}", out)
        self.assertIn("(accounts: acme)", out)
        self.assertEqual(apis.keys, set())

    def test_an_account_name_that_is_not_a_file_name_is_refused(self):
        for bad in ("../credentials", ".hidden"):
            code, out = self.run_script(Account([]), "list", "--account", bad)
            self.assertEqual(code, 2)
            self.assertIn("--account takes letters, digits", out)

    def test_an_account_file_readable_by_others_is_refused(self):
        self.write_account("acme", "pk1_acme", mode=0o644)
        code, out = self.run_script(Account([]), "list", "--account", "acme")
        self.assertEqual(code, 2)
        self.assertIn("readable by others", out)


if __name__ == "__main__":
    unittest.main()


class SharedPlan(unittest.TestCase):
    """porkbun_client.plan and apply, which every Porkbun skill writes through."""

    def record(self, sub, rtype, content, rid):
        return pc.Record(sub, rtype, content, 0, 600, rid)

    def test_a_name_with_a_conflict_gets_no_writes(self):
        a, txt = self.record("www", "A", "192.0.2.1", "1"), self.record("www", "TXT", "keep", "2")
        steps = pc.plan([pc.Request("delete", a, old=a),
                         pc.Request("create", pc.Record("www", "CNAME", "host.example.net", 0, None))], [a, txt])
        self.assertEqual([s.action for s in steps], ["CONFLICT"])  # the A is not deleted without its CNAME

    def test_a_delete_given_the_record_removes_only_that_one(self):
        first, second = self.record("", "MX", "mx.example.net", "1"), self.record("", "MX", "mx.example.net", "2")
        steps = pc.plan([pc.Request("delete", second, old=second)], [first, second])
        self.assertEqual([(s.action, s.id) for s in steps], [("DELETE", "2")])

    def test_an_update_given_the_record_may_change_its_type(self):
        a = self.record("", "A", "192.0.2.1", "1")
        steps = pc.plan([pc.Request("update", pc.Record("", "ALIAS", "sites.example.net", 0, None), old=a)], [a])
        self.assertEqual([(s.action, s.record.type, s.id, s.note) for s in steps],
                         [("EDIT", "ALIAS", "1", "was A 192.0.2.1")])

    def test_apply_deletes_first_then_edits_then_creates(self):
        done = []
        porkbun = mock.Mock(delete=lambda i: done.append("delete"), edit=lambda i, r: done.append("edit"),
                            create=lambda r: done.append("create"))
        new = self.record("a", "A", "192.0.2.9", None)
        pc.apply(porkbun, [pc.Step("CREATE", new), pc.Step("EDIT", new, "1"), pc.Step("DELETE", new, "2")])
        self.assertEqual(done, ["delete", "edit", "create"])

