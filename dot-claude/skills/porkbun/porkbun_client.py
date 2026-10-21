"""The Porkbun DNS client the Porkbun skills share: credentials, HTTP, the record form, and
plan rows. Its one home; porkbun.py, configure_eninesites.py and fe_porkbun.py import it.

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
PORKBUN_KEYS = ("PORKBUN_API_KEY", "PORKBUN_SECRET_API_KEY")

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


def porkbun_credentials():
    """(key, secret) from PORKBUN_CREDENTIALS."""
    creds = load_credentials({PORKBUN_CREDENTIALS: PORKBUN_KEYS})
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


def show(steps, domain):
    for step in steps:
        r = step.record
        name = f"{r.sub}.{domain}" if r.sub else domain
        prio = f" prio={r.prio}" if r.type in ("MX", "SRV") else ""
        content = r.content if len(r.content) <= 70 else r.content[:67] + "..."
        note = f"  [{step.note}]" if step.note else ""
        print(f"  {step.action:<8} {r.type:<5} {name:<38} {content}{prio}{note}")
