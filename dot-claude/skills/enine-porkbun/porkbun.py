#!/usr/bin/env python3
"""Create, update or delete one DNS record at Porkbun, list the account's domains, show a
domain's records, capture the records a domain publishes elsewhere into Porkbun's zone, list
or restore Porkbun's restore points, or run Porkbun's preflight for a change.

Usage
  porkbun.py <verb> <TYPE> <name> [target] [--prio N] [--ttl N] [--match P] [--apply] [--account NAME]
  porkbun.py list [--account NAME]
  porkbun.py domain <name> [--type TYPE] [--account NAME]
  porkbun.py capture <domain> [--apply] [--account NAME]
  porkbun.py history <domain> [--account NAME]
  porkbun.py restore <domain> <point> [--prune] [--apply] [--account NAME]
  porkbun.py check <domain> [--intent INTENT] [--account NAME]

Examples (each is a dry run that writes nothing; add --apply to write)
  porkbun.py create A     example.com      192.168.0.1        A record at the apex
  porkbun.py create A     www.example.com  192.168.0.1        A record at www
  porkbun.py create CNAME blog.example.com host.example.net   blog is an alias of another host
  porkbun.py create TXT   example.com      "v=spf1 -all"      quote a target that has spaces
  porkbun.py create MX    example.com      mx.example.net --prio 10
  porkbun.py update A     example.com      192.168.0.2        change the one A record at the apex
  porkbun.py update TXT   example.com      "v=spf1 include:_spf.example.net -all" --match v=spf1
                                                              change only the apex TXT starting v=spf1
  porkbun.py delete A     www.example.com                     remove every A record at www
  porkbun.py delete A     www.example.com  192.168.0.1        remove only that one
  porkbun.py list                                             every domain in the account
  porkbun.py domain example.com                               every record in the zone
  porkbun.py domain www.example.com --type A                  the A records at www and under it
  porkbun.py list --account acme                              every domain in the account acme
  porkbun.py capture example.com                              copy what example.com publishes today
  porkbun.py history example.com                              the zone's restore points, newest first
  porkbun.py restore example.com 1234 --prune                 put the zone back as it was at point 1234
  porkbun.py check example.com --intent move-nameservers      what moving the nameservers would break

Verbs
  create   add the record; does nothing when an identical record exists
  update   change the one <TYPE> record at <name> (with --match, the one whose value starts
           with it); CONFLICT when there is none, or several
  delete   remove every <TYPE> record at <name>, or only the one holding <target>
  list     print every domain in the account, one row per name with its TLDs; writes nothing
  domain   print the records at <name> and every name under it; writes nothing
  capture  ask the domain's live nameservers what they publish (Porkbun's /dns/scan, at most
           20 an hour) and create in Porkbun's zone each record it lacks, deleting Porkbun's
           parking placeholders; run it before the nameservers move to Porkbun. A scan cannot
           list a zone, so a name nobody guesses is missed
  history  print the zone's restore points, newest first; Porkbun takes one before the first
           write each hour, before an import and before a restore; writes nothing
  restore  put the zone back as it was at <point>: add back what is missing; with --prune also
           delete what was added since. A dry run shows Porkbun's diff and Porkbun's own dry run
  check    print Porkbun's preflight for --intent ({intents}; default general):
           what it would break, what it would warn about, and every check that ran; writes nothing

Arguments
  <TYPE>     {types}
  <name>     the record's full name: example.com (apex), www.example.com, *.example.com
  <target>   the value: IPv4 for A, IPv6 for AAAA, a host for CNAME/ALIAS/MX/NS, text for TXT
  --prio N   MX or SRV priority (create: 0; update: keeps the record's)
  --ttl N    seconds (create: 600; update: keeps the record's)
  --match P  update: edit only the <TYPE> record whose value starts with P (any case)
  --apply    write, re-read the zone, run Porkbun's preflight
  --type T   domain: only records of type T
  --account NAME  use the Porkbun account in ~/.config/porkbun/credentials.d/NAME

Rows printed
  ok        already as asked
  CREATE    adds the record; a dry run notes Porkbun's own check of it, which writes nothing
  EDIT      changes it in place; the note shows the old value
  DELETE    removes it
  CONFLICT  cannot be applied (a CNAME sharing a name, an update with none or several, a
            second SPF record)
  FAILED    refused by Porkbun (the note names its code, hint and request id), or skipped
            because an earlier write at its name was refused; other names are still written

Exit
  0  done, already as asked, or printed
  1  changes pending (dry run), a conflict, a refused write, the check after --apply failed,
     a record a restore could not put back, or a preflight blocker
  2  refused: a bad argument, the credentials, Porkbun's API, or a scan that found nothing

Credentials: ~/.config/porkbun/credentials (PORKBUN_API_KEY, PORKBUN_SECRET_API_KEY), mode 600,
holds the default account. A sandbox key (pk1_sb_) works on Porkbun's test system: the run
prints SANDBOX and refuses any answer not marked sandbox. Each other account has its own file of the same two lines,
~/.config/porkbun/credentials.d/<NAME>, used only with --account NAME. They are read only
after the arguments check out. The zone is the longest suffix of <name> the account holds.
"""

