# Porkbun DNS: the shared procedure

`/enine-porkbun` and the verb skills `/enine-porkbun-create`, `/enine-porkbun-update`,
`/enine-porkbun-delete`, `/enine-porkbun-list` and `/enine-porkbun-domain` follow this file.
The caller fixes the verb; everything else is here, once.

One script does the work: `python3 ~/.claude/skills/enine-porkbun/porkbun.py`. Run it; do not edit
records by hand in Porkbun's dashboard. With no arguments it prints its help, with examples.

## The parts of each verb

| Verb | Parts, in the order they are asked | Command |
|---|---|---|
| `create` | name, type, target | `porkbun.py create <TYPE> <name> <target>` |
| `update` | name, type, target | `porkbun.py update <TYPE> <name> <target> [--match <prefix>]` |
| `delete` | name, type, target or "all" | `porkbun.py delete <TYPE> <name> [target]` |
| `list` | none | `porkbun.py list` |
| `domain` | name, type or "all" | `porkbun.py domain <name> [--type <TYPE>]` |
| `capture` | name (the domain) | `porkbun.py capture <domain>` |

The name is the record's full name: the domain itself for the apex, `*.<domain>` for a
wildcard. The script finds the domain it belongs to in the account. `--prio` sets an MX or SRV
priority and `--ttl` the time to live; a create defaults to 0 and 600, an update keeps the
record's own. Pass `--match` only when the prompt gives it, or when a dry run's `CONFLICT`
names several records and the owner says which one: an apex TXT holds SPF beside site
verifications, so `--match v=spf1` picks the SPF record.

| Verb | Does |
|---|---|
| `create` | adds the record; `ok` when one with the same value exists, whatever its TTL. Another record of the same type at the name stays (round-robin A is legitimate). |
| `update` | edits the one record of that type at the name; with `--match <prefix>`, the one whose value starts with it, in any case. None is `CONFLICT` (use create); several is `CONFLICT` (delete the extras, or pass `--match`). |
| `delete` | deletes every record of that type at the name, or only the one whose target the prompt gives; `ok` when none exists. |
| `list` | prints every domain in the account, one row per name with its TLDs. Writes nothing. |
| `domain` | prints the records at the name and every name under it; the domain itself prints the whole zone. Writes nothing. |
| `capture` | asks the domain's live nameservers what they publish (Porkbun's scan, at most 20 an hour) and creates each record Porkbun's zone lacks; deletes Porkbun's parking placeholders. A second SPF record is `CONFLICT`. |

## Collect the missing parts

1. Ask only for the parts the prompt does not give, one question at a time, in the table's order.
2. Do not read the credentials, call Porkbun or run the script on a record until every part is known.
3. Do not mark any option "(Recommended)".
4. Do not offer a name or a target the owner has not given in this session.
5. Name: ask in a plain message for the record's full name, the domain itself for the apex. End the turn.
   For `capture`, ask for the domain.
6. Type for create, update or delete: ask with AskUserQuestion, options `A`, `AAAA`, `CNAME`, `TXT`.
7. Type for domain: ask with AskUserQuestion, options `All types`, `A`, `CNAME`, `TXT`.
8. In either type question, Other takes any type the help lists.
9. Target for create or update: ask in a plain message what value the `<TYPE>` record at `<name>` should hold. End the turn.
10. Target for delete: ask in a plain message for the value to delete, or "all" for every `<TYPE>` record at `<name>`. End the turn.
11. IF the owner answers "all" or "All types", THEN leave the target, or `--type`, out of the command.
12. With every part known, continue at "Write" or "Read".

**Why:** the owner states each part; the skill does not propose one. A name or a value has no
honest set of options, so it is asked as free text. The credentials matter only once there is
a record to plan, so they come last.

## Write: create, update, delete, capture

1. Pass the target as one quoted argument; IF it starts with `-`, THEN put `--` before it.
2. Always run the dry run first, without `--apply`, and read every row.
3. IF every row is `ok`, THEN report that the record is already as asked and stop.
4. IF a row is `CONFLICT`, THEN report it to the owner and stop; do not work around it.
5. IF a row is `CREATE`, `EDIT` or `DELETE`, THEN show the owner every such row as a table.
6. IF a row is `EDIT` or `DELETE`, THEN include the old value from its note.
7. After you show the rows, ask the owner with AskUserQuestion: "Apply" or "Do not apply".
8. Ask in the same turn as the dry run. Do not wait for the owner to type a request.
9. IF the owner chooses "Apply", THEN run the same command with `--apply`.
10. IF the owner chooses anything else, THEN stop and write nothing.
11. After `--apply`, paste the final output and the exit code.
12. IF the exit code is 2, THEN report the refusal message; do not work around it.

**Why:** `--apply` changes public DNS, and invoking the skill does not approve rows the owner
has not seen. The dry run shows what the prompt resolves to (the zone, the record it matches,
the old value an edit or delete removes) before anything is written.

For `capture`, tell the owner before you ask in step 7: run it while the domain's current DNS
host still answers, before the nameservers move to Porkbun; and a scan cannot list a zone, so
compare the rows with the list at the current host and add any name it missed with `create`.
IF a row is `FAILED` after `--apply`, THEN show the owner its reason and stop; Porkbun names
the fix in it. The other names were written.

Rows: `ok` matches, `CREATE` adds, `EDIT` changes the record in place (the note shows the old
value), `DELETE` removes, `CONFLICT` cannot be applied, `FAILED` was refused by Porkbun or skipped after a refusal at its name. Exit 0 conforms or was applied and
verified, 1 something drifts, conflicts, was refused or failed to verify (a dry run with changes pending
exits 1), 2 could not decide. A preflight blocker exits 1 even when the record is right; it
names a problem elsewhere in the zone, so report it.

## Read: list, domain

1. For `list`, run `porkbun.py list`.
2. For `domain`, run `porkbun.py domain <name>`, with `--type <TYPE>` when the owner named a type.
3. Show the owner what it prints as a table. Ask nothing more: neither verb writes.
4. IF the exit code is 2, THEN report the refusal message; do not work around it.

## Account

1. IF the prompt names a Porkbun account, THEN add `--account <name>` to every run of the script.
2. IF a run refuses because the account does not hold the name, and it lists other accounts, THEN ask the owner.
3. Ask with AskUserQuestion which account holds the name; give the accounts the refusal lists as options.
4. Rerun the same command with `--account <the chosen name>`.

**Why:** the owner picks the account; the script never tries one on its own. A refusal
writes nothing, so asking after it costs nothing.

## Credentials

The script reads them only when it plans or prints, after it has checked the arguments.

- The Porkbun keys are in `~/.config/porkbun/credentials` (`PORKBUN_API_KEY`,
  `PORKBUN_SECRET_API_KEY`), mode 600. That file is the default account.
- Each other account has its own file of the same two lines, mode 600:
  `~/.config/porkbun/credentials.d/<name>`. `--account <name>` reads it instead.
- The key needs the `read` and `dns` permissions, and the domain needs API access turned on.
- The Porkbun client is `porkbun_client.py`, beside the script. The email and eninesites
  skills import it from here too, and write through its one plan: the same create, update
  and delete, with deletes applied first and no write at a name that holds a `CONFLICT`.
