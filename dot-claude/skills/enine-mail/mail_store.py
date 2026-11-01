"""The mail store: message identity, the account and counterparty rules, ingest, resolve, and
the user-immutable lock that pins a stored message.

Ported from delphi's mail/lib/store.py, core/identity.py (the owner addresses only) and the
chflags helpers of mail/lib/_pin.py. ingest files on this layout (ADR-MAIL):

    <store>/<account>/archive/<counterparty-domain>/<YYYY-MM>/<sha1(message-key)[:16]>.eml

The message-key is the normalized RFC Message-ID (<> stripped, lowercased), with a
`<provider>:<native>` fallback when the header is missing. The wicket harvest uses the same
folders but names its files another way, so a tracked and a harvested copy of one message can
both be stored, and resolve finds a message by the Message-ID inside the file when no file
carries its computed name.

No path lives here: every function that touches the store takes it as `store`, and the owner
addresses come from the file the caller names (mail_paths.py is the paths' one home).
"""

import email
import email.utils
import hashlib
import json
import os
import shutil
import stat
import sys
from email.policy import default as default_policy
from functools import lru_cache
from pathlib import Path

from mail_eml import header_values, read_head

# Filing bucket for a message with no resolvable counterparty (owner-only recipients, or
# several distinct external domains at once). Mirrors wicket.
UNFILED = "unfiled"

# Account partition when no delivery recipient can be read off the headers.
UNKNOWN_ACCOUNT = "unknown"


class Refusal(Exception):
    """The run cannot proceed: a bad argument, missing config, a target that does not exist.
    mail.py prints it and exits 2."""


@lru_cache(maxsize=None)
def _owners(path):
    """The owner addresses in `path`, lowercased; empty when the file is absent or malformed."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return frozenset()
    raw = data.get("owner_addresses", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(a).strip().lower() for a in raw if str(a).strip())


def owner_addresses(identity):
    """The mailbox owner's own addresses, from the owner-address file at `identity`:
    {"owner_addresses": ["someone@example.com"]}. Matched by exact address, never by domain:
    a domain the owner uses carries other people's mail too. A file that is absent or
    malformed reads as no addresses."""
    return _owners(identity)


def normalize_message_id(raw):
    """The bare RFC Message-ID (<> stripped, lowercased), or None when absent or empty."""
    if not raw:
        return None
    _, addr = email.utils.parseaddr(raw)
    candidate = (addr or raw).strip().strip("<>").strip().lower()
    return candidate or None


def message_key(*, message_id, provider="delphi", native_id=""):
    """Provider-neutral key: the normalized Message-ID, else `provider:native`."""
    normalized = normalize_message_id(message_id)
    if normalized:
        return normalized
    return f"{provider}:{native_id}"


def local_msgid(key):
    """Compact 16-hex archive stem for a message-key (Gmail X-GM-MSGID parity)."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _addresses(headers):
    return [addr.lower() for _, addr in email.utils.getaddresses(headers) if addr]


def _domain_of(addr):
    addr = addr.strip().lower()
    if "@" not in addr:
        return None
    return addr.split("@", 1)[1] or None


def _is_owner(addr, owners):
    return addr.strip().lower() in owners


def counterparty_domain(msg, owners):
    """The domain a message files under, or UNFILED.

    Inbound (From is not the owner): the sender's domain. Outbound: the single external
    recipient domain, UNFILED when several distinct ones appear.
    """
    from_addrs = _addresses(msg.get_all("From", []))
    sender = from_addrs[0] if from_addrs else ""
    if sender and not _is_owner(sender, owners):
        return _domain_of(sender) or UNFILED
    recipients = _addresses(msg.get_all("To", []) + msg.get_all("Cc", []))
    external = {d for a in recipients if not _is_owner(a, owners) and (d := _domain_of(a))}
    return next(iter(external)) if len(external) == 1 else UNFILED


def _canonical_account(addr):
    """Lowercase the address and drop a `+tag`, so tagged deliveries share one account."""
    addr = addr.strip().lower()
    local, _, domain = addr.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}" if domain else (local or UNKNOWN_ACCOUNT)


