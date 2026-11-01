"""Send mail through AWS SES: build a numbered draft, show it, send it.

Ported from delphi's mail/lib/_send.py, split so that building a draft and sending one are
separate calls: `build` writes draft N, `listing` and `show` read, `apply` sends.

A draft is two files in the drafts folder: N.eml, the exact bytes SES gets, and N.json, its
sending address, SES profile and region, destinations and state. `apply` marks the draft
`sending` before it calls SES, so a crash or a retry cannot send it twice; on success it
appends one line to the sent log and removes both files, and on an SES refusal it returns the
draft to `draft`.

Every per-domain value (SES profile, region, Bcc, Reply-To, name, deprecated) comes from the
sender config and nothing else; no flag overrides one. The Bcc goes only in the SES
destinations, never in a header. No Message-ID is set: SES assigns
<ses_id@email.amazonses.com>, which the harvested Bcc copy also carries, so the mail store
files that one copy under the key the sent log records. Nothing here writes to the mail store.

No path lives here: the drafts folder (`drafts`), the sent log (`sent`), the sender config
(`senders`) and the mail store (`store`, read for reply threading) are arguments, and
mail_paths.py is their one home.
"""

import base64
import email.utils
import json
import mimetypes
import os
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import mail_store
from mail_store import Refusal

# The sender config is TOML: [defaults] and [domains."<domain>"] with profile, region, bcc,
# reply_to, name ({domain} is substituted), deprecated.

# The default policy RFC 2047-encodes and folds a long Message-ID in In-Reply-To and
# References, which breaks threading. SMTP line endings, folded only past RFC 5322's 998.
POLICY = policy.SMTP.clone(max_line_length=998)

# The domain of every Message-ID SES assigns, so the manifest can record it at send time.
SES_MESSAGE_DOMAIN = "email.amazonses.com"


@dataclass(frozen=True)
class Sender:
    """One sending domain's settings, read from the sender config."""

    address: str
    domain: str
    profile: str
    region: str
    bcc: str | None
    reply_to: str | None
    name: str | None


def _senders(senders):
    """The sender config at `senders`, parsed; a refusal when it is missing or malformed."""
    try:
        with senders.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise Refusal(f"no sender config at {senders}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise Refusal(f"{senders}: {exc}") from exc


def sender_settings(from_header, senders):
    """The settings for the From address's domain: its entry, then [defaults]."""
    address = email.utils.parseaddr(from_header)[1].lower()
    if "@" not in address:
        raise Refusal(f"--from {from_header!r} holds no address")
    domain = address.rsplit("@", 1)[1]
    config = _senders(senders)
    defaults = config.get("defaults", {})
    entry = config.get("domains", {}).get(domain)
    if entry is None:
        raise Refusal(f"{domain} is not a sending domain in {senders}; add it there to send")
    if entry.get("deprecated"):
        raise Refusal(f"{domain} is marked deprecated in {senders}; send from another domain")

    def setting(key):
        value = entry.get(key, defaults.get(key))
        return str(value).format(domain=domain) if value is not None else None

    profile, region = entry.get("profile"), setting("region")
    if not profile or not region:
        raise Refusal(f"{domain} has no SES profile or region in {senders}")
    return Sender(address, domain, str(profile), region, setting("bcc"), setting("reply_to"), setting("name"))


def _bracketed(message_id):
    """A Message-ID in angle brackets, whitespace unfolded."""
    bare = " ".join(message_id.split()).strip("<>")
    return f"<{bare}>"


def sent_lines(sent):
    """Every line of the sent log, oldest first; none when nothing has been sent."""
    if not sent.exists():
        return []
    with sent.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _thread(in_reply_to, sent, store):
    """(In-Reply-To, References) for a reply: from the sent log, then the store, then bare.

    References is the parent's own References with the parent's Message-ID appended, which
    is what a mail client builds, so the recipient sees one thread. The sent log goes first
    because it is one small file, while a store miss walks every stored message; a message
    this sender sent carries the same Message-ID and References in both.
    """
    wanted = mail_store.normalize_message_id(in_reply_to)
    for line in reversed(sent_lines(sent)):
        if mail_store.normalize_message_id(line["message_id"]) == wanted:
            prior = line["references"] or ""
            return line["message_id"], f"{prior} {line['message_id']}".strip()
    found = mail_store.resolve(in_reply_to, store)
    if found is not None:
        with found.open("rb") as fh:
            parent = BytesParser(policy=policy.compat32).parse(fh, headersonly=True)
        parent_id = _bracketed(str(parent["Message-ID"] or in_reply_to))
        prior = " ".join(str(parent["References"] or "").split())
        return parent_id, f"{prior} {parent_id}".strip()
    bare = _bracketed(in_reply_to)
    return bare, bare


def _addresses(values):
    """The addr-specs in header values, each value possibly holding several."""
    return [addr for _, addr in email.utils.getaddresses(values) if addr]


def _make_private_dir(path):
    """Create `path` and any missing parent, each mode 700."""
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)


