---
name: porkbun-create
description: >
  Create one DNS record at Porkbun: "/porkbun-create A www.example.com 192.0.2.1", or a bare /porkbun-create to be asked for the name, type and target.
  Dry-runs first and asks the owner before --apply writes. Use for "add a DNS record", "point <name> at <address>", or "create a <type> record".
---

# porkbun-create

The verb is `create`. The prompt's arguments are `<TYPE> <name> <target>`.

1. Read `~/.claude/skills/porkbun/PROCEDURE.md` and follow it with the verb `create`.
2. IF the prompt gives no arguments, THEN start at its "Collect the missing parts".

**Why:** the procedure, the script and the help have one home, in `../porkbun/`. This skill
fixes the verb, so the verb question is not asked.