import argparse
import ipaddress
import sys
from pathlib import Path

# The Porkbun client, credentials, plan rows and normal form have one home: porkbun_client.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import porkbun_client as pc  # noqa: E402

VERBS = ("create", "update", "delete")
# The types /dns/create accepts (Porkbun's OpenAPI spec, CreateDnsRequest.type).
TYPES = ("A", "AAAA", "MX", "CNAME", "ALIAS", "TXT", "NS", "SRV", "TLSA", "CAA", "SSHFP", "HTTPS", "SVCB", "DS")
ADDRESS = {"A": ipaddress.IPv4Address, "AAAA": ipaddress.IPv6Address}
LIST_PAGE = 1000  # /domain/listAll returns up to this many domains per call
# What /dns/preflight checks for: the change about to be made (Porkbun's OpenAPI spec).
INTENTS = ("general", "move-nameservers", "transfer-out", "enable-dnssec")


def zone_of(account, name, label=None):
    """The longest suffix of `name` that the account holds, by /domain/listAll's exact match.
    The refusal names the other accounts in credentials.d/, which --account can pick."""
    labels = name.split(".")
    for i in range(len(labels) - 1):
        candidate = ".".join(labels[i:])
        if account.call("POST", "/domain/listAll", {"domain": candidate}).get("domains"):
            return candidate
    others = [a for a in pc.porkbun_accounts() if a != label]
    hint = f"; other accounts: {', '.join(others)} (pass --account NAME)" if others else ""
    raise pc.Refusal(f"no domain in Porkbun account {label or 'default'} holds {name}{hint}")


def connect(name, account=None):
    """(client, zone) for `name`. The first point a run reads the credentials or calls Porkbun."""
    key, secret = pc.porkbun_credentials(account)
    domain = zone_of(pc.Porkbun(key, secret, name), name, account)
    return pc.Porkbun(key, secret, domain), domain


def under(record, sub):
    """True when the record is at `sub` or at a name under it; the apex ("") holds the zone."""
    return not sub or record.sub == sub or record.sub.endswith("." + sub)


def list_records(name, rtype, account=None):
    porkbun, domain = connect(name, account)
    sub = pc.to_sub(name, domain)
    pc.banner(porkbun)
    rows = sorted((r for r in porkbun.records() if under(r, sub) and rtype in (None, r.type)),
                  key=lambda r: (r.sub, r.type, r.content))
    kind = f"{rtype} records" if rtype else "records"
    print(f"Porkbun {kind} at {name} and under it (zone {domain}): {len(rows)}")
    for r in rows:
        fqdn = f"{r.sub}.{domain}" if r.sub else domain
        prio = f" prio={r.prio}" if r.type in ("MX", "SRV") else ""
        print(f"  {r.type:<5} {fqdn:<38} {r.content}{prio}  ttl={r.ttl}")
    return 0


def list_domains(label=None):
    """Every domain in the account, one row per name with its TLDs, from /domain/listAll."""
    account = pc.Porkbun(*pc.porkbun_credentials(label), "")
    pc.banner(account)
    domains, start = [], 0
    while True:
        page = account.call("POST", "/domain/listAll", {"start": start}).get("domains") or []
        domains += page
        if len(page) < LIST_PAGE:
            break
        start += len(page)
    names = {}
    for item in domains:
        domain = item["domain"].lower()
        tld = (item.get("tld") or domain.split(".", 1)[-1]).lower()
        names.setdefault(domain[: -len(tld) - 1], []).append(tld)
    named = f" {label}" if label else ""
    print(f"Porkbun account{named}: {len(domains)} domains under {len(names)} names")
    if names:
        width = max(len("NAME"), *map(len, names))
        print(f"  {'NAME':<{width}}  TLD")
        for name in sorted(names):
            print(f"  {name:<{width}}  {', '.join(sorted(names[name]))}")
    return 0


def captured(found, zone):
    """Requests that copy `found` into `zone`: the parking placeholders go, each found record is
    created unless the zone holds it, and a found SPF record the zone holds another of is a
    CONFLICT (RFC 7208 allows one). Found records pointing at the parking host are not copied."""
    spf = [r for r in zone if r.sub == "" and r.type == "TXT" and r.content.lower().startswith("v=spf1")]
    requests = pc.parking(zone)
    for record in found:
        if record.content in pc.PORKBUN_PARKING:
            continue
        other = [r for r in spf if r.content != record.content]
        if record.sub == "" and record.type == "TXT" and record.content.lower().startswith("v=spf1") and other:
            note = f"the zone holds another SPF record, {other[0].content}; update it to this one first"
            requests.append(pc.Step("CONFLICT", record, note=note))
        else:
            requests.append(pc.Request("create", record))
    return requests


