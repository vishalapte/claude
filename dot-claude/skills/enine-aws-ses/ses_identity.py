#!/usr/bin/env python3
"""Set up a domain to send through AWS SES: the identity at SES, then the six Porkbun records.

    ses_identity.py example.com --profile admin               # dry run: read SES and the zone, give each record a row
    ses_identity.py example.com --profile admin --apply       # create the identity or change it, read it again
    ses_identity.py example.com --dkim T1 T2 T3               # no AWS: SES's three DKIM tokens
    ses_identity.py example.com                               # no AWS, no tokens: confirm what the zone holds

At SES, with --profile: the domain's identity exists, its Easy DKIM signs with a 2048-bit key,
and its MAIL FROM domain is hermes.<domain>. Without --profile, or when SES refuses the profile,
that half is skipped and the run says so.

At Porkbun, the zone is read first, with porkbun.py's own client and credentials. Each record
gets one row: DKIM (the three CNAMEs, as one set), the MAIL FROM MX, the MAIL FROM SPF, DMARC.

    in the zone   provided          row
    no            yes               CREATE   its porkbun.py create command prints
    no            no                ERROR
    yes           matches           CONFIRM
    yes           differs           PROMPT   both shown; the update commands print (delete the
                                             zone's, create the provided), run only on the owner's word

DKIM with no tokens provided is CONFIRM when the zone holds exactly three SES DKIM records in AWS
form (`<t>._domainkey.<domain>` -> `<t>.dkim.amazonses.com`, `<t>` 32 of [a-z0-9]), else ERROR.
Every row CONFIRM prints `CONFIRMED: <domain>`. The script writes nothing at Porkbun.

Region and MAIL FROM come from the sender config (--senders FILE, or $ENINE_MAIL_SENDERS) when it
lists the domain, else us-east-1 and hermes; --region and --mail-from override them. The sender
config has no default. A domain it does not list gets an entry there, shown in a dry run and
appended on --apply: `mail_from`, the `region` when it is not the default, `# sends_for = []`, and
the profile line commented out, holding the profile most listed domains use. With no sender config set, or no file at its path, nothing is
added (the file is never created); a domain already
listed is flagged and its entry is never changed.

Exit 0 every row CONFIRM and nothing pending at SES or in the sender config; 1 a CREATE or PROMPT
row, or a change pending at SES or in the sender config, or SES holds DKIM that is not Easy DKIM;
2 an ERROR row, or could not decide (a bad argument, an unreadable sender config, a Porkbun refusal).
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

# The sender config: per domain profile, mail_from, region; [defaults] region. The same
# variable enine-mail reads; no default.
SENDERS_VARIABLE = "ENINE_MAIL_SENDERS"
PORKBUN = Path(__file__).resolve().parent.parent / "enine-porkbun" / "porkbun.py"
# The zone is read with porkbun.py's own connect and client: one home for credentials and zones.
sys.path.insert(0, str(PORKBUN.parent))
import porkbun as pb  # noqa: E402

pc = pb.pc
REGION = "us-east-1"
MAIL_FROM = "hermes"
KEY_LENGTH = "RSA_2048_BIT"
SPF = "v=spf1 include:amazonses.com -all"
DMARC = "v=DMARC1; p=reject; pct=100; rua=mailto:dmarc@{domain}"
AWS_TOKEN = re.compile(r"[a-z0-9]{32}")


class Refusal(Exception):
    """The run cannot decide; exit 2 with this message."""


def aws(*argv):
    """(exit code, parsed JSON or None, stderr) of one `aws` CLI call. The tests replace it."""
    try:
        done = subprocess.run(["aws", *argv, "--output", "json"], capture_output=True, text=True,
                              timeout=120, check=False)
    except FileNotFoundError:
        return 127, None, "the aws CLI is not installed"
    try:
        parsed = json.loads(done.stdout) if done.stdout.strip() else {}
    except ValueError:
        parsed = None
    return done.returncode, parsed, done.stderr.strip()


def senders_path(args):
    """The sender config's path: --senders, else $ENINE_MAIL_SENDERS; None when neither is set."""
    value = args.senders or os.environ.get(SENDERS_VARIABLE) or None
    return Path(value).expanduser() if value else None


