---
name: enine-mail
description: >
  A mail store and outgoing mail: search stored mail by header or a Gmail-style query,
  resolve a message-id to its stored .eml, file a tracked .eml into the store, pin the .eml of
  given message-ids so nothing can delete them, draft a message, and send a draft through AWS
  SES, from a prompt such as "/enine-mail search --from alice@example.com",
  "/enine-mail draft" or "/enine-mail send 3". A bare /enine-mail shows the help and asks for
  the verb. Writes dry-run first, and nothing is sent until the owner approves the draft
  number that was shown. Every path comes from the consumer (a flag or an ENINE_MAIL_*
  variable); it refuses when one is unset. Use for "find the mail from <someone>", "where is
  message <id>", "file this .eml", "pin these messages", "draft an email", or "send draft <N>".
---

# Mail

One script does the work: `python3 ~/.claude/skills/enine-mail/mail.py`. Run it; do not read,
move or delete files in the mail store, the drafts folder or the sent log by hand. With no
arguments it prints its help, with examples.

| Verb | Does | Writes |
|---|---|---|
| `search` | finds stored mail by `--from --to --cc --bcc --subject --after --before`, or `--query` in Gmail's syntax, newest first; each term matches whole words, and `word*` a prefix | nothing |
| `resolve <message-id>` | prints the stored .eml's path | nothing |
| `ingest <file.eml>...` | files a tracked .eml into the store on the harvest's layout; moves it, or keeps it with `--copy` | with `--apply` |
| `pin <message-id>...` | locks each id's stored .eml immutable; `--check` reports any unlocked or missing; `--unpin <id>...` unlocks | with `--apply` |
| `draft` | with message args, builds draft N, the exact bytes SES will get, and shows it; with none, lists drafts and sent lines; with N, shows draft N | a draft, never a send |
| `send N` | shows draft N as it will go; `send N --apply` sends it through SES | with `--apply` |

## Paths come from the consumer

The skill holds no path and no sender. A consumer (the repo or the person that uses it) names
each one, and the skill uses only what it is given.

| Path | Flag | Variable | Used by |
|---|---|---|---|
| the mail store | `--store DIR` | `ENINE_MAIL_STORE` | `search`, `resolve`, `ingest`, `pin`; a reply's threading |
| the owner-address file | `--identity FILE` | `ENINE_MAIL_IDENTITY` | `ingest` |
| the sender config | `--senders FILE` | `ENINE_MAIL_SENDERS` | building a draft |
| the drafts folder | `--drafts DIR` | `ENINE_MAIL_DRAFTS` | `draft`, `send` |
| the sent log | `--sent FILE` | `ENINE_MAIL_SENT` | `draft` with no args, `send N --apply`; a reply's threading |

1. Expect the variables to be set by the consumer's install, in the environment Claude Code runs in.
2. IF the prompt names a path, THEN pass it as the flag; a flag wins over its variable.
3. IF a run refuses with "needs ... pass --X or set ENINE_MAIL_X", THEN report that message to the owner.
4. Do not guess a path, search the disk for one, or set a variable yourself.

**Why:** the skill is public and works for anyone's store and senders, so it carries none of
them. A path guessed wrong reads or writes someone else's mail, so a missing one refuses.

## Choose the verb

1. IF the prompt gives no arguments, THEN run `mail.py` and show its output as the help.
2. IF the prompt names no verb, THEN ask with AskUserQuestion: `search`, `draft`, `send`, `resolve`.
3. Say in that question that Other takes `ingest` or `pin`.
4. Do not mark any option "(Recommended)".

**Why:** AskUserQuestion holds four options, so the two verbs the owner uses least go under Other.

## Read: search, resolve

