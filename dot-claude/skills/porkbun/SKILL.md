---
name: porkbun
description: >
  Porkbun DNS records: create, update or delete one record, or list the records at a name,
  from a prompt such as "/porkbun create A auth.example.com 192.0.2.1" or "/porkbun list
  example.com". A bare /porkbun shows the help and asks for the verb, then the name, the
  record type and the target. Writes always dry-run first and ask the owner before --apply.
  /porkbun-create, /porkbun-update, /porkbun-delete and /porkbun-list do the same with the
  verb fixed. Use for "DNS at porkbun", "add a DNS record", "change a DNS record", "remove a
  DNS record", or "what DNS records does <name> have".
---

# Porkbun DNS

1. IF the prompt gives no arguments, THEN run `python3 ~/.claude/skills/porkbun/porkbun.py` and show its output as the help.
2. IF the prompt names no verb, THEN ask with AskUserQuestion: `create`, `update`, `delete`, `list`.
3. Do not mark any option "(Recommended)".
4. With the verb known, read `~/.claude/skills/porkbun/PROCEDURE.md` and follow it for that verb.

**Why:** one procedure serves this skill and the four verb skills, so it lives in one file.
This skill adds only the help and the verb question.
