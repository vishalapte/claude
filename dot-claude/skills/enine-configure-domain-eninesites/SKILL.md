---
name: enine-configure-domain-eninesites
description: >
  Point a Porkbun domain's website at eninesites: set ALIAS records for example.com and
  www.example.com to eninesites.com (or to another host with --to), replacing whatever web
  record each name holds and deleting Porkbun's parking placeholders, in one deterministic
  run that re-reads the zone and runs Porkbun's preflight. Always dry-runs first, shows the
  planned changes, and asks the owner before --apply writes. Use for "point <domain> at
  eninesites", "set up <domain> for eninesites", "ALIAS <domain> and www to <host>", or "is
  <domain> pointed at eninesites".
---

# Point a Porkbun domain at eninesites

One script does the work. Run it; do not edit the records by hand in Porkbun's dashboard.

    python3 ~/.claude/skills/enine-configure-domain-eninesites/configure_eninesites.py example.com            # dry run
    python3 ~/.claude/skills/enine-configure-domain-eninesites/configure_eninesites.py example.com --apply    # apply + verify
    python3 ~/.claude/skills/enine-configure-domain-eninesites/configure_eninesites.py example.com --to host.example.net --apply

The default is a dry run: it reads the zone, prints the plan and writes nothing. Only
`--apply` changes a record. `--to` names the host to point at; it defaults to
`eninesites.com` and must be a host name, because an ALIAS cannot point at an address.

## Before the first run

- The Porkbun keys are in `~/.config/porkbun/credentials` (`PORKBUN_API_KEY`,
  `PORKBUN_SECRET_API_KEY`), mode 600. No Forward Email key is needed.
- The key needs the `read` and `dns` permissions, and the domain needs API access turned on.
- The script imports its Porkbun client from `../enine-configure-domain-email-via-forwardemail/fe_porkbun.py`.
  `./install` ships both directories, so they are installed together.

## How to run it

1. Run the dry run first, without `--apply`, and read every row.
2. IF every row is `ok`, THEN report that the domain points at the target and stop.
3. IF a row is `CONFLICT`, THEN report it to the owner; the script never removes a record of another type.
4. IF a row is `CREATE`, `EDIT` or `DELETE`, THEN show the owner every such row as a table.
5. IF a row is `EDIT` or `DELETE` on a record that is not a parking placeholder, THEN name the site it pointed at.
6. After you show the rows, ask the owner with AskUserQuestion: "Apply" or "Do not apply".
7. Ask in the same turn as the dry run. Do not wait for the owner to type a request.
8. IF the owner chooses "Apply", THEN run with `--apply`, then paste the final output and the exit code.
9. IF the owner chooses anything else, THEN stop and write nothing.
10. IF the exit code is 2, THEN report the refusal message; do not work around it.

**Why:** `--apply` changes public DNS, and invoking the skill does not approve rows the
owner has not seen. Each name holds one web record, so "set" means replace. A second A or
CNAME left beside the ALIAS splits visitors between two sites.

Rows: `ok` matches, `CREATE` adds, `EDIT` changes a record in place (the note shows the old
type and value), `DELETE` removes, `CONFLICT` cannot be applied. Exit 0 conforms or was
applied and verified, 1 something drifts or failed to verify (a dry run with changes pending
exits 1), 2 could not decide.

## What it changes, and what it leaves alone

| Name and type | Rule |
|---|---|
| Apex and `www`: `ALIAS`, `CNAME`, `A`, `AAAA` | exactly one `ALIAS -> target`; one existing record is edited into it, the rest are deleted |
| Porkbun's parking placeholders (any record pointing at `pixie.porkbun.com`) | deleted |
| `www` holding another type (TXT, MX) | `CONFLICT`, not applied: Porkbun serves a non-apex ALIAS as a CNAME (RFC 1034) |
| Apex MX and TXT, NS records, every other name | untouched |
