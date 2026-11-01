---
name: enine-porkbun-list
description: >
  List every domain in the Porkbun account, one row per name with its TLDs in a second
  column: "/enine-porkbun-list". Writes nothing, so it asks no approval. Use for "which domains do
  I have at Porkbun", "list my domains", or "what TLDs do I hold for <name>". For one
  domain's DNS records, use /enine-porkbun-domain.
---

# enine-porkbun-list

The verb is `list`. It takes no arguments.

1. Read `~/.claude/skills/enine-porkbun/PROCEDURE.md` and follow it with the verb `list`.
2. IF the prompt names a domain, THEN follow it with the verb `domain` instead, as /enine-porkbun-domain does.

**Why:** the procedure, the script and the help have one home, in `../enine-porkbun/`. This skill
fixes the verb, so the verb question is not asked.
