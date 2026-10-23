"""Tests for dot-claude/skills/linux-create-user. Run: python3 -m unittest test_linux_create_user

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude. Every test runs against FakeHost, which replaces the script's one ssh
function: it answers the read-only probe from its own passwd, group and file tables and
carries out the commands of the apply script. `bash -n` checks every script the run sends.
Nothing touches the network or a real host.
"""

import contextlib
import importlib.util
import io
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent / "dot-claude" / "skills" / "linux-create-user" / "create_user.py"
spec = importlib.util.spec_from_file_location("create_user", SCRIPT)
cu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cu)

HOST = "host.example.com"
KEYS = ["ssh-ed25519 AAAAC3NzaOne alice@laptop", "ssh-rsa AAAAB3NzaTwo alice@desk"]


class FakeHost:
    """A host with root, ubuntu and the sudo group, enough for the probe and the apply script."""

    def __init__(self):
        self.passwd = {"root": [0, 0, "/root", "/bin/bash"], "ubuntu": [1000, 1000, "/home/ubuntu", "/bin/bash"]}
        self.groups = {"root": [0, []], "sudo": [27, ["ubuntu"]], "ubuntu": [1000, []]}
        self.files = {}
        self.probes = 0
        self.applied = []
        self.aliases = []
        self.logins = []

    def ssh(self, host, user, script, alias=None):
        assert host == HOST
        self.aliases.append(alias)
        self.logins.append(user)
        check = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
        assert check.returncode == 0, f"bash -n: {check.stderr}\n{script}"
        if 'echo "hostname=' in script:
            self.probes += 1
            return self.probe(script)
        self.applied.append(script)
        self.apply(script)
        return ""

    def probe(self, script):
        line = next(l for l in script.splitlines() if l.startswith("name="))
        values = dict(part.strip().split("=", 1) for part in line.split(";"))
        name, dk = (shlex.split(values[k])[0] if shlex.split(values[k]) else "" for k in ("name", "dk"))
        out = ["hostname=host"]
        out += [f"passwd={user}:x:{u}:{g}::{home}:{shell}" for user, (u, g, home, shell) in self.passwd.items()]
        out += [f"grp={group}:x:{gid}:{','.join(members)}" for group, (gid, members) in self.groups.items()]
        out += [f"sudoers={l}" for l in self.lines(f"/etc/sudoers.d/90-{name}")]
        grants = "".join(text for path, text in self.files.items() if path.startswith("/etc/sudoers"))
        if name in self.passwd and re.search(rf"^{re.escape(name)} .*NOPASSWD: *ALL *$", grants, re.M):
            out.append("sudo_nopasswd=yes")
        if name in self.passwd:
            ssh_dir = self.passwd[name][2] + "/.ssh"
            out += [f"key={l}" for l in self.lines(f"{ssh_dir}/authorized_keys")]
            if dk:
                if f"{ssh_dir}/id_ed25519_{dk}" in self.files:
                    out.append("deploy_private=yes")
                out += [f"deploy_public={l}" for l in self.lines(f"{ssh_dir}/id_ed25519_{dk}.pub")]
                if f"Host {dk}.github.com" in self.lines(f"{ssh_dir}/config"):
                    out.append("deploy_alias=yes")
        return "\n".join(out) + "\n"

    def lines(self, path):
        return self.files[path].splitlines() if path in self.files else []

    def apply(self, script):
        home, tmp = None, []
        for line in script.splitlines():
            words = shlex.split(line) if not line.startswith("if ") else []
            if home:
                words = [w.replace("$home", home) for w in words]
            if words[:1] == ["useradd"] and "--user-group" in words:
                uid, name = int(words[words.index("--uid") + 1]), words[-1]
                self.passwd[name] = [uid, uid, f"/home/{name}", "/bin/bash"]
                self.groups[name] = [uid, []]
            elif words[:2] == ["groupadd", "-r"]:
                self.groups[words[-1]] = [int(words[3]), []]
            elif words[:2] == ["useradd", "-r"]:
                opts = {flag: words[words.index(flag) + 1] for flag in ("-u", "-g", "-d", "-s")}
                self.passwd[words[-1]] = [int(opts["-u"]), int(opts["-g"]), opts["-d"], opts["-s"]]
            elif words[:3] == ["usermod", "-aG", "sudo"]:
                self.groups["sudo"][1].append(words[3])
            elif line.startswith("home=$(getent passwd"):
                home = self.passwd[shlex.split(line.split("passwd ", 1)[1].split(" |")[0])[0]][2]
            elif words[:2] == ["printf", "%s\\n"] and words[-2] == ">":
                tmp = words[2:-2]
            elif words[:2] == ["printf", "%s\\n"] and words[-2] == ">>":
                self.files[words[-1]] = self.files.get(words[-1], "") + "".join(w + "\n" for w in words[2:-2])
            elif words[:1] == ["install"] and "$tmp" in words:
                assert "visudo -cqf \"$tmp\"" in script.split(line)[0], "visudo must check before install"
                self.files[words[-1]] = "".join(w + "\n" for w in tmp)
            elif "ssh-keygen" in words:
                path, comment = words[words.index("-f") + 1], words[words.index("-C") + 1]
                self.files[path] = "PRIVATE\n"
                self.files[path + ".pub"] = f"ssh-ed25519 AAAAC3NzaDeploy {comment}\n"


