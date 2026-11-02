#!/usr/bin/env python3
"""Create, update or delete a Forward Email domain or alias, list the account's domains, or show
one domain's settings and aliases.

Usage
  forwardemail.py <verb> domain <domain> [--set K=V ...] [--apply]
  forwardemail.py <verb> alias <name@domain> [recipient ...] [--set K=V ...] [--apply]
  forwardemail.py list
  forwardemail.py domain <domain>

Examples (each write is a dry run that writes nothing; add --apply to write)
  forwardemail.py create domain example.com                       add the domain to the account
  forwardemail.py create domain example.com --set catchall=false  without the catch-all alias
  forwardemail.py update domain example.com --set ignore_mx_check=true
                                                                  keep the domain's current mail host
  forwardemail.py delete domain example.com                       remove the domain from the account
  forwardemail.py create alias info@example.com alice@example.net forward info@ to one address
  forwardemail.py update alias info@example.com alice@example.net bob@example.net
                                                                  forward it to these two instead
  forwardemail.py update alias info@example.com --set is_enabled=false
  forwardemail.py delete alias info@example.com                   remove the alias
  forwardemail.py list                                            every domain on the account
  forwardemail.py domain example.com                              its settings and every alias

Verbs
  create   add the domain or alias; ok when it exists with the values given, and CONFLICT
           when it exists with others, which update changes
  update   change the values given on the domain or alias; CONFLICT when there is none (use create)
  delete   remove the domain or alias; ok when there is none. The row says what it holds:
           a domain's aliases, an alias's mailbox
  list     print every domain on the account with its plan and DNS checks; writes nothing
  domain   print the domain's settings and every alias with its recipients; writes nothing

Arguments
  <domain>      the domain: example.com
  <name@domain> the alias: info@example.com; `*@example.com` is the catch-all
  recipient     an address, a domain, a URL or an IP the alias forwards to; on update, the
                recipients replace the alias's list
  --set K=V     one field, repeatable. true and false are booleans, digits a number;
                recipients and labels take comma-separated values.
                domain create: {domain_create_keys}
                domain update: {domain_keys}
                alias:         {alias_keys}
  --apply       write, then read back and show what is not as asked

Rows printed
  ok        already as asked
  CREATE    adds it; the note shows the values sent
  EDIT      changes it; each changed field is shown old -> new
  DELETE    removes it; the note says what it holds
  CONFLICT  cannot be applied (create of one that differs, update of one that does not exist)
  FAILED    refused by Forward Email; the note is its message
  A field that a read does not return (an alias's description, a domain's catchall) is sent but
  cannot be compared; the note names it.

Exit
  0  done, already as asked, or printed
  1  changes pending (dry run), a conflict, a refused write, or a field not as asked after --apply
  2  refused: a bad argument, the credentials, or Forward Email's API

Credentials: ~/.config/forwardemail/credentials (FORWARDEMAIL_API_KEY), mode 600, read only
after the arguments check out. A domain's DNS records are not set here: /enine-porkbun-forwardemail
sets them at Porkbun.
"""

import argparse
import sys
from collections import namedtuple
from pathlib import Path

# The Forward Email client and its credentials have one home: forwardemail_client.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import forwardemail_client as fc  # noqa: E402
from forwardemail_client import Refusal  # noqa: E402

VERBS = ("create", "update", "delete")
KINDS = ("domain", "alias")
# The one write a run makes: `body` is what is sent, `id` the alias id a PUT or DELETE names.
# `changes` holds (field, old, new) for an EDIT or a CONFLICT.
Row = namedtuple("Row", "action kind target note body id changes", defaults=("", None, None, ()))


def split_alias(address):
    """(name, domain) of name@domain. The domain is lower case; the name is kept as given."""
    name, at, domain = address.strip().rpartition("@")
    if not at or not name or "." not in domain:
        raise Refusal(f"an alias is name@domain: {address}")
    return name, clean(domain)


def clean(domain):
    return domain.strip().rstrip(".").lower()


def settings_of(pairs, allowed, where):
    """The --set K=V pairs as a body; a key the API page does not list for `where` is refused."""
    body = {}
    for pair in pairs or ():
        key, eq, text = pair.partition("=")
        key = key.strip()
        if not eq or key not in allowed:
            raise Refusal(f"--set takes K=V with K one of the {where} fields: {', '.join(allowed)} (got {pair})")
        body[key] = fc.value_of(key, text.strip())
    return body


def read_back(obj, key):
    """(found, value) of `key` in what the API returned. The vacation_responder_* fields come
    back nested under vacation_responder; recipients is the alias's list."""
    if key in obj:
        return True, obj[key]
    nested = obj.get("vacation_responder")
    prefix = "vacation_responder_"
    if key.startswith(prefix) and isinstance(nested, dict) and key[len(prefix):] in nested:
        return True, nested[key[len(prefix):]]
    return False, None


