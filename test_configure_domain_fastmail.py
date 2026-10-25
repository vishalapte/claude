"""Tests for dot-claude/skills/enine-porkbun-fastmail. Run: python3 -m unittest test_configure_domain_fastmail

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude. Uses the Porkbun fake from test_configure_domain_email and never touches
the network or the real credentials files.
"""

import unittest

from test_configure_domain_email import D, FakeApis, Run, load, porkbun_new_domain

fm = load("enine-porkbun-fastmail", "fm_porkbun.py")
DKIM = [f"fm{n}.{D}.dkim.fmhosted.com" for n in (1, 2, 3)]


def mx(apis):
    return sorted((r["content"], r["prio"]) for r in apis.records.values() if r["type"] == "MX")


class Fastmail(Run):
    module = fm
    argv0 = "fm_porkbun.py"


class DryRun(Fastmail):
    def test_pending_changes_are_shown_and_nothing_is_written(self):
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis)
        self.assertEqual(code, 1)
        for n in (1, 2, 3):
            self.assertIn(f"CREATE   CNAME fm{n}._domainkey.{D}", out)
        self.assertIn("merged SPF", out)
        self.assertIn("Dry run: nothing written", out)
        self.assertEqual(apis.writes, [])

    def test_the_steps_at_fastmail_are_printed(self):
        code, out = self.run_script(FakeApis(porkbun_new_domain()))
        self.assertIn(f"Settings > Domains > Add domain: {D}", out)
        self.assertIn("Fastmail sends only", out)
        self.assertIn(f"dkim=pass header.d={D}", out)


class Apply(Fastmail):
    def setUp(self):
        super().setUp()
        self.apis = FakeApis(porkbun_new_domain())
        self.code, self.out = self.run_script(self.apis, "--apply")

    def test_converges(self):
        self.assertEqual(self.code, 0, self.out)
        self.assertIn("After apply: 0 rows not ok", self.out)

    def test_each_dkim_name_holds_its_cname(self):
        for n, target in zip((1, 2, 3), DKIM):
            self.assertEqual(self.apis.find("CNAME", f"fm{n}._domainkey"), [target])

    def test_spf_gains_the_include_and_keeps_the_rest(self):
        txt = self.apis.find("TXT")
        self.assertIn("v=spf1 include:spf.messagingengine.com include:_spf.porkbun.com ~all", txt)
        self.assertIn("google-site-verification=keepme", txt)

    def test_the_mx_are_left_alone(self):
        self.assertEqual(mx(self.apis), [("fwd1.porkbun.com", 10), ("fwd2.porkbun.com", 20)])

    def test_parking_placeholders_are_deleted_and_ns_kept(self):
        self.assertNotIn("pixie.porkbun.com", [r["content"] for r in self.apis.records.values()])
        self.assertEqual(self.apis.find("NS"), ["curitiba.porkbun.com", "salvador.porkbun.com"])

    def test_a_second_apply_writes_nothing(self):
        before = len(self.apis.writes)
        code, out = self.run_script(self.apis, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.apis.writes), before)


class Mx(Fastmail):
    def test_mx_moves_the_apex_mx_in_place(self):
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis, "--mx", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(mx(apis), [("in1-smtp.messagingengine.com", 10), ("in2-smtp.messagingengine.com", 20)])
        self.assertEqual([w[0] for w in apis.writes if w[-1].endswith("messagingengine.com") and "smtp" in w[-1]],
                         ["edit", "edit"])
        self.assertNotIn("sends only", out)

    def test_an_extra_mx_is_deleted(self):
        apis = FakeApis(porkbun_new_domain() + [{"name": "", "type": "MX", "content": "fwd3.porkbun.com", "prio": 30}])
        code, out = self.run_script(apis, "--mx", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(mx(apis)), 2)


class Records(Fastmail):
    def test_with_no_spf_fastmails_own_is_created(self):
        apis = FakeApis([r for r in porkbun_new_domain() if not r["content"].startswith("v=spf1")])
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn(fm.SPF, apis.find("TXT"))

    def test_a_stale_dkim_cname_is_edited_in_place(self):
        apis = FakeApis([{"name": "fm1._domainkey", "type": "CNAME", "content": "fm1.old.example.net"}])
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.find("CNAME", "fm1._domainkey"), [DKIM[0]])
        self.assertIn("was fm1.old.example.net", out)

    def test_a_txt_at_a_dkim_name_is_a_conflict_and_the_rest_is_written(self):
        apis = FakeApis(porkbun_new_domain() + [{"name": "fm1._domainkey", "type": "TXT", "content": "v=DKIM1; p=x"}])
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 1)
        self.assertIn(f"CONFLICT CNAME fm1._domainkey.{D}", out)
        self.assertEqual(apis.find("CNAME", "fm1._domainkey"), [])
        self.assertEqual(apis.find("CNAME", "fm2._domainkey"), [DKIM[1]])

    def test_two_spf_records_are_a_conflict(self):
        apis = FakeApis(porkbun_new_domain() + [{"name": "", "type": "TXT", "content": "v=spf1 mx -all"}])
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("2 SPF records at the apex", out)


class Accounts(Fastmail):
    def test_only_the_porkbun_credentials_are_needed(self):
        self.files["forwardemail"].unlink()
        code, out = self.run_script(FakeApis(porkbun_new_domain()), "--apply")
        self.assertEqual(code, 0, out)

    def test_account_reads_its_own_porkbun_file(self):
        self.write_account("acme", "pk1_acme")
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis, "--account", "acme", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(apis.keys, {"pk1_acme"})


if __name__ == "__main__":
    unittest.main()