class Case(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        patches = [mock.patch.object(cu, "ssh", self.host.ssh),
                   mock.patch.object(cu, "known_as", lambda host, conf=None: None),
                   mock.patch.object(cu, "ssh_config", lambda host: {"user": "admin", "hostname": host}),
                   mock.patch.object(cu, "github_keys", lambda gid: list(KEYS))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_script(self, *argv):
        out = io.StringIO()
        saved, sys.argv = sys.argv, ["create_user.py", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                with self.assertRaises(SystemExit) as exit_:
                    cu.main()
        finally:
            sys.argv = saved
        return exit_.exception.code, out.getvalue()


class Person(Case):
    ARGS = (HOST, "alice", "--uid", "1001", "--github", "alice", "--sudo")

    def test_dry_run_reads_the_host_and_writes_nothing(self):
        code, out = self.run_script(*self.ARGS)
        self.assertEqual(code, 1, out)
        for row in ("CREATE   account", "ADD      sudo group", "CREATE   sudoers", "ADD      authorized_keys"):
            self.assertIn(row, out)
        self.assertIn("Dry run: nothing written", out)
        self.assertEqual(self.host.applied, [])

    def test_apply_makes_the_person_as_the_history_did(self):
        code, out = self.run_script(*self.ARGS, "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("After apply: 0 rows not ok", out)
        self.assertEqual(self.host.passwd["alice"], [1001, 1001, "/home/alice", "/bin/bash"])
        self.assertEqual(self.host.groups["alice"][0], 1001)
        self.assertIn("alice", self.host.groups["sudo"][1])
        self.assertEqual(self.host.files["/etc/sudoers.d/90-alice"], "alice ALL=(ALL) NOPASSWD:ALL\n")
        self.assertEqual(self.host.lines("/home/alice/.ssh/authorized_keys"), KEYS)
        script = self.host.applied[0]
        self.assertIn("useradd --uid 1001 --user-group --create-home --shell /bin/bash alice", script)
        self.assertIn("chmod 600", script)

    def test_a_second_apply_writes_nothing(self):
        self.run_script(*self.ARGS, "--apply")
        code, out = self.run_script(*self.ARGS, "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.host.applied), 1)

    def test_keys_are_added_never_removed(self):
        self.run_script(HOST, "alice", "--uid", "1001", "--apply")
        self.host.files["/home/alice/.ssh/authorized_keys"] = "ssh-ed25519 AAAAC3NzaOther other\n" + KEYS[0] + "\n"
        code, out = self.run_script(HOST, "alice", "--uid", "1001", "--github", "alice", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("1 of 2 keys", out)
        keys = self.host.lines("/home/alice/.ssh/authorized_keys")
        self.assertEqual(keys, ["ssh-ed25519 AAAAC3NzaOther other", KEYS[0], KEYS[1]])

    def test_a_key_with_options_counts_as_present(self):
        self.run_script(HOST, "alice", "--uid", "1001", "--apply")
        self.host.files["/home/alice/.ssh/authorized_keys"] = "".join(f'from="10.0.0.1" {k}\n' for k in KEYS)
        code, out = self.run_script(HOST, "alice", "--uid", "1001", "--github", "alice")
        self.assertEqual(code, 0, out)
        self.assertIn("all 2 keys", out)

    def test_another_sudoers_line_is_edited_and_named(self):
        self.run_script(*self.ARGS, "--apply")
        self.host.files["/etc/sudoers.d/90-alice"] = "alice ALL=(ALL) ALL\n"
        code, out = self.run_script(*self.ARGS)
        self.assertIn("EDIT     sudoers", out)
        self.assertIn("was: alice ALL=(ALL) ALL", out)

    def test_nopasswd_granted_in_another_file_is_ok(self):
        self.run_script(HOST, "alice", "--uid", "1001", "--apply")
        self.host.groups["sudo"][1].append("alice")
        self.host.files["/etc/sudoers.d/90-cloud-init-users"] = "ubuntu ALL=(ALL) NOPASSWD:ALL\nalice ALL=(ALL) NOPASSWD: ALL\n"
        code, out = self.run_script(HOST, "alice", "--uid", "1001", "--sudo", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("ok       sudoers          NOPASSWD: ALL already granted elsewhere", out)
        self.assertNotIn("/etc/sudoers.d/90-alice", self.host.files)

    def test_a_host_without_a_sudo_group_is_a_conflict(self):
        del self.host.groups["sudo"]
        code, out = self.run_script(*self.ARGS, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT sudo group", out)
        self.assertEqual(self.host.applied, [])


class Service(Case):
    def test_apply_makes_the_service_account_as_the_history_did(self):
        code, out = self.run_script(HOST, "svc", "--uid", "703", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.host.passwd["svc"], [703, 703, "/var/lib/svc", "/usr/sbin/nologin"])
        self.assertEqual(self.host.groups["svc"][0], 703)
        script = self.host.applied[0]
        self.assertIn("groupadd -r -g 703 svc", script)
        self.assertIn("useradd -r -u 703 -g 703 -m -d /var/lib/svc -s /usr/sbin/nologin svc", script)

    def test_deploy_key_and_alias(self):
        code, out = self.run_script(HOST, "svc", "--uid", "703", "--deploy-key", "app", "--apply")
        self.assertEqual(code, 0, out)
        config = self.host.lines("/var/lib/svc/.ssh/config")
        self.assertEqual(config, ["Host app.github.com", "    HostName github.com", "    User git",
                                  "    IdentityFile /var/lib/svc/.ssh/id_ed25519_app", "    IdentitiesOnly yes"])
        self.assertIn("ssh-ed25519 AAAAC3NzaDeploy app@host", out)
        self.assertIn("git clone git@app.github.com:<org>/<repo>.git", out)
        code, out = self.run_script(HOST, "svc", "--uid", "703", "--deploy-key", "app", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.host.applied), 1)

    def test_a_private_key_without_its_public_half_is_a_conflict(self):
        self.run_script(HOST, "svc", "--uid", "703", "--apply")
        self.host.files["/var/lib/svc/.ssh/id_ed25519_app"] = "PRIVATE\n"
        code, out = self.run_script(HOST, "svc", "--uid", "703", "--deploy-key", "app", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("exists without its .pub", out)
        self.assertEqual(len(self.host.applied), 1)


class Defaults(Case):
    def test_no_uid_picks_the_next_person_uid_and_says_so(self):
        self.host.passwd["bob"] = [1001, 1001, "/home/bob", "/bin/bash"]
        self.host.groups["bob"] = [1001, []]
        code, out = self.run_script(HOST, "alice", "--github", "alice")
        self.assertEqual(code, 1, out)
        self.assertIn("uid         1002     next free in 1000..59999; highest in use 1001", out)
        self.assertIn("kind        person   no --service or --deploy-key", out)
        self.assertIn("sudo        no       no --sudo", out)
        self.assertNotIn("ssh keys", out)  # given, so not a default
        self.assertIn("CREATE   account          alice uid=1002 gid=1002", out)

    def test_a_uid_whose_gid_is_taken_is_skipped(self):
        self.host.groups["docker"] = [1001, []]
        code, out = self.run_script(HOST, "alice", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.host.passwd["alice"][0], 1002)

    def test_a_person_without_github_is_told_no_key_can_log_in(self):
        code, out = self.run_script(HOST, "alice")
        self.assertIn("ssh keys    none     no --github, so no SSH key can log in as this user", out)

    def test_service_without_uid_starts_at_700_then_follows_the_highest(self):
        code, out = self.run_script(HOST, "deploy", "--service", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.host.passwd["deploy"][0], 700)
        self.assertIn("first in 700..999; none in use", out)
        self.assertIn("deploy key  none", out)
        code, out = self.run_script(HOST, "worker", "--deploy-key", "app", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.host.passwd["worker"][0], 701)
        self.assertNotIn("kind ", out)  # --deploy-key made it a service; nothing defaulted

    def test_a_service_uid_skips_the_systems_accounts_counting_down_from_999(self):
        # As Ubuntu leaves them: user and group ids interleave, and together they run to 999.
        for name, uid in (("sshd", 997), ("messagebus", 998)):
            self.host.passwd[name] = [uid, uid, "/nonexistent", "/usr/sbin/nologin"]
            self.host.groups[name] = [uid, []]
        self.host.groups["systemd-journal"] = [999, []]
        code, out = self.run_script(HOST, "deploy", "--service", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.host.passwd["deploy"][0], 700)
        self.assertIn("first in 700..999; none in use; 997..999 are the system's", out)
        code, out = self.run_script(HOST, "worker", "--service", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.host.passwd["worker"][0], 701)
        self.assertIn("next free in 700..999; highest in use 700; 997..999 are the system's", out)

    def test_an_existing_account_without_a_uid_keeps_its_own(self):
        self.run_script(HOST, "alice", "--uid", "1005", "--apply")
        code, out = self.run_script(HOST, "alice")
        self.assertEqual(code, 0, out)
        self.assertIn("uid         1005     alice already exists at it", out)
        self.assertEqual(len(self.host.applied), 1)

    def test_an_existing_service_named_without_a_uid_is_checked_as_a_service(self):
        self.run_script(HOST, "svc", "--uid", "703", "--apply")
        code, out = self.run_script(HOST, "svc", "--github", "x")
        self.assertEqual(code, 2)
        self.assertIn("uid 703 is a service account", out)

    def test_given_values_are_not_listed_as_defaults(self):
        code, out = self.run_script(f"admin@{HOST}", "alice", "--uid", "1003", "--github", "alice", "--sudo")
        self.assertNotIn("Defaults", out)


class Login(Case):
    def test_login_at_host_is_the_account_ssh_logs_in_as(self):
        code, out = self.run_script(f"ops@{HOST}", "alice")
        self.assertEqual(set(self.host.logins), {"ops"})
        self.assertIn(f"{HOST} as ops", out)
        self.assertNotIn("login ", out)  # given, so not a default

    def test_without_login_the_ssh_config_user_is_used_and_shown(self):
        code, out = self.run_script(HOST, "alice")
        self.assertEqual(set(self.host.logins), {"admin"})
        self.assertIn("login       admin    the User your ssh config sets for this host", out)

    def test_without_login_or_config_the_local_username_is_shown(self):
        me = cu.getpass.getuser()
        with mock.patch.object(cu, "ssh_config", lambda host: {"user": me}):
            code, out = self.run_script(HOST, "alice")
        self.assertIn("your local username; no User for this host in your ssh config", out)

    def test_a_bad_login_is_refused_before_ssh(self):
        code, out = self.run_script(f"Bad@{HOST}", "alice")
        self.assertEqual(code, 2)
        self.assertIn("is not a Linux user name to log in as", out)
        self.assertEqual(self.host.probes, 0)

    def test_uid_and_service_cannot_both_be_given(self):
        code, out = self.run_script(HOST, "svc", "--uid", "703", "--service")
        self.assertEqual(code, 2)
        self.assertIn("not allowed with argument", out)


class Conflicts(Case):
    def test_an_existing_name_at_another_uid_is_never_renumbered(self):
        code, out = self.run_script(HOST, "ubuntu", "--uid", "1005", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("exists at uid 1000; an account is never renumbered", out)
        self.assertEqual(self.host.applied, [])

    def test_a_uid_that_belongs_to_another_account_is_a_conflict(self):
        code, out = self.run_script(HOST, "alice", "--uid", "1000", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("uid 1000 belongs to ubuntu", out)

    def test_a_group_with_the_name_and_no_user_is_a_conflict(self):
        self.host.groups["alice"] = [2000, []]
        code, out = self.run_script(HOST, "alice", "--uid", "1001", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("group alice exists at gid 2000", out)


class Refusals(Case):
    def test_a_bare_call_prints_the_help_and_reads_nothing(self):
        code, out = self.run_script()
        self.assertEqual(code, 2)
        self.assertEqual(out, cu.__doc__.strip() + "\n")
        self.assertEqual(self.host.probes, 0)

    def test_arguments_are_refused_before_ssh(self):
        cases = [(("alice", "--uid", "703", "--sudo"), "--sudo and --github are for a person"),
                 (("svc", "--uid", "1003", "--deploy-key", "app"), "--deploy-key is for a service account"),
                 (("Alice", "--uid", "1001"), "not a Linux user name"),
                 (("alice", "--uid", "0"), "outside 1..59999"),
                 (("svc", "--uid", "703", "--deploy-key", "a/b"), "letters, digits")]
        for argv, message in cases:
            code, out = self.run_script(HOST, *argv)
            self.assertEqual(code, 2, argv)
            self.assertIn(message, out)
        self.assertEqual(self.host.probes, 0)

    def test_a_github_account_with_no_keys_is_refused(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"\n"
        with mock.patch.object(cu.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(cu.Refusal) as err:
                SCRIPT_GITHUB_KEYS("nobody")
        self.assertIn("lists no keys", str(err.exception))

    def test_ssh_and_sudo_failures_are_refusals(self):
        for code, stderr, message in ((255, "Connection timed out", "ssh host.example.com failed"),
                                      (1, "sudo: a password is required", "a password is required")):
            done = subprocess.CompletedProcess([], code, "", stderr)
            with mock.patch.object(cu.subprocess, "run", return_value=done):
                with self.assertRaises(cu.Refusal) as err:
                    SCRIPT_SSH(HOST, None, "true")
            self.assertIn(message, str(err.exception))


class HostKey(unittest.TestCase):
    """known_as(): ssh -G, ssh-keygen and ssh-keyscan are stubbed; known_hosts is a temp file."""

    KEY = ("ssh-ed25519", "AAAAC3NzaHost")

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.known = self.dir / "known_hosts"
        self.port, self.known_names, self.calls = "22", set(), []

    def local(self, argv, what, stdin=None):
        self.calls.append(argv)
        if argv[:2] == ["ssh", "-G"]:
            out = f"hostname {argv[2]}\nport {self.port}\nuserknownhostsfile {self.known} {self.dir / 'none'}\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if argv[:2] == ["ssh-keygen", "-F"]:
            return subprocess.CompletedProcess(argv, 0 if argv[2] in self.known_names else 1, "", "")
        if argv[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(argv, 0, f"{argv[-1]} {' '.join(self.KEY)}\n", "")
        if argv[:2] == ["ssh-keygen", "-lf"]:
            return subprocess.CompletedProcess(argv, 0, "256 SHA256:abc api.example.com (ED25519)\n", "")
        raise AssertionError(argv)

    def known_as(self, host):
        with mock.patch.object(cu, "local", self.local):
            return SCRIPT_KNOWN_AS(host)

    def test_a_name_ssh_knows_is_used_as_it_is(self):
        self.known.write_text("")
        self.known_names.add("api.example.com")
        self.assertIsNone(self.known_as("api.example.com"))
        self.assertFalse(any(c[0] == "ssh-keyscan" for c in self.calls))

    def test_an_unknown_name_with_a_known_key_uses_the_known_name(self):
        self.known.write_text("|1|hashed= ssh-rsa AAAAother\nmcp.example.com,52.0.0.1 " + " ".join(self.KEY) + "\n")
        self.assertEqual(self.known_as("api.example.com"), "mcp.example.com")

    def test_a_host_name_is_preferred_to_an_address(self):
        self.known.write_text("52.0.0.1 " + " ".join(self.KEY) + "\nmcp.example.com " + " ".join(self.KEY) + "\n")
        self.assertEqual(self.known_as("api.example.com"), "mcp.example.com")

    def test_a_key_known_only_under_a_hashed_name_is_refused(self):
        self.known.write_text("|1|hashed= " + " ".join(self.KEY) + "\n")
        with self.assertRaises(cu.Refusal) as err:
            self.known_as("api.example.com")
        self.assertIn("no name in known_hosts holds", str(err.exception))

    def test_an_unknown_key_is_refused_with_its_fingerprint(self):
        self.known.write_text("mcp.example.com ssh-ed25519 AAAAC3NzaOther\n")
        with self.assertRaises(cu.Refusal) as err:
            self.known_as("api.example.com")
        self.assertIn("SHA256:abc", str(err.exception))
        self.assertIn("ssh api.example.com true", str(err.exception))

    def test_a_port_is_part_of_the_lookup(self):
        self.known.write_text("")
        self.port = "2222"
        self.known_names.add("[api.example.com]:2222")
        self.assertIsNone(self.known_as("api.example.com"))

    def test_the_vouching_name_reaches_ssh_and_the_output(self):
        host = FakeHost()
        with mock.patch.object(cu, "ssh", host.ssh), mock.patch.object(cu, "known_as", lambda h, c=None: "mcp.example.com"), \
             mock.patch.object(cu, "ssh_config", lambda h: {"user": "admin"}):
            out = io.StringIO()
            saved, sys.argv = sys.argv, ["create_user.py", HOST, "svc", "--uid", "703"]
            try:
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    cu.main()
            finally:
                sys.argv = saved
        self.assertEqual(host.aliases, ["mcp.example.com"])
        self.assertIn("presents the key known as mcp.example.com", out.getvalue())


# The unpatched functions, for the tests that exercise them directly.
SCRIPT_GITHUB_KEYS = cu.github_keys
SCRIPT_SSH = cu.ssh
SCRIPT_KNOWN_AS = cu.known_as


if __name__ == "__main__":
    unittest.main()
