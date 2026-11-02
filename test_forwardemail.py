"""Tests for dot-claude/skills/enine-forwardemail. Run: python3 -m unittest test_forwardemail

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude.

Every test runs against an in-memory Forward Email (FakeForwardEmail replaces the one HTTP
function, in porkbun_client.py) and never touches the network or the real credentials file.
"""

import contextlib
import io
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from test_configure_domain_email import load

fe = load("enine-forwardemail", "forwardemail.py")
fc = sys.modules["forwardemail_client"]
pc = sys.modules["porkbun_client"]
D = "example.com"


class FakeForwardEmail:
    """Forward Email's domains and aliases, enough for the script; `writes` logs every write."""

    def __init__(self, domains=(D,), aliases=()):
        self.domains = {name: {"id": f"d-{name}", "name": name, "plan": "team", "has_mx_record": True,
                               "has_txt_record": True, "ignore_mx_check": False, "retention_days": 0,
                               "smtp_port": "25"} for name in domains}
        self.aliases = {}
        for i, (address, recipients, extra) in enumerate(aliases):
            name, _, domain = address.partition("@")
            self.aliases[f"a{i}"] = {"id": f"a{i}", "name": name, "domain": {"name": domain},
                                     "recipients": list(recipients), "is_enabled": True, "has_imap": False,
                                     "vacation_responder": {"is_enabled": False}, **extra}
        self.next_id = 100
        self.writes = []
        self.gets = []

    def http(self, method, url, headers, body=None):
        assert url.startswith(fc.FE_API) and headers["Authorization"].startswith("Basic ")
        path, _, query = url[len(fc.FE_API):].partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/")]
        if method == "GET":
            self.gets.append(path)
        if parts == ["domains"] and method == "GET":
            return 200, self.page(list(self.domains.values()), params)
        if parts == ["domains"] and method == "POST":
            self.writes.append(("create-domain", body))
            self.domains[body["domain"]] = {"id": "d-new", "name": body["domain"], "plan": body.get("plan", "free"),
                                            "ignore_mx_check": False}
            return 200, self.domains[body["domain"]]
        domain = self.domains.get(parts[1]) if len(parts) > 1 else None
        if domain is None:
            return 404, {"message": "Domain does not exist"}
        if len(parts) == 2:
            if method == "GET":
                return 200, domain
            if method == "PUT":
                self.writes.append(("update-domain", parts[1], body))
                domain.update(body)
                return 200, domain
            if method == "DELETE":
                self.writes.append(("delete-domain", parts[1]))
                del self.domains[parts[1]]
                return 200, domain
        held = {k: a for k, a in self.aliases.items() if a["domain"]["name"] == parts[1]}
        if len(parts) == 3 and method == "GET":
            return 200, self.page(list(held.values()), params)
        if len(parts) == 3 and method == "POST":
            if any(a["name"] == body["name"] for a in held.values()):
                return 400, {"message": "Alias already exists for domain."}
            self.writes.append(("create-alias", parts[1], body))
            alias_id = f"a{self.next_id}"
            self.next_id += 1
            self.aliases[alias_id] = {"id": alias_id, "domain": {"name": parts[1]}, "is_enabled": True,
                                      "has_imap": False, **body}
            return 200, self.aliases[alias_id]
        if len(parts) == 4 and method == "GET":
            found = [a for a in held.values() if parts[3] in (a["id"], a["name"])]
            return (200, found[0]) if found else (404, {"message": "Alias does not exist."})
        if len(parts) == 4 and parts[3] in held:
            if method == "PUT":
                self.writes.append(("update-alias", parts[3], body))
                self.aliases[parts[3]].update({k: v for k, v in body.items() if k != "description"})
                return 200, self.aliases[parts[3]]
            if method == "DELETE":
                self.writes.append(("delete-alias", parts[3]))
                return 200, self.aliases.pop(parts[3])
        raise AssertionError(f"unexpected Forward Email call {method} {url}")

    @staticmethod
    def page(items, params):
        """A page as the API returns one, by `page` and `limit`."""
        limit, page = int(params.get("limit", 10)), int(params.get("page", 1))
        return items[(page - 1) * limit:page * limit]


