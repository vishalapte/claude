#!/usr/bin/env python3
"""Create a user on a remote Linux host over SSH, the way the host's history made them.

Usage
  create_user.py [login@]host name [--uid N | --service] [--github ID] [--sudo] [--deploy-key K]
                 [--apply]

  login@ is the account to log in to the host as, as in ssh; name is the account to create.

Examples (each is a dry run that reads the host and writes nothing; add --apply to write)
  create_user.py host.example.com alice --github alice --sudo          a person, next free uid
  create_user.py admin@host.example.com bob --uid 1004 --github bob    ... log in as admin, uid 1004
  create_user.py host.example.com svc --service                        a service, next free uid
  create_user.py host.example.com svc --uid 703 --deploy-key app       ... with a GitHub deploy key

The uid decides the kind of account
  uid >= 1000  person    useradd --uid N --user-group --create-home --shell /bin/bash
  uid <  1000  service   groupadd -r -g N; useradd -r -u N -g N -m -d /var/lib/<name> -s /usr/sbin/nologin

Defaults, printed with the plan under "Defaults" whenever one is used
  login            without login@: what ssh uses for the host, your ssh config's User or else
                   your local username; it needs passwordless sudo on the host
  uid              the account's own uid if <name> exists; else one past the highest uid in use
                   in the kind's range (1000.., or 700..999 for a service), skipping any taken gid
  kind             without --uid: a person, unless --service or --deploy-key
  sudo, ssh keys   a person gets neither unless --sudo or --github is given
  deploy key       a service gets none unless --deploy-key is given

Options
  --uid N          the uid, and the gid; 1000 and above a person, below it a service
  --service        instead of --uid: a service account at the next free uid
  --github ID      person: add the keys at https://github.com/ID.keys to ~/.ssh/authorized_keys
                   (additive; a GitHub account with no keys is refused)
  --sudo           person: the sudo group, and /etc/sudoers.d/90-<name> with NOPASSWD, because
                   the account has no password (checked with visudo -cf before it is installed)
  --deploy-key K   service: ~/.ssh/id_ed25519_K and an ssh config alias `Host K.github.com`,
                   so `git clone git@K.github.com:<org>/<repo>.git` uses that key
  --apply          make the changes, then read the host again and verify

Host keys
  When ssh has no key stored under <host>'s name, the script reads the key <host> presents
  and looks for it in known_hosts under any other name (a host name before an address). Found,
  it connects with HostKeyAlias=<that name> and says so; ssh still checks the key. Not found,
  it refuses with the key's fingerprint and the command that trusts it once.

Rows printed
  ok        already so
  CREATE    makes it
  ADD       adds to it (a group membership, keys)
  EDIT      replaces it; the note shows the old value
  CONFLICT  cannot be applied: the name exists at another uid (never renumbered), or the uid
            or gid belongs to another account

Exit
  0  done, or already so
  1  changes pending (dry run), a conflict, or the check after --apply failed
  2  refused: a bad argument, an unknown host key, ssh or sudo failed, or the GitHub keys
     could not be read
"""

import argparse
import getpass
import ipaddress
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path

PERSON_MIN = 1000  # Debian's first regular uid; below it, system (service) accounts
SERVICE_MIN = 700  # where the host's history began its service accounts
UID_MAX = 59999
NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
DEPLOY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")
GITHUB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
KEY_TYPE = re.compile(r"(ssh-|ecdsa-|sk-)")
SUDOERS = "/etc/sudoers.d/90-{}"

# One line of the plan. Actions: ok, CREATE, ADD, EDIT, CONFLICT.
Row = namedtuple("Row", "action item detail")


class Refusal(Exception):
    """The run cannot decide; exit 2 with this message."""


# --- the host -------------------------------------------------------------------------


def is_address(name):
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def local(argv, what, stdin=None):
    """Run a command on this machine; refuse when it cannot start or does not finish."""
    try:
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise Refusal(f"{what}: {err}") from err


