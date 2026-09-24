---
description: Review code for bugs, flaws, and quality issues. Runs benchmarks and reports findings. Read-only — does not modify any files.
allowed-tools: Read, Glob, Grep, Bash, Agent
---

IMPORTANT: This command is READ-ONLY. Do NOT edit, write, or modify any files. Do NOT ask for permission to make changes. Only report findings.

## Phase 1: Benchmarks

Benchmarks are stored in `.claude/benchmarks/` keyed by git commit short hash.

1. Get the current commit hash: `git rev-parse --short HEAD`
2. Check if `.claude/benchmarks/<hash>.json` exists.
   - **If it exists**: load and display the cached results. Skip to Phase 2.
   - **If it does not exist**: run benchmarks:
     a. Run `python manage.py check` to verify no Django system errors.
     b. Run `python manage.py profile_queries` to capture query counts per URL.
     c. Start the dev server in the background, then curl each URL from the sitemap and record HTTP status code and response time:
        ```
        curl -s -o /dev/null -w "%{http_code} %{time_total}" <url>
        ```
        Stop the dev server when done.
     d. Save results to `.claude/benchmarks/<hash>.json` as:
        ```json
        {
          "commit": "<hash>",
          "timestamp": "<ISO 8601>",
          "django_check": "ok" or "<error text>",
          "profile_queries": { "<url>": {"queries": N, "net": N, "time_ms": N}, ... },
          "curl": { "<url>": {"status": N, "time_s": N}, ... }
        }
        ```
3. If a previous benchmark file exists (different commit), compare against it and flag:
   - Any new non-200 status codes
   - Query count regressions (increase > 2)
   - Response time regressions (> 2x slower)
4. Present benchmark results (and comparison if available).

## Phase 2: Code Review

Review the code in the current project. Focus on:

1. **Fatal flaws first**: broken imports, missing dependencies, runtime errors, incorrect model relationships, wrong return types
2. **Fragile patterns**: N+1 queries, race conditions, unhandled edge cases, complected code that will break when extended
3. **Quality issues**: dead code, unused imports, duplicated logic that argues for DRY, mark_safe on untrusted input, synchronous calls at render time

For each issue found:
- State the file and line
- Classify severity: fatal / fragile / quality
- Describe what needs to change (but do NOT make the change)

Do NOT flag: minor style preferences, missing docstrings, type annotations on unchanged code, or theoretical future problems. Only flag what matters now.

$ARGUMENTS