def account_of(msg, owners):
    """The owner mailbox a message belongs to: the top-level filing partition.

    Outbound (From is the owner): the owner's From address. Inbound: Delivered-To, then
    X-Original-To, then the first owner address among To/Cc, else the first recipient.
    UNKNOWN_ACCOUNT when no address can be read. Lowercased, `+tag` stripped.
    """
    from_addrs = _addresses(msg.get_all("From", []))
    sender = from_addrs[0] if from_addrs else ""
    if sender and _is_owner(sender, owners):
        return _canonical_account(sender)
    for header in ("Delivered-To", "X-Original-To"):
        addrs = _addresses(msg.get_all(header, []))
        if addrs:
            return _canonical_account(addrs[0])
    recipients = _addresses(msg.get_all("To", []) + msg.get_all("Cc", []))
    for addr in recipients:
        if _is_owner(addr, owners):
            return _canonical_account(addr)
    return _canonical_account(recipients[0]) if recipients else UNKNOWN_ACCOUNT


def _month_of(msg):
    dt = email.utils.parsedate_to_datetime(msg.get("Date", "") or "")
    return dt.strftime("%Y-%m")


def _key_of(msg, raw):
    native = hashlib.sha1(raw).hexdigest()
    return message_key(message_id=msg.get("Message-ID"), native_id=native)


def archive_relpath(msg, raw, owners):
    """The path relative to the store a message files at."""
    stem = local_msgid(_key_of(msg, raw))
    account, domain = account_of(msg, owners), counterparty_domain(msg, owners)
    return Path(account) / "archive" / domain / _month_of(msg) / f"{stem}.eml"


def ingest(src, *, store, identity, move=True, dry_run=False):
    """File one .eml into the store; return what was (or would be) done.

    Idempotent and byte-preserving. A message already stored, at the path its key gives or at
    the path wicket's manifest records for its Message-ID (a harvested copy is named another
    way), is `exists`: nothing is written or overwritten, and the source is consumed on `move`
    only when it is byte-identical to the stored file. `action` is filed, exists or plan.
    """
    raw = src.read_bytes()
    msg = email.message_from_bytes(raw, policy=default_policy)
    key = _key_of(msg, raw)
    owners = owner_addresses(identity)
    rel = archive_relpath(msg, raw, owners)
    dst = store / rel
    info = {
        "src": str(src),
        "message_key": key,
        "msgid": normalize_message_id(msg.get("Message-ID")),
        "account": account_of(msg, owners),
        "domain": counterparty_domain(msg, owners),
        "month": _month_of(msg),
        "relpath": str(rel),
        "path": str(dst),
    }
    held = _held(info["msgid"], dst, store)
    if held is not None:
        info.update(action="exists", relpath=str(held.relative_to(store)), path=str(held))
        if not dry_run and move and src.resolve() != held.resolve() and held.read_bytes() == raw:
            src.unlink()  # a byte-identical duplicate of the stored file
        return info
    if dry_run:
        info["action"] = "plan"
        return info
    dst.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(dst))
    else:
        shutil.copy2(str(src), str(dst))
    info["action"] = "filed"
    return info


def _held(msgid, dst, store):
    """The stored file already holding this message: the path its key gives, else the file
    wicket's manifest records for its Message-ID; None when neither has it."""
    if dst.exists():
        return dst
    return _from_manifests({msgid}, store).get(msgid) if msgid else None


def resolve(message_id, store):
    """The stored .eml for a message-id in `store`, or None; see resolve_many."""
    return resolve_many([message_id], store)[message_id]


def resolve_many(message_ids, store):
    """{message-id: the stored .eml holding it in `store`, or None}.

    Answered in three passes, each only for the ids the one before left open:
      1. wicket's manifests: a row whose id is the id, with a path to a file that exists and
         is not excluded, whose Message-ID header is the id (the row is a cache; the file
         decides). Of several such files, the first in path order.
      2. a file named by the id's computed stem, which is how ingest files and which no
         manifest records;
      3. every stored file's Message-ID header, in path order, from one walk.
    One Message-ID can sit on several files (a tracked and a harvested copy, or the same
    message under two accounts); each pass returns the first in sorted path order.
    """
    found = dict.fromkeys(message_ids)
    if not found or not store.exists():
        return found
    normalized = {mid: normalize_message_id(mid) for mid in found}
    answered = _from_manifests({n for n in normalized.values() if n}, store)
    for mid, key in normalized.items():
        found[mid] = answered.get(key)
    open_ids = [mid for mid, path in found.items() if path is None]
    if not open_ids:
        return found
    files = sorted(store.rglob("*.eml"))
    by_stem = {}
    for path in files:
        by_stem.setdefault(path.stem, path)
    wanted = {}  # normalized id -> the given ids that normalize to it
    for mid in open_ids:
        hit = by_stem.get(local_msgid(message_key(message_id=mid)))
        if hit is not None:
            found[mid] = hit
        elif normalized[mid]:
            wanted.setdefault(normalized[mid], []).append(mid)
    if wanted:
        _match_headers(files, wanted, found)
    return found


