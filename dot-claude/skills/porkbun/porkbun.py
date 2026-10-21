#!/usr/bin/env python3
"""Create, update or delete one DNS record at Porkbun, or list the records at a name.

Usage
  porkbun.py <verb> <TYPE> <name> [target] [--prio N] [--ttl N] [--apply]
  porkbun.py list <name> [--type TYPE]

Examples (each is a dry run that writes nothing; add --apply to write)
  porkbun.py create A     example.com      192.168.0.1        A record at the apex
  porkbun.py create A     www.example.com  192.168.0.1        A record at www
  porkbun.py create CNAME blog.example.com host.example.net   blog is an alias of another host
  porkbun.py create TXT   example.com      "v=spf1 -all"      quote a target that has spaces
  porkbun.py create MX    example.com      mx.example.net --prio 10
  porkbun.py update A     example.com      192.168.0.2        change the one A record at the apex
  porkbun.py delete A     www.example.com                     remove every A record at www
  porkbun.py delete A     www.example.com  192.168.0.1        remove only that one
  porkbun.py list example.com                                 every record in the zone
  porkbun.py list www.example.com --type A                    the A records at www and under it

Verbs
  create   add the record; does nothing when an identical record exists
  update   change the one <TYPE> record at <name>; CONFLICT when there is none, or several
  delete   remove every <TYPE> record at <name>, or only the one holding <target>
  list     print the records at <name> and every name under it; writes nothing

Arguments
  <TYPE>     {types}
  <name>     the record's full name: example.com (apex), www.example.com, *.example.com
  <target>   the value: IPv4 for A, IPv6 for AAAA, a host for CNAME/ALIAS/MX/NS, text for TXT
  --prio N   MX or SRV priority (create: 0; update: keeps the record's)
  --ttl N    seconds (create: 600; update: keeps the record's)
  --apply    write, re-read the zone, run Porkbun's preflight
  --type T   list only records of type T

Rows printed
  ok        already as asked
  CREATE    adds the record
  EDIT      changes it in place; the note shows the old value
  DELETE    removes it
  CONFLICT  cannot be applied (a CNAME sharing a name, an update with none or several)

Exit
  0  done, already as asked, or listed
  1  changes pending (dry run), a conflict, or the check after --apply failed
  2  refused: a bad argument, the credentials, or Porkbun's API

Credentials: ~/.config/porkbun/credentials (PORKBUN_API_KEY, PORKBUN_SECRET_API_KEY), mode 600.
They are read only after the arguments check out. The zone is the longest suffix of <name>
the account holds.
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
TTL = 600  # Porkbun's usual minimum


def zone_of(account, name):
    """The longest suffix of `name` that the account holds, by /domain/listAll's exact match."""
    labels = name.split(".")
    for i in range(len(labels) - 1):
        candidate = ".".join(labels[i:])
        if account.call("POST", "/domain/listAll", {"domain": candidate}).get("domains"):
            return candidate
    raise pc.Refusal(f"no domain in this Porkbun account holds {name}")


def matches(record, want):
    """Same content; prio and ttl count only where the prompt gave them (None otherwise)."""
    return record.content == want.content and want.prio in (None, record.prio) and want.ttl in (None, record.ttl)


def plan(verb, want, current):
    """Steps that make one record true at `want.sub`.

    create adds the record unless an identical one exists; a CNAME cannot share its name
    (RFC 1034). update edits the one `want.type` record at the name; none, or several, is a
    CONFLICT. delete removes every `want.type` record at the name, or only the one whose
    content is `want.content` when that is not empty.
    """
    same = [r for r in current if r.sub == want.sub and r.type == want.type]
    filled = want._replace(prio=want.prio or 0, ttl=want.ttl or TTL)
    if verb == "delete":
        gone = [r for r in same if want.content in ("", r.content)]
        return [pc.Step("DELETE", r, r.id) for r in gone] or [pc.Step("ok", filled, note="absent")]
    match = next((r for r in same if matches(r, want)), None)
    if verb == "create":
        clash = sorted({r.type for r in current if r.sub == want.sub and (r.type == "CNAME") != (want.type == "CNAME")})
        if match:
            return [pc.Step("ok", match, match.id)]
        if clash:
            return [pc.Step("CONFLICT", filled, note=f"{', '.join(clash)} already at this name (RFC 1034)")]
        if want.type == "CNAME" and same:
            return [pc.Step("CONFLICT", filled, note="a name holds one CNAME; use update")]
        return [pc.Step("CREATE", filled, note=f"{len(same)} other {want.type} at this name stay" if same else "")]
    if not same:
        return [pc.Step("CONFLICT", filled, note=f"no {want.type} record at this name; use create")]
    if len(same) > 1:
        note = f"{len(same)} {want.type} records at this name; delete the extras first"
        return [pc.Step("CONFLICT", r, r.id, note) for r in same]
    old = same[0]
    if match:
        return [pc.Step("ok", old, old.id)]
    new = old._replace(content=want.content, prio=old.prio if want.prio is None else want.prio,
                       ttl=want.ttl or old.ttl)
    was = f"was {old.content}" + (f" ttl {old.ttl}" if new.ttl != old.ttl else "")
    return [pc.Step("EDIT", new, old.id, was)]


