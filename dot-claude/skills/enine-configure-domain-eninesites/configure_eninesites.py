#!/usr/bin/env python3
"""Point a Porkbun domain and its www at a host with ALIAS records (eninesites.com by default).

    configure_eninesites.py example.com                       # dry run: plan, preflight; writes nothing
    configure_eninesites.py example.com --apply               # apply, re-read, preflight
    configure_eninesites.py example.com --to other.host --apply

Exit 0 conforms or was applied and verified, 1 something drifts or failed to verify,
2 could not decide (credentials, API access, an API error, a bad target).
"""

import argparse
import re
import sys
from pathlib import Path

# The Porkbun client, credentials, plan rows and normal form have one home: porkbun_client.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "porkbun"))
import porkbun_client as pc  # noqa: E402

DEFAULT_TARGET = "eninesites.com"
NAMES = ("", "www")
WEB_TYPES = {"ALIAS", "CNAME", "A", "AAAA"}
TTL = 600  # Porkbun's usual minimum
HOSTNAME = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")


def plan(current, domain, target):
    """Steps that leave exactly `ALIAS -> target` as the web record at the apex and at www.

    At each name the one existing web record (ALIAS, CNAME, A, AAAA) is edited into the
    ALIAS and any others are deleted. Porkbun's parking placeholders are deleted wherever
    they are. A www holding another type is a CONFLICT: Porkbun serves a non-apex ALIAS as a
    CNAME, which cannot share a name (RFC 1034). The apex keeps its MX and TXT.
    """
    parking = [r for r in current if r.type != "NS" and r.content.rstrip(".").lower() in pc.PORKBUN_PARKING]
    current = [r for r in current if r not in parking]
    steps = []
    for sub in NAMES:
        want = pc.Record(sub, "ALIAS", target, 0, TTL)
        web = [r for r in current if r.sub == sub and r.type in WEB_TYPES]
        other = [r for r in current if r.sub == sub and r.type not in WEB_TYPES]
        if sub and other:
            types = ", ".join(sorted({r.type for r in other}))
            steps.append(pc.Step("CONFLICT", want, note=f"{types} already at this name (RFC 1034)"))
            continue
        match = next((r for r in web if r.type == "ALIAS" and r.content == target), None)
        if match:
            steps.append(pc.Step("ok", want, match.id))
        elif web:
            steps.append(pc.Step("EDIT", want, web[0].id, f"was {web[0].type} {web[0].content}"))
            match = web[0]
        else:
            steps.append(pc.Step("CREATE", want))
        steps.extend(pc.Step("DELETE", r, r.id, f"replaced by ALIAS {target}") for r in web if r is not match)
    steps.extend(pc.Step("DELETE", r, r.id, "Porkbun parking placeholder") for r in parking)
    return steps


def apply(porkbun, steps):
    """Deletes first: Porkbun refuses a record that clashes with one still present."""
    write = {
        "DELETE": lambda step: porkbun.delete(step.id),
        "EDIT": lambda step: porkbun.edit(step.id, step.record),
        "CREATE": lambda step: porkbun.create(step.record),
    }
    for action in ("DELETE", "EDIT", "CREATE"):
        for step in steps:
            if step.action == action:
                write[action](step)


def run(args):
    domain = args.domain.strip().rstrip(".").lower()
    target = args.to.strip().rstrip(".").lower()
    if not HOSTNAME.fullmatch(target):
        raise pc.Refusal(f"--to must be a host name (an ALIAS cannot point at an address): {args.to}")
    if target in (domain, f"www.{domain}"):
        raise pc.Refusal(f"--to {target} would point {domain} at itself")
    porkbun = pc.Porkbun(*pc.porkbun_credentials(), domain)

    steps = plan(porkbun.records(), domain, target)
    changes = [s for s in steps if s.action in ("CREATE", "EDIT", "DELETE")]
    print(f"Porkbun web records for {domain} -> {target}: {len(changes)} changes")
    pc.show(steps, domain)

    if changes and not args.apply:
        print("\nDry run: nothing written. Run again with --apply to make these changes.")
    if changes and args.apply:
        apply(porkbun, changes)
        steps = plan(porkbun.records(), domain, target)
        left = [s for s in steps if s.action != "ok"]
        print(f"\nAfter apply: {len(left)} rows not ok")
        pc.show(left, domain)
    drift = any(s.action != "ok" for s in steps)

    for warning in porkbun.warnings:
        print(f"\nPorkbun warning: {warning}")
    blockers, warnings = porkbun.preflight()
    print(f"\nPorkbun preflight: blockers={blockers or 'none'} warnings={warnings or 'none'}")
    return 1 if drift or blockers else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("domain", help="the domain, as registered at Porkbun (example.com)")
    parser.add_argument("--to", default=DEFAULT_TARGET, help=f"the host to point at (default {DEFAULT_TARGET})")
    parser.add_argument("--apply", action="store_true",
                        help="change DNS (without it, a dry run that writes nothing)")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except pc.Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
