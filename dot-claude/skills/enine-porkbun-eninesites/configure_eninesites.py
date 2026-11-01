#!/usr/bin/env python3
"""Point a Porkbun domain and its www at a host with ALIAS records (eninesites.com by default).

    configure_eninesites.py example.com                       # dry run: plan, preflight; writes nothing
    configure_eninesites.py example.com --apply               # apply, re-read, preflight
    configure_eninesites.py example.com --to other.host --apply
    configure_eninesites.py example.com --account acme       # the account in ~/.config/porkbun/credentials.d/acme

Exit 0 conforms or was applied and verified, 1 something drifts or failed to verify,
2 could not decide (credentials, API access, an API error, a bad target).
"""

import argparse
import re
import sys
from pathlib import Path

# The Porkbun client, credentials, plan rows and normal form have one home: porkbun_client.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "enine-porkbun"))
import porkbun_client as pc  # noqa: E402

DEFAULT_TARGET = "eninesites.com"
NAMES = ("", "www")
WEB_TYPES = {"ALIAS", "CNAME", "A", "AAAA"}
HOSTNAME = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")


def requests(current, target):
    """Requests that leave exactly `ALIAS -> target` as the web record at the apex and at www.

    At each name the one existing web record (ALIAS, CNAME, A, AAAA) is updated into the
    ALIAS in place and any others are deleted. Porkbun's parking placeholders are deleted
    wherever they are. A www holding another type is a CONFLICT: Porkbun serves a non-apex
    ALIAS as a CNAME, which cannot share a name (RFC 1034). The apex keeps its MX and TXT.
    """
    asks = pc.parking(current)
    current = [r for r in current if all(r is not a.record for a in asks)]
    for sub in NAMES:
        want = pc.Record(sub, "ALIAS", target, 0, None)  # an update keeps the record's ttl
        web = [r for r in current if r.sub == sub and r.type in WEB_TYPES]
        other = [r for r in current if r.sub == sub and r.type not in WEB_TYPES]
        if sub and other:
            types = ", ".join(sorted({r.type for r in other}))
            asks.append(pc.Step("CONFLICT", want, note=f"{types} already at this name (RFC 1034)"))
            continue
        keep = next((r for r in web if r.type == "ALIAS" and r.content == target), web[0] if web else None)
        asks += [pc.Request("delete", r, old=r, note=f"replaced by ALIAS {target}") for r in web if r is not keep]
        asks.append(pc.Request("update", want, old=keep) if keep else pc.Request("create", want))
    return asks


def run(args):
    domain = args.domain.strip().rstrip(".").lower()
    target = args.to.strip().rstrip(".").lower()
    if not HOSTNAME.fullmatch(target):
        raise pc.Refusal(f"--to must be a host name (an ALIAS cannot point at an address): {args.to}")
    if target in (domain, f"www.{domain}"):
        raise pc.Refusal(f"--to {target} would point {domain} at itself")
    porkbun = pc.Porkbun(*pc.porkbun_credentials(args.account), domain)
    title = f"Porkbun web records for {domain} -> {target}"
    return 1 if pc.converge(porkbun, title, lambda current: requests(current, target), args.apply) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("domain", help="the domain, as registered at Porkbun (example.com)")
    parser.add_argument("--to", default=DEFAULT_TARGET, help=f"the host to point at (default {DEFAULT_TARGET})")
    parser.add_argument("--apply", action="store_true",
                        help="change DNS (without it, a dry run that writes nothing)")
    parser.add_argument("--account", metavar="NAME",
                        help="the Porkbun account in ~/.config/porkbun/credentials.d/NAME (default: the credentials file)")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except pc.Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
