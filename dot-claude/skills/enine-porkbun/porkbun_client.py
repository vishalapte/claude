"""The Porkbun DNS client the Porkbun skills share: credentials, HTTP, the record form, and
the create, update and delete requests every skill writes through. Its one home; porkbun.py,
configure_eninesites.py and fe_porkbun.py import it.

Exit-code convention for the scripts that use it: Refusal means 2, could not decide.
"""

import json
import re
import stat
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path

PB_API = "https://api.porkbun.com/api/json/v3"
# Each service's keys live in that service's own file, as KEY=VALUE lines, mode 600.
PORKBUN_CREDENTIALS = Path.home() / ".config" / "porkbun" / "credentials"
# Every other Porkbun account: credentials.d/<account>, the same lines; a run picks one with --account.
PORKBUN_ACCOUNTS = Path.home() / ".config" / "porkbun" / "credentials.d"
PORKBUN_KEYS = ("PORKBUN_API_KEY", "PORKBUN_SECRET_API_KEY")
ACCOUNT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# One DNS record in Porkbun's terms: `sub` is the name without the domain ("" is the apex),
# `content` is as Porkbun stores it (no trailing dot, TXT unquoted, SRV as "weight port target").
Record = namedtuple("Record", "sub type content prio ttl id", defaults=(0, 3600, None))

HOST_TYPES = {"ALIAS", "CNAME", "MX", "NS", "SRV"}

# Porkbun's parking page. A new domain gets an apex ALIAS and a wildcard CNAME pointing here;
# they are placeholders, not the owner's records, and are deleted. NS records never are.
PORKBUN_PARKING = {"pixie.porkbun.com"}
TXT_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')

# Each step: (action, record, existing id, note). Actions: ok, CREATE, EDIT, DELETE, CONFLICT.
Step = namedtuple("Step", "action record id note", defaults=(None, ""))

# One write a skill asks for. `verb` is create, update or delete. `old` is the record an update
# edits or a delete removes, when the caller has read it; otherwise an update's `match`, a
# content prefix, picks it from the records of the type at the name. A None prio or ttl means "keep the record's" on an update
# and "the default" on a create. `note` is printed on the row.
Request = namedtuple("Request", "verb record old match note", defaults=(None, None, ""))

TTL = 600  # Porkbun's usual minimum: a create's ttl when the request gives none


class Refusal(Exception):
    """The run cannot decide; exit 2 with this message."""


# --- credentials ----------------------------------------------------------------------


def load_credentials(files):
    """Every key in `files` ({path: names}), read from its file; refuse a missing key or an open file."""
    found = {}
    for path, names in files.items():
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


def porkbun_accounts():
    """The account names in PORKBUN_ACCOUNTS, sorted; a dotfile is not an account."""
    if not PORKBUN_ACCOUNTS.is_dir():
        return []
    return sorted(p.name for p in PORKBUN_ACCOUNTS.iterdir() if p.is_file() and ACCOUNT_NAME.fullmatch(p.name))


def porkbun_file(account=None):
    """The credentials file of `account`: PORKBUN_CREDENTIALS for None, else credentials.d/<account>."""
    if account is None:
        return PORKBUN_CREDENTIALS
    if not ACCOUNT_NAME.fullmatch(account):
        raise Refusal(f"--account takes letters, digits, '.', '_' and '-': {account}")
    path = PORKBUN_ACCOUNTS / account
    if not path.is_file():
        others = porkbun_accounts()
        raise Refusal(f"no Porkbun account {account}: create {path} with {' and '.join(PORKBUN_KEYS)}, "
                      f"mode 600 (accounts: {', '.join(others) if others else 'none'})")
    return path


def porkbun_credentials(account=None):
    """(key, secret) of `account`, from porkbun_file(account)."""
    creds = load_credentials({porkbun_file(account): PORKBUN_KEYS})
    return creds["PORKBUN_API_KEY"], creds["PORKBUN_SECRET_API_KEY"]


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


