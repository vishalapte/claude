---
name: enine-porkbun-create
description: >
  Create one DNS record at Porkbun: "/enine-porkbun-create A www.example.com 192.0.2.1", or a bare /enine-porkbun-create to be asked for the name, type and target.
  Dry-runs first and asks the owner before --apply writes. Use for "add a DNS record", "point <name> at <address>", or "create a <type> record".
---

# enine-porkbun-create

The verb is `create`. The prompt's arguments are `<TYPE> <name> <target>`.

1. Read `~/.claude/skills/enine-porkbun/PROCEDURE.md` and follow it with the verb `create`.
2. IF the prompt gives no arguments, THEN start at its "Collect the missing parts".

**Why:** the procedure, the script and the help have one home, in `../enine-porkbun/`. This skill
fixes the verb, so the verb question is not asked.
