#!/usr/bin/env python3
"""Add a domain to Forward Email and hold its Porkbun DNS to the records Forward Email asks for.

The records come from the `zone_file` field of Forward Email's domain object, never from a
list kept here, so a change on Forward Email's side reaches the zone without editing this file.

    fe_porkbun.py example.com                  # dry run: plan, preflight, verify; writes nothing
    fe_porkbun.py example.com --apply          # create on Forward Email if absent, apply, verify
    fe_porkbun.py example.com --apply --force  # also replace the apex MX of another mail host
    fe_porkbun.py example.com --account acme   # the Porkbun account in ~/.config/porkbun/credentials.d/acme

Exit 0 conforms or was applied and verified, 1 something drifts or failed to verify,
2 could not decide (credentials, plan, API access, an API error).
"""

import argparse
import sys
from pathlib import Path

# The Porkbun client, credentials, record form and plan rows have one home: the porkbun skill.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "enine-porkbun"))
import porkbun_client as pc  # noqa: E402
from porkbun_client import (  # noqa: E402
    PORKBUN_KEYS, TXT_STRING, Porkbun, Record, Refusal, Request,
    normalize, to_sub, unquote_txt,
)

# The Forward Email client and its credentials have one home: the forwardemail skill.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "enine-forwardemail"))
import forwardemail_client as fc  # noqa: E402
from forwardemail_client import FE_API, CREDENTIALS, ForwardEmail  # noqa: E402,F401


# --- records --------------------------------------------------------------------------


def parse_zone(text, domain):
    """Forward Email's zone_file (helpers/get-zone-file.js): `name. ttl IN TYPE data` per line."""
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        parts = line.split(None, 4)
        if len(parts) != 5 or parts[2].upper() != "IN":
            raise Refusal(f"cannot read zone_file line: {line}")
        name, ttl, _, rtype, data = parts
        rtype = rtype.upper()
        prio = 0
        if rtype == "MX":
            prio_text, data = data.split(None, 1)
            prio = int(prio_text)
        elif rtype == "SRV":
            prio_text, data = data.split(None, 1)
            prio = int(prio_text)
            weight, port, target = data.split()
            data = f"{weight} {port} {target.rstrip('.')}"
        elif rtype == "TXT":
            if not TXT_STRING.search(data):
                raise Refusal(f"cannot read TXT data: {line}")
            data = unquote_txt(data)
        records.append(normalize(Record(to_sub(name, domain), rtype, data, prio, int(ttl))))
    if not records:
        raise Refusal("Forward Email returned an empty zone_file")
    return records


# --- plan -----------------------------------------------------------------------------

def requests(desired, current, force=False):
    """Requests that bring `current` to `desired`.

    Every (name, type) Forward Email names is held to exactly its records: a stale DKIM or
    DMARC value is updated in place. An apex MX of another mail host is replaced only with
    `force`; without it each one is a CONFLICT, which holds every write at the apex. The apex
    TXT set is the one exception, because unrelated verifications live there: missing values
    are added, nothing is removed, and Forward Email's SPF is attached to the one SPF record
    instead of becoming a second (pc.spf_merge: its include added, the record's `all`
    unchanged). Porkbun's placeholders are deleted, parking and forwarding alike, except that
    Porkbun's forwarding MX are edited in place into Forward Email's, so the apex always has MX.
    A CNAME that would share its name with another type is a CONFLICT in the shared plan (RFC 1034).
    """
    asks = pc.parking(current, replacing={("", "MX")})
    current = [r for r in current if all(r is not a.record for a in asks)]
    groups = {}
    for record in desired:
        groups.setdefault((record.sub, record.type), []).append(record)
    for (sub, rtype), want in groups.items():
        have = [r for r in current if r.sub == sub and r.type == rtype]
        kept = other_hosts(want, have) if (sub, rtype) == ("", "MX") and not force else []
        if (sub, rtype) == ("", "TXT"):
            asks += apex_txt(want, have)
        else:
            asks += kept or pc.exactly(want, have)
    return asks


def other_hosts(want, have):
    """A CONFLICT for each apex MX whose host Forward Email does not name: another mail host's.
    Forward Email's own host at another priority is a stale value, and Porkbun's forwarding MX a
    placeholder, which `exactly` edits."""
    wanted = {record.content for record in want}
    return [pc.Step("CONFLICT", r, r.id, "another mail host; --force replaces it")
            for r in have if r.content not in wanted and not pc.placeholder(r)]


def apex_txt(want, have):
    """Missing apex TXT values are added and none removed; SPF is attached to the one there."""
    return [pc.spf_include(record, have) if pc.is_spf(record.content)
            else Request("create", record) for record in want]


# --- main -----------------------------------------------------------------------------


def run(args):
    domain = args.domain.strip().rstrip(".").lower()
    creds = pc.load_credentials({**CREDENTIALS, pc.porkbun_file(args.account): PORKBUN_KEYS})
    fe = ForwardEmail(creds["FORWARDEMAIL_API_KEY"])
    porkbun = Porkbun(creds["PORKBUN_API_KEY"], creds["PORKBUN_SECRET_API_KEY"], domain)

    current = porkbun.records()  # read first: a Porkbun refusal must stop the run before any write
    info = fe.domain(domain)
    if info is None:
        if not args.apply:
            print(f"Forward Email: {domain} is not on the account; a run with --apply adds it.")
            return 1
        settings = {"plan": args.plan} if args.plan else {}
        if args.catchall is not None:
            settings["catchall"] = fc.value_of("catchall", args.catchall)
        info = fe.create_domain(domain, settings)
        print(f"Forward Email: added {domain} (plan {info.get('plan', '?')}).")
    else:
        print(f"Forward Email: {domain} is on the account (plan {info.get('plan', '?')}).")
    if not info.get("zone_file"):
        raise Refusal("Forward Email returned no zone_file; the API key's user must be an admin of the domain")
    desired = parse_zone(info["zone_file"], domain)

    title = f"\nPorkbun DNS for {domain}: {len(desired)} records wanted"
    drift = pc.converge(porkbun, title, lambda zone: requests(desired, zone, args.force), args.apply, current)

    verified, message = fe.verify(domain)
    print(f"\nForward Email verify-records: {'verified' if verified else 'NOT verified'}")
    if message:
        print(f"  {message}")
    if not verified:
        drift = True
    return 1 if drift else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("domain", help="the domain, as registered at Porkbun (example.com)")
    parser.add_argument("--apply", action="store_true",
                        help="create the domain and change DNS (without it, a dry run that writes nothing)")
    parser.add_argument("--force", action="store_true",
                        help="replace the apex MX of another mail host (without it, they stay and the apex is not changed)")
    parser.add_argument("--plan", choices=("free", "enhanced_protection", "team"),
                        help="Forward Email plan for a new domain (default: the account's plan)")
    parser.add_argument("--catchall", help="new domain only: 'false', or comma-separated recipients "
                        "(Forward Email's default forwards everything to the account's email)")
    parser.add_argument("--account", metavar="NAME",
                        help="the Porkbun account in ~/.config/porkbun/credentials.d/NAME (default: the credentials file)")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
