---
name: enine-porkbun-fastmail
description: >
  Set a Porkbun domain's DNS for Fastmail (fastmail.com), in one deterministic run: the three
  DKIM CNAMEs (fm1, fm2, fm3._domainkey) and Fastmail's include in the one SPF record, so
  Fastmail can send as the domain; with --mx, also the apex MX, so mail arrives at Fastmail.
  Re-reads the zone and runs Porkbun's preflight, then prints the steps at Fastmail its API
  cannot do. Always dry-runs first, shows the planned changes, and asks the owner before
  --apply writes. Use for "set up <domain> for fastmail", "send from <domain> with fastmail",
  "fastmail DKIM for <domain>", "move <domain>'s mail to fastmail", or "is <domain> set up
  for fastmail".
---

# Fastmail on a Porkbun domain

One script does the work. Run it; do not edit the records by hand in Porkbun's dashboard.

    python3 ~/.claude/skills/enine-porkbun-fastmail/fm_porkbun.py example.com              # dry run
    python3 ~/.claude/skills/enine-porkbun-fastmail/fm_porkbun.py example.com --apply      # apply + verify
    python3 ~/.claude/skills/enine-porkbun-fastmail/fm_porkbun.py example.com --mx --apply # also move MX

The default is a dry run: it reads the zone, prints the plan and writes nothing. Only
`--apply` changes a record. Without `--mx` the MX are left alone, so mail keeps arriving where
it does now, for example at Forward Email, and Fastmail only sends.

## Before the first run

- The Porkbun keys are in `~/.config/porkbun/credentials` (`PORKBUN_API_KEY`,
  `PORKBUN_SECRET_API_KEY`), mode 600. No Fastmail key is needed.
- IF the domain is in another Porkbun account, THEN pass `--account <name>`.
  That reads `~/.config/porkbun/credentials.d/<name>`, the same two lines.
- The key needs the `read` and `dns` permissions, and the domain needs API access turned on.
- The script imports its Porkbun client from `../enine-porkbun/porkbun_client.py`.
  `./install` ships both directories, so they are installed together.

## How to run it

1. Pass `--mx` only when the prompt asks for mail to arrive at Fastmail.
2. Run the dry run first, without `--apply`, and read every row.
3. IF every row is `ok`, THEN report that the DNS is set for Fastmail, and show the steps it prints for Fastmail.
4. IF a row is `CONFLICT`, THEN report it to the owner; the script never removes a record of another type.
5. IF a row is `CREATE`, `EDIT` or `DELETE`, THEN show the owner every such row as a table.
6. IF a row is `EDIT` on an MX record, THEN say which mail service stops receiving for the domain.
7. After you show the rows, ask the owner with AskUserQuestion: "Apply" or "Do not apply".
8. Ask in the same turn as the dry run. Do not wait for the owner to type a request.
9. IF the owner chooses "Apply", THEN run with `--apply`, then paste the final output and the exit code.
10. IF the owner chooses anything else, THEN stop and write nothing.
11. IF the exit code is 2, THEN report the refusal message; do not work around it.
12. After every run, show the owner the steps at Fastmail that the script prints last.

**Why:** `--apply` changes public DNS, and invoking the skill does not approve rows the owner
has not seen. Moving the MX moves where the domain's mail arrives, so it is asked for by name.
Fastmail's API (JMAP) has no call that adds a domain, so those steps stay with the owner.

Rows: `ok` matches, `CREATE` adds, `EDIT` changes a record in place (the note shows the old
value), `DELETE` removes, `CONFLICT` cannot be applied, `FAILED` was refused by Porkbun or skipped after a refusal at its name. Exit 0 conforms or was applied and
verified, 1 something drifts, was refused or failed to verify (a dry run with changes pending exits 1),
2 could not decide.

## What it changes, and what it leaves alone

| Name and type | Rule | Source |
|---|---|---|
| `fm1`, `fm2`, `fm3._domainkey` CNAME | exactly `fmN.<domain>.dkim.fmhosted.com`; a different value is edited in place | Fastmail's "Manual DNS configuration" |
| Apex SPF TXT | one SPF; `include:spf.messagingengine.com` is added to an existing one, nothing else in it changes; with none, `v=spf1 include:spf.messagingengine.com ?all` | the same page; a name has one SPF record (RFC 7208) |
| Apex MX, with `--mx` | exactly `in1-smtp.messagingengine.com` (10) and `in2-smtp.messagingengine.com` (20); old MX are edited into them in place, extras deleted | the same page |
| Apex MX, without `--mx` | untouched | |
| A CNAME where another type exists | `CONFLICT`, not applied | RFC 1034 |
| Porkbun's parking placeholders (any record pointing at `pixie.porkbun.com`) | deleted | as the other Porkbun skills do |
| DMARC, other apex TXT, NS records, every other name | untouched | |

Every record it writes has Fastmail's 1-hour TTL. It does not write Fastmail's optional
records: the `www` and wildcard website CNAMEs, `mail.<domain>`, subdomain MX, or the
autodiscovery SRV records.
