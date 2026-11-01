#!/usr/bin/env python3
"""Search, resolve, file and pin the mail in a mail store, and draft and send mail through
AWS SES.

Usage
  mail.py search [--from T] [--to T] [--cc T] [--bcc T] [--subject T] [--after D] [--before D]
                 [--query Q] [--account MAILBOX] [--limit N]
  mail.py resolve <message-id>
  mail.py ingest <file.eml>... [--copy] [--apply]
  mail.py pin <message-id>... [--check | --apply]
  mail.py pin --unpin <message-id>... [--apply]
  mail.py draft [N] [--from A --to A --subject S --body FILE] [--cc A] [--bcc A] [--reply-to A]
                [--attach FILE] [--in-reply-to MSGID]
  mail.py send N [--apply]
  mail.py send --from A --to A --subject S --body FILE [...]
Every verb takes --json, which prints the result as JSON instead of text, and the path flags
below that it uses, before or after the verb.

Examples
  mail.py search --from "alice or bob" --after 2026-08-01      mail from either, since 1 August
  mail.py search --query 'from:example.org subject:(dues OR invoice)'
  mail.py search --subject "art*"                                Art, Arts, Artwork; not Smart
  mail.py resolve "<abc123@mail.example.com>"                    the stored .eml for a message-id
  mail.py ingest ~/Downloads/note.eml                            where it would file; writes nothing
  mail.py ingest ~/Downloads/note.eml --apply                    file it (moves it; --copy keeps it)
  mail.py pin abc123@mail.example.com                            what would be locked; writes nothing
  mail.py pin abc123@mail.example.com --apply                    lock it
  mail.py pin abc123@mail.example.com --check                    exit 1 if it is unlocked or missing
  mail.py pin --unpin abc123@mail.example.com --apply            unlock it
  mail.py draft --from alice@example.org --to bob@example.net --subject Hi --body body.txt
                                                                 build draft N and show it
  mail.py draft                                                  the drafts, and the mail sent
  mail.py draft 3                                                draft 3 as it will go
  mail.py send 3                                                 draft 3 as it will go; sends nothing
  mail.py send 3 --apply                                         send draft 3 through SES

Verbs
  search   find stored mail by header flags or a Gmail-style --query, newest first; read-only.
           A term matches whole words: oci finds "OCI card", not "Social". An address or domain
           is a run of words, so alice@example.com and example.org find mail to or from them.
           A trailing * makes a prefix: art* finds "Arts". There is no substring form.
           One grammar, Gmail's: words are ANDed, or/and (any case) combine them, -word
           excludes, ( ) groups, "quotes" make a phrase or the literal word "or". A field flag
           is its field's query: --from X is from:(X), and --after D is after:D
  resolve  print the stored .eml path for a message-id; read-only
  ingest   file a tracked .eml byte-preserved into the store, on the harvest's layout
  pin      lock the stored .eml of each message-id immutable (chflags uchg) so nothing can delete
           it; add-only: --unpin is the only unlock. macOS only
  draft    with message args, build draft N, the exact bytes SES will get, and show it; with
           none, list the drafts and the sent lines; with N, show draft N. Never sends
  send     with N, show draft N as it will go; with N --apply, send it through SES. With message
           args and no N, run draft first, then show the new draft. --apply takes only a draft
           number, so what goes out is the draft that was shown

Exit
  0  done, nothing to do, or printed
  1  changes pending (a dry run), or something wrong found: nothing matched, a message-id not
     in the store, an unlocked or dangling pin, a file skipped
  2  refused: a bad argument, a path not given, a missing sender config, a missing draft, an
     SES refusal

Paths: none has a default. Each comes from its flag, else its environment variable; a verb
that needs one that is not given refuses and names both.
  --store DIR       ENINE_MAIL_STORE     the mail store: <account>/archive/... and wicket's
                                         <account>/manifest/
  --identity FILE   ENINE_MAIL_IDENTITY  the owner-address file, {"owner_addresses": [...]}, to
                                         tell sent mail from received when filing
  --senders FILE    ENINE_MAIL_SENDERS   the sender config (TOML): per domain the SES profile,
                                         region, Bcc, Reply-To, name; no flag overrides one
  --drafts DIR      ENINE_MAIL_DRAFTS    the drafts folder: N.eml and N.json
  --sent FILE       ENINE_MAIL_SENT      the sent log, one JSON line per message sent

Which verb needs which
  search, resolve, pin           --store
  ingest                         --store --identity
  draft (no args)                --drafts --sent
  draft N, send N                --drafts
  send N --apply                 --drafts --sent
  draft or send with message     --senders --drafts, and --sent --store with --in-reply-to
  args                           (a reply is threaded from the sent log, then the store)
"""