def same(old, new):
    if isinstance(new, list) or isinstance(old, list):
        return sorted(map(str, old or [])) == sorted(map(str, new or []))
    return str(old).lower() == str(new).lower()


def compare(obj, body):
    """(changes, unread): each (field, old, new) that differs, and each field a read does not return."""
    changes, unread = [], []
    for key, new in body.items():
        found, old = read_back(obj, key)
        if not found:
            unread.append(key)
        elif not same(old, new):
            changes.append((key, old, new))
    return changes, unread


def shown(body):
    return ", ".join(f"{k}={','.join(map(str, v)) if isinstance(v, list) else v}" for k, v in body.items())


def unread_note(unread):
    return f"not read back: {', '.join(unread)}" if unread else ""


# --- plans: what one request would do, from what the API holds now -----------------------


def plan_domain(fe, verb, domain, body):
    obj = fe.domain(domain)
    if verb == "create":
        if obj is None:
            return Row("CREATE", "domain", domain, shown(body), body)
        changes, unread = compare(obj, body)
        if changes:
            return Row("CONFLICT", "domain", domain, "exists with other values; update changes it", changes=changes)
        return Row("ok", "domain", domain, unread_note(unread))
    if verb == "update":
        if obj is None:
            return Row("CONFLICT", "domain", domain, "not on the account; use create")
        changes, unread = compare(obj, body)
        if changes or unread:
            return Row("EDIT", "domain", domain, unread_note(unread), body, changes=changes)
        return Row("ok", "domain", domain)
    if obj is None:
        return Row("ok", "domain", domain, "not on the account")
    count = len(fe.aliases(domain))
    return Row("DELETE", "domain", domain, f"holds {count} alias{'' if count == 1 else 'es'}")


def plan_alias(fe, verb, address, body):
    name, domain = split_alias(address)
    target = f"{name}@{domain}"
    obj = fe.alias(domain, name)
    if verb == "create":
        if obj is None:
            return Row("CREATE", "alias", target, shown(body), {"name": name, **body})
        changes, unread = compare(obj, body)
        if changes:
            return Row("CONFLICT", "alias", target, "exists with other values; update changes it", changes=changes)
        return Row("ok", "alias", target, unread_note(unread))
    if verb == "update":
        if obj is None:
            return Row("CONFLICT", "alias", target, "no such alias; use create")
        changes, unread = compare(obj, body)
        if changes or unread:
            return Row("EDIT", "alias", target, unread_note(unread), body, obj.get("id"), changes)
        return Row("ok", "alias", target)
    if obj is None:
        return Row("ok", "alias", target, "no such alias")
    holds = [f"forwards to {', '.join(obj.get('recipients') or []) or 'nobody'}"]
    if obj.get("has_imap"):
        holds.append("has a mailbox")
    return Row("DELETE", "alias", target, "; ".join(holds), id=obj.get("id"))


def plan(fe, verb, kind, target, body):
    return (plan_domain if kind == "domain" else plan_alias)(fe, verb, target, body)


def write(fe, row):
    """Send the row's one write. A refusal from Forward Email is a FAILED row, not a stop."""
    domain = row.target.rpartition("@")[2]
    calls = {
        ("CREATE", "domain"): lambda: fe.create_domain(row.target, row.body),
        ("EDIT", "domain"): lambda: fe.update_domain(row.target, row.body),
        ("DELETE", "domain"): lambda: fe.delete_domain(row.target),
        ("CREATE", "alias"): lambda: fe.create_alias(domain, row.body),
        ("EDIT", "alias"): lambda: fe.update_alias(domain, row.id, row.body),
        ("DELETE", "alias"): lambda: fe.delete_alias(domain, row.id),
    }
    try:
        calls[(row.action, row.kind)]()
    except Refusal as err:
        return row._replace(action="FAILED", note=str(err))
    return None


def show(row):
    note = f"  [{row.note}]" if row.note else ""
    print(f"  {row.action:<8} {row.kind:<6} {row.target}{note}")
    for key, old, new in row.changes:
        print(f"           {key}: {old!r} -> {new!r}")


def change(fe, args, body):
    target = clean(args.target) if args.kind == "domain" else args.target
    row = plan(fe, args.verb, args.kind, target, body)
    pending = row.action in ("CREATE", "EDIT", "DELETE")
    print(f"Forward Email {args.verb} {args.kind} {row.target}: {1 if pending else 0} changes")
    show(row)
    if pending and not args.apply:
        print("\nDry run: nothing written. Run again with --apply to make this change.")
        return 1
    if pending:
        failed = write(fe, row)
        if failed:
            print("\nRefused:")
            show(failed)
            return 1
        after = plan(fe, args.verb, args.kind, row.target, body)
        if args.verb == "update" and after.action == "EDIT" and not after.changes:
            after = after._replace(action="ok")  # only unread fields remain, and they were sent
        print(f"\nAfter apply: {0 if after.action == 'ok' else 1} rows not ok")
        if after.action != "ok" or after.note:
            show(after)
        row = after
    if args.verb == "create" and args.kind == "domain" and row.action == "ok":
        print(f"\nDNS: /enine-porkbun-forwardemail {row.target} sets its records at Porkbun.")
    return 0 if row.action == "ok" else 1