def senders(path):
    """The sender config as a dict; empty when no path is given or the file does not exist."""
    if path is None or not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as err:
        raise Refusal(f"{path}: {err}") from err


def settings(args, known):
    """(region, MAIL FROM host) for the domain: the flags, else the sender config, else the defaults."""
    listed = known.get("domains", {}).get(args.domain, {})
    region = args.region or listed.get("region") or known.get("defaults", {}).get("region") or REGION
    mail_from = args.mail_from or listed.get("mail_from") or MAIL_FROM
    if not mail_from.endswith("." + args.domain):
        mail_from = f"{mail_from}.{args.domain}"
    return region, mail_from


# --- SES -------------------------------------------------------------------------------


def first_line(text):
    return (text.strip().splitlines() or ["no answer"])[-1].removeprefix("aws: [ERROR]: ").strip()


class Ses:
    def __init__(self, profile, region, domain):
        self.scope = ("--profile", profile, "--region", region)
        self.domain = domain

    def call(self, *argv):
        return aws("sesv2", *argv, *self.scope)

    def identity(self):
        """SES's identity for the domain; None when SES has none. A refusal raises Refusal."""
        code, body, err = self.call("get-email-identity", "--email-identity", self.domain)
        if code == 0 and isinstance(body, dict):
            return body
        if "NotFoundException" in err:
            return None
        raise Refusal(first_line(err))


def ses_plan(identity, mail_from):
    """Rows (action, what, value, note) that bring the identity to 2048-bit Easy DKIM and MAIL FROM."""
    if identity is None:
        return [("CREATE", "identity", "", "2048-bit Easy DKIM"), ("CREATE", "MAIL FROM", mail_from, "")]
    dkim = identity.get("DkimAttributes") or {}
    rows = []
    origin = dkim.get("SigningAttributesOrigin", "AWS_SES")
    length = dkim.get("NextSigningKeyLength") or dkim.get("CurrentSigningKeyLength") or "unknown"
    if origin != "AWS_SES":
        rows.append(("CONFLICT", "DKIM", origin, "not Easy DKIM; not changed"))
    elif length == KEY_LENGTH:
        rows.append(("ok", "DKIM", "2048-bit Easy DKIM", ""))
    else:
        rows.append(("EDIT", "DKIM", "2048-bit Easy DKIM", f"was {length}"))
    current = (identity.get("MailFromAttributes") or {}).get("MailFromDomain") or ""
    rows.append(("ok" if current == mail_from else "EDIT", "MAIL FROM", mail_from,
                 "" if current == mail_from else f"was {current or 'none'}"))
    return rows


def ses_apply(ses, rows, mail_from):
    """Make each change the rows plan. A refusal raises Refusal with SES's message."""
    def run(*argv):
        code, _, err = ses.call(*argv)
        if code != 0:
            raise Refusal(f"SES {argv[0]}: {first_line(err)}")

    for action, what, _, _ in rows:
        if action == "CREATE" and what == "identity":
            run("create-email-identity", "--email-identity", ses.domain,
                "--dkim-signing-attributes", f"NextSigningKeyLength={KEY_LENGTH}")
        elif action == "EDIT" and what == "DKIM":
            run("put-email-identity-dkim-signing-attributes", "--email-identity", ses.domain,
                "--signing-attributes-origin", "AWS_SES", "--signing-attributes", f"NextSigningKeyLength={KEY_LENGTH}")
        elif action in ("CREATE", "EDIT") and what == "MAIL FROM":
            run("put-email-identity-mail-from-attributes", "--email-identity", ses.domain,
                "--mail-from-domain", mail_from, "--behavior-on-mx-failure", "USE_DEFAULT_VALUE")


