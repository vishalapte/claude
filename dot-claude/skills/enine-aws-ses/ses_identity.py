#!/usr/bin/env python3
"""Set up a domain to send through AWS SES: the identity at SES, then the six Porkbun records.

    ses_identity.py example.com --profile admin               # dry run: read SES, plan, print the records
    ses_identity.py example.com --profile admin --apply       # create the identity or change it, read it again
    ses_identity.py example.com --dkim T1 T2 T3               # no AWS: the records from SES's three DKIM tokens
    ses_identity.py example.com --sender-profile ses-sender   # also add the domain to the sender config

At SES, with --profile: the domain's identity exists, its Easy DKIM signs with a 2048-bit key,
and its MAIL FROM domain is hermes.<domain>. Without --profile, or when SES refuses the profile,
that half is skipped and the run says so. The DNS half is the six records, printed as
porkbun.py create commands for the skill to run; this script never calls Porkbun.

Region and MAIL FROM come from the sender config (--senders FILE, or $ENINE_MAIL_SENDERS) when it
lists the domain, else us-east-1 and hermes; --region and --mail-from override them. The sender
config has no default.

Exit 0 conforms or was applied, and the six commands were printed; 1 a change is pending (dry
run), SES holds something this script will not change, or the DKIM tokens are unknown; 2 could
not decide (a bad argument, an unreadable sender config).
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

# The sender config: per domain profile, mail_from, region; [defaults] region. The same
# variable enine-mail reads; no default.
SENDERS_VARIABLE = "ENINE_MAIL_SENDERS"
PORKBUN = Path(__file__).resolve().parent.parent / "enine-porkbun" / "porkbun.py"
REGION = "us-east-1"
MAIL_FROM = "hermes"
KEY_LENGTH = "RSA_2048_BIT"
SPF = "v=spf1 include:amazonses.com -all"
DMARC = "v=DMARC1; p=reject; pct=100; rua=mailto:dmarc@{domain}"


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
    """(tokens or None, True when something is pending or held). Prints the SES rows or the skip."""
    if not args.profile:
        print(f"SES: skipped (no --profile; pass an AWS profile that may change SES in {args.domain}'s account)")
        return None, False
    ses = Ses(args.profile, region, args.domain)
    try:
        identity = ses.identity()
    except Refusal as err:
        print(f"SES: skipped (profile {args.profile}: {err})")
        return None, False
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
    return tokens, pending


# --- the records ------------------------------------------------------------------------


def records(domain, tokens, region, mail_from, args):
    """The porkbun.py create commands, as argv lists: the three DKIM CNAMEs when tokens are known,
    then the MAIL FROM MX and SPF, then DMARC."""
    rows = [["CNAME", f"{t}._domainkey.{domain}", f"{t}.dkim.amazonses.com"] for t in tokens or ()]
    rows += [["MX", mail_from, args.mx or f"feedback-smtp.{region}.amazonses.com", "--prio", "10"],
             ["TXT", mail_from, args.spf or SPF],
             ["TXT", f"_dmarc.{domain}", args.dmarc or DMARC.format(domain=domain)]]
    account = ["--account", args.account] if args.account else []
    return [["python3", str(PORKBUN), "create", *row, *account] for row in rows]


def pick_tokens(ses_tokens, given):
    """(tokens, note): SES's tokens win; --dkim tokens that differ from SES's are a conflict."""
    if ses_tokens and given and sorted(ses_tokens) != sorted(given):
        return None, f"CONFLICT: --dkim differs from SES's tokens ({', '.join(ses_tokens)}); DKIM commands not printed"
    if ses_tokens:
        return list(ses_tokens), "from SES"
    if given:
        return list(given), "from --dkim"
    return None, "no tokens (no SES identity read, no --dkim); DKIM commands not printed"


# --- senders.toml -----------------------------------------------------------------------


def sender_block(domain, profile, region, mail_from, default_region):
    lines = [f'[domains."{domain}"]', f'profile   = "{profile}"', f'mail_from = "{mail_from}"']
    if region != default_region:
        lines.append(f'region    = "{region}"')
    return "\n".join(lines) + "\n"


def add_sender(args, known, region, mail_from, path):
    """True when a change is pending. Appends, never rewrites, so the sender config keeps its comments."""
    if path is None:
        raise Refusal(f"--sender-profile needs the sender config: pass --senders FILE or set {SENDERS_VARIABLE}")
    if args.domain in known.get("domains", {}):
        print(f"\nSender config: ok, {path} lists {args.domain}")
        return False
    if not path.is_file():
        raise Refusal(f"no {path} to add {args.domain} to")
    block = sender_block(args.domain, args.sender_profile, region, mail_from,
                         known.get("defaults", {}).get("region", REGION))
    print(f"\nSender config: ADD to {path}\n" + "\n".join("  " + line for line in block.splitlines()))
    if not args.apply:
        return True
    text = path.read_text()
    path.write_text(text + ("" if text.endswith("\n") else "\n") + "\n" + block)
    print("  added")
    return False


def run(args):
    args.domain = args.domain.strip().rstrip(".").lower()
    for text, prefix in ((args.spf, "v=spf1"), (args.dmarc, "v=DMARC1")):
        if text and not text.lower().startswith(prefix.lower()):
            raise Refusal(f"the record must start {prefix}: {text}")
    if args.dkim and len(args.dkim) != 3:
        raise Refusal(f"--dkim takes SES's three tokens, got {len(args.dkim)}")
    path = senders_path(args)
    known = senders(path)
    region, mail_from = settings(args, known)

    ses_tokens, pending = ses_half(args, region, mail_from)
    tokens, note = pick_tokens(ses_tokens, args.dkim)
    print(f"\nDKIM tokens: {note}")
    print("\nPorkbun records: run each, without --apply first (a dry run), then with --apply")
    for argv in records(args.domain, tokens, region, mail_from, args):
        print("  " + shlex.join(argv))
    if args.sender_profile:
        pending = add_sender(args, known, region, mail_from, path) or pending
    return 1 if pending or tokens is None else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("domain", help="the domain SES sends as (example.com)")
    parser.add_argument("--profile", help="an AWS profile that may change SES in the domain's account; without it the SES half is skipped")
    parser.add_argument("--senders", metavar="FILE", help=f"the sender config, TOML (or ${SENDERS_VARIABLE}; no default)")
    parser.add_argument("--region", help=f"the SES region (default: the sender config, else {REGION})")
    parser.add_argument("--mail-from", help=f"the MAIL FROM subdomain or host (default: the sender config, else {MAIL_FROM})")
    parser.add_argument("--dkim", nargs="+", metavar="TOKEN", help="SES's three Easy DKIM tokens, when SES cannot be read")
    parser.add_argument("--mx", metavar="HOST", help="the MAIL FROM MX host (default feedback-smtp.<region>.amazonses.com)")
    parser.add_argument("--spf", metavar="TEXT", help=f"the MAIL FROM SPF record (default {SPF!r})")
    parser.add_argument("--dmarc", metavar="TEXT", help="the _dmarc record (default p=reject, rua to dmarc@<domain>)")
    parser.add_argument("--account", metavar="NAME", help="the Porkbun account, passed to each printed command")
    parser.add_argument("--sender-profile", metavar="NAME",
                        help="add the domain to the sender config with this send-only AWS profile")
    parser.add_argument("--apply", action="store_true",
                        help="change SES and the sender config (without it, a dry run that changes nothing)")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