1. For `search`, pass the prompt's terms as the flags they name; pass free text as `--query`.
2. Pass each term as the owner gave it; a term matches whole words, so `oci` does not find "Social".
3. IF the owner asks for words that start with a term, THEN add `*` to it: `art*` finds "Arts".
4. Combine terms with `or` and `and` in any case; words side by side are ANDed.
5. IF the owner wants an exact phrase, or the word "or" or "and" itself, THEN put it in double quotes.
6. For `resolve`, run `mail.py resolve <message-id>`.
7. Show the owner what it prints. Ask nothing more: neither verb writes.
8. IF the exit code is 1, THEN say that nothing matched, or that the id is not in the store.
9. IF the exit code is 2, THEN report the refusal message; do not work around it.

**Why:** a substring search for a short term finds it inside longer words and buries the mail
the owner wants. An address or a domain is a run of words joined by `@` and `.`, so it still
matches whole. A flag is its field's query (`--from X` is `from:(X)`), so the flags and
`--query` share one set of rules.

## Write: ingest, pin

1. Always run the command first without `--apply`, and read every row.
2. IF every row is already as asked, THEN report that and stop.
3. Show the owner every row the run would change, and every `skip`, `DANGLING` or `UNRESOLVED` row.
4. Ask the owner with AskUserQuestion, in the same turn: "Apply" or "Do not apply".
5. IF the owner chooses "Apply", THEN run the same command with `--apply`.
6. IF the owner chooses anything else, THEN stop and write nothing.
7. After `--apply`, paste the final output and the exit code.
8. For `pin --check`, report every `UNLOCKED` and `DANGLING` row; `--check` writes nothing.

**Why:** `ingest` moves the source file unless `--copy` is given, and a pinned file cannot be
deleted until it is unpinned. Invoking the skill does not approve rows the owner has not seen.

## Draft

1. Collect what the prompt does not give: `--from` (an address at a domain in the sender config), `--to`, `--subject`, the body.
2. Ask for each missing part in a plain message, one at a time. End the turn.
3. Do not offer an address, a subject or body text the owner has not given in this session.
4. IF the owner gives the body as text, THEN write it to a UTF-8 file in the scratchpad.
5. Pass that file as `--body <file>`.
6. IF the message replies to one, THEN pass `--in-reply-to <message-id>`.
7. Run `mail.py draft` with the message args, and show the owner the whole draft it prints.

**Why:** the draft is the exact bytes SES will get, so the owner reviews what will go, not a
summary. The SES profile, region, Bcc, Reply-To and sender name come only from the sender
config; no flag overrides one.

## Send

1. IF the prompt gives message args and no draft number, THEN run `mail.py send` with them.
2. That drafts the message and shows the new draft N as a dry run; it sends nothing.
3. IF the prompt gives a draft number, THEN run `mail.py send N`, which shows it and sends nothing.
4. Show the owner the whole draft: the SES destinations, Bcc included, the headers and the text.
5. Ask the owner with AskUserQuestion, in the same turn: "Send draft N" or "Do not send".
6. IF the owner chooses "Send draft N", THEN run `mail.py send N --apply`.
7. Never pass `--apply` with a draft number the owner has not seen and approved in this session.
8. IF the owner chooses anything else, THEN stop; the draft stays for later.
9. After `--apply`, paste the final output and the exit code.
10. IF the refusal says the draft is marked `sending`, THEN report it to the owner and stop.
11. Do not change a draft's state by hand; an earlier send may have gone.

**Why:** a sent mail cannot be taken back. `--apply` takes only a draft number, so what goes
out is the draft the owner was shown. The draft is marked `sending` before SES is called, so
a retry after a crash cannot send it twice; the owner checks for its Bcc copy first.

## Exit and files

Exit 0 done, nothing to do, or printed; 1 a dry run with changes pending, nothing matched, an
id not in the store, or an unlocked or dangling pin; 2 refused.

- Every path is the consumer's, as the table under "Paths come from the consumer" says.
- The sender config holds the SES profiles. Never print, echo or paste it.
- `pin` needs the macOS user-immutable flag and refuses elsewhere.