def parking(current):
    """Delete requests for Porkbun's parking placeholders. NS records are never placeholders."""
    return [Request("delete", r, old=r, note="Porkbun parking placeholder")
            for r in current if r.type != "NS" and r.content.rstrip(".").lower() in PORKBUN_PARKING]


def key(record):
    return (record.content, record.prio)


def exactly(want, have):
    """Requests that leave a name and type holding exactly `want`: a match stays, the rest of
    `have` is updated into the rest of `want` in place, and what is left over on either side is
    deleted or created."""
    same, want, have = [], list(want), list(have)
    for record in list(want):
        match = next((h for h in have if key(h) == key(record)), None)
        if match:
            same.append(Request("create", record))  # ok: the identical record is there
            want.remove(record)
            have.remove(match)
    gone = [Request("delete", old, old=old) for old in have[len(want):]]
    edits = [Request("update", record, old=old) for record, old in zip(want, have)]
    return gone + same + edits + [Request("create", record) for record in want[len(have):]]


def spf_include(record, have):
    """The request that puts `record`'s `include:` terms into the one SPF record among the apex
    TXT `have`, leaving the rest of it, its `~all` or `-all` among them, as it is. With no SPF
    record it creates `record`; with several it is a CONFLICT (RFC 7208 allows one)."""
    spf = [h for h in have if h.content.lower().startswith("v=spf1")]
    if not spf:
        return Request("create", record)
    if len(spf) > 1:
        return Step("CONFLICT", record, note=f"{len(spf)} SPF records at the apex (RFC 7208 allows one)")
    old = spf[0]
    terms = old.content.split()
    missing = [t for t in record.content.split()[1:] if t.startswith("include:") and t not in terms]
    merged = " ".join([terms[0], *missing, *terms[1:]])
    return Request("update", old._replace(content=merged), old=old, note="merged SPF")


# --- plan and apply -------------------------------------------------------------------


def cname_like(record):
    """A CNAME, or an ALIAS below the apex, which Porkbun serves as a CNAME: alone at its name."""
    return record.type == "CNAME" or (record.type == "ALIAS" and record.sub != "")


def clash(record, current, old=None):
    """The types at the record's name that it cannot share the name with (RFC 1034)."""
    return sorted({r.type for r in current
                   if r.sub == record.sub and r is not old and cname_like(r) != cname_like(record)})


def plan_one(request, current):
    """Steps for one request against `current`.

    create adds the record unless one with the same content (and prio, when given) exists; a
    CNAME cannot share its name. update edits `old`, or the one record of the type at the name
    whose content starts with `match`; none, or several, is a CONFLICT. delete removes `old`,
    or every record of the type at the name, or only those whose content is the request's.
    """
    want = request.record
    same = [r for r in current if r.sub == want.sub and r.type == want.type]
    filled = want._replace(prio=want.prio or 0, ttl=want.ttl or TTL)
    if request.verb == "delete":
        gone = [request.old] if request.old else [r for r in same if want.content in ("", r.content)]
        return [Step("DELETE", r, r.id, request.note) for r in gone] or [Step("ok", filled, note="absent")]
    if request.verb == "create":
        match = next((r for r in same if r.content == want.content and want.prio in (None, r.prio)), None)
        if match:
            return [Step("ok", match, match.id, request.note)]
        types = clash(want, current)
        if types:
            return [Step("CONFLICT", filled, note=f"{', '.join(types)} already at this name (RFC 1034)")]
        if cname_like(want) and same:
            return [Step("CONFLICT", filled, note=f"a name holds one {want.type}; use update")]
        return [Step("CREATE", filled, note=request.note or (f"{len(same)} other {want.type} at this name stay" if same else ""))]
    old = request.old
    if old is None:
        pick = [r for r in same if r.content.casefold().startswith((request.match or "").casefold())]
        held = f" starting {request.match}" if request.match else ""
        if not pick:
            return [Step("CONFLICT", filled, note=f"no {want.type} record{held} at this name; use create")]
        if len(pick) > 1:
            note = f"{len(pick)} {want.type} records{held} at this name; delete the extras first, or pass --match"
            return [Step("CONFLICT", r, r.id, note) for r in pick]
        old = pick[0]
    new = old._replace(type=want.type, content=want.content, prio=old.prio if want.prio is None else want.prio,
                       ttl=want.ttl or old.ttl)
    if new == old:
        return [Step("ok", old, old.id, request.note)]
    types = clash(new, current, old)
    if types:
        return [Step("CONFLICT", new, old.id, f"{', '.join(types)} already at this name (RFC 1034)")]
    was = f"was {'' if old.type == new.type else old.type + ' '}{old.content}"
    was += f" prio {old.prio}" if new.prio != old.prio else ""
    was += f" ttl {old.ttl}" if new.ttl != old.ttl else ""
    return [Step("EDIT", new, old.id, f"{request.note}; {was}" if request.note else was)]