def apply(porkbun, steps):
    write = {
        "DELETE": lambda step: porkbun.delete(step.id),
        "EDIT": lambda step: porkbun.edit(step.id, step.record),
        "CREATE": lambda step: porkbun.create(step.record),
    }
    for step in steps:
        write[step.action](step)


def connect(name):
    """(client, zone) for `name`. The first point a run reads the credentials or calls Porkbun."""
    key, secret = pc.porkbun_credentials()
    domain = zone_of(pc.Porkbun(key, secret, name), name)
    return pc.Porkbun(key, secret, domain), domain


def under(record, sub):
    """True when the record is at `sub` or at a name under it; the apex ("") holds the zone."""
    return not sub or record.sub == sub or record.sub.endswith("." + sub)


def list_records(name, rtype):
    porkbun, domain = connect(name)
    sub = pc.to_sub(name, domain)
    rows = sorted((r for r in porkbun.records() if under(r, sub) and rtype in (None, r.type)),
                  key=lambda r: (r.sub, r.type, r.content))
    kind = f"{rtype} records" if rtype else "records"
    print(f"Porkbun {kind} at {name} and under it (zone {domain}): {len(rows)}")
    for r in rows:
        fqdn = f"{r.sub}.{domain}" if r.sub else domain
        prio = f" prio={r.prio}" if r.type in ("MX", "SRV") else ""
        print(f"  {r.type:<5} {fqdn:<38} {r.content}{prio}  ttl={r.ttl}")
    return 0


def run(args):
    name = args.name.strip().rstrip(".").lower()
    if args.verb == "list":
        return list_records(name, args.type)
    content = " ".join(args.content).strip()
    if args.verb != "delete" and not content:
        raise pc.Refusal(f"{args.verb} needs the record's content")
    if content and args.type in ADDRESS:
        try:
            ADDRESS[args.type](content)
        except ValueError as err:
            raise pc.Refusal(f"{args.type} content must be an IPv{4 if args.type == 'A' else 6} address: {content}") from err
    porkbun, domain = connect(name)
    want = pc.normalize(pc.Record(pc.to_sub(name, domain), args.type, content, args.prio, args.ttl))

    steps = plan(args.verb, want, porkbun.records())
    changes = [s for s in steps if s.action in ("CREATE", "EDIT", "DELETE")]
    print(f"Porkbun {args.verb} {args.type} {name} (zone {domain}): {len(changes)} changes")
    pc.show(steps, domain)

    if changes and not args.apply:
        print("\nDry run: nothing written. Run again with --apply to make these changes.")
    if changes and args.apply:
        apply(porkbun, changes)
        steps = plan(args.verb, want, porkbun.records())
        left = [s for s in steps if s.action != "ok"]
        print(f"\nAfter apply: {len(left)} rows not ok")
        pc.show(left, domain)
    drift = any(s.action != "ok" for s in steps)

    for warning in porkbun.warnings:
        print(f"\nPorkbun warning: {warning}")
    blockers, warnings = porkbun.preflight()
    print(f"\nPorkbun preflight: blockers={blockers or 'none'} warnings={warnings or 'none'}")
    return 1 if drift or blockers else 0


def help_text():
    """The usage, verbs and types: what a bare call prints, without reading the credentials."""
    return __doc__.strip().replace("{types}", " ".join(TYPES))


def main():
    if len(sys.argv) == 1:
        print(help_text())
        sys.exit(2)
    parser = argparse.ArgumentParser(description=help_text(), formatter_class=argparse.RawDescriptionHelpFormatter)
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in VERBS:
        write = verbs.add_parser(verb, help=f"{verb} one record (a dry run without --apply)")
        write.add_argument("type", type=str.upper, choices=TYPES, help="the record type")
        write.add_argument("name", help="the record's full name (the domain itself for the apex)")
        write.add_argument("content", nargs="*", metavar="target",
                           help="the record's value; optional for delete, where it picks one record")
        write.add_argument("--prio", type=int, help="priority, for MX and SRV (default 0 on create)")
        write.add_argument("--ttl", type=int, help=f"time to live in seconds (default {TTL} on create)")
        write.add_argument("--apply", action="store_true",
                           help="change DNS (without it, a dry run that writes nothing)")
    listing = verbs.add_parser("list", help="print the records at a name and under it")
    listing.add_argument("name", help="the name; the domain itself lists the whole zone")
    listing.add_argument("--type", type=str.upper, choices=TYPES, help="only records of this type")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except pc.Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
