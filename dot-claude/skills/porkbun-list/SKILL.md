---
name: porkbun-list
description: >
  List the DNS records at a name at Porkbun, and every name under it: "/porkbun-list example.com", or a bare /porkbun-list to be asked for the name and type.
  Writes nothing, so it asks no approval. Use for "what DNS records does <name> have", "show the zone for <domain>", or "list DNS records".
---

# porkbun-list

The verb is `list`. The prompt's arguments are `<name> [TYPE]`.

1. Read `~/.claude/skills/porkbun/PROCEDURE.md` and follow it with the verb `list`.
2. IF the prompt gives no arguments, THEN start at its "Collect the missing parts".

**Why:** the procedure, the script and the help have one home, in `../porkbun/`. This skill
fixes the verb, so the verb question is not asked.
