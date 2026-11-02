"""The Forward Email API (https://forwardemail.net/en/email-api): one client for every skill
that calls it. HTTP, credentials and refusals come from the porkbun skill's client, so one
`http` serves both APIs and the tests replace it in one place.
"""

import base64
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "enine-porkbun"))
import porkbun_client as pc  # noqa: E402
from porkbun_client import Refusal, load_credentials, message_of  # noqa: E402,F401

FE_API = "https://api.forwardemail.net/v1"
# Each service's keys live in that service's own file, as KEY=VALUE lines, mode 600.
FORWARDEMAIL_CREDENTIALS = Path.home() / ".config" / "forwardemail" / "credentials"
CREDENTIALS = {
    FORWARDEMAIL_CREDENTIALS: ("FORWARDEMAIL_API_KEY",),
}
PAGE = 50  # the most a list call returns per page

# The fields the API page lists for each write. A domain's update fields; create also takes
# `plan`, `catchall` and `team_domain`, which no update changes.
DOMAIN_KEYS = (
    "smtp_port", "has_adult_content_protection", "has_phishing_protection", "has_executable_protection",
    "has_virus_protection", "has_recipient_verification", "ignore_mx_check", "retention_days",
    "bounce_webhook", "max_quota_per_alias",
)
DOMAIN_CREATE_KEYS = ("plan", "catchall", "team_domain") + DOMAIN_KEYS
ALIAS_KEYS = (
    "name", "recipients", "description", "labels", "has_recipient_verification", "is_enabled",
    "error_code_if_disabled", "has_imap", "has_pgp", "public_key", "max_quota",
    "vacation_responder_is_enabled", "vacation_responder_start_date", "vacation_responder_end_date",
    "vacation_responder_subject", "vacation_responder_message",
)
LIST_KEYS = ("recipients", "labels")  # given as comma-separated text, sent as a list


def value_of(key, text):
    """A field's value from the command line: true and false are booleans, digits a number, and a
    list field comma-separated text. Anything else is sent as written."""
    if key in LIST_KEYS:
        return [item.strip() for item in text.split(",") if item.strip()]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if text.isdigit():
        return int(text)
    return text


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

    def expect(self, method, path, body=None):
        """The answer to a call that must succeed; any other status is a refusal."""
        status, parsed = self.call(method, path, body)
        if not 200 <= status < 300:
            raise Refusal(f"Forward Email {method} {path} -> {status}: {message_of(parsed)}")
        return parsed

    def pages(self, path):
        """Every item of a list call, a page at a time."""
        items, seen, page = [], set(), 1
        while True:
            batch = self.expect("GET", f"{path}?paginate=true&limit={PAGE}&page={page}") or []
            fresh = [item for item in batch if item.get("id") not in seen]
            seen.update(item.get("id") for item in fresh)
            items += fresh
            if len(batch) < PAGE or not fresh:
                return items
            page += 1

    # --- domains ----------------------------------------------------------------------

    def domains(self):
        return self.pages("/domains")

    def domain(self, name):
        """The domain object with its zone_file, or None when the account does not hold it."""
        status, body = self.call("GET", f"/domains/{name}")
        if status == 404:
            return None
        if status != 200:
            raise Refusal(f"Forward Email GET /domains/{name} -> {status}: {message_of(body)}")
        return body

    def create_domain(self, name, settings):
        return self.expect("POST", "/domains", {"domain": name, **settings})

    def update_domain(self, name, settings):
        return self.expect("PUT", f"/domains/{name}", settings)

    def delete_domain(self, name):
        return self.expect("DELETE", f"/domains/{name}")

    def verify(self, name):
        """(verified, message) from Forward Email's own lookup of the published records."""
        status, body = self.call("GET", f"/domains/{name}/verify-records")
        if 200 <= status < 300:
            return True, message_of(body)
        if status == 400:
            return False, message_of(body)
        raise Refusal(f"Forward Email verify-records -> {status}: {message_of(body)}")

    # --- aliases ----------------------------------------------------------------------

    def aliases(self, domain):
        return self.pages(f"/domains/{domain}/aliases")

    def alias(self, domain, name):
        """The alias called `name` (the part before the @), or None when the domain has none."""
        status, body = self.call("GET", f"/domains/{domain}/aliases/{urllib.parse.quote(name, safe='')}")
        if status == 404:
            return None
        if status != 200:
            raise Refusal(f"Forward Email GET alias {name}@{domain} -> {status}: {message_of(body)}")
        return body

    def create_alias(self, domain, settings):
        return self.expect("POST", f"/domains/{domain}/aliases", settings)

    def update_alias(self, domain, alias_id, settings):
        return self.expect("PUT", f"/domains/{domain}/aliases/{alias_id}", settings)

    def delete_alias(self, domain, alias_id):
        return self.expect("DELETE", f"/domains/{domain}/aliases/{alias_id}")
