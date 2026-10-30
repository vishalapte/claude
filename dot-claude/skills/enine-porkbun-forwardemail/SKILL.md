---
name: enine-porkbun-forwardemail
description: >
  Add a domain to Forward Email (forwardemail.net) and hold its DNS at Porkbun to exactly the
  records Forward Email asks for, in one deterministic run: creates the domain on Forward
  Email if absent, reads its zone_file, plans the Porkbun changes (MX replaced, SPF merged,
  DKIM, return-path, DMARC, verification TXT, autodiscovery CNAME and SRV), applies them,
  re-reads the zone to confirm, runs Porkbun's preflight and Forward Email's verify-records.
  Always dry-runs first, shows the planned changes, and asks the owner before --apply
  writes. Use for "add <domain> to forward email", "set up email forwarding for <domain> on
  porkbun", "fix the forwardemail DNS for <domain>", or "is <domain>'s email DNS right".
---

# Forward Email on a Porkbun domain

One script does the work. Run it; do not create records by hand in either dashboard.

    python3 ~/.claude/skills/enine-porkbun-forwardemail/fe_porkbun.py example.com           # dry run
    python3 ~/.claude/skills/enine-porkbun-forwardemail/fe_porkbun.py example.com --apply   # apply + verify

The default is a dry run: it reads both services, prints the plan and writes nothing. Only
`--apply` creates the domain on Forward Email or changes a DNS record.

New domain only: `--plan free|enhanced_protection|team` (default: the account's plan) and
`--catchall false|a@x.com,b@y.com`. Forward Email's own default for the catch-all is the
account's email address, so every address at the domain forwards there unless this is passed.

## Before the first run

The owner supplies the keys, one file per service, as `KEY=VALUE` lines with mode 600. The
script reads nothing else, and refuses a missing key or a file readable by group or others.

    # ~/.config/forwardemail/credentials   (forwardemail.net > My Account > Security > Developer Access)
    FORWARDEMAIL_API_KEY=...

    # ~/.config/porkbun/credentials        (porkbun.com > Account > API Access)
    PORKBUN_API_KEY=pk1_...
    PORKBUN_SECRET_API_KEY=sk1_...

- IF the domain is in another Porkbun account, THEN pass `--account <name>`.
  That reads `~/.config/porkbun/credentials.d/<name>`, the same two lines, mode 600.
- The Porkbun key needs the `read` and `dns` permissions.
- The script imports its Porkbun client from `../enine-porkbun/porkbun_client.py`.
  `./install` ships both directories, so they are installed together.
- The domain must have API access turned on at Porkbun (per domain, or for all domains).
- Forward Email's API refuses an account on the free plan (HTTP 402).
- The domain must use Porkbun's nameservers, or the records change nothing that resolves.

Never print, echo or paste the keys. Never write them anywhere except those two files.

## How to run it

1. Run the dry run first, without `--apply`, and read every row.
2. IF the domain is not on Forward Email, THEN tell the owner `--apply` adds it, with its plan and catch-all.
3. IF every row is `ok` and verify-records says `verified`, THEN report that the domain conforms and stop.
4. IF a row is `CONFLICT`, THEN report it to the owner; the script never removes a record of another type.
5. IF a row is `CREATE`, `EDIT` or `DELETE`, THEN show the owner every such row as a table.
6. IF a row is `DELETE` on an MX record, THEN say which mail service stops receiving for the domain.
7. After you show the rows, ask the owner with AskUserQuestion: "Apply" or "Do not apply".
8. Ask in the same turn as the dry run. Do not wait for the owner to type a request.
9. IF the owner chooses "Apply", THEN run with `--apply`, then paste the final output and the exit code.
10. IF the owner chooses anything else, THEN stop and write nothing.
11. IF the exit code is 2, THEN report the refusal message; do not work around it.
12. IF verify-records says `NOT verified` after a clean apply, THEN wait a few minutes and run the dry run again.

**Why:** `--apply` changes public DNS and can add the domain to Forward Email, and invoking
the skill does not approve rows the owner has not seen. The zone file is Forward Email's
statement of what the domain needs, so the plan is a diff against it rather than a list kept
here. A record added by hand drifts from it silently.

Rows: `ok` matches, `CREATE` adds, `EDIT` changes a record in place (the note shows the old
value), `DELETE` removes, `CONFLICT` cannot be applied, `FAILED` was refused by Porkbun or skipped after a refusal at its name. Exit 0 conforms or was applied and
verified, 1 something drifts, was refused or failed to verify (a dry run with changes pending exits 1),
2 could not decide.

## What it changes, and what it leaves alone

| Name and type | Rule | Source of the rule |
|---|---|---|
| Apex MX | exactly Forward Email's MX; every other apex MX is deleted | zone file: "Existing MX records for this name must be removed" |
| Apex SPF TXT | one SPF; Forward Email's `include:` is added to an existing one, nothing else in it changes | zone file: "a name has one SPF record" |
| Other apex TXT | missing values added; existing values kept | apex TXT holds unrelated verifications |
| Any other name Forward Email lists (DKIM, `_dmarc`, return path, `autoconfig`, `autodiscover`, SRV) | exactly Forward Email's value; a different value is edited in place | zone file: "if you already have one, replace it" (DMARC) |
| A CNAME where another type exists, or the reverse | `CONFLICT`, not applied | RFC 1034 |
| Porkbun's parking placeholders: any record pointing at `pixie.porkbun.com` (a new domain's apex `ALIAS` and wildcard `CNAME *`) | deleted | owner's instruction, 2026-10-16 |
| NS records | never touched | they delegate the domain to Porkbun |
| Every other name Forward Email does not list | untouched | out of scope |

An existing SPF keeps its own `~all` or `-all`; that choice is the owner's.

## Facts the script rests on

- Forward Email's `POST /v1/domains` and `GET /v1/domains/:domain` return `zone_file`, every
  record the domain needs, built by `helpers/get-zone-file.js` in the forwardemail.net repo.
  Names are absolute, TTL 3600, TXT quoted in 255-character strings.
- A free-plan domain gets MX plus a `forward-email=` TXT. A paid-plan domain also gets the
  verification TXT, SPF, DKIM, return-path CNAME, DMARC, and autodiscovery CNAME and SRV.
- `verify-records` checks only the MX and TXT records. `verify-smtp` is not called: it needs
  SMTP access and emails Forward Email's admins for approval. The outbound records are
  confirmed by the re-read of the Porkbun zone instead.
- Porkbun takes `name` as the subdomain ("" for the apex) and returns it as the full host.
  SRV `content` is `weight port target`, with the priority in `prio`.
- Porkbun's `GET /dns/preflight/:domain` reports duplicate SPF, CNAME conflicts and foreign
  nameservers. Its `warnings` on a write name a domain not delegated to Porkbun.
- Porkbun takes a restore point before the first write to a zone each hour
  (`GET /dns/history/:domain`), so an apply can be undone from the dashboard or the API.
