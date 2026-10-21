"""Tests for dot-claude/skills/enine-configure-domain-email-via-forwardemail. Run: python3 -m unittest test_configure_domain_email

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude.

Every test runs against in-memory Forward Email and Porkbun fakes (FakeApis replaces the
one HTTP function, in porkbun_client.py) and never touches the network or the real credentials files.
The zone file is written in the format of helpers/get-zone-file.js in forwardemail.net.
test_configure_domain_eninesites imports FakeApis and load() from here.
"""

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parent / "dot-claude" / "skills"


def load(name, filename):
    """Import a skill script by path, as the skill runs it.

    Registered in sys.modules, so a script that imports it by name (`import fe_porkbun`) gets
    this same module and the fakes patched into it.
    """
    spec = importlib.util.spec_from_file_location(filename[:-3], SKILLS / name / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fp = load("enine-configure-domain-email-via-forwardemail", "fe_porkbun.py")
pc = sys.modules["porkbun_client"]  # the Porkbun client fe_porkbun imports: one http for both APIs
D = "example.com"
DKIM = "v=DKIM1; k=rsa; p=" + "A" * 392 + ";"


def quoted(value):
    """TXT data as get-zone-file.js writes it: quoted 255-character strings."""
    return " ".join('"%s"' % value[i:i + 255] for i in range(0, len(value), 255))


ZONE = f"""; DNS records for {D} from Forward Email (https://forwardemail.net)
; Existing MX records for this name must be removed.
{D}.  3600 IN MX    0 mx1.forwardemail.net.
{D}.  3600 IN MX    0 mx2.forwardemail.net.
{D}.  3600 IN TXT   {quoted('forward-email-site-verification=AbCdEf1234')}

; SPF: a name has one SPF record, so if you already have one, add include:spf.forwardemail.net to it instead
{D}.  3600 IN TXT   {quoted('v=spf1 include:spf.forwardemail.net -all')}

; DKIM
fe-a1b2c3._domainkey.{D}.  3600 IN TXT   {quoted(DKIM)}
fe-bounces.{D}.  3600 IN CNAME forwardemail.net.
_dmarc.{D}.  3600 IN TXT   {quoted('v=DMARC1; p=reject; pct=100; rua=mailto:dmarc-abc@forwardemail.net;')}
autoconfig.{D}.  3600 IN CNAME autoconfig.forwardemail.net.
_submission._tcp.{D}.  3600 IN SRV   5 1 587 smtp.forwardemail.net.
"""
WANTED = 9


def porkbun_new_domain():
    """What Porkbun holds for a newly registered domain, plus its forwarding mail records."""
    return [
        {"name": "", "type": "ALIAS", "content": "pixie.porkbun.com"},
        {"name": "*", "type": "CNAME", "content": "pixie.porkbun.com"},
        {"name": "", "type": "NS", "content": "curitiba.porkbun.com"},
        {"name": "", "type": "NS", "content": "salvador.porkbun.com"},
        {"name": "", "type": "MX", "content": "fwd1.porkbun.com", "prio": 10},
        {"name": "", "type": "MX", "content": "fwd2.porkbun.com", "prio": 20},
        {"name": "", "type": "TXT", "content": "v=spf1 include:_spf.porkbun.com ~all"},
        {"name": "", "type": "TXT", "content": "google-site-verification=keepme"},
        {"name": "_dmarc", "type": "TXT", "content": "v=DMARC1; p=none"},
    ]


class FakeApis:
    """Forward Email and Porkbun, enough of each for the scripts; `writes` logs every write."""

    def __init__(self, records, fe_exists=False, api_access=True):
        self.records = {str(i): dict(r) for i, r in enumerate(records, 100)}
        self.next_id = 500
        self.fe_exists = fe_exists
        self.api_access = api_access
        self.writes = []

    def find(self, rtype, name=""):
        return sorted(r["content"] for r in self.records.values() if r["type"] == rtype and r["name"] == name)

    def http(self, method, url, headers, body=None):
        if url.startswith(fp.FE_API):
            return self.forward_email(method, url[len(fp.FE_API):], body)
        if not self.api_access:
            return 400, {"status": "ERROR", "code": "API_ACCESS_DISABLED", "message": "not opted in"}
        parts = url[len(pc.PB_API):].strip("/").split("/")
        if method == "POST":
            assert body["apikey"] and body["secretapikey"]
        if parts[:2] == ["dns", "retrieve"]:
            return 200, {"status": "SUCCESS", "records": [self.as_porkbun(i, r) for i, r in self.records.items()]}
        if parts[:2] == ["dns", "preflight"]:
            spf = [c for c in self.find("TXT") if c.startswith("v=spf1")]
            return 200, {"status": "SUCCESS", "blockers": ["spf-duplicate"] if len(spf) > 1 else [], "warnings": []}
        if parts[:2] == ["dns", "create"]:
            self.writes.append(("create", body["name"], body["type"], body["content"]))
            self.records[str(self.next_id)] = self.from_body(body)
            self.next_id += 1
            return 200, {"status": "SUCCESS", "id": self.next_id - 1}
        if parts[:2] == ["dns", "edit"]:
            self.writes.append(("edit", parts[3], body["type"], body["content"]))
            self.records[parts[3]] = self.from_body(body)
            return 200, {"status": "SUCCESS"}
        if parts[:2] == ["dns", "delete"]:
            self.writes.append(("delete", parts[3], self.records.pop(parts[3])["content"]))
            return 200, {"status": "SUCCESS"}
        raise AssertionError(f"unexpected call {method} {url}")

    def forward_email(self, method, path, body):
        if path == f"/domains/{D}":
            if not self.fe_exists:
                return 404, {"message": "Domain does not exist"}
            return 200, {"name": D, "plan": "team", "zone_file": ZONE}
        if path == "/domains" and method == "POST":
            self.writes.append(("fe-create", body))
            self.fe_exists = True
            return 200, {"name": D, "plan": "team", "zone_file": ZONE}
        if path.endswith("/verify-records"):
            if "mx1.forwardemail.net" in self.find("MX"):
                return 200, "Domain's DNS records have been verified."
            return 400, {"statusCode": 400, "message": ["MX record not found"]}
        raise AssertionError(f"unexpected Forward Email call {method} {path}")

    @staticmethod
    def as_porkbun(record_id, r):
        content = r["content"]
        if r["type"] == "TXT" and len(content) > 255:
            content = quoted(content)  # Porkbun may return long TXT data split and quoted
        return {"id": record_id, "name": f"{r['name']}.{D}" if r["name"] else D, "type": r["type"],
                "content": content, "ttl": "600", "notes": None,
                "prio": str(r.get("prio", 0)) if r["type"] in ("MX", "SRV") else None}

    @staticmethod
    def from_body(body):
        return {"name": body["name"], "type": body["type"], "content": body["content"], "prio": body.get("prio", 0)}


class Run(unittest.TestCase):
    """Base: credentials files in a temporary home, and a call through main()."""

    module = fp
    argv0 = "fe_porkbun.py"

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.files = {
            "forwardemail": self.home / "forwardemail" / "credentials",
            "porkbun": self.home / "porkbun" / "credentials",
        }
        self.write_credentials("forwardemail", "FORWARDEMAIL_API_KEY=fe\n")
        self.write_credentials("porkbun", "PORKBUN_API_KEY=pk1_x\nPORKBUN_SECRET_API_KEY='sk1_x'\n")
        self.saved = (pc.http, fp.CREDENTIALS, pc.PORKBUN_CREDENTIALS)
        pc.PORKBUN_CREDENTIALS = self.files["porkbun"]
        fp.CREDENTIALS = {
            self.files["forwardemail"]: ("FORWARDEMAIL_API_KEY",),
            self.files["porkbun"]: ("PORKBUN_API_KEY", "PORKBUN_SECRET_API_KEY"),
        }

    def tearDown(self):
        pc.http, fp.CREDENTIALS, pc.PORKBUN_CREDENTIALS = self.saved

    def write_credentials(self, service, text, mode=0o600):
        path = self.files[service]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)

    def argv(self, *argv):
        """The command line run_script passes; a script whose first argument is not the domain overrides it."""
        return [self.argv0, D, *argv]

    def run_script(self, apis, *argv):
        pc.http = apis.http
        out, err = io.StringIO(), io.StringIO()
        saved_argv, sys.argv = sys.argv, self.argv(*argv)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as exit_:
                    self.module.main()
        finally:
            sys.argv = saved_argv
        return exit_.exception.code, out.getvalue() + err.getvalue()


