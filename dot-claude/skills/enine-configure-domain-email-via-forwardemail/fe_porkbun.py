#!/usr/bin/env python3
"""Add a domain to Forward Email and hold its Porkbun DNS to the records Forward Email asks for.

The records come from the `zone_file` field of Forward Email's domain object, never from a
list kept here, so a change on Forward Email's side reaches the zone without editing this file.

    fe_porkbun.py example.com                  # dry run: plan, preflight, verify; writes nothing
    fe_porkbun.py example.com --apply          # create on Forward Email if absent, apply, verify

Exit 0 conforms or was applied and verified, 1 something drifts or failed to verify,
2 could not decide (credentials, plan, API access, an API error).
"""

import argparse
import base64
import sys
from pathlib import Path

# The Porkbun client, credentials, record form and plan rows have one home: the porkbun skill.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "porkbun"))
import porkbun_client as pc  # noqa: E402
from porkbun_client import (  # noqa: E402
    PORKBUN_CREDENTIALS, PORKBUN_KEYS, PORKBUN_PARKING, TXT_STRING, Porkbun, Record, Refusal, Step,
    message_of, normalize, show, to_sub, unquote_txt,
)

FE_API = "https://api.forwardemail.net/v1"
# Each service's keys live in that service's own file, as KEY=VALUE lines, mode 600.
FORWARDEMAIL_CREDENTIALS = Path.home() / ".config" / "forwardemail" / "credentials"
CREDENTIALS = {
    FORWARDEMAIL_CREDENTIALS: ("FORWARDEMAIL_API_KEY",),
    PORKBUN_CREDENTIALS: PORKBUN_KEYS,
}


# --- Forward Email --------------------------------------------------------------------


class ForwardEmail:
    def __init__(self, token):
        basic = base64.b64encode(f"{token}:".encode()).decode()
        self.headers = {"Authorization": f"Basic {basic}", "Accept": "application/json"}

    def call(self, method, path, body=None):
        status, parsed = pc.http(method, FE_API + path, self.headers, body)
        if status == 401:
            raise Refusal("Forward Email rejected FORWARDEMAIL_API_KEY (401)")
        if status == 402:
            raise Refusal(f"Forward Email's API needs a paid plan: {message_of(parsed)}")
        return status, parsed

    def domain(self, name):
        """The domain object with its zone_file, or None when the account does not hold it."""
        status, body = self.call("GET", f"/domains/{name}")
        if status == 404:
            return None
        if status != 200:
            raise Refusal(f"Forward Email GET /domains/{name} -> {status}: {message_of(body)}")
        return body

    def create(self, name, plan, catchall):
        body = {"domain": name}
        if plan:
            body["plan"] = plan
        if catchall is not None:
            body["catchall"] = {"true": True, "false": False}.get(catchall.lower(), catchall)
        status, response = self.call("POST", "/domains", body)
        if status not in (200, 201):
            raise Refusal(f"Forward Email POST /domains -> {status}: {message_of(response)}")
        return response

    def verify(self, name):
        """(verified, message) from Forward Email's own lookup of the published records."""
        status, body = self.call("GET", f"/domains/{name}/verify-records")
        if 200 <= status < 300:
            return True, message_of(body)
        if status == 400:
            return False, message_of(body)
        raise Refusal(f"Forward Email verify-records -> {status}: {message_of(body)}")


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

def key(record):
    return (record.content, record.prio)


def plan(desired, current):
    """Steps that bring `current` to `desired`.

    Every (name, type) Forward Email names is held to exactly its records: other MX at the
    apex are removed, a stale DKIM or DMARC value is replaced. The apex TXT set is the one
    exception, because unrelated verifications live there: missing values are added, nothing
    is removed, and the one SPF record gains Forward Email's include instead of a second SPF.
    Porkbun's parking placeholders are deleted, and never count as a conflict.
    """
    parking = [r for r in current if r.type != "NS" and r.content.rstrip(".").lower() in PORKBUN_PARKING]
    current = [r for r in current if r not in parking]
    steps = []
    groups = {}
    for record in desired:
        groups.setdefault((record.sub, record.type), []).append(record)
    for (sub, rtype), want in groups.items():
        have = [r for r in current if r.sub == sub and r.type == rtype]
        clash = [r for r in current if r.sub == sub and (r.type == "CNAME") != (rtype == "CNAME")]
        if clash:
            types = ", ".join(sorted({r.type for r in clash}))
            for record in want:
                steps.append(Step("CONFLICT", record, note=f"{types} already at this name (RFC 1034)"))
        elif sub == "" and rtype == "TXT":
            steps.extend(plan_apex_txt(want, have))
        else:
            steps.extend(plan_exclusive(want, have))
    steps.extend(Step("DELETE", old, old.id, "Porkbun parking placeholder") for old in parking)
    return steps


