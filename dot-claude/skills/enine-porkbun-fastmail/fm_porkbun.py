#!/usr/bin/env python3
"""Set a Porkbun domain's DNS for Fastmail: DKIM and SPF to send, and with --mx, MX to receive.

    fm_porkbun.py example.com                       # dry run: plan, preflight; writes nothing
    fm_porkbun.py example.com --apply               # apply, re-read, preflight
    fm_porkbun.py example.com --mx --apply          # also move the apex MX to Fastmail
    fm_porkbun.py example.com --account acme        # the account in ~/.config/porkbun/credentials.d/acme

The records are the ones Fastmail's "Manual DNS configuration" help page lists. Fastmail's API
cannot add a domain, so the steps at Fastmail are printed at the end, to be done by hand.

Exit 0 conforms or was applied and verified, 1 something drifts or failed to verify,
2 could not decide (credentials, API access, an API error).
"""

import argparse
import sys
from pathlib import Path

# The Porkbun client, credentials, plan rows and normal form have one home: porkbun_client.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "enine-porkbun"))
import porkbun_client as pc  # noqa: E402

TTL = 3600  # Fastmail's records "use a 1-hour TTL"
DKIM = ("fm1", "fm2", "fm3")
SPF = "v=spf1 include:spf.messagingengine.com ?all"
MX = (("in1-smtp.messagingengine.com", 10), ("in2-smtp.messagingengine.com", 20))


def requests(current, domain, mx):
    """Requests for Fastmail's records.

    Each `fmN._domainkey` holds exactly its CNAME. The apex SPF gains Fastmail's include and
    keeps the rest of it; with no SPF record, Fastmail's own is created. With `mx`, the apex
    holds exactly Fastmail's two MX, the old ones updated into them in place. Without it, the
    MX are left alone, so mail keeps arriving where it does now. Porkbun's parking
    placeholders are deleted; NS records never are.
    """
    asks = pc.parking(current)
    current = [r for r in current if all(r is not a.record for a in asks)]

    def at(sub, rtype):
        return [r for r in current if r.sub == sub and r.type == rtype]

    for name in DKIM:
        sub = f"{name}._domainkey"
        want = pc.Record(sub, "CNAME", f"{name}.{domain}.dkim.fmhosted.com", 0, TTL)
        asks += pc.exactly([want], at(sub, "CNAME"))
    asks.append(pc.spf_include(pc.Record("", "TXT", SPF, 0, TTL), at("", "TXT")))
    if mx:
        asks += pc.exactly([pc.Record("", "MX", host, prio, TTL) for host, prio in MX], at("", "MX"))
    return asks


def by_hand(domain, mx):
    """What Fastmail needs that its API cannot do."""
    receive = "" if mx else "; the MX stay where they are, so Fastmail sends only"
    return "\n".join([
        "\nAt Fastmail, by hand (its API cannot add a domain):",
        f"  1. Settings > Domains > Add domain: {domain}, with DNS managed elsewhere{receive}.",
        "  2. Settings > Sending identities: add each address that sends from it.",
        f"  3. Send a test to another provider and look for dkim=pass header.d={domain}.",
    ])


def run(args):
    domain = args.domain.strip().rstrip(".").lower()
    porkbun = pc.Porkbun(*pc.porkbun_credentials(args.account), domain)
    title = f"Porkbun Fastmail records for {domain}{' with MX' if args.mx else ''}"
    drift = pc.converge(porkbun, title, lambda current: requests(current, domain, args.mx), args.apply)
    print(by_hand(domain, args.mx))
    return 1 if drift else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("domain", help="the domain, as registered at Porkbun (example.com)")
    parser.add_argument("--mx", action="store_true",
                        help="also replace the apex MX with Fastmail's, so mail arrives at Fastmail")
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