def _write_private(path, data):
    """Write `path` atomically, mode 600."""
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    tmp.chmod(0o600)
    os.replace(tmp, path)


def _append_private(path, line):
    """Append one line to `path`, creating it mode 600."""
    _make_private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(line)
    path.chmod(0o600)


def numbers(drafts):
    """The numbers of the drafts in the folder, ascending."""
    if not drafts.is_dir():
        return []
    return sorted(int(p.stem) for p in drafts.glob("*.json") if p.stem.isdigit())


def _meta_path(drafts, number):
    return drafts / f"{number}.json"


def _eml_path(drafts, number):
    return drafts / f"{number}.eml"


def _meta(drafts, number):
    """Draft `number`'s sidecar; a refusal when there is no such draft."""
    path = _meta_path(drafts, number)
    if not path.exists() or not _eml_path(drafts, number).exists():
        raise Refusal(f"no draft {number} in {drafts}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_meta(drafts, number, meta):
    _write_private(_meta_path(drafts, number), (json.dumps(meta, indent=2) + "\n").encode())


def draft(drafts, number):
    """Draft `number` as data: its sidecar, its header block as stored, its text."""
    meta = _meta(drafts, number)
    raw = _eml_path(drafts, number).read_bytes()
    message = BytesParser(policy=POLICY).parsebytes(raw)
    head = raw.split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="replace")
    body = message.get_body(("plain",))
    return {
        "number": number,
        "state": meta["state"],
        "from": meta["from"],
        "profile": meta["profile"],
        "region": meta["region"],
        "to": meta["to"],
        "cc": meta["cc"],
        "bcc": meta["bcc"],
        "subject": str(message["Subject"] or ""),
        "attachments": [str(p.get_filename()) for p in message.iter_attachments()],
        "path": str(_eml_path(drafts, number)),
        "head": head.replace("\r\n", "\n"),
        "text": str(body.get_content()).replace("\r\n", "\n") if body is not None else "",
    }


def _read_file(path, flag):
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Refusal(f"{flag} {path}: {exc.strerror or exc}") from exc


def build(*, senders, drafts, sent=None, store=None,  # pylint: disable=too-many-arguments,too-many-locals
          sender, to, cc, bcc, reply_to, subject, body, attach, in_reply_to):
    """Build the message, write it as the next draft number, and return that draft. `sent` and
    `store` are read only to thread a reply (`in_reply_to`)."""
    if not sender or not to or not subject or body is None:
        raise Refusal("a draft needs --from, --to, --subject and --body")
    config = sender_settings(sender, senders)
    try:
        text = _read_file(body, "--body").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refusal(f"--body {body} is not UTF-8 text") from exc
    message = EmailMessage(policy=POLICY)
    shown, _ = email.utils.parseaddr(sender)
    message["From"] = sender if shown or not config.name else email.utils.formataddr((config.name, config.address))
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    replies = reply_to or ([config.reply_to] if config.reply_to else [])
    if replies:
        message["Reply-To"] = ", ".join(replies)
    message["Subject"] = subject
    message["Date"] = email.utils.formatdate(localtime=True)
    if in_reply_to:
        message["In-Reply-To"], message["References"] = _thread(in_reply_to, sent, store)
    message.set_content(text)
    for path in attach:
        data = _read_file(path, "--attach")
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = kind.split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)

    to_addrs, cc_addrs = _addresses(to), _addresses(cc)
    if not to_addrs:
        raise Refusal(f"--to {to!r} holds no address")
    seen = {a.lower() for a in to_addrs + cc_addrs}
    bcc_addrs = []
    for addr in ([config.bcc] if config.bcc else []) + _addresses(bcc):
        if addr.lower() not in seen:
            bcc_addrs.append(addr)
            seen.add(addr.lower())

    _make_private_dir(drafts)
    number = (numbers(drafts) or [0])[-1] + 1
    _write_private(_eml_path(drafts, number), message.as_bytes())
    _write_meta(drafts, number, {"from": config.address, "profile": config.profile, "region": config.region,
                         "to": to_addrs, "cc": cc_addrs, "bcc": bcc_addrs, "state": "draft"})
    return draft(drafts, number)