# wicket's per-account manifest: <store>/<account>/manifest/<YYYY>.jsonl, one JSON row per
# message (wicket ADR 0002). wicket replaces a shard whole through a temp file and a rename,
# so no reader sees it half written; a line that does not parse is skipped all the same.
# A row's `path` is relative to <store>/<account>/archive.
MANIFEST_DIRNAME = "manifest"


def manifest_rows(store_dir):
    """Every row in one store's manifest shards, oldest shard first; none when it has none."""
    for shard in sorted((store_dir / MANIFEST_DIRNAME).glob("*.jsonl")):
        try:
            text = shard.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def manifest_stores(store):
    """The account directories under `store` that hold a manifest, sorted."""
    if not store.is_dir():
        return []
    return sorted(d for d in store.iterdir() if (d / MANIFEST_DIRNAME).is_dir())


def _from_manifests(keys, store):
    """{normalized id: its stored file} for the ids the manifests answer, each verified.

    A row's id is wicket's key, made by the same normalization as ours; a second copy's id is
    `<key>|<provider>:<native-id>`, so the key is the part before `|`.
    """
    candidates = {}
    for store_dir in manifest_stores(store):
        for row in manifest_rows(store_dir):
            key = str(row.get("id", "")).split("|", 1)[0]
            if key in keys and row.get("path") and not row.get("excluded"):
                candidates.setdefault(key, set()).add(store_dir / "archive" / str(row["path"]))
    answered = {}
    for key, paths in candidates.items():
        for path in sorted(paths):
            if key in message_ids_in(path):
                answered[key] = path
                break
    return answered


def message_ids_in(path):
    """The normalized Message-IDs in a file's header block; none when it cannot be read."""
    try:
        low = read_head(path).lower()
    except OSError:
        return set()
    return {normalize_message_id(value.decode("utf-8", errors="replace"))
            for value in header_values(low, (b"message-id",))}


def _match_headers(files, wanted, found):
    """Fill `found` for each wanted id from the first file whose Message-ID header is it.

    Each file's header block is read and its Message-ID values taken from the raw bytes, folds
    joined, then normalized as the id was; no header is parsed, and a body or another
    message's References never counts.
    """
    for path in files:
        if not wanted:
            return
        for normalized in message_ids_in(path):
            for mid in wanted.pop(normalized, ()):
                found[mid] = path


# The user-immutable flag is a BSD/macOS file flag: os.chflags and st_flags exist only there.
# Off macOS the three helpers refuse, and mail.py's pin refuses before reaching them.
if sys.platform == "darwin":

    def is_locked(p):
        """Whether the user-immutable flag is set on a file."""
        return bool(os.stat(p).st_flags & stat.UF_IMMUTABLE)

    def lock(p):
        """Set the user-immutable flag on a file (chflags uchg)."""
        os.chflags(p, os.stat(p).st_flags | stat.UF_IMMUTABLE)  # pylint: disable=no-member

    def unlock(p):
        """Clear the user-immutable flag on a file (chflags nouchg)."""
        os.chflags(p, os.stat(p).st_flags & ~stat.UF_IMMUTABLE)  # pylint: disable=no-member

else:

    def _unavailable():
        return OSError("mail pin needs the macOS user-immutable flag (os.chflags)")

    def is_locked(p):
        """Whether the user-immutable flag is set: unavailable off macOS."""
        raise _unavailable()

    def lock(p):
        """Set the user-immutable flag: unavailable off macOS."""
        raise _unavailable()

    def unlock(p):
        """Clear the user-immutable flag: unavailable off macOS."""
        raise _unavailable()