class Run(unittest.TestCase):
    """Base: the credentials file in a temporary home, and a call through main()."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.credentials = self.home / "forwardemail" / "credentials"
        self.credentials.parent.mkdir(parents=True)
        self.credentials.write_text("FORWARDEMAIL_API_KEY=fe\n")
        self.credentials.chmod(0o600)
        self.saved = (pc.http, fc.CREDENTIALS)
        fc.CREDENTIALS = {self.credentials: ("FORWARDEMAIL_API_KEY",)}

    def tearDown(self):
        pc.http, fc.CREDENTIALS = self.saved

    def run_script(self, api, *argv):
        pc.http = api.http
        out = io.StringIO()
        saved_argv, sys.argv = sys.argv, ["forwardemail.py", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                with self.assertRaises(SystemExit) as exit_:
                    fe.main()
        finally:
            sys.argv = saved_argv
        return exit_.exception.code, out.getvalue()


class Read(Run):
    def test_list_prints_every_domain_across_pages(self):
        api = FakeForwardEmail(domains=[f"d{i:03}.example" for i in range(fc.PAGE + 3)])
        code, out = self.run_script(api, "list")
        self.assertEqual(code, 0, out)
        self.assertIn(f"{fc.PAGE + 3} domains", out)
        self.assertIn("d052.example", out)
        self.assertEqual(api.writes, [])

    def test_domain_prints_its_settings_and_aliases(self):
        api = FakeForwardEmail(aliases=[("info@example.com", ["alice@example.net"], {}),
                                        ("box@example.com", ["bob@example.net"], {"has_imap": True})])
        code, out = self.run_script(api, "domain", D)
        self.assertEqual(code, 0, out)
        self.assertIn("ignore_mx_check", out)
        self.assertIn("info@example.com -> alice@example.net", out)
        self.assertIn("box@example.com -> bob@example.net  [mailbox]", out)

    def test_domain_not_on_the_account_is_refused(self):
        code, out = self.run_script(FakeForwardEmail(domains=()), "domain", D)
        self.assertEqual(code, 2)
        self.assertIn("not on the Forward Email account", out)


class Domains(Run):
    def test_create_is_a_dry_run_without_apply(self):
        api = FakeForwardEmail(domains=())
        code, out = self.run_script(api, "create", "domain", D, "--set", "catchall=false")
        self.assertEqual(code, 1)
        self.assertIn("CREATE   domain example.com  [catchall=False]", out)
        self.assertIn("Dry run: nothing written", out)
        self.assertEqual(api.writes, [])

    def test_create_with_apply_sends_the_values_and_names_the_dns_skill(self):
        api = FakeForwardEmail(domains=())
        code, out = self.run_script(api, "create", "domain", D, "--set", "catchall=false", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(api.writes, [("create-domain", {"domain": D, "catchall": False})])
        self.assertIn("/enine-porkbun-forwardemail example.com", out)
        self.assertIn("not read back: catchall", out)

    def test_create_of_a_held_domain_with_the_same_values_is_ok(self):
        api = FakeForwardEmail()
        code, out = self.run_script(api, "create", "domain", D, "--set", "plan=team", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("ok       domain example.com", out)
        self.assertEqual(api.writes, [])

    def test_create_of_a_held_domain_with_other_values_is_a_conflict(self):
        api = FakeForwardEmail()
        code, out = self.run_script(api, "create", "domain", D, "--set", "plan=free", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT domain example.com  [exists with other values; update changes it]", out)
        self.assertIn("plan: 'team' -> 'free'", out)
        self.assertEqual(api.writes, [])

    def test_update_sends_only_the_values_given_and_shows_the_old(self):
        api = FakeForwardEmail()
        code, out = self.run_script(api, "update", "domain", D, "--set", "ignore_mx_check=true", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("ignore_mx_check: False -> True", out)
        self.assertEqual(api.writes, [("update-domain", D, {"ignore_mx_check": True})])
        self.assertIn("After apply: 0 rows not ok", out)

    def test_a_second_update_writes_nothing(self):
        api = FakeForwardEmail()
        self.run_script(api, "update", "domain", D, "--set", "ignore_mx_check=true", "--apply")
        code, out = self.run_script(api, "update", "domain", D, "--set", "ignore_mx_check=true", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(api.writes), 1)

    def test_update_of_a_domain_not_held_is_a_conflict(self):
        code, out = self.run_script(FakeForwardEmail(domains=()), "update", "domain", D, "--set", "retention_days=7")
        self.assertEqual(code, 1)
        self.assertIn("not on the account; use create", out)

    def test_delete_says_how_many_aliases_the_domain_holds(self):
        api = FakeForwardEmail(aliases=[("info@example.com", ["a@example.net"], {}),
                                        ("sales@example.com", ["b@example.net"], {})])
        code, out = self.run_script(api, "delete", "domain", D)
        self.assertEqual(code, 1)
        self.assertIn("DELETE   domain example.com  [holds 2 aliases]", out)
        self.assertEqual(api.writes, [])

    def test_delete_with_apply_removes_it_and_a_second_run_is_ok(self):
        api = FakeForwardEmail()
        code, out = self.run_script(api, "delete", "domain", D, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(api.writes, [("delete-domain", D)])
        code, out = self.run_script(api, "delete", "domain", D, "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("not on the account", out)


class Aliases(Run):
    def test_create_sends_the_name_and_recipients(self):
        api = FakeForwardEmail()
        code, out = self.run_script(api, "create", "alias", "info@example.com", "alice@example.net", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(api.writes, [("create-alias", D, {"name": "info", "recipients": ["alice@example.net"]})])

    def test_create_of_an_identical_alias_is_ok(self):
        api = FakeForwardEmail(aliases=[("info@example.com", ["alice@example.net"], {})])
        code, out = self.run_script(api, "create", "alias", "info@example.com", "alice@example.net", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(api.writes, [])

    def test_create_of_an_alias_with_other_recipients_is_a_conflict(self):
        api = FakeForwardEmail(aliases=[("info@example.com", ["alice@example.net"], {})])
        code, out = self.run_script(api, "create", "alias", "info@example.com", "bob@example.net", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("update changes it", out)
        self.assertIn("recipients: ['alice@example.net'] -> ['bob@example.net']", out)
        self.assertEqual(api.writes, [])

    def test_update_replaces_the_recipients_by_the_aliass_id(self):
        api = FakeForwardEmail(aliases=[("info@example.com", ["alice@example.net"], {})])
        code, out = self.run_script(api, "update", "alias", "info@example.com",
                                    "bob@example.net", "carol@example.net", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(api.writes, [("update-alias", "a0", {"recipients": ["bob@example.net", "carol@example.net"]})])

    def test_update_reads_vacation_fields_from_the_nested_object(self):
        api = FakeForwardEmail(aliases=[("info@example.com", ["alice@example.net"], {})])
        code, out = self.run_script(api, "update", "alias", "info@example.com",
                                    "--set", "vacation_responder_is_enabled=false")
        self.assertEqual(code, 0, out)
        self.assertIn("ok       alias  info@example.com", out)

    def test_a_field_not_read_back_is_sent_and_named(self):
        api = FakeForwardEmail(aliases=[("info@example.com", ["alice@example.net"], {})])
        code, out = self.run_script(api, "update", "alias", "info@example.com", "--set", "description=Front desk",
                                    "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("not read back: description", out)
        self.assertEqual(api.writes, [("update-alias", "a0", {"description": "Front desk"})])

    def test_update_of_a_missing_alias_is_a_conflict(self):
        code, out = self.run_script(FakeForwardEmail(), "update", "alias", "info@example.com", "bob@example.net")
        self.assertEqual(code, 1)
        self.assertIn("no such alias; use create", out)

    def test_delete_says_the_alias_has_a_mailbox(self):
        api = FakeForwardEmail(aliases=[("box@example.com", ["bob@example.net"], {"has_imap": True})])
        code, out = self.run_script(api, "delete", "alias", "box@example.com")
        self.assertEqual(code, 1)
        self.assertIn("[forwards to bob@example.net; has a mailbox]", out)
        self.assertEqual(api.writes, [])

    def test_delete_with_apply_removes_it_by_id(self):
        api = FakeForwardEmail(aliases=[("info@example.com", ["alice@example.net"], {})])
        code, out = self.run_script(api, "delete", "alias", "info@example.com", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(api.writes, [("delete-alias", "a0")])

    def test_the_catch_all_is_addressed_as_star(self):
        api = FakeForwardEmail(aliases=[("*@example.com", ["alice@example.net"], {})])
        code, out = self.run_script(api, "delete", "alias", "*@example.com")
        self.assertEqual(code, 1)
        self.assertIn("DELETE   alias  *@example.com", out)

    def test_a_refused_write_is_a_failed_row(self):
        api = FakeForwardEmail(domains=())
        code, out = self.run_script(api, "create", "alias", "info@example.com", "alice@example.net", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("FAILED", out)
        self.assertIn("Domain does not exist", out)


class Refusals(Run):
    def assert_refused_before_any_call(self, *argv, says):
        api = FakeForwardEmail()
        self.credentials.unlink()  # a call would need it
        code, out = self.run_script(api, *argv)
        self.assertEqual(code, 2, out)
        self.assertIn(says, out)
        self.assertEqual(api.gets + api.writes, [])

    def test_an_unknown_field_is_refused(self):
        self.assert_refused_before_any_call("update", "domain", D, "--set", "colour=blue", says="--set takes K=V")

    def test_a_create_only_field_is_refused_on_update(self):
        self.assert_refused_before_any_call("update", "domain", D, "--set", "plan=free", says="--set takes K=V")

    def test_an_alias_without_a_domain_is_refused(self):
        self.assert_refused_before_any_call("create", "alias", "info", "a@example.net", says="name@domain")

    def test_update_with_nothing_to_change_is_refused(self):
        self.assert_refused_before_any_call("update", "alias", "info@example.com", says="needs a value")

    def test_delete_with_values_is_refused(self):
        self.assert_refused_before_any_call("delete", "alias", "info@example.com", "a@example.net",
                                            says="delete takes no values")

    def test_a_missing_key_is_refused(self):
        self.credentials.write_text("# nothing\n")
        code, out = self.run_script(FakeForwardEmail(), "list")
        self.assertEqual(code, 2)
        self.assertIn("must set FORWARDEMAIL_API_KEY", out)

    def test_a_bare_call_prints_the_help(self):
        code, out = self.run_script(FakeForwardEmail())
        self.assertEqual(code, 2)
        self.assertIn("ignore_mx_check", out)
        self.assertIn("Verbs", out)


if __name__ == "__main__":
    unittest.main()
