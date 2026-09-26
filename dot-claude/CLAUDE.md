# CLAUDE.md

## Commands that ASK

The rules live in `permissions` in `~/.claude/settings.json`, and only there. The harness
enforces them; do not restate them here. What settings.json cannot hold is the intent:

- **Mutating asks. Irreversible is blocked.** Irreversibility is judged by audience, not
  by the verb. Pushing a feature branch or opening a PR inside my orgs is mutating: it
  asks. Leaving that boundary (a public repo, a gist, email), rewriting or deleting shared
  history (force-push, deleting a remote branch, pushing to `main`/`master`), or
  destroying data with no copy (`manage.py flush`) is irreversible: it is blocked. A
  command no rule matches is judged by the same line.
- **Commits and pushes are mine.** An agent's job ends at emitting the runbook and telling
  me the one line. Approving a prompt is a per-action act, not a standing grant.
- **Only what `permissions.deny` names is blocked.** Everything else prompts at most, so do
  not tell me something is blocked when it is not: an agent that believes `git commit`
  cannot run will report a runbook as un-runnable and route around a prompt I would have
  approved.
- **The `manage.py` exclusions are a denylist.** A custom management command that writes
  is allowed until it is added to `permissions.ask`, or to `deny` if it cannot be undone.

## Named Repos
Refer to a repo by slug. A slug resolves to `~/dev/<org>/<slug>`, one directory per GitHub
owner; find it with `ls -d ~/dev/*/<slug>`.

## Installed by repos
A repo's `./install` writes its own file beside this one. The line below is a placeholder that
racecar's `./install` replaces with the import of its `RACECAR.md`; without racecar it stays an
inert comment.

<!-- RACECAR -->
