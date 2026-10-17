"""Tests for dot-claude/skills/enine-configure-domain-eninesites. Run: python3 -m unittest test_configure_domain_eninesites

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude. Uses the Porkbun fake from test_configure_domain_email and never touches
the network or the real credentials files.
"""

import unittest

from test_configure_domain_email import D, FakeApis, Run, load, porkbun_new_domain

ce = load("enine-configure-domain-eninesites", "configure_eninesites.py")


def web(apis, name):
    return sorted((r["type"], r["content"]) for r in apis.records.values()
                  if r["name"] == name and r["type"] in ce.WEB_TYPES)


class Eninesites(Run):
    module = ce
    argv0 = "configure_eninesites.py"

    def test_dry_run_shows_the_plan_and_writes_nothing(self):
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis)
        self.assertEqual(code, 1)
        self.assertIn("Dry run: nothing written", out)
        self.assertEqual(apis.writes, [])

    def test_a_new_domain_points_apex_and_www_at_eninesites(self):
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(web(apis, ""), [("ALIAS", "eninesites.com")])
        self.assertEqual(web(apis, "www"), [("ALIAS", "eninesites.com")])
        self.assertNotIn("pixie.porkbun.com", [r["content"] for r in apis.records.values()])

    def test_mail_and_ns_records_are_untouched(self):
        apis = FakeApis(porkbun_new_domain())
        self.run_script(apis, "--apply")
        self.assertEqual(apis.find("MX"), ["fwd1.porkbun.com", "fwd2.porkbun.com"])
        self.assertIn("google-site-verification=keepme", apis.find("TXT"))
        self.assertEqual(apis.find("NS"), ["curitiba.porkbun.com", "salvador.porkbun.com"])

    def test_to_overrides_the_target(self):
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis, "--to", "Sites.Example.NET.", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(web(apis, ""), [("ALIAS", "sites.example.net")])
        self.assertEqual(web(apis, "www"), [("ALIAS", "sites.example.net")])

    def test_existing_web_records_are_replaced_by_one_alias(self):
        apis = FakeApis([
            {"name": "", "type": "A", "content": "1.2.3.4"},
            {"name": "", "type": "AAAA", "content": "2001:db8::1"},
            {"name": "www", "type": "CNAME", "content": "old.example.net"},
        ])
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("was A 1.2.3.4", out)
        self.assertEqual(web(apis, ""), [("ALIAS", "eninesites.com")])
        self.assertEqual(web(apis, "www"), [("ALIAS", "eninesites.com")])

    def test_a_second_apply_writes_nothing(self):
        apis = FakeApis(porkbun_new_domain())
        self.run_script(apis, "--apply")
        before = len(apis.writes)
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(apis.writes), before)

    def test_www_holding_another_type_is_a_conflict(self):
        apis = FakeApis([{"name": "www", "type": "TXT", "content": "keep"}])
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT ALIAS www.example.com", out)
        self.assertEqual(web(apis, "www"), [])
        self.assertEqual(web(apis, ""), [("ALIAS", "eninesites.com")])

    def test_an_address_as_target_is_refused(self):
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis, "--to", "192.0.2.1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("must be a host name", out)
        self.assertEqual(apis.writes, [])

    def test_the_domain_itself_as_target_is_refused(self):
        code, out = self.run_script(FakeApis([]), "--to", f"www.{D}", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("at itself", out)

    def test_only_the_porkbun_credentials_are_needed(self):
        self.files["forwardemail"].unlink()
        code, out = self.run_script(FakeApis(porkbun_new_domain()), "--apply")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
