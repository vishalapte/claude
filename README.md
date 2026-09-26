# claude

User-level Claude Code configuration: permissions, auto-mode policy, UI preferences, and
generic commands. Nothing repo-specific lives here; a repo that needs hooks, skills or
commands ships them as its own Claude Code plugin.

## Install

One command, no clone. Pick the form that matches the repo's visibility.

**Private** (the default for this repo). `gh` supplies the auth:

    gh api repos/vishalapte/claude/tarball/main \
      | tar -xz -C ~/.claude --strip-components=2

**Public**:

    curl -fsSL https://github.com/vishalapte/claude/archive/refs/heads/main.tar.gz \
      | tar -xz -C ~/.claude --strip-components=2

Both overwrite only the files under `dot-claude/`. Credentials, history, projects and
memory in `~/.claude` are never touched, and file modes are kept, so
`statusline-command.sh` stays executable.

## Private org list

A git `pre-push` hook in `dot-claude/git-hooks/` refuses any push whose GitHub owner is not
listed in `~/.claude/orgs.json`. That file is private and never in this repo; copy
`orgs.example.json` there and put your own owners in it. With no file, or a malformed one,
every push is refused: the absence of the config protects rather than exposes.

The same hook refuses any push that deletes a remote ref. git marks a deletion with an
all-zero sha however it was typed (`:branch`, `--delete`, `-d`), so the hook catches every
spelling, where a `settings.json` rule matches only the text it names. A rule for the
`:branch` form also cannot be written without Claude Code warning about it on every start.

`settings.json` points git at the hooks through `env` (`GIT_CONFIG_*` setting
`core.hooksPath`), so it applies only to commands Claude Code runs, never to your terminal.
Git hands the hook the real destination URL however the push was started (an alias, a
script, `bash -c`), so nothing parses shell commands. Every other hook name is a symlink to
`_dispatch`, which runs the repo's own hook, so pre-commit and the rest keep working.
Skipping or redirecting hooks (`--no-verify`, `core.hooksPath`, `GIT_CONFIG_*`) is denied.

`gh` has no hook point. Public gists, public repo creation and visibility changes are denied
in `settings.json`; other `gh` writes fall to the ask rules and the auto-mode classifier.

Tests: `python3 -m unittest test_git_boundary`.

## Why the install needs no filter

`--strip-components=2` drops the archive's top directory and `dot-claude/`. A top-level
file such as this README strips to nothing and is skipped, so no member filter is needed,
and the same command works with GNU tar and macOS bsdtar. That holds only while
`dot-claude/` is the one directory at the top of the repo: a second one would be
extracted into `~/.claude` too.
