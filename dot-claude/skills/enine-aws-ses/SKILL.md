---
name: enine-aws-ses
description: >
  Set up a domain to send through AWS SES, or confirm it already is: at SES, the domain's
  identity with 2048-bit Easy DKIM and MAIL FROM hermes.<domain>; at Porkbun, the zone is read
  first and each record SES needs (the three DKIM CNAMEs, the MAIL FROM MX and SPF, DMARC) gets
  one row: CONFIRM when it matches, CREATE when absent, PROMPT when it differs, ERROR when it is
  absent and nothing provides it. A domain set up for the first time is added to the sender
  config, its profile line commented out. Always dry-runs first and asks the owner before
  --apply writes.
  Use for "set up <domain> for SES", "add the SES records for <domain>", "configure <domain>
  on aws ses", or "is <domain>'s SES DNS right".
---

# AWS SES for a domain on Porkbun

One script reads and changes SES, then prints the Porkbun commands. Run it; do not change the
identity in the AWS console or the records in Porkbun's dashboard.

    python3 ~/.claude/skills/enine-aws-ses/ses_identity.py example.com --profile admin            # dry run
    python3 ~/.claude/skills/enine-aws-ses/ses_identity.py example.com --profile admin --apply    # change SES
    python3 ~/.claude/skills/enine-aws-ses/ses_identity.py example.com --dkim T1 T2 T3            # no AWS profile
    python3 ~/.claude/skills/enine-aws-ses/ses_identity.py example.com                            # confirm from the zone

The default is a dry run: it reads SES and the Porkbun zone, prints one row per record and the
`porkbun.py` commands those rows need, and changes nothing. Only `--apply` changes SES or
senders.toml. The script never writes to Porkbun; the printed commands are themselves dry runs
until `--apply` is added to them.

## Before the first run

- The SES half needs an AWS CLI profile allowed to change SES in the domain's account. A
  send-only profile cannot; the run then says "SES: skipped".
- Without a profile, pass SES's three Easy DKIM tokens with `--dkim`, from the identity's page.
  Without either, the DKIM row confirms the three the zone holds.
- The zone is read with `~/.config/porkbun/credentials`, or `--account <name>` for another account.
- Region and MAIL FROM come from the sender config when it lists the domain, else `us-east-1`
  and `hermes`. The sender config is `--senders FILE`, or `ENINE_MAIL_SENDERS`, which the
  consumer's install sets; it has no default. `--region` and `--mail-from` override them.

Never print, echo or paste a key.

## How to run it

1. IF the prompt names an AWS profile, THEN pass `--profile <it>`.
2. IF the prompt gives the DKIM tokens, THEN pass `--dkim <the three>`.
3. Run `ses_identity.py <domain>` without `--apply`, and read every row.
4. IF it prints "SES: skipped", THEN tell the owner the reason it gives.
5. IF it refuses with "must set PORKBUN_API_KEY" or "no Porkbun account", THEN report "Porkbun: skipped".
6. IF it exits 2 with any other refusal, THEN report the refusal message; do not work around it.
7. IF it prints `CONFIRMED: <domain>`, THEN report that line and stop.
8. IF an SES row is `CONFLICT`, THEN report it to the owner and do not change SES.
9. IF an SES row is `CREATE` or `EDIT`, THEN show the owner every SES row as a table, with its old value.
10. IF a Porkbun row is `ERROR`, THEN report its note, and run nothing for that row.
11. IF the DKIM row's note says SES creates the identity on `--apply`, THEN ask the owner to apply SES first.
12. Show the owner every `CREATE` and `PROMPT` row as a table, with what the zone holds for a `PROMPT` row.
13. Run each printed `create` command without `--apply`, and read its rows as PROCEDURE.md "Write" steps 4-8 say.
14. Ask the owner once, with AskUserQuestion, in the same turn as the dry runs.
15. For the `CREATE` rows, offer "Apply" and "Do not apply".
16. For each `PROMPT` row, offer "Do nothing (Recommended)" first, then "Update records"; Other is the owner's own.
17. IF the owner applies and SES has changes, THEN run `ses_identity.py` again with `--apply`.
18. IF the owner chooses "Apply", THEN run each printed `create` command with `--apply`.
19. IF the owner chooses "Update records", THEN run that row's update commands in order, each without, then with, `--apply`.
20. After each `--apply`, paste the final output and the exit code.
21. IF the owner chooses "Do not apply" or "Do nothing", THEN write nothing for those rows.
22. IF a `CREATE` was applied and the run showed "Sender config: ADD", THEN run `ses_identity.py` with `--apply`.
23. IF SES has changes the owner did not apply, THEN skip step 22 and report the entry was not added.
24. Report the file it names and the profile line it wrote.
25. Run `ses_identity.py <domain>` again, without `--apply`, and report its `CONFIRMED` line or its rows.

**Why:** `--apply` changes what the domain's mail is signed with and where its bounces go, and
invoking the skill does not approve rows the owner has not seen. The zone is read before any
command is printed, so a record already there is never created beside itself, merged, or replaced
without the owner's "Update records". An update deletes the zone's record and then creates the
provided one; for DKIM that is the old tokens' CNAMEs and the new tokens'. A zone record nothing
provided is not looked for: the script reads only the six records SES needs. The sender config
entry is written with the profile line commented out, so enine-mail refuses to send as the
domain until the owner sets a profile there.

## The sender config

2. IF the run says "Sender config: not set" or "no <path>", THEN report that nothing was added; do not guess a path.
3. IF the run says the sender config "already lists" the domain, THEN report it; the entry is not changed.
4. IF the domain is not listed, THEN the dry run shows the entry, and `--apply` appends it to the file `ENINE_MAIL_SENDERS` names.

A new entry holds `mail_from` (default `hermes.<domain>`), `region` when it is not the default,
`# sends_for = []`, and the profile line, commented out. It holds the profile most domains in the file use (a tie: the first listed; none: empty).

## Rows and exit

SES rows: `ok` matches, `CREATE` adds the identity or MAIL FROM, `EDIT` changes the DKIM key
length or MAIL FROM (the note shows the old value), `CONFLICT` is DKIM that is not Easy DKIM, which
this skill does not change.

Porkbun rows, one per record (DKIM is one row for its three CNAMEs):

| in the zone | provided | row |
|---|---|---|
| no | yes | `CREATE`: its `create` command prints |
| no | no | `ERROR` |
| yes | matches | `CONFIRM` |
| yes | differs | `PROMPT`: both shown; its update commands print |

With no DKIM tokens provided, the DKIM row is `CONFIRM` when the zone holds exactly three SES DKIM
CNAMEs in AWS form (`<t>._domainkey.<domain>` to `<t>.dkim.amazonses.com`, `<t>` 32 lowercase
letters or digits), else `ERROR`.

Exit 0 every row `CONFIRM` and nothing pending (it printed `CONFIRMED`); 1 a `CREATE` or `PROMPT`
row, or a change pending at SES or in the sender config, or SES holds a `CONFLICT`; 2 an `ERROR`
row, or could not decide.

Moving to 2048-bit DKIM takes effect at SES's next key rotation, and SES allows the change once a day.
