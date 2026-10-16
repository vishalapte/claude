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
import json
import re
import stat
import sys
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path

FE_API = "https://api.forwardemail.net/v1"
PB_API = "https://api.porkbun.com/api/json/v3"
# Each service's keys live in that service's own file, as KEY=VALUE lines, mode 600.
FORWARDEMAIL_CREDENTIALS = Path.home() / ".config" / "forwardemail" / "credentials"
PORKBUN_CREDENTIALS = Path.home() / ".config" / "porkbun" / "credentials"
CREDENTIALS = {
    FORWARDEMAIL_CREDENTIALS: ("FORWARDEMAIL_API_KEY",),
    PORKBUN_CREDENTIALS: ("PORKBUN_API_KEY", "PORKBUN_SECRET_API_KEY"),
}

# One DNS record in Porkbun's terms: `sub` is the name without the domain ("" is the apex),
# `content` is as Porkbun stores it (no trailing dot, TXT unquoted, SRV as "weight port target").
Record = namedtuple("Record", "sub type content prio ttl id", defaults=(0, 3600, None))

HOST_TYPES = {"CNAME", "MX", "SRV"}

# Porkbun's parking page. A new domain gets an apex ALIAS and a wildcard CNAME pointing here;
# they are placeholders, not the owner's records, and are deleted. NS records never are.
PORKBUN_PARKING = {"pixie.porkbun.com"}
TXT_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


class Refusal(Exception):
    """The run cannot decide; exit 2 with this message."""


# --- credentials ----------------------------------------------------------------------


def load_credentials(files=None):
    """Every key in `files` (default CREDENTIALS), read from its file; refuse a missing key or an open file."""
    found = {}
    for path, names in (CREDENTIALS if files is None else files).items():
        values = {}
        if path.exists():
            if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise Refusal(f"{path} is readable by others; run: chmod 600 {path}")
            for line in path.read_text().splitlines():
                key, sep, value = line.strip().partition("=")
                if sep:
                    values[key.strip()] = value.strip().strip("'\"")
        missing = [name for name in names if not values.get(name)]
        if missing:
            raise Refusal(f"{path} must set {', '.join(missing)}")
        found.update({name: values[name] for name in names})
    return found


# --- HTTP -----------------------------------------------------------------------------


def http(method, url, headers, body=None):
    """Return (status, parsed body). The body is JSON when it parses, else text."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    except urllib.error.URLError as err:
        raise Refusal(f"{method} {url}: {err.reason}") from err
    text = raw.decode(errors="replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def message_of(body):
    if isinstance(body, dict):
        body = body.get("message") or body.get("error") or json.dumps(body)
    if isinstance(body, list):
        return "; ".join(message_of(item) for item in body)
    return str(body).strip()


# --- Forward Email --------------------------------------------------------------------


class ForwardEmail:
    def __init__(self, token):
        basic = base64.b64encode(f"{token}:".encode()).decode()
        self.headers = {"Authorization": f"Basic {basic}", "Accept": "application/json"}

    def call(self, method, path, body=None):
        status, parsed = http(method, FE_API + path, self.headers, body)
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


# --- Porkbun --------------------------------------------------------------------------


class Porkbun:
    def __init__(self, key, secret, domain):
        self.auth = {"apikey": key, "secretapikey": secret}
        self.headers = {"X-API-Key": key, "X-Secret-API-Key": secret, "Accept": "application/json"}
        self.domain = domain
        self.warnings = []

    def call(self, method, path, body=None):
        payload = None if method == "GET" else {**self.auth, **(body or {})}
        status, parsed = http(method, PB_API + path, self.headers, payload)
        if not isinstance(parsed, dict) or parsed.get("status") != "SUCCESS":
            code = parsed.get("code", "") if isinstance(parsed, dict) else ""
            hint = ""
            if code == "API_ACCESS_DISABLED":
                hint = f" (turn on API access for {self.domain} at porkbun.com > Domain Management)"
            raise Refusal(f"Porkbun {path} -> {status} {code}: {message_of(parsed)}{hint}")
        for warning in parsed.get("warnings") or []:
            text = message_of(warning)
            if text not in self.warnings:
                self.warnings.append(text)
        return parsed

    def records(self):
        body = self.call("GET", f"/dns/retrieve/{self.domain}")
        return [
            normalize(
                Record(
                    sub=to_sub(item["name"], self.domain),
                    type=item["type"].upper(),
                    content=item.get("content") or "",
                    prio=int(item.get("prio") or 0),
                    ttl=int(item.get("ttl") or 0),
                    id=str(item["id"]),
                )
            )
            for item in body.get("records", [])
        ]

    def fields(self, record):
        fields = {"name": record.sub, "type": record.type, "content": record.content, "ttl": record.ttl}
        if record.type in ("MX", "SRV"):
            fields["prio"] = record.prio
        return fields

    def create(self, record):
        self.call("POST", f"/dns/create/{self.domain}", self.fields(record))

    def edit(self, record_id, record):
        self.call("POST", f"/dns/edit/{self.domain}/{record_id}", self.fields(record))

    def delete(self, record_id):
        self.call("POST", f"/dns/delete/{self.domain}/{record_id}")

    def preflight(self):
        body = self.call("GET", f"/dns/preflight/{self.domain}")
        return body.get("blockers") or [], body.get("warnings") or []


# --- records --------------------------------------------------------------------------


def to_sub(fqdn, domain):
    name = fqdn.rstrip(".").lower()
    if name == domain:
        return ""
    if name.endswith("." + domain):
        return name[: -len(domain) - 1]
    raise Refusal(f"record {fqdn} is outside the zone {domain}")


def normalize(record):
    """Compare in one form: hosts lowercase without the trailing dot, TXT without quotes."""
    content = record.content.strip()
    if record.type in HOST_TYPES:
        content = content.rstrip(".").lower()
    elif record.type == "TXT" and content.startswith('"'):
        content = unquote_txt(content) or content
    prio = record.prio if record.type in ("MX", "SRV") else 0
    return record._replace(content=content, prio=prio)


def unquote_txt(data):
    """TXT data as one string: quoted strings joined, escapes removed (RFC 1035 5.1)."""
    return "".join(re.sub(r"\\(.)", r"\1", s) for s in TXT_STRING.findall(data))


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

# Each step: (action, record, existing id, note). Actions: ok, CREATE, EDIT, DELETE, CONFLICT.
Step = namedtuple("Step", "action record id note", defaults=(None, ""))


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


def show(steps, domain):
    for step in steps:
        r = step.record
        name = f"{r.sub}.{domain}" if r.sub else domain
        prio = f" prio={r.prio}" if r.type in ("MX", "SRV") else ""
        content = r.content if len(r.content) <= 70 else r.content[:67] + "..."
        note = f"  [{step.note}]" if step.note else ""
        print(f"  {step.action:<8} {r.type:<5} {name:<38} {content}{prio}{note}")


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
    creds = load_credentials()
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