def list_domains(fe):
    domains = sorted(fe.domains(), key=lambda d: d.get("name", ""))
    print(f"Forward Email account: {len(domains)} domains")
    if domains:
        width = max(len("NAME"), *(len(d.get("name", "")) for d in domains))
        print(f"  {'NAME':<{width}}  {'PLAN':<20} MX   TXT")
        for d in domains:
            print(f"  {d.get('name', '?'):<{width}}  {d.get('plan', '?'):<20} {yes(d.get('has_mx_record')):<4} "
                  f"{yes(d.get('has_txt_record'))}")
    return 0


def yes(value):
    return "yes" if value else "no"


def show_domain(fe, domain):
    obj = fe.domain(domain)
    if obj is None:
        raise Refusal(f"{domain} is not on the Forward Email account")
    print(f"Forward Email domain {domain}: plan {obj.get('plan', '?')}, "
          f"MX {yes(obj.get('has_mx_record'))}, TXT {yes(obj.get('has_txt_record'))}")
    for key in fc.DOMAIN_KEYS:
        print(f"  {key:<30} {obj.get(key)!r}")
    aliases = sorted(fe.aliases(domain), key=lambda a: a.get("name", ""))
    print(f"\nAliases: {len(aliases)}")
    for a in aliases:
        flags = ("" if a.get("is_enabled", True) else "  [disabled]") + ("  [mailbox]" if a.get("has_imap") else "")
        print(f"  {a.get('name', '?')}@{domain} -> {', '.join(a.get('recipients') or []) or '(none)'}{flags}")
    return 0


def body_of(args):
    """The request body from the arguments, checked before the credentials are read."""
    if args.kind == "domain":
        if args.recipients:
            raise Refusal(f"a domain takes no recipients: {' '.join(args.recipients)}")
        allowed = fc.DOMAIN_CREATE_KEYS if args.verb == "create" else fc.DOMAIN_KEYS
        body = settings_of(args.set, allowed, f"domain {args.verb}")
    else:
        split_alias(args.target)
        body = settings_of(args.set, fc.ALIAS_KEYS, "alias")
        if args.recipients:
            if "recipients" in body:
                raise Refusal("give the recipients as arguments or as --set recipients=, not both")
            body["recipients"] = args.recipients
    if args.verb == "delete" and body:
        raise Refusal("delete takes no values")
    if args.verb == "update" and not body:
        raise Refusal(f"update {args.kind} needs a value to change (--set K=V{', or recipients' if args.kind == 'alias' else ''})")
    return body


def connect():
    return fc.ForwardEmail(fc.load_credentials(fc.CREDENTIALS)["FORWARDEMAIL_API_KEY"])


def run(args):
    if args.verb == "list":
        return list_domains(connect())
    if args.verb == "domain":
        return show_domain(connect(), clean(args.name))
    body = body_of(args)
    return change(connect(), args, body)


def help_text():
    """The usage and fields: what a bare call prints, without reading the credentials."""
    return (__doc__.strip()
            .replace("{domain_create_keys}", " ".join(fc.DOMAIN_CREATE_KEYS[:3]) + " and the update fields")
            .replace("{domain_keys}", " ".join(fc.DOMAIN_KEYS))
            .replace("{alias_keys}", " ".join(fc.ALIAS_KEYS)))


def main():
    if len(sys.argv) == 1:
        print(help_text())
        sys.exit(2)
    parser = argparse.ArgumentParser(description=help_text(), formatter_class=argparse.RawDescriptionHelpFormatter)
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in VERBS:
        write_parser = verbs.add_parser(verb, help=f"{verb} a domain or alias (a dry run without --apply)")
        write_parser.add_argument("kind", choices=KINDS, help="domain or alias")
        write_parser.add_argument("target", help="the domain (example.com) or the alias (info@example.com)")
        write_parser.add_argument("recipients", nargs="*", metavar="recipient",
                                  help="alias create or update: where it forwards")
        write_parser.add_argument("--set", action="append", metavar="K=V", help="one field; repeatable")
        write_parser.add_argument("--apply", action="store_true",
                                  help="write (without it, a dry run that writes nothing)")
    verbs.add_parser("list", help="print every domain on the account")
    verbs.add_parser("domain", help="print a domain's settings and aliases").add_argument("name", metavar="domain")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
