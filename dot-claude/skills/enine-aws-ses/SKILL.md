---
name: enine-aws-ses
description: >
  Set up a domain to send through AWS SES, in two halves that each run only with their own
  credentials: at SES, the domain's identity with 2048-bit Easy DKIM and MAIL FROM
  hermes.<domain>; at Porkbun, the six records SES needs (three DKIM CNAMEs, the MAIL FROM MX
  and SPF, DMARC), set through porkbun.py. Optionally adds the domain to the sender config.
  Always dry-runs first, shows the planned changes, and asks the owner before --apply writes.
  Use for "set up <domain> for SES", "add the SES records for <domain>", "configure <domain>
  on aws ses", or "is <domain>'s SES DNS right".
---

# AWS SES for a domain on Porkbun

One script reads and changes SES, then prints the Porkbun commands. Run it; do not change the
identity in the AWS console or the records in Porkbun's dashboard.

    python3 ~/.claude/skills/enine-aws-ses/ses_identity.py example.com --profile admin            # dry run
    python3 ~/.claude/skills/enine-aws-ses/ses_identity.py example.com --profile admin --apply    # change SES
    python3 ~/.claude/skills/enine-aws-ses/ses_identity.py example.com --dkim T1 T2 T3            # no AWS profile

The default is a dry run: it reads SES, prints the plan and the six `porkbun.py create` commands,
and changes nothing. Only `--apply` changes SES or senders.toml. The commands are themselves dry
runs until `--apply` is added to them.

## Before the first run

- The SES half needs an AWS CLI profile allowed to change SES in the domain's account. A
  send-only profile cannot; the run then says "SES: skipped".
- Without a profile, pass SES's three Easy DKIM tokens with `--dkim`, from the identity's page.
- The Porkbun half needs `~/.config/porkbun/credentials`, or `--account <name>` for another account.
- Region and MAIL FROM come from the sender config when it lists the domain, else `us-east-1`
  and `hermes`. The sender config is `--senders FILE`, or `ENINE_MAIL_SENDERS`, which the
  consumer's install sets; it has no default. `--region` and `--mail-from` override them.

Never print, echo or paste a key.

## How to run it

1. IF the prompt names an AWS profile, THEN pass `--profile <it>`.
2. IF the prompt gives the DKIM tokens, THEN pass `--dkim <the three>`.
3. Run `ses_identity.py <domain>` without `--apply`, and read every row.
4. IF it prints "SES: skipped", THEN tell the owner the reason it gives.
5. IF an SES row is `CONFLICT`, THEN report it to the owner and do not change SES.
6. IF an SES row is `CREATE` or `EDIT`, THEN show the owner every SES row as a table, with its old value.
7. Run each printed `porkbun.py create` command as printed, without `--apply`, and read every row.
8. IF a command refuses with "must set PORKBUN_API_KEY" or "no Porkbun account", THEN report "Porkbun: skipped".
9. IF a command exits 2 with any other refusal, THEN report the refusal message; do not work around it.
10. IF every row is `ok`, THEN report that the domain is set up for SES and stop.
11. Read the Porkbun rows as `~/.claude/skills/enine-porkbun/PROCEDURE.md` "Write" steps 4-8 say.
12. IF a row shows a record already at the MAIL FROM name's MX, THEN say which mail host receives there now.
13. IF the DMARC row is the `CONFLICT` "update replaces it", THEN say the old DMARC policy it would replace.
14. Ask the owner once, with AskUserQuestion, in the same turn as the dry runs.
15. Offer the choices PROCEDURE.md "Write" steps 10-11 give for each command, and "Do not apply".
16. IF the owner applies and SES has changes, THEN run `ses_identity.py` again with `--apply`.
17. For each Porkbun command, do what the owner chose, as PROCEDURE.md "Write" steps 12-16 say.
18. After each `--apply`, paste the final output and the exit code.
19. IF the owner chooses "Do not apply", THEN stop and write nothing.

**Why:** `--apply` changes what the domain's mail is signed with and where its bounces go, and
invoking the skill does not approve rows the owner has not seen. `create` never leaves two SPF
or two DMARC records at a name: an SPF record attaches to the one there, and a DMARC record
where another is held waits for the owner's "Replace it", which runs `update`.

## Old DKIM records

1. IF the run printed the three DKIM commands, THEN run `porkbun.py domain _domainkey.<domain> --type CNAME`.
2. IF a CNAME there points at `dkim.amazonses.com` and is not one of the three, THEN show it to the owner.
3. Ask the owner whether to delete each one. Delete only on a yes, with `porkbun.py delete CNAME <name> --apply`.

**Why:** a token SES no longer uses leaves its CNAME behind at another name. Without the three
tokens there is nothing to compare against, so this step runs only when they were printed.

## The sender config

1. IF the prompt asks to add the domain to the sender config, THEN pass `--sender-profile <send-only profile>`.
2. IF the run refuses because the sender config is not set, THEN report it; do not guess a path.
3. The dry run shows the block it would add; `--apply` appends it. A listed domain is `ok` and is not edited.

## Rows and exit

SES rows: `ok` matches, `CREATE` adds the identity or MAIL FROM, `EDIT` changes the DKIM key
length or MAIL FROM (the note shows the old value), `CONFLICT` is DKIM that is not Easy DKIM, which
this skill does not change. Porkbun rows are `porkbun.py`'s (see
`~/.claude/skills/enine-porkbun/PROCEDURE.md`).

Exit 0 conforms or was applied and the six commands were printed; 1 a change is pending (dry
run), SES holds a `CONFLICT`, or the DKIM tokens are unknown; 2 could not decide.

Moving to 2048-bit DKIM takes effect at SES's next key rotation, and SES allows the change once a day.