def capture(name, write, account=None):
    porkbun, domain = connect(name, account)
    found = porkbun.scan()
    if not found:
        raise pc.Refusal(f"Porkbun's scan found no records at {domain}'s live nameservers; nothing to capture")
    title = f"Porkbun capture {domain}: {len(found)} records found at its live nameservers"
    return 1 if pc.converge(porkbun, title, lambda zone: captured(found, zone), write) else 0


def history_row(record):
    """One record from /dns/diff, whose names are fully qualified."""
    prio = f" prio={record.get('prio')}" if record.get("type") in ("MX", "SRV") else ""
    return f"{record.get('type', '?'):<5} {record.get('name', '?'):<38} {record.get('content', '')}{prio}"


def history(name, account=None):
    porkbun, domain = connect(name, account)
    pc.banner(porkbun)
    points = porkbun.call("GET", f"/dns/history/{domain}").get("restorePoints") or []
    print(f"Porkbun restore points for {domain}, newest first: {len(points)}")
    for point in points:
        live = "  (the live zone)" if point.get("matchesLive") else ""
        print(f"  {point.get('id', '?'):>8}  {point.get('takenAt', '?')}  {point.get('reason', '?'):<20} "
              f"{point.get('source', '?'):<6} {point.get('recordCount', '?')} records{live}")
    return 0


def restore(name, point, prune, write, account=None):
    """Show what restoring `point` adds and deletes, from /dns/diff and Porkbun's dry run; with
    `write`, restore it. Porkbun saves the zone first, and names that save to undo it."""
    porkbun, domain = connect(name, account)
    pc.banner(porkbun)
    diff = porkbun.call("GET", f"/dns/diff/{domain}/{point}")
    missing, extra = diff.get("missing") or [], diff.get("extra") or []
    taken = diff.get("snapshot") or {}
    print(f"Porkbun restore {domain} to point {point} ({taken.get('takenAt', '?')}, {taken.get('reason', '?')}): "
          f"{len(missing) + (len(extra) if prune else 0)} changes")
    for record in missing:
        print(f"  ADD      {history_row(record)}")
    for record in extra:
        print(f"  {'DELETE' if prune else 'stays':<8} {history_row(record)}{'' if prune else '  [added since; --prune deletes it]'}")
    request = {"snapshotId": point, "prune": prune}
    changes = bool(missing or (prune and extra))
    if not write:
        verdict = porkbun.call("POST", f"/dns/restore/{domain}", {**request, "dryRun": True})
        if verdict.get("previousStateSavedAs") is not None:
            raise pc.Refusal(f"Porkbun's dry-run restore saved the zone as point {verdict['previousStateSavedAs']}, "
                             f"so it may have restored; check porkbun.py history {domain}")
        print(f"\nPorkbun dry run: adds {verdict.get('restored', 0)}, deletes {verdict.get('removed', 0)}")
        failed = report_failed(verdict)
        if changes:
            print("\nDry run: nothing written. Run again with --apply to restore.")
        return 1 if changes or failed else 0
    done = porkbun.call("POST", f"/dns/restore/{domain}", request)
    print(f"\nRestored: added {done.get('restored', 0)}, deleted {done.get('removed', 0)}")
    failed = report_failed(done)
    for warning in porkbun.warnings:
        print(f"\nPorkbun warning: {warning}")
    if done.get("previousStateSavedAs") is not None:
        print(f"\nTo undo this restore: porkbun.py restore {domain} {done['previousStateSavedAs']} --prune --apply")
    return 1 if failed else 0


def report_failed(body):
    """Print the records Porkbun could not put back (parking and other masked types); True when any."""
    failed = body.get("failed") or []
    for item in failed:
        print(f"  FAILED   {item.get('type', '?'):<5} {item.get('name', '?'):<38} [{item.get('reason', '')}]")
    return bool(failed)


def check(name, intent, account=None):
    porkbun, domain = connect(name, account)
    pc.banner(porkbun)
    body = porkbun.call("GET", f"/dns/preflight/{domain}?intent={intent}")
    blockers, warnings = body.get("blockers") or [], body.get("warnings") or []
    print(f"Porkbun preflight for {domain}, intent {intent}: "
          f"blockers={', '.join(blockers) or 'none'} warnings={', '.join(warnings) or 'none'}")
    for item in body.get("checks") or []:
        state = "ok" if item.get("ok") else str(item.get("severity", "?")).upper()
        print(f"  {state:<8} {item.get('id', '?'):<24} {item.get('title', '')}")
        if not item.get("ok"):
            for line in (item.get("detail"), item.get("next_action")):
                if line:
                    print(f"           {line}")
    return 1 if blockers else 0