class ZoneFile(unittest.TestCase):
    def test_every_record_is_read_in_porkbun_terms(self):
        records = {(r.sub, r.type, r.content, r.prio) for r in fp.parse_zone(ZONE, D)}
        self.assertEqual(len(records), WANTED)
        self.assertIn(("", "MX", "mx1.forwardemail.net", 0), records)
        self.assertIn(("fe-a1b2c3._domainkey", "TXT", DKIM, 0), records)
        self.assertIn(("_submission._tcp", "SRV", "1 587 smtp.forwardemail.net", 5), records)
        self.assertIn(("fe-bounces", "CNAME", "forwardemail.net", 0), records)

    def test_a_record_outside_the_zone_is_refused(self):
        with self.assertRaises(fp.Refusal):
            fp.parse_zone("other.org. 3600 IN MX 0 mx1.forwardemail.net.\n", D)


class DryRun(Run):
    def test_a_domain_not_on_forward_email_is_reported_and_nothing_is_written(self):
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis)
        self.assertEqual(code, 1)
        self.assertIn("a run with --apply adds it", out)
        self.assertEqual(apis.writes, [])

    def test_pending_changes_are_shown_and_nothing_is_written(self):
        apis = FakeApis(porkbun_new_domain(), fe_exists=True)
        code, out = self.run_script(apis)
        self.assertEqual(code, 1)
        self.assertIn("Dry run: nothing written", out)
        self.assertEqual(apis.writes, [])