def show(rows):
    for action, what, value, note in rows:
        print(f"  {action:<8} {what:<10} {value}{f'  [{note}]' if note else ''}")


def ses_half(args, region, mail_from):
    """(tokens or None, True when something is pending or held, True when SES was read).
    Prints the SES rows or the skip."""
    if not args.profile:
        print(f"SES: skipped (no --profile; pass an AWS profile that may change SES in {args.domain}'s account)")
        return None, False, False
    ses = Ses(args.profile, region, args.domain)
    try:
        identity = ses.identity()
    except Refusal as err:
        print(f"SES: skipped (profile {args.profile}: {err})")
        return None, False, False
    rows = ses_plan(identity, mail_from)
    changes = [r for r in rows if r[0] in ("CREATE", "EDIT")]
    print(f"SES identity {args.domain} (profile {args.profile}, region {region}): {len(changes)} changes")
    show(rows)
    if changes and not args.apply:
        print("\nDry run: nothing changed at SES. Run again with --apply to make these changes.")
    if changes and args.apply:
        ses_apply(ses, rows, mail_from)
        identity = ses.identity()
        rows = ses_plan(identity, mail_from)
        left = [r for r in rows if r[0] != "ok"]
        print(f"\nAfter apply: {len(left)} rows not ok")
        show(left)
    pending = any(r[0] != "ok" for r in rows)
    tokens = ((identity or {}).get("DkimAttributes") or {}).get("Tokens") or None
    return tokens, pending, True


# --- the records ------------------------------------------------------------------------


class Row:
    """One record's verdict: CONFIRM, CREATE, PROMPT or ERROR, and the porkbun.py commands it prints."""

    def __init__(self, status, kind, name, value, note="", create=(), update=()):
        self.status, self.kind, self.name, self.value, self.note = status, kind, name, value, note
        self.create, self.update = list(create), list(update)


def command(account, verb, rtype, name, *rest):
    """One porkbun.py command as an argv list."""
    return ["python3", str(PORKBUN), verb, rtype, name, *rest, *(["--account", account] if account else [])]


def given_tokens(ses_tokens, given):
    """(tokens or None, error or ""): SES's tokens win; --dkim tokens that differ from SES's are an error."""
    if ses_tokens and given and sorted(ses_tokens) != sorted(given):
        return None, f"--dkim differs from SES's tokens ({', '.join(ses_tokens)})"
    return (list(ses_tokens or given) if ses_tokens or given else None), ""


def zone_tokens(records, zone, domain):
    """The tokens of the zone's SES DKIM CNAMEs: `<t>._domainkey.<domain>` -> `<t>.dkim.amazonses.com`."""
    under = pc.to_sub(f"_domainkey.{domain}", zone)
    found = []
    for r in records:
        token = r.sub[: -len(under) - 1] if r.type == "CNAME" and r.sub.endswith("." + under) else ""
        if token and "." not in token and r.content == f"{token}.dkim.amazonses.com":
            found.append(token)
    return sorted(found)


def dkim_row(domain, given, error, held, ses_pending, account):
    """The DKIM row: the zone's SES DKIM set against the provided tokens."""
    name = f"<token>._domainkey.{domain}"

    def cname(verb, token):
        return command(account, verb, "CNAME", f"{token}._domainkey.{domain}", f"{token}.dkim.amazonses.com")

    if error:
        return Row("ERROR", "DKIM", name, ", ".join(held) or "none in the zone", error)
    if not held and not given:
        why = "SES creates the identity on --apply; run again after it" if ses_pending else \
            "no tokens provided (no SES identity read, no --dkim) and none in the zone"
        return Row("ERROR", "DKIM", name, "none in the zone", why)
    if not held:
        return Row("CREATE", "DKIM", name, ", ".join(given), create=[cname("create", t) for t in given])
    if not given:
        if len(held) == 3 and all(AWS_TOKEN.fullmatch(t) for t in held):
            return Row("CONFIRM", "DKIM", name, ", ".join(held), "from the zone; no tokens provided")
        return Row("ERROR", "DKIM", name, ", ".join(held),
                   f"no tokens provided, and the zone holds {len(held)} SES DKIM records, not three in AWS form")
    if sorted(given) == held:
        return Row("CONFIRM", "DKIM", name, ", ".join(held))
    update = [cname("delete", t) for t in held if t not in given] + [cname("create", t) for t in given if t not in held]
    return Row("PROMPT", "DKIM", name, ", ".join(given), f"zone holds {', '.join(held)}", update=update)


