---
name: enine-porkbun-update
description: >
  Update the one DNS record of a type at a name at Porkbun: "/enine-porkbun-update A www.example.com 192.0.2.2", or a bare /enine-porkbun-update to be asked for the name, type and target.
  Dry-runs first and asks the owner before --apply writes. Use for "change the <type> record for <name>", "repoint <name>", or "update a DNS record".
---

# enine-porkbun-update

The verb is `update`. The prompt's arguments are `<TYPE> <name> <target>`.

1. Read `~/.claude/skills/enine-porkbun/PROCEDURE.md` and follow it with the verb `update`.
2. IF the prompt gives no arguments, THEN start at its "Collect the missing parts".

**Why:** the procedure, the script and the help have one home, in `../enine-porkbun/`. This skill
fixes the verb, so the verb question is not asked.