class Apply(Run):
    def setUp(self):
        super().setUp()
        self.apis = FakeApis(porkbun_new_domain())
        self.code, self.out = self.run_script(self.apis, "--apply", "--catchall", "false")

    def test_creates_the_domain_converges_and_verifies(self):
        self.assertEqual(self.code, 0, self.out)
        self.assertEqual(self.apis.writes[0], ("fe-create", {"domain": D, "catchall": False}))
        self.assertIn("After apply: 0 rows not ok", self.out)
        self.assertIn("verify-records: verified", self.out)

    def test_other_mx_are_replaced(self):
        self.assertEqual(self.apis.find("MX"), ["mx1.forwardemail.net", "mx2.forwardemail.net"])

    def test_spf_is_merged_and_other_apex_txt_kept(self):
        txt = self.apis.find("TXT")
        self.assertIn("v=spf1 include:spf.forwardemail.net include:_spf.porkbun.com ~all", txt)
        self.assertIn("google-site-verification=keepme", txt)
        self.assertEqual(sum(t.startswith("v=spf1") for t in txt), 1)

    def test_dmarc_is_replaced(self):
        self.assertEqual(self.apis.find("TXT", "_dmarc"),
                         ["v=DMARC1; p=reject; pct=100; rua=mailto:dmarc-abc@forwardemail.net;"])

    def test_parking_placeholders_are_deleted_and_ns_kept(self):
        contents = [r["content"] for r in self.apis.records.values()]
        self.assertNotIn("pixie.porkbun.com", contents)
        self.assertEqual(self.apis.find("NS"), ["curitiba.porkbun.com", "salvador.porkbun.com"])

    def test_a_second_apply_writes_nothing(self):
        before = len(self.apis.writes)
        code, out = self.run_script(self.apis, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.apis.writes), before)


class Conflicts(Run):
    def test_a_cname_wanted_where_another_type_exists_is_not_applied(self):
        apis = FakeApis(porkbun_new_domain() + [{"name": "autoconfig", "type": "A", "content": "1.2.3.4"}],
                        fe_exists=True)
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT CNAME autoconfig.example.com", out)
        self.assertEqual(apis.find("A", "autoconfig"), ["1.2.3.4"])

    def test_two_spf_records_are_not_merged(self):
        apis = FakeApis(porkbun_new_domain() + [{"name": "", "type": "TXT", "content": "v=spf1 mx -all"}],
                        fe_exists=True)
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("2 SPF records at the apex", out)


class Refusals(Run):
    def test_a_missing_key_refuses_before_any_call(self):
        self.write_credentials("forwardemail", "# nothing\n")
        apis = FakeApis(porkbun_new_domain())
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 2)
        self.assertIn("must set FORWARDEMAIL_API_KEY", out)
        self.assertEqual(apis.writes, [])

    def test_a_credentials_file_readable_by_others_is_refused(self):
        self.write_credentials("porkbun", "PORKBUN_API_KEY=a\nPORKBUN_SECRET_API_KEY=b\n", mode=0o644)
        code, out = self.run_script(FakeApis(porkbun_new_domain()), "--apply")
        self.assertEqual(code, 2)
        self.assertIn("readable by others", out)

    def test_porkbun_api_access_off_stops_before_forward_email_is_touched(self):
        apis = FakeApis(porkbun_new_domain(), api_access=False)
        code, out = self.run_script(apis, "--apply")
        self.assertEqual(code, 2)
        self.assertIn("API_ACCESS_DISABLED", out)
        self.assertEqual(apis.writes, [])


if __name__ == "__main__":
    unittest.main()