import argparse
import json
import os
import sys
from pathlib import Path

# The store, search and send code have one home each, beside this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mail_paths  # noqa: E402
import mail_search  # noqa: E402
import mail_send  # noqa: E402
import mail_store  # noqa: E402
from mail_store import Refusal  # noqa: E402

COMMAND = "python3 ~/.claude/skills/enine-mail/mail.py"


# --- search, resolve --------------------------------------------------------------------


def search(args, paths):
    store = paths.need("search", "store").store
    result = mail_search.search(store=store, sender=args.sender, to=args.to, cc=args.cc, bcc=args.bcc,
                                subject=args.subject, before=args.before, after=args.after,
                                query=args.query, account=args.account, limit=args.limit)
    return result, 0 if result["matched"] else 1


def print_search(result):
    for row in result["rows"]:
        when = row["date"][:16].replace("T", " ") if row["date"] else "(no date)"
        attached = f"  [{row['attachments']} attached]" if row["attachments"] else ""
        print(f"{when}  {row['account']}  {row['from']}")
        print(f"    {row['subject']}{attached}")
        print(f"    {row['path']}")
    shown = len(result["rows"])
    more = f", newest {shown} shown (--limit)" if result["matched"] > shown else ""
    print(f"{result['matched']} matched{more}; {result['scanned']} .eml scanned, "
          f"{result['unparseable']} unparseable skipped.")


def resolve(args, paths):
    path = mail_store.resolve(args.message_id, paths.need("resolve", "store").store)
    result = {"message_id": args.message_id, "path": str(path) if path else None}
    return result, 0 if path else 1


def print_resolve(result):
    if result["path"] is None:
        print(f"not found: {result['message_id']}", file=sys.stderr)
        return
    print(result["path"])


# --- ingest -----------------------------------------------------------------------------


def ingest(args, paths):
    """File each .eml into the store, or with no --apply say where each would go."""
    paths.need("ingest", "store", "identity")
    rows = []
    for src in args.files:
        if not src.is_file():
            rows.append({"src": str(src), "action": "skip", "reason": "not a file", "relpath": None})
            continue
        info = mail_store.ingest(src, store=paths.store, identity=paths.identity,
                                 move=not args.copy, dry_run=not args.apply)
        rows.append({"src": str(src), "action": str(info["action"]), "reason": None,
                     "relpath": str(info["relpath"])})
    result = {"dry_run": not args.apply, "files": rows,
              "skipped": sum(1 for r in rows if r["action"] == "skip")}
    pending = any(r["action"] == "plan" for r in rows)
    return result, 1 if result["skipped"] or pending else 0


def print_ingest(result):
    for row in result["files"]:
        if row["action"] == "skip":
            print(f"skip ({row['reason']}): {row['src']}", file=sys.stderr)
            continue
        print(f"{row['action']:6}  {row['relpath']}  <-  {Path(row['src']).name}")
    if any(row["action"] == "plan" for row in result["files"]):
        print("\nDry run: nothing written. Run again with --apply to file.")


# --- pin --------------------------------------------------------------------------------


def pin(args, paths):
    """Lock, preview, check or unlock the stored .eml of the given message-ids."""
    if args.unpin and args.ids:
        raise Refusal("give message-ids to pin, or --unpin with the ids to unlock, not both")
    if args.check and (args.apply or args.unpin):
        raise Refusal("--check writes nothing; it takes neither --apply nor --unpin")
    if not args.unpin and not args.ids:
        raise Refusal("give the message-ids to pin, or --unpin with the ids to unlock")
    if not hasattr(os, "chflags"):
        raise Refusal("mail pin needs the macOS user-immutable flag (os.chflags); unavailable here")
    store = paths.need("pin", "store").store
    try:
        if args.unpin:
            return unpin(args.unpin, store, apply=args.apply)
        return lock(args.ids, store, check=args.check, apply=args.apply)
    except OSError as exc:
        raise Refusal(f"{exc}; unavailable here") from exc


def lock(ids, store, *, check, apply):
    """Each given id's stored file locked (`apply`), listed (a dry run) or checked."""
    ids = list(dict.fromkeys(ids))
    hits = mail_store.resolve_many(ids, store)
    resolved, dangling = [], []
    for mid in ids:
        hit = hits[mid]
        if hit is None:
            dangling.append(mid)
        else:
            resolved.append(hit)
    unlocked = [p for p in resolved if not mail_store.is_locked(p)]
    if apply:
        for p in unlocked:
            mail_store.lock(p)
    mode = "check" if check else "pin" if apply else "dry-run"
    result = {"mode": mode, "given": len(ids), "resolved": len(resolved),
              "newly": [str(p.relative_to(store)) for p in unlocked], "already_locked": len(resolved) - len(unlocked),
              "dangling": dangling}
    pending = bool(unlocked) and not apply
    return result, 1 if dangling or pending else 0