def ses_request(meta, raw):
    """The `sesv2 send-email` input: the raw bytes, and every destination. No FromEmailAddress:
    with it SES rewrites the From header without its display name; without it SES takes the
    sender from the draft's own From header. SES sets the Date to the time of sending either way."""
    destination = {key: meta[field] for key, field in
                   (("ToAddresses", "to"), ("CcAddresses", "cc"), ("BccAddresses", "bcc")) if meta[field]}
    return {
        "Destination": destination,
        "Content": {"Raw": {"Data": base64.b64encode(raw).decode("ascii")}},
    }


def _call_ses(drafts, number, meta, raw):
    """Hand draft `number` to SES; its MessageId, or a refusal saying what happened."""
    request = drafts / f".{number}.ses.json"
    _write_private(request, json.dumps(ses_request(meta, raw)).encode())
    command = ["aws", "sesv2", "send-email", "--profile", meta["profile"], "--region", meta["region"],
               "--cli-input-json", f"file://{request}", "--output", "json"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        meta["state"] = "draft"
        _write_meta(drafts, number, meta)
        raise Refusal(f"could not run aws: {exc}; draft {number} not sent") from exc
    finally:
        request.unlink(missing_ok=True)
    if done.returncode != 0:
        meta["state"] = "draft"
        _write_meta(drafts, number, meta)
        raise Refusal(f"SES refused draft {number} (aws exit {done.returncode}): {done.stderr.strip()}")
    try:
        return str(json.loads(done.stdout)["MessageId"])
    except (ValueError, KeyError, TypeError) as exc:
        # aws exited 0, so SES may have accepted it: the draft stays `sending`, which refuses
        # a second --apply, rather than risk sending it twice.
        raise Refusal(f"aws exited 0 but printed no MessageId; draft {number} stays 'sending' so it "
                      f"cannot go twice. aws printed: {done.stdout.strip()!r}") from exc


def apply(drafts, number, sent):
    """Send draft `number`: mark it, call SES, append its line to the sent log, remove the draft."""
    meta = _meta(drafts, number)
    if meta["state"] == "sending":
        raise Refusal(f"draft {number} is marked 'sending': an earlier send may have gone. Look for its "
                      f"Bcc copy before setting its state to 'draft' in {_meta_path(drafts, number)}")
    raw = _eml_path(drafts, number).read_bytes()
    shown = draft(drafts, number)
    meta["state"] = "sending"
    _write_meta(drafts, number, meta)
    ses_id = _call_ses(drafts, number, meta, raw)

    message = BytesParser(policy=POLICY).parsebytes(raw)
    line = {
        "ses_id": ses_id,
        "message_id": f"<{ses_id}@{SES_MESSAGE_DOMAIN}>",
        "sent_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "from": meta["from"],
        "to": meta["to"],
        "cc": meta["cc"],
        "bcc": meta["bcc"],
        "reply_to": _addresses([str(message["Reply-To"])]) if message["Reply-To"] else [],
        "subject": shown["subject"],
        "in_reply_to": str(message["In-Reply-To"]) if message["In-Reply-To"] else None,
        "references": str(message["References"]) if message["References"] else None,
        "attachments": shown["attachments"],
        "profile": meta["profile"],
        "region": meta["region"],
    }
    _append_private(sent, json.dumps(line, ensure_ascii=False) + "\n")
    _eml_path(drafts, number).unlink()
    _meta_path(drafts, number).unlink()
    return line