def record_row(kind, rtype, name, want, held, account):
    """One MX, SPF or DMARC row: the zone's records of that kind at the name against `want`."""
    flags = ["--prio", str(want.prio)] if rtype == "MX" else []
    prio = f" prio={want.prio}" if rtype == "MX" else ""
    create = command(account, "create", rtype, name, want.content, *flags)
    if not held:
        return Row("CREATE", kind, name, want.content + prio, create=[create])
    if [(r.content, r.prio) for r in held] == [(want.content, want.prio)]:
        return Row("CONFIRM", kind, name, want.content + prio)
    zone = "; ".join(r.content + (f" prio={r.prio}" if rtype == "MX" else "") for r in held)
    update = [command(account, "delete", rtype, name, r.content) for r in held] + [create]
    return Row("PROMPT", kind, name, want.content + prio, f"zone holds {zone}", update=update)


def rows(args, records, zone, tokens, error, ses_pending, region, mail_from):
    """The four rows, in the order a reader checks them."""
    domain, account = args.domain, args.account

    def want(rtype, name, content, prio=0):
        return pc.normalize(pc.Record(pc.to_sub(name, zone), rtype, content, prio))

    def held(rtype, name, kind=lambda content: True):
        sub = pc.to_sub(name, zone)
        return [r for r in records if r.sub == sub and r.type == rtype and kind(r.content)]

    dmarc = f"_dmarc.{domain}"
    mx = f"feedback-smtp.{region}.amazonses.com"
    return [
        dkim_row(domain, tokens, error, zone_tokens(records, zone, domain), ses_pending, account),
        record_row("MX", "MX", mail_from, want("MX", mail_from, mx, 10), held("MX", mail_from), account),
        record_row("SPF", "TXT", mail_from, want("TXT", mail_from, SPF),
                   held("TXT", mail_from, pc.is_spf), account),
        record_row("DMARC", "TXT", dmarc, want("TXT", dmarc, args.dmarc or DMARC.format(domain=domain)),
                   held("TXT", dmarc, pc.is_dmarc), account),
    ]


def show_rows(found, zone):
    print(f"\nPorkbun: the zone {zone}, read before any change")
    for row in found:
        print(f"  {row.status:<8} {row.kind:<6} {row.name:<38} {row.value}{f'  [{row.note}]' if row.note else ''}")
    creates = [argv for row in found for argv in row.create]
    updates = [argv for row in found for argv in row.update]
    if creates:
        print("\nCreate: run each, without --apply first (a dry run), then with --apply")
        for argv in creates:
            print("  " + shlex.join(argv))
    if updates:
        print("\nUpdate records, only on the owner's word: run in order, each without --apply first")
        for argv in updates:
            print("  " + shlex.join(argv))


# --- senders.toml -----------------------------------------------------------------------


def prevailing(known):
    """The profile most listed domains use; a tie goes to the one listed first; None when none has one."""
    counts = {}
    for entry in known.get("domains", {}).values():
        profile = entry.get("profile") if isinstance(entry, dict) else None
        if profile:
            counts[profile] = counts.get(profile, 0) + 1
    return max(counts, key=counts.get) if counts else None  # max keeps the first of equal counts