def unpin(ids, store, *, apply):
    """Each given id's stored file unlocked (`apply`), or listed (a dry run)."""
    rows = []
    hits = mail_store.resolve_many(list(dict.fromkeys(ids)), store)
    for mid, hit in hits.items():
        locked = mail_store.is_locked(hit) if hit else None
        rows.append({"message_id": mid, "path": str(hit.relative_to(store)) if hit else None, "locked": locked})
        if apply and locked:
            mail_store.unlock(hit)
    result = {"mode": "unpin" if apply else "unpin dry-run", "unpinned": rows,
              "unresolved": sum(1 for r in rows if r["path"] is None)}
    pending = any(r["locked"] for r in rows) and not apply
    return result, 1 if result["unresolved"] or pending else 0


def print_pin(result):
    if result["mode"] in ("unpin", "unpin dry-run"):
        verb = "unlocked" if result["mode"] == "unpin" else "would unlock"
        for row in result["unpinned"]:
            if row["path"] is None:
                print(f"UNRESOLVED  {row['message_id']}", file=sys.stderr)
            elif row["locked"]:
                print(f"{verb}  {row['path']}")
            else:
                print(f"ok (not locked)  {row['path']}")
        if result["mode"] == "unpin dry-run" and any(row["locked"] for row in result["unpinned"]):
            print("\nDry run: nothing unlocked. Run again with --apply to unlock.")
        return

    if result["mode"] == "check":
        for rel in result["newly"]:
            print(f"UNLOCKED  {rel}")
        for mid in result["dangling"]:
            print(f"DANGLING  {mid}")
        if result["newly"] or result["dangling"]:
            print(f"\n{len(result['newly'])} unlocked, {len(result['dangling'])} dangling "
                  f"of {result['given']} message-id(s).")
            return
        print(f"OK: all {result['resolved']} .eml locked; nothing dangling.")
        return

    verb = "would lock" if result["mode"] == "dry-run" else "locked"
    for rel in result["newly"]:
        print(f"{verb}  {rel}")
    for mid in result["dangling"]:
        print(f"DANGLING  {mid}", file=sys.stderr)
    print(f"{len(result['newly'])} {verb}, {result['already_locked']} already locked, "
          f"{len(result['dangling'])} dangling of {result['given']} message-id(s).")
    if result["mode"] == "dry-run" and result["newly"]:
        print("\nDry run: nothing locked. Run again with --apply to lock.")


# --- draft, send ------------------------------------------------------------------------


def _built(verb, args, paths):
    """Build a draft from the message args. A reply (--in-reply-to) is threaded from the sent
    log and then the store, so it needs both."""
    reply = ("sent", "store") if args.in_reply_to else ()
    paths.need(verb, "senders", "drafts", *reply)
    return mail_send.build(senders=paths.senders, drafts=paths.drafts, sent=paths.sent,
                           store=paths.store, **_message(args))


def _message(args):
    """The message args, as mail_send.build takes them."""
    return {"sender": args.sender, "to": args.to or [], "cc": args.cc or [], "bcc": args.bcc or [],
            "reply_to": args.reply_to or [], "subject": args.subject, "body": args.body,
            "attach": args.attach or [], "in_reply_to": args.in_reply_to}


def _building(args):
    return any((args.sender, args.to, args.cc, args.bcc, args.reply_to, args.subject, args.body,
                args.attach, args.in_reply_to))


def draft(args, paths):
    """Build draft N from message args, list the drafts and sent lines, or show draft N."""
    if args.number is None:
        if _building(args):
            return {"mode": "build", "drafts": [_built("draft", args, paths)], "sent": []}, 0
        drafts, sent = paths.need("draft", "drafts", "sent").drafts, paths.sent
        return {"mode": "list", "drafts": [mail_send.draft(drafts, n) for n in reversed(mail_send.numbers(drafts))],
                "sent": list(reversed(mail_send.sent_lines(sent)))}, 0
    if _building(args):
        raise Refusal(f"draft {args.number} is already built; a number takes no message args")
    drafts = paths.need("draft", "drafts").drafts
    return {"mode": "show", "drafts": [mail_send.draft(drafts, args.number)], "sent": []}, 0