def ssh_config(host):
    """What ssh resolves for `host` (`ssh -G`): its user, hostname, port, known_hosts files."""
    resolved = local(["ssh", "-G", host], f"ssh -G {host}")
    if resolved.returncode != 0:
        raise Refusal(f"ssh -G {host}: {resolved.stderr.strip()}")
    conf = {}
    for line in resolved.stdout.splitlines():
        field, _, value = line.partition(" ")
        conf.setdefault(field, value)
    return conf


def known_as(host, conf=None):
    """The known_hosts name that vouches for `host`: None when ssh already knows `host` itself;
    else a name already in known_hosts that holds a key `host` presents, used as HostKeyAlias.

    Identity is the host key, not the name or the address: an address can move to another
    machine, a key cannot. ssh still checks the key on the real connection, so a scanned key
    that lies cannot pass.
    """
    conf = conf if conf is not None else ssh_config(host)
    name, port = conf.get("hostkeyalias") or conf.get("hostname", host), conf.get("port", "22")
    lookup = name if port == "22" else f"[{name}]:{port}"
    files = [Path(f).expanduser() for f in conf.get("userknownhostsfile", "~/.ssh/known_hosts").split()]
    files = [f for f in files if f.is_file()]
    for path in files:
        if local(["ssh-keygen", "-F", lookup, "-f", str(path)], "ssh-keygen -F").returncode == 0:
            return None
    scan = local(["ssh-keyscan", "-T", "10", "-p", port, conf.get("hostname", host)], f"ssh-keyscan {host}")
    presented = {tuple(line.split()[1:3]) for line in scan.stdout.splitlines()
                 if line.strip() and not line.startswith("#") and len(line.split()) >= 3}
    if not presented:
        raise Refusal(f"ssh-keyscan {host} returned no host key: {scan.stderr.strip()}")
    names = []
    for path in files:
        for line in path.read_text(errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 3 and not line.startswith(("#", "@")) and tuple(fields[1:3]) in presented:
                names += [n for n in fields[0].split(",") if not n.startswith(("|", "["))]
    if names:
        return min(names, key=is_address)  # a host name reads better than an address; first of each kind
    prints = local(["ssh-keygen", "-lf", "-"], "ssh-keygen -lf", stdin=scan.stdout)  # fingerprints of what was scanned
    raise Refusal(f"{host} presents a host key that no name in known_hosts holds. Check a fingerprint "
                  f"below against the host, then trust it once with: ssh {host} true\n{prints.stdout.strip()}")


def ssh(host, user, script, alias=None):
    """Run `script` as root on `host` (sudo -n bash -s, the script on stdin); return its stdout.
    `alias` is the known_hosts name whose key `host` must present (HostKeyAlias)."""
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if alias:
        command += ["-o", f"HostKeyAlias={alias}"]
    if user:
        command += ["-l", user]
    command += [host, "sudo -n bash -s"]
    try:
        done = subprocess.run(command, input=script, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise Refusal(f"ssh {host}: {err}") from err
    if done.returncode == 255:
        raise Refusal(f"ssh {host} failed: {done.stderr.strip()}")
    if done.returncode != 0:
        raise Refusal(f"on {host}, exit {done.returncode}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def probe_script(name, deploy_key):
    """Read-only: print what the host holds as key=value lines, every account and group included,
    so the plan can find the name, who holds a uid or gid, and the next free uid."""
    q = shlex.quote
    return "\n".join([
        "set -u",
        f"name={q(name)}; dk={q(deploy_key or '')}",
        'echo "hostname=$(hostname)"',
        'getent passwd | sed "s/^/passwd=/"',
        'getent group | sed "s/^/grp=/"',
        'f="/etc/sudoers.d/90-$name"; if [ -f "$f" ]; then sed "s/^/sudoers=/" "$f"; fi',
        # A grant from any file (90-cloud-init-users, say) counts, as `sudo -l -U` reports it.
        'if getent passwd "$name" >/dev/null && sudo -l -U "$name" 2>/dev/null | grep -qE "NOPASSWD: *ALL *$"; then',
        "  echo sudo_nopasswd=yes",
        "fi",
        'home=$(getent passwd "$name" | cut -d: -f6)',
        'if [ -n "$home" ]; then',
        '  if [ -f "$home/.ssh/authorized_keys" ]; then sed "s/^/key=/" "$home/.ssh/authorized_keys"; fi',
        '  if [ -n "$dk" ]; then',
        '    if [ -f "$home/.ssh/id_ed25519_$dk" ]; then echo deploy_private=yes; fi',
        '    if [ -f "$home/.ssh/id_ed25519_$dk.pub" ]; then sed "s/^/deploy_public=/" "$home/.ssh/id_ed25519_$dk.pub"; fi',
        '    if [ -f "$home/.ssh/config" ] && grep -qxF "Host $dk.github.com" "$home/.ssh/config"; then',
        "      echo deploy_alias=yes",
        "    fi",
        "  fi",
        "fi",
        "",
    ])


def parse(output):
    """The probe's key=value lines; `passwd`, `grp`, `key`, `sudoers` and `deploy_public` repeat."""
    state = {"passwd": [], "grp": [], "key": [], "sudoers": [], "deploy_public": []}
    for line in output.splitlines():
        field, sep, value = line.partition("=")
        if not sep:
            continue
        if isinstance(state.get(field), list):
            state[field].append(value)
        else:
            state[field] = value
    return state


def entry(lines, field, value):
    """The first passwd or group line whose `field` (0 the name, 2 the id) equals `value`."""
    return next((line for line in lines if line.split(":")[field] == str(value)), None)


def next_uid(state, person):
    """(uid, why): one past the highest uid in the kind's range, skipping any uid or gid already
    held. People start at PERSON_MIN, services at SERVICE_MIN (the host's history began at 700)."""
    low, high = (PERSON_MIN, UID_MAX) if person else (SERVICE_MIN, PERSON_MIN - 1)
    taken = ({int(line.split(":")[2]) for line in state["passwd"]}
             | {int(line.split(":")[2]) for line in state["grp"]})
    in_range = [int(line.split(":")[2]) for line in state["passwd"] if low <= int(line.split(":")[2]) <= high]
    uid = max(in_range) + 1 if in_range else low
    while uid in taken:
        uid += 1
    if uid > high:
        raise Refusal(f"no free uid left in {low}..{high}")
    why = (f"next free in {low}..{high}; highest in use {max(in_range)}" if in_range
           else f"first in {low}..{high}; none in use")
    return uid, why


# --- GitHub ---------------------------------------------------------------------------


def github_keys(github_id):
    """The public keys GitHub publishes for an account; refuse an account with none."""
    url = f"https://github.com/{github_id}.keys"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            text = response.read().decode()
    except urllib.error.HTTPError as err:
        raise Refusal(f"{url} -> {err.code}") from err
    except urllib.error.URLError as err:
        raise Refusal(f"{url}: {err.reason}") from err
    keys = [line.strip() for line in text.splitlines() if line.strip()]
    if not keys:
        raise Refusal(f"{url} lists no keys, so the account would have no way to log in")
    return keys


def key_id(line):
    """(type, base64) of an authorized_keys line, past any options; None for a line with no key."""
    tokens = line.split()
    for i, token in enumerate(tokens[:-1]):
        if KEY_TYPE.match(token):
            return token, tokens[i + 1]
    return None


# --- plan -----------------------------------------------------------------------------


def sudoers_line(name):
    return f"{name} ALL=(ALL) NOPASSWD:ALL"


def plan(args, state, keys):
    """Rows that make the account as asked. A CONFLICT on the account stops the rest."""
    name, uid = args.name, args.uid
    person = uid >= PERSON_MIN
    kind = (f"person, home /home/{name}, shell /bin/bash" if person
            else f"service, home /var/lib/{name}, shell /usr/sbin/nologin")
    user, group = entry(state["passwd"], 0, name), entry(state["grp"], 0, name)
    uid_owner, gid_owner = entry(state["passwd"], 2, uid), entry(state["grp"], 2, uid)
    if user:
        fields = user.split(":")
        if fields[2] != str(uid):
            return [Row("CONFLICT", "account", f"{name} exists at uid {fields[2]}; an account is never renumbered")]
        rows = [Row("ok", "account", f"{name} uid={uid} home={fields[5]} shell={fields[6]}")]
    elif uid_owner:
        return [Row("CONFLICT", "account", f"uid {uid} belongs to {uid_owner.split(':')[0]}")]
    elif gid_owner:
        return [Row("CONFLICT", "account", f"gid {uid} belongs to group {gid_owner.split(':')[0]}")]
    elif group:
        return [Row("CONFLICT", "account", f"group {name} exists at gid {group.split(':')[2]} with no user {name}")]
    else:
        rows = [Row("CREATE", "account", f"{name} uid={uid} gid={uid} ({kind})")]

    if args.sudo:
        sudo_group = entry(state["grp"], 0, "sudo")
        if not sudo_group:
            rows.append(Row("CONFLICT", "sudo group", "this host has no group named sudo"))
        elif name in sudo_group.split(":")[3].split(","):
            rows.append(Row("ok", "sudo group", f"{name} is in sudo"))
        else:
            rows.append(Row("ADD", "sudo group", f"usermod -aG sudo {name}"))
        want, have = sudoers_line(name), [line.strip() for line in state["sudoers"]]
        path = SUDOERS.format(name)
        if have == [want]:
            rows.append(Row("ok", "sudoers", f"{path}: {want}"))
        elif not have and state.get("sudo_nopasswd"):
            rows.append(Row("ok", "sudoers", f"NOPASSWD: ALL already granted elsewhere (sudo -l -U {name})"))
        elif have:
            rows.append(Row("EDIT", "sudoers", f"{path}: {want} (was: {' | '.join(have)})"))
        else:
            rows.append(Row("CREATE", "sudoers", f"{path}: {want}"))

    if keys is not None:
        present = {key_id(line) for line in state["key"]}
        missing = [k for k in keys if key_id(k) not in present]
        github = f"github.com/{args.github}.keys"
        if missing:
            rows.append(Row("ADD", "authorized_keys", f"{len(missing)} of {len(keys)} keys from {github}"))
        else:
            rows.append(Row("ok", "authorized_keys", f"all {len(keys)} keys from {github}"))

    if args.deploy_key:
        key = f"id_ed25519_{args.deploy_key}"
        if state["deploy_public"]:
            rows.append(Row("ok", "deploy key", f"~{name}/.ssh/{key}"))
        elif state.get("deploy_private"):
            rows.append(Row("CONFLICT", "deploy key", f"~{name}/.ssh/{key} exists without its .pub"))
        else:
            rows.append(Row("CREATE", "deploy key", f"~{name}/.ssh/{key} (ed25519, no passphrase)"))
        alias = f"Host {args.deploy_key}.github.com"
        if state.get("deploy_alias"):
            rows.append(Row("ok", "ssh alias", f"~{name}/.ssh/config: {alias}"))
        else:
            rows.append(Row("CREATE", "ssh alias", f"~{name}/.ssh/config: {alias} -> github.com with {key}"))
    return rows


# --- apply ----------------------------------------------------------------------------


def append_lines(path, words):
    """Shell that appends one line per word (each already quoted) to `path`, first ending a last
    line that has no newline."""
    return [
        f'if [ -s {path} ] && [ -n "$(tail -c1 {path})" ]; then echo >> {path}; fi',
        f"printf '%s\\n' {' '.join(words)} >> {path}",
    ]


def apply_script(args, rows, keys, state):
    """One bash script, run as root, that makes every CREATE, ADD and EDIT row true."""
    q = shlex.quote
    name, uid = q(args.name), str(args.uid)
    todo = {row.item for row in rows if row.action in ("CREATE", "ADD", "EDIT")}
    out = ["set -euo pipefail"]
    if "account" in todo:
        if args.uid >= PERSON_MIN:
            out.append(f"useradd --uid {uid} --user-group --create-home --shell /bin/bash {name}")
        else:
            out.append(f"groupadd -r -g {uid} {name}")
            out.append(f"useradd -r -u {uid} -g {uid} -m -d {q('/var/lib/' + args.name)} -s /usr/sbin/nologin {name}")
    out.append(f"home=$(getent passwd {name} | cut -d: -f6)")
    ssh_dir = '"$home/.ssh"'
    if todo & {"authorized_keys", "deploy key", "ssh alias"}:
        out.append(f"install -d -m 700 -o {name} -g {name} {ssh_dir}")
    if "sudo group" in todo:
        out.append(f"usermod -aG sudo {name}")
    if "sudoers" in todo:
        out += [
            'tmp=$(mktemp)',
            f"printf '%s\\n' {q(sudoers_line(args.name))} > \"$tmp\"",
            'visudo -cqf "$tmp"',
            f'install -m 0440 -o root -g root "$tmp" {q(SUDOERS.format(args.name))}',
            'rm -f "$tmp"',
        ]
    if "authorized_keys" in todo:
        present = {key_id(line) for line in state["key"]}
        auth = '"$home/.ssh/authorized_keys"'
        out += append_lines(auth, [q(k) for k in keys if key_id(k) not in present])
        out += [f"chown {name}:{name} {auth}", f"chmod 600 {auth}"]
    if "deploy key" in todo:
        key = f'"$home/.ssh/id_ed25519_{args.deploy_key}"'
        comment = q(f"{args.deploy_key}@{state.get('hostname', 'host')}")
        out.append(f"sudo -u {name} ssh-keygen -q -t ed25519 -N '' -f {key} -C {comment}")
    if "ssh alias" in todo:
        config = '"$home/.ssh/config"'
        # Double quotes on IdentityFile: the host's shell writes the real home path. The key
        # name is letters, digits, _ and - (check()), so it needs no quoting of its own.
        block = [q(f"Host {args.deploy_key}.github.com"), q("    HostName github.com"), q("    User git"),
                 f'"    IdentityFile $home/.ssh/id_ed25519_{args.deploy_key}"', q("    IdentitiesOnly yes")]
        out += append_lines(config, block)
        out += [f"chown {name}:{name} {config}", f"chmod 600 {config}"]
    return "\n".join(out) + "\n"


# --- main -----------------------------------------------------------------------------


def show(rows):
    for row in rows:
        print(f"  {row.action:<8} {row.item:<16} {row.detail}")


def is_person(args):
    """The kind: from the uid when one is given, else a person unless --service or --deploy-key."""
    if args.uid is not None:
        return args.uid >= PERSON_MIN
    return not (args.service or args.deploy_key)


def check(args):
    """Refuse a bad argument before anything leaves this machine."""
    if not NAME.fullmatch(args.name):
        raise Refusal(f"{args.name!r} is not a Linux user name (lowercase, start with a letter or _, at most 32)")
    if args.login is not None and not NAME.fullmatch(args.login):
        raise Refusal(f"{args.login!r} in {args.login}@{args.host} is not a Linux user name to log in as")
    if not args.host:
        raise Refusal("the host is empty")
    if args.uid is not None and not 0 < args.uid <= UID_MAX:
        raise Refusal(f"uid {args.uid} is outside 1..{UID_MAX}")
    person = is_person(args)
    what = f"uid {args.uid} is" if args.uid is not None else "this is"
    if person and args.service:
        raise Refusal(f"{what} a person; --service needs a uid below {PERSON_MIN}")
    if not person and (args.sudo or args.github):
        raise Refusal(f"{what} a service account; --sudo and --github are for a person (uid >= {PERSON_MIN})")
    if person and args.deploy_key:
        raise Refusal(f"{what} a person; --deploy-key is for a service account (uid < {PERSON_MIN})")
    if args.github and not GITHUB_ID.fullmatch(args.github):
        raise Refusal(f"{args.github!r} is not a GitHub account name")
    if args.deploy_key and not DEPLOY_KEY.fullmatch(args.deploy_key):
        raise Refusal(f"--deploy-key {args.deploy_key!r} must be letters, digits, _ or -")


def pick_defaults(args, state):
    """Fill what the command line left out; return (setting, value, why) for each, to print."""
    picked = []
    if args.uid is None:
        kind_given = args.service or args.deploy_key
        user = entry(state["passwd"], 0, args.name)
        if user:
            args.uid = int(user.split(":")[2])
            picked.append(("uid", args.uid, f"{args.name} already exists at it"))
        else:
            args.uid, why = next_uid(state, is_person(args))
            picked.append(("uid", args.uid, why))
        if not kind_given:
            picked.append(("kind", "person" if is_person(args) else "service", "no --service or --deploy-key"))
        check(args)  # an existing account's uid can be of the other kind
    if is_person(args):
        if not args.sudo:
            picked.append(("sudo", "no", "no --sudo"))
        if not args.github:
            picked.append(("ssh keys", "none", "no --github, so no SSH key can log in as this user"))
    elif not args.deploy_key:
        picked.append(("deploy key", "none", "no --deploy-key"))
    return picked


def run(args):
    login, _, args.host = args.host.rpartition("@")
    args.login = login or None
    check(args)
    keys = github_keys(args.github) if args.github else None
    conf = ssh_config(args.host)
    alias = known_as(args.host, conf)
    login = args.login or conf.get("user")
    probe = probe_script(args.name, args.deploy_key)
    state = parse(ssh(args.host, login, probe, alias))
    picked = pick_defaults(args, state)
    if not args.login:
        why = ("your local username; no User for this host in your ssh config" if login == getpass.getuser()
               else "the User your ssh config sets for this host")
        picked.insert(0, ("login", login, why))
    rows = plan(args, state, keys)
    changes = [r for r in rows if r.action in ("CREATE", "ADD", "EDIT")]
    conflicts = [r for r in rows if r.action == "CONFLICT"]
    print(f"{args.host} as {login} (hostname {state.get('hostname', '?')}): user {args.name} uid {args.uid}: "
          f"{len(changes)} changes")
    if alias:
        print(f"Host key: {args.host} is not in known_hosts by that name; it presents the key known as {alias}")
    if picked:
        print("Defaults, for what the command did not give:")
        for setting, value, why in picked:
            print(f"  {setting:<11} {str(value):<8} {why}")
        print("Plan:")
    show(rows)

    if conflicts:
        print("\nNothing written: resolve the CONFLICT rows first.")
    elif changes and not args.apply:
        print("\nDry run: nothing written. Run again with --apply to make these changes.")
    elif changes:
        ssh(args.host, login, apply_script(args, rows, keys, state), alias)
        state = parse(ssh(args.host, login, probe, alias))
        rows = plan(args, state, keys)
        left = [r for r in rows if r.action != "ok"]
        print(f"\nAfter apply: {len(left)} rows not ok")
        show(left)
    for public in state["deploy_public"]:
        print(f"\nDeploy key: add this as a read-only deploy key on the GitHub repo, then clone with\n"
              f"git clone git@{args.deploy_key}.github.com:<org>/<repo>.git\n{public}")
    return 1 if any(r.action != "ok" for r in rows) else 0


def main():
    if len(sys.argv) == 1:
        print(__doc__.strip())
        sys.exit(2)
    parser = argparse.ArgumentParser(description=__doc__.strip(), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", metavar="[login@]host", help="the host, as ssh knows it; login@ to log in as login")
    parser.add_argument("name", help="the user name to create")
    kind = parser.add_mutually_exclusive_group()
    kind.add_argument("--uid", type=int, metavar="N",
                      help=f"the uid (and gid); >= {PERSON_MIN} a person, below it a service; default the next free")
    kind.add_argument("--service", action="store_true", help="instead of --uid: a service account, next free uid")
    parser.add_argument("--github", metavar="ID", help="person: authorized_keys from github.com/ID.keys")
    parser.add_argument("--sudo", action="store_true", help="person: sudo group and a NOPASSWD sudoers file")
    parser.add_argument("--deploy-key", metavar="K", help="service: id_ed25519_K and Host K.github.com")
    parser.add_argument("--apply", action="store_true", help="make the changes (without it, a dry run)")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
