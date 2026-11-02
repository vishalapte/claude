---
name: enine-forwardemail
description: >
  Forward Email domains and aliases: create, update or delete a domain or an alias, list the
  account's domains, or show one domain's settings and aliases, from a prompt such as
  "/enine-forwardemail create alias info@example.com alice@example.net",
  "/enine-forwardemail update domain example.com --set ignore_mx_check=true" or
  "/enine-forwardemail domain example.com". A bare /enine-forwardemail shows the help and asks
  for the verb. Writes always dry-run first and ask the owner before --apply. DNS records are
  not set here; /enine-porkbun-forwardemail sets them at Porkbun. Use for "add an alias",
  "forward <address> to <address>", "change a Forward Email setting", "remove an alias", or
  "which domains are on Forward Email".
---

# Forward Email

One script does the work: `python3 ~/.claude/skills/enine-forwardemail/forwardemail.py`. Run it;
do not change domains or aliases in Forward Email's web page. With no arguments it prints its
help, with examples and every field `--set` takes.

| Verb | Parts, in the order they are asked | Command |
|---|---|---|
| `create` | domain or alias, the name, recipients (alias), values | `forwardemail.py create <domain\|alias> <name> [recipient ...] [--set K=V ...]` |
| `update` | domain or alias, the name, what to change | `forwardemail.py update <domain\|alias> <name> [recipient ...] [--set K=V ...]` |
| `delete` | domain or alias, the name | `forwardemail.py delete <domain\|alias> <name>` |
| `list` | none | `forwardemail.py list` |
| `domain` | the domain | `forwardemail.py domain <domain>` |

A domain's name is `example.com`; an alias's is `info@example.com`, and `*@example.com` is
the catch-all. On `update alias`, the recipients given replace the alias's list.

## Choose the verb and collect the parts

1. IF the prompt gives no arguments, THEN run `forwardemail.py` and show its output as the help.
2. IF the prompt names no verb, THEN ask with AskUserQuestion: `create`, `update`, `delete`, `list`.
3. Say in that question that Other takes `domain`, to show one domain's settings and aliases.
4. Do not mark any option "(Recommended)".
5. Ask only for the parts the prompt does not give, one at a time, in the table's order.
6. Ask domain or alias with AskUserQuestion; ask each name, recipient or value in a plain message. End the turn.
7. Do not offer a name, an address or a value the owner has not given in this session.
8. Pass each field as `--set K=V`, with K a field the help lists for that kind and verb.

**Why:** the owner states each part; the skill does not propose one. An address has no honest
set of options, so it is asked as free text.

## Write: create, update, delete

1. Follow `~/.claude/skills/enine-porkbun/PROCEDURE.md` "Write" steps 2, 3, 5, 6, 7 and 9, with this script's rows.
2. IF a `create` row is `CONFLICT` "update changes it", THEN show the owner each field's old and new value.
3. For that row, offer "Update it" and "Do not apply"; IF "Update it", THEN run `update` with the same values and `--apply`.
4. For any other row, offer "Apply" and "Do not apply"; IF "Apply", THEN run the same command with `--apply`.
5. IF a `DELETE` row's note says the domain holds aliases or the alias has a mailbox, THEN say so in the question.
6. Then follow PROCEDURE.md "Write" steps 17, 18 and 19.
7. IF a row is `FAILED`, THEN show the owner Forward Email's message and stop.
8. IF a note says `not read back`, THEN tell the owner that field is sent but a read cannot confirm it.
9. IF a `create domain` ran, THEN tell the owner `/enine-porkbun-forwardemail <domain>` sets its DNS records.

**Why:** a write changes where mail goes, and invoking the skill does not approve rows the owner
has not seen. `create` creates, `update` edits and `delete` deletes, so changing one that
exists is the owner's choice, made in the same prompt. Forward Email does not say what a delete
takes with it, so the row says what the domain or alias holds.

## Read: list, domain

1. For `list`, run `forwardemail.py list`; for `domain`, run `forwardemail.py domain <domain>`.
2. Show the owner what it prints as a table. Ask nothing more: neither verb writes.
3. IF the exit code is 2, THEN report the refusal message; do not work around it.

## Credentials and exit

The API key is in `~/.config/forwardemail/credentials` (`FORWARDEMAIL_API_KEY`), mode 600, read
only after the arguments check out. Never print, echo or paste it. The client,
`forwardemail_client.py`, is beside the script; `/enine-porkbun-forwardemail` imports it too.

Exit 0 done, already as asked, or printed; 1 a change pending (dry run), a `CONFLICT`, a
`FAILED` write, or a field not as asked after `--apply`; 2 refused.