def plan_exclusive(want, have):
    steps, want, have = [], list(want), list(have)
    for record in list(want):
        match = next((h for h in have if key(h) == key(record)), None)
        if match:
            steps.append(Step("ok", record, match.id))
            want.remove(record)
            have.remove(match)
    for record, old in zip(want, have):
        steps.append(Step("EDIT", record, old.id, f"was {old.content}"))
    steps.extend(Step("CREATE", record) for record in want[len(have):])
    steps.extend(Step("DELETE", old, old.id) for old in have[len(want):])
    return steps


def plan_apex_txt(want, have):
    steps = []
    have_spf = [h for h in have if h.content.lower().startswith("v=spf1")]
    for record in want:
        if not record.content.lower().startswith("v=spf1"):
            match = next((h for h in have if h.content == record.content), None)
            steps.append(Step("ok", record, match.id) if match else Step("CREATE", record))
        elif not have_spf:
            steps.append(Step("CREATE", record))
        elif len(have_spf) > 1:
            steps.append(Step("CONFLICT", record, note=f"{len(have_spf)} SPF records at the apex (RFC 7208 allows one)"))
        else:
            old = have_spf[0]
            terms = old.content.split()
            missing = [t for t in record.content.split()[1:] if t.startswith("include:") and t not in terms]
            if not missing:
                steps.append(Step("ok", old, old.id, "merged SPF"))
            else:
                merged = " ".join([terms[0], *missing, *terms[1:]])
                steps.append(Step("EDIT", old._replace(content=merged), old.id, f"was {old.content}"))
    return steps


def apply(porkbun, steps):
    for step in steps:
        if step.action == "EDIT":
            porkbun.edit(step.id, step.record)
    for step in steps:
        if step.action == "CREATE":
            porkbun.create(step.record)
    for step in steps:
        if step.action == "DELETE":
            porkbun.delete(step.id)


# --- main -----------------------------------------------------------------------------


def run(args):
    domain = args.domain.strip().rstrip(".").lower()
    creds = pc.load_credentials(CREDENTIALS)
    fe = ForwardEmail(creds["FORWARDEMAIL_API_KEY"])
    porkbun = Porkbun(creds["PORKBUN_API_KEY"], creds["PORKBUN_SECRET_API_KEY"], domain)
    drift = False

    current = porkbun.records()  # read first: a Porkbun refusal must stop the run before any write
    info = fe.domain(domain)
    if info is None:
        if not args.apply:
            print(f"Forward Email: {domain} is not on the account; a run with --apply adds it.")
            return 1
        info = fe.create(domain, args.plan, args.catchall)
        print(f"Forward Email: added {domain} (plan {info.get('plan', '?')}).")
    else:
        print(f"Forward Email: {domain} is on the account (plan {info.get('plan', '?')}).")
    if not info.get("zone_file"):
        raise Refusal("Forward Email returned no zone_file; the API key's user must be an admin of the domain")
    desired = parse_zone(info["zone_file"], domain)

    steps = plan(desired, current)
    changes = [s for s in steps if s.action in ("CREATE", "EDIT", "DELETE")]
    print(f"\nPorkbun DNS for {domain}: {len(desired)} records wanted, {len(changes)} changes")
    show(steps, domain)

    if changes and not args.apply:
        print("\nDry run: nothing written. Run again with --apply to make these changes.")
    if changes and args.apply:
        apply(porkbun, changes)
        steps = plan(desired, porkbun.records())
        left = [s for s in steps if s.action != "ok"]
        print(f"\nAfter apply: {len(left)} rows not ok")
        show(left, domain)
    if any(s.action != "ok" for s in steps):
        drift = True

    for warning in porkbun.warnings:
        print(f"\nPorkbun warning: {warning}")
    blockers, warnings = porkbun.preflight()
    print(f"\nPorkbun preflight: blockers={blockers or 'none'} warnings={warnings or 'none'}")
    if blockers:
        drift = True

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
    parser.add_argument("--plan", choices=("free", "enhanced_protection", "team"),
                        help="Forward Email plan for a new domain (default: the account's plan)")
    parser.add_argument("--catchall", help="new domain only: 'false', or comma-separated recipients "
                        "(Forward Email's default forwards everything to the account's email)")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