def run(args):
    if args.verb == "list":
        return list_domains(args.account)
    name = args.name.strip().rstrip(".").lower()
    if args.verb == "domain":
        return list_records(name, args.type, args.account)
    if args.verb == "capture":
        return capture(name, args.apply, args.account)
    if args.verb == "history":
        return history(name, args.account)
    if args.verb == "restore":
        return restore(name, args.point, args.prune, args.apply, args.account)
    if args.verb == "check":
        return check(name, args.intent, args.account)
    content = " ".join(args.content).strip()
    if args.verb != "delete" and not content:
        raise pc.Refusal(f"{args.verb} needs the record's content")
    if content and args.type in ADDRESS:
        try:
            ADDRESS[args.type](content)
        except ValueError as err:
            raise pc.Refusal(f"{args.type} content must be an IPv{4 if args.type == 'A' else 6} address: {content}") from err
    porkbun, domain = connect(name, args.account)
    want = pc.normalize(pc.Record(pc.to_sub(name, domain), args.type, content, args.prio, args.ttl))
    request = pc.Request(args.verb, want, match=getattr(args, "match", None))
    title = f"Porkbun {args.verb} {args.type} {name} (zone {domain})"
    return 1 if pc.converge(porkbun, title, lambda current: [request], args.apply) else 0


def help_text():
    """The usage, verbs and types: what a bare call prints, without reading the credentials."""
    return __doc__.strip().replace("{types}", " ".join(TYPES)).replace("{intents}", ", ".join(INTENTS))


def main():
    if len(sys.argv) == 1:
        print(help_text())
        sys.exit(2)
    parser = argparse.ArgumentParser(description=help_text(), formatter_class=argparse.RawDescriptionHelpFormatter)
    account_help = "the Porkbun account in ~/.config/porkbun/credentials.d/NAME (default: the credentials file)"
    parser.add_argument("--account", metavar="NAME", help=account_help)
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in VERBS:
        write = verbs.add_parser(verb, help=f"{verb} one record (a dry run without --apply)")
        write.add_argument("type", type=str.upper, choices=TYPES, help="the record type")
        write.add_argument("name", help="the record's full name (the domain itself for the apex)")
        write.add_argument("content", nargs="*", metavar="target",
                           help="the record's value; optional for delete, where it picks one record")
        write.add_argument("--prio", type=int, help="priority, for MX and SRV (default 0 on create)")
        write.add_argument("--ttl", type=int, help=f"time to live in seconds (default {pc.TTL} on create)")
        if verb == "update":
            write.add_argument("--match", metavar="PREFIX",
                               help="edit only the record whose value starts with PREFIX (any case)")
        write.add_argument("--apply", action="store_true",
                           help="change DNS (without it, a dry run that writes nothing)")
    verbs.add_parser("list", help="print every domain in the account, grouped by name")
    domain = verbs.add_parser("domain", help="print the records at a name and under it")
    domain.add_argument("name", help="the name; the domain itself prints the whole zone")
    domain.add_argument("--type", type=str.upper, choices=TYPES, help="only records of this type")
    capture_parser = verbs.add_parser("capture", help="copy the records the domain publishes today into Porkbun's zone")
    capture_parser.add_argument("name", metavar="domain", help="the domain")
    capture_parser.add_argument("--apply", action="store_true",
                                help="change DNS (without it, a dry run that writes nothing)")
    verbs.add_parser("history", help="print the zone's restore points").add_argument("name", metavar="domain")
    restore_parser = verbs.add_parser("restore", help="put the zone back as it was at a restore point")
    restore_parser.add_argument("name", metavar="domain")
    restore_parser.add_argument("point", type=int, help="the restore point's id, from history")
    restore_parser.add_argument("--prune", action="store_true", help="also delete the records added since the point")
    restore_parser.add_argument("--apply", action="store_true",
                                help="change DNS (without it, a dry run that writes nothing)")
    check_parser = verbs.add_parser("check", help="print Porkbun's preflight for a change")
    check_parser.add_argument("name", metavar="domain")
    check_parser.add_argument("--intent", choices=INTENTS, default="general", help="the change about to be made")
    # Before the verb or after it: a verb's own --account leaves the top-level one alone when absent.
    for verb_parser in verbs.choices.values():
        verb_parser.add_argument("--account", metavar="NAME", default=argparse.SUPPRESS, help=account_help)
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except pc.Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
