"""Tests for dot-claude/skills/porkbun. Run: python3 -m unittest test_porkbun

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude. Uses the Porkbun fake from test_configure_domain_email, plus the account's
domain list, and never touches the network or the real credentials files.
"""

import unittest

from test_configure_domain_email import D, FakeApis, Run, load, pc, porkbun_new_domain

pb = load("porkbun", "porkbun.py")


class Account(FakeApis):
    """FakeApis plus /domain/listAll, which answers an exact `domain` match with 0 or 1 domain."""

    domains = (D,)

    def __init__(self, records, **kwargs):
        super().__init__(records, **kwargs)
        self.lookups = []

    def http(self, method, url, headers, body=None):
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


class List(Porkbun):
    def setUp(self):
        super().setUp()
        self.apis = Account([{"name": "", "type": "A", "content": "192.0.2.1"},
                             {"name": "www", "type": "A", "content": "192.0.2.2"},
                             {"name": "www", "type": "TXT", "content": "keep"},
                             {"name": "api.www", "type": "A", "content": "192.0.2.3"},
                             {"name": "awww", "type": "A", "content": "192.0.2.4"}])

    def test_the_domain_lists_the_whole_zone_and_writes_nothing(self):
        code, out = self.run_script(self.apis, "list", D)
        self.assertEqual(code, 0, out)
        self.assertIn(f"(zone {D}): 5", out)
        self.assertEqual(self.apis.writes, [])
        self.assertNotIn("preflight", out)

    def test_a_name_lists_itself_and_the_names_under_it_only(self):
        code, out = self.run_script(self.apis, "list", f"www.{D}")
        self.assertEqual(code, 0, out)
        self.assertIn(f"api.www.{D}", out)
        self.assertNotIn("192.0.2.4", out)  # awww is not under www
        self.assertNotIn("192.0.2.1", out)  # nor is the apex
        self.assertIn(f"at www.{D} and under it (zone {D}): 3", out)

    def test_type_narrows_the_list(self):
        code, out = self.run_script(self.apis, "list", f"www.{D}", "--type", "txt")
        self.assertEqual(code, 0, out)
        self.assertIn("TXT records", out)
        self.assertIn(": 1", out)

    def test_a_name_with_no_records_lists_none(self):
        code, out = self.run_script(self.apis, "list", f"none.{D}")
        self.assertEqual(code, 0, out)
        self.assertIn(": 0", out)


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
        self.assertIn("no domain in this Porkbun account holds auth.other.org", out)

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


if __name__ == "__main__":
    unittest.main()