def send(args, paths):
    """Show draft N as it will go, send it with --apply, or draft from message args and show it."""
    if args.number is None:
        if args.apply:
            raise Refusal(f"--apply needs a draft number, so what goes is what was shown: {COMMAND} send N --apply")
        if not _building(args):
            raise Refusal(f"send needs a draft number, or message args to draft one; {COMMAND} draft lists the drafts")
        return {"mode": "dry-run", "drafts": [_built("send", args, paths)], "sent": []}, 1
    if _building(args):
        raise Refusal(f"draft {args.number} is already built; a number takes no message args")
    if args.apply:
        paths.need("send --apply", "drafts", "sent")
        return {"mode": "apply", "drafts": [], "sent": [mail_send.apply(paths.drafts, args.number, paths.sent)]}, 0
    drafts = paths.need("send", "drafts").drafts
    return {"mode": "dry-run", "drafts": [mail_send.draft(drafts, args.number)], "sent": []}, 1


def _print_draft(d):
    """One draft in full: where it goes, then its headers and text exactly as stored."""
    print(f"draft {d['number']} ({d['state']})  {d['path']}")
    print(f"  SES:  profile {d['profile']}, region {d['region']}")
    print(f"  from: {d['from']}")
    for field in ("to", "cc", "bcc"):
        if d[field]:
            print(f"  {field + ':':5} {', '.join(d[field])}")
    print()
    print(d["head"])
    print()
    print(d["text"].rstrip("\n"))
    for name in d["attachments"]:
        print(f"[attachment: {name}]")


def print_draft(result):
    if result["mode"] in ("build", "show"):
        for d in result["drafts"]:
            _print_draft(d)
            print(f"\nsend it with: {COMMAND} send {d['number']} --apply")
        return
    if not result["drafts"] and not result["sent"]:
        print("no drafts and nothing sent")
        return
    for d in result["drafts"]:
        print(f"draft {d['number']:<3} {d['state']:7}  {d['subject']}  -> {', '.join(d['to'])}")
    for line in result["sent"]:
        print(f"sent  {line['sent_at']}  {line['subject']}  -> {', '.join(line['to'])}  {line['message_id']}")


def print_send(result):
    if result["mode"] == "apply":
        for line in result["sent"]:
            print(f"sent  {line['message_id']}  {line['subject']}")
            print(f"  to {', '.join(line['to'] + line['cc'] + line['bcc'])}")
        return
    for d in result["drafts"]:
        _print_draft(d)
        print(f"\nDry run: nothing sent. Send draft {d['number']} with: {COMMAND} send {d['number']} --apply")


# --- the command line -------------------------------------------------------------------

SEARCH_DESCRIPTION = (
    "Search every stored .eml, read-only, newest first. Every term matches whole words, any case: "
    "oci finds 'OCI card', not 'Social'; alice@example.com and example.org match those addresses "
    "and domains; a trailing * is a prefix, so art* finds 'Arts'. The flags and --query share one "
    "grammar, Gmail's: words are ANDed, 'or' and 'and' in any case combine them ('and' binding "
    "tighter), -word excludes, parentheses group, and double quotes make a phrase or the literal "
    'word "or". A field flag is that field\'s query: --from "alice@example.com or bob@example.net" '
    "is from:(alice@example.com or bob@example.net), and --after D is after:D. Separate flags and "
    "--query are ANDed. Dates are IST. Exit 1 when nothing matches."
)


def _search_args(p):
    p.add_argument("--from", dest="sender", metavar="TERMS", help="the From header contains TERMS")
    p.add_argument("--to", metavar="TERMS", help="the To header contains TERMS")
    p.add_argument("--cc", metavar="TERMS", help="the Cc header contains TERMS")
    p.add_argument("--bcc", "-bcc", metavar="TERMS",
                   help="the Bcc header contains TERMS; only your own sent copies carry one")
    p.add_argument("--subject", metavar="TERMS", help="the Subject header contains TERMS")
    p.add_argument("--after", metavar="DATE", help="dated DATE or later (IST); YYYY-MM-DD or YYYY/MM/DD")
    p.add_argument("--before", metavar="DATE", help="dated strictly before DATE (IST); YYYY-MM-DD or YYYY/MM/DD")
    p.add_argument("--query", metavar="Q",
                   help=("a Gmail-style query, ANDed with the flags: from: to: cc: bcc: subject: after: "
                         "before: older_than: newer_than: has:attachment filename:, words and \"phrases\" "
                         "(headers and text body), -term, or/and in any case, ( ); no in: or label:. "
                         "Terms match whole words; word* is a prefix"))
    p.add_argument("--account", action="append", metavar="MAILBOX",
                   help="search only this mailbox directory (repeatable; default every one)")
    p.add_argument("--limit", type=int, default=50, metavar="N",
                   help="show the newest N matches (default 50); the count covers all")