def plan(requests, current):
    """Steps for the requests in order, each planned against the zone the earlier ones leave.

    An item that is already a Step (a caller's own CONFLICT) is passed through. A name with a
    CONFLICT gets no writes, so a delete is never applied without the record that replaces it.
    """
    current, steps = list(current), []
    for request in requests:
        new = [request] if isinstance(request, Step) else plan_one(request, current)
        for step in new:
            if step.action == "DELETE":
                current = [r for r in current if r.id != step.id]
            elif step.action == "EDIT":
                current = [step.record if r.id == step.id else r for r in current]
            elif step.action == "CREATE":
                current.append(step.record)
        steps += new
    held = {s.record.sub for s in steps if s.action == "CONFLICT"}
    return [s for s in steps if s.action in ("ok", "CONFLICT") or s.record.sub not in held]


def apply(porkbun, steps):
    """Deletes first: Porkbun refuses a record that clashes with one still present."""
    for action, write in (("DELETE", lambda s: porkbun.delete(s.id)),
                          ("EDIT", lambda s: porkbun.edit(s.id, s.record)),
                          ("CREATE", lambda s: porkbun.create(s.record))):
        for step in steps:
            if step.action == action:
                write(step)


def converge(porkbun, title, requests_of, write, current=None):
    """Plan `requests_of(zone)`, print it, and with `write` apply it, re-read and re-plan.

    Then print Porkbun's warnings and preflight. True when a row is not ok after the run (a dry
    run with changes pending, a conflict, a failed check) or preflight names a blocker.
    """
    current = porkbun.records() if current is None else current
    steps = plan(requests_of(current), current)
    changes = [s for s in steps if s.action in ("CREATE", "EDIT", "DELETE")]
    print(f"{title}: {len(changes)} changes")
    show(steps, porkbun.domain)

    if changes and not write:
        print("\nDry run: nothing written. Run again with --apply to make these changes.")
    if changes and write:
        apply(porkbun, changes)
        current = porkbun.records()
        steps = plan(requests_of(current), current)
        left = [s for s in steps if s.action != "ok"]
        print(f"\nAfter apply: {len(left)} rows not ok")
        show(left, porkbun.domain)
    drift = any(s.action != "ok" for s in steps)

    for warning in porkbun.warnings:
        print(f"\nPorkbun warning: {warning}")
    blockers, warnings = porkbun.preflight()
    print(f"\nPorkbun preflight: blockers={blockers or 'none'} warnings={warnings or 'none'}")
    return drift or bool(blockers)


def show(steps, domain):
    for step in steps:
        r = step.record
        name = f"{r.sub}.{domain}" if r.sub else domain
        prio = f" prio={r.prio}" if r.type in ("MX", "SRV") else ""
        content = r.content if len(r.content) <= 70 else r.content[:67] + "..."
        note = f"  [{step.note}]" if step.note else ""
        print(f"  {step.action:<8} {r.type:<5} {name:<38} {content}{prio}{note}")
