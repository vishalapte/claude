---
name: enine-porkbun-domain
description: >
  List the DNS records at a name at Porkbun, and every name under it: "/enine-porkbun-domain example.com", or a bare /enine-porkbun-domain to be asked for the name and type.
  Writes nothing, so it asks no approval. Use for "what DNS records does <name> have", "show the zone for <domain>", or "list DNS records".
---

# enine-porkbun-domain

The verb is `domain`. The prompt's arguments are `<name> [TYPE]`.

1. Read `~/.claude/skills/enine-porkbun/PROCEDURE.md` and follow it with the verb `domain`.
2. IF the prompt gives no arguments, THEN start at its "Collect the missing parts".

**Why:** the procedure, the script and the help have one home, in `../enine-porkbun/`. This skill
fixes the verb, so the verb question is not asked.