def sender_block(domain, region, mail_from, default_region, known):
    """The domain's entry, its profile line commented out: the prevailing profile, or empty."""
    other = prevailing(known)
    lines = [f'[domains."{domain}"]', f'# profile = "{other}"' if other else "# profile = ",
             f'mail_from = "{mail_from}"']
    if region != default_region:
        lines.append(f'region    = "{region}"')
    lines.append("# sends_for = []")
    return "\n".join(lines) + "\n"


def add_sender(args, known, region, mail_from, path):
    """True when a change is pending. Appends, never rewrites, so the sender config keeps its comments.
    No sender config set, or no file at its path: nothing to do. A listed domain is flagged and never edited."""
    if path is None:
        print(f"\nSender config: not set (--senders FILE or {SENDERS_VARIABLE}); nothing added")
        return False
    if args.domain in known.get("domains", {}):
        print(f"\nSender config: {path} already lists {args.domain}; not changed")
        return False
    if not path.is_file():
        print(f"\nSender config: no {path}; nothing added (the file is never created)")
        return False
    block = sender_block(args.domain, region, mail_from,
                         known.get("defaults", {}).get("region", REGION), known)
    print(f"\nSender config: ADD to {path}\n" + "\n".join("  " + line for line in block.splitlines()))
    if not args.apply:
        return True
    text = path.read_text()
    path.write_text(text + ("" if text.endswith("\n") else "\n") + "\n" + block)
    print("  added")
    return False


def run(args):
    args.domain = args.domain.strip().rstrip(".").lower()
    if args.dmarc and not args.dmarc.lower().startswith("v=dmarc1"):
        raise Refusal(f"the record must start v=DMARC1: {args.dmarc}")
    if args.dkim and len(args.dkim) != 3:
        raise Refusal(f"--dkim takes SES's three tokens, got {len(args.dkim)}")
    path = senders_path(args)
    known = senders(path)
    region, mail_from = settings(args, known)

    ses_tokens, pending, ses_read = ses_half(args, region, mail_from)
    tokens, error = given_tokens([t.lower() for t in ses_tokens or ()], [t.lower() for t in args.dkim or ()])
    porkbun, zone = pb.connect(args.domain, args.account)
    pc.banner(porkbun)
    found = rows(args, porkbun.records(), zone, tokens, error, pending, region, mail_from)
    show_rows(found, zone)
    pending = add_sender(args, known, region, mail_from, path) or pending
    statuses = {row.status for row in found}
    if statuses == {"CONFIRM"} and not pending:
        print(f"\nCONFIRMED: {args.domain}" + ("" if ses_read else " (DNS only; SES not read)"))
    if "ERROR" in statuses:
        return 2
    return 1 if pending or statuses != {"CONFIRM"} else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("domain", help="the domain SES sends as (example.com)")
    parser.add_argument("--profile", help="an AWS profile that may change SES in the domain's account; without it the SES half is skipped")
    parser.add_argument("--senders", metavar="FILE", help=f"the sender config, TOML (or ${SENDERS_VARIABLE}; no default)")
    parser.add_argument("--region", help=f"the SES region (default: the sender config, else {REGION})")
    parser.add_argument("--mail-from", help=f"the MAIL FROM subdomain or host (default: the sender config, else {MAIL_FROM})")
    parser.add_argument("--dkim", nargs="+", metavar="TOKEN", help="SES's three Easy DKIM tokens, when SES cannot be read; without them DKIM is confirmed from the zone")
    parser.add_argument("--dmarc", metavar="TEXT", help="the _dmarc record (default p=reject, rua to dmarc@<domain>)")
    parser.add_argument("--account", metavar="NAME", help="the Porkbun account: the zone is read there, and each printed command passes it")
    parser.add_argument("--apply", action="store_true",
                        help="change SES and the sender config (without it, a dry run that changes nothing)")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except (Refusal, pc.Refusal) as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
