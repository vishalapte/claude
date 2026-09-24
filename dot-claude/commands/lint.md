---
description: Run pylint and black in audit mode, report findings, then ask before fixing.
allowed-tools: Read, Glob, Grep, Bash, Agent
---

IMPORTANT: This command starts READ-ONLY. Do NOT edit or write any files until explicitly asked to fix.

## Phase 1: Audit (read-only — no permission needed)

1. Run `pylint $ARGUMENTS` with no additional flags (respect the project's pylintrc). If $ARGUMENTS is empty, lint all Python files in the project (excluding migrations, venv, and managed directories). Use `git ls-files '*.py'` to find files, filtering out `migrations/` and `qux/` (submodule).
2. Run `black --check --diff $ARGUMENTS` on the same file set.
3. Present a combined summary of all issues found, grouped by severity:
   - Errors (E) and Fatal (F)
   - Warnings (W)
   - Refactor (R)
   - Black formatting
4. Do NOT proceed to fix. Ask the user: "Would you like me to fix these issues?"

## Phase 2: Fix (only if user says yes)

- Fix errors in order of severity:
  - Errors (E) and Fatal (F) first
  - Warnings (W) second
  - Refactor (R) suggestions only when they are clearly correct (e.g., too-many-locals that can be trivially reduced)
  - Black formatting issues
- Do NOT suppress warnings by adding pylint disable comments or modifying pylintrc
- Do NOT fix Convention (C) issues unless they are obviously wrong

## Phase 3: Verify

1. Re-run both `pylint` and `black --check` on the same path to confirm all fixes.
2. Show a summary of what was fixed.