def _message_args(p):
    p.add_argument("--from", dest="sender", metavar="ADDR",
                   help="the address to send as; its domain must be in the sender config")
    for flag, what in (("--to", "a To recipient"), ("--cc", "a Cc recipient"),
                       ("--bcc", "a Bcc recipient, besides the domain's own Bcc"),
                       ("--reply-to", "a Reply-To address, replacing the domain's default")):
        p.add_argument(flag, action="append", metavar="ADDR", help=f"{what} (repeatable)")
    p.add_argument("--subject", help="the Subject line")
    p.add_argument("--body", type=Path, metavar="FILE", help="UTF-8 text file holding the body")
    p.add_argument("--attach", action="append", type=Path, metavar="FILE", help="a file to attach (repeatable)")
    p.add_argument("--in-reply-to", metavar="MSGID",
                   help="the Message-ID this replies to; sets In-Reply-To and References")


# The path flags each verb accepts after it: those it can use (see "Which verb needs which").
VERB_PATHS = {
    "search": ("store",),
    "resolve": ("store",),
    "ingest": ("store", "identity"),
    "pin": ("store",),
    "draft": ("senders", "drafts", "sent", "store"),
    "send": ("senders", "drafts", "sent", "store"),
}

VERBS = {
    "search": (search, print_search),
    "resolve": (resolve, print_resolve),
    "ingest": (ingest, print_ingest),
    "pin": (pin, print_pin),
    "draft": (draft, print_draft),
    "send": (send, print_send),
}


def parser():
    p = argparse.ArgumentParser(prog="mail.py", description=__doc__.strip(),
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    mail_paths.add_flags(p, mail_paths.NAMES)
    verbs = p.add_subparsers(dest="verb", required=True)
    _search_args(verbs.add_parser("search", help="find stored mail; read-only", description=SEARCH_DESCRIPTION))
    resolve_parser = verbs.add_parser("resolve", help="the stored .eml path for a message-id; read-only")
    resolve_parser.add_argument("message_id", metavar="MESSAGE_ID", help="RFC Message-ID (with or without <>)")
    ingest_parser = verbs.add_parser("ingest", help="file a tracked .eml into the store (a dry run without --apply)")
    ingest_parser.add_argument("files", metavar="FILE", nargs="+", type=Path, help=".eml file(s) to file")
    ingest_parser.add_argument("--copy", action="store_true", help="copy instead of move (keep the source)")
    ingest_parser.add_argument("--apply", action="store_true", help="file it (without it, print the target path)")
    pin_parser = verbs.add_parser("pin", help="lock stored .eml immutable (a dry run without --apply)")
    pin_parser.add_argument("ids", metavar="MESSAGE_ID", nargs="*", help="the message-ids whose .eml to lock")
    pin_parser.add_argument("--check", action="store_true",
                            help="exit 1 if any given id's .eml is unlocked or missing; writes nothing")
    pin_parser.add_argument("--unpin", nargs="+", metavar="MESSAGE_ID",
                            help="unlock (chflags nouchg) these message-ids' .eml instead")
    pin_parser.add_argument("--apply", action="store_true", help="lock, or with --unpin unlock (without it, a dry run)")
    draft_parser = verbs.add_parser("draft", help="build, list or show the drafts; never sends")
    draft_parser.add_argument("number", metavar="N", type=int, nargs="?", help="a draft number to show")
    _message_args(draft_parser)
    send_parser = verbs.add_parser("send", help="show draft N as it will go; send it with --apply")
    send_parser.add_argument("number", metavar="N", type=int, nargs="?", help="the draft number to send")
    _message_args(send_parser)
    send_parser.add_argument("--apply", action="store_true", help="send draft N through SES (without it, show it)")
    for verb, verb_parser in verbs.choices.items():
        verb_parser.add_argument("--json", action="store_true", help="print the result as JSON")
        mail_paths.add_flags(verb_parser, VERB_PATHS[verb], suppress=True)
    return p


def main(argv=None):
    """Run one verb; return its exit code. No arguments prints the help and returns 2."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__.strip())
        return 2
    args = parser().parse_args(argv)
    compute, render = VERBS[args.verb]
    try:
        result, code = compute(args, mail_paths.resolve(args))
    except Refusal as err:
        print(f"refused: {err}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        render(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
