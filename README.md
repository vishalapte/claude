# claude

User-level Claude Code configuration: permissions, auto-mode policy, UI preferences, and
generic commands. Nothing repo-specific lives here; a repo that needs hooks, skills or
commands ships them as its own Claude Code plugin.

## Install

**From a checkout**, which installs what is in it, unpushed changes included:

    ./install            # --dry-run lists what would be replaced

It replaces each file under `dot-claude/` in `~/.claude`, saving the previous `settings.json`
and `CLAUDE.md` in `~/.claude/backups/` first.

**Without a clone**, one command. Pick the form that matches the repo's visibility.

**Private** (the default for this repo). `gh` supplies the auth:

    gh api repos/vishalapte/claude/tarball/main \
      | tar -xz -C ~/.claude --strip-components=2

**Public**:

    curl -fsSL https://github.com/vishalapte/claude/archive/refs/heads/main.tar.gz \
      | tar -xz -C ~/.claude --strip-components=2

**Then run `./install` in each repo that installs into Claude** (racecar, and any other
repo that ships an install). The download replaces `settings.json`, which removes the hooks
those installs wrote, and nothing tells you: the hooks that would notice are the ones that
were removed. The install is safe to re-run.

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

## Commits and main

The `commit-msg` hook refuses three things in a commit Claude Code makes, in any repo:

- a merge commit. git runs the hook for the commit a merge makes, conflicted or not, and
  for `--no-ff`; a squash merge passes.
- a `Co-Authored-By: Claude` or `Claude-Session:` trailer. `attribution` in `settings.json`
  stops Claude Code writing them, and its line in PR descriptions; the hook catches one written by hand.
- a `Bump version to X.Y.Z.` line that is not the last line of the message.

`git commit -n` skips the hook, so it is denied like `--no-verify`. `pull.ff=only` makes a
pull that cannot fast-forward fail instead of merging; a pull that can fast-forward runs.
Commands that move a branch (merge, reset, rebase, `pull --rebase`, cherry-pick, revert, am,
update-ref, `branch -f`/`-M`, `checkout -B`, `switch -C`, a forced fetch) ask first. A
plain fetch into a branch is fast-forward only, so it runs. `gh pr merge` is denied:
it moves main on GitHub.

## Auto-approving compound commands

Claude Code asks before running a compound command (`a && b`, `a | b`) even when each part
is allowed on its own. `dot-claude/hooks/compound-command-allow.py` approves it when every
part matches an `allow` rule and no part matches an `ask` or `deny` rule. Otherwise it prints
nothing, and Claude Code decides as it would without the hook.

Tests: `python3 -m unittest test_compound_allow`. The test reads the rules in
`dot-claude/settings.json`, turns each rule into a command that matches it, and checks what
the hook does with that command. It also checks commands the hook must never approve
whatever the rules say, such as one containing `$(...)`, which runs a second command no rule
was checked against.

## Sizing an Omarchy display

`dot-claude/skills/omarchy-display-sizing/` holds an Omarchy desktop's display sizes to one
set of targets, for a fleet of 27" iMacs: monitor scale, bar icons and height, the GTK
interface font (which Chrome's tabs use), the GTK text factor and Ghostty's font. Its script
derives the scale from the panel's physical width, so a 2560x1440 iMac gets 1.0 and a
3840x2160 one gets 1.25, and sets each size with its own control. `--check` audits and
changes nothing. It runs over SSH as the logged-in desktop user, without Claude:

    ssh imac01 python3 .claude/skills/omarchy-display-sizing/display_sizing.py --check

Tests: `python3 -m unittest test_display_sizing`.

## Domain email and website at Porkbun

Two skills set a Porkbun domain's DNS from the services that use it. Each defaults to a dry
run that prints the plan and writes nothing; `--apply` writes, re-reads the zone and runs
Porkbun's preflight. Keys live in `~/.config/porkbun/credentials` and
`~/.config/forwardemail/credentials`, mode 600, never in this repo.

- `dot-claude/skills/enine-configure-domain-email-via-forwardemail/` adds the domain to
  Forward Email and holds the zone to the records in Forward Email's own `zone_file`: other
  MX replaced, SPF merged, DKIM, DMARC, return path, verification and autodiscovery set,
  then Forward Email verifies.
- `dot-claude/skills/enine-configure-domain-eninesites/` sets `ALIAS` records for the domain
  and `www` to `eninesites.com`, or to the host `--to` names.

Both import the Porkbun client from `dot-claude/skills/porkbun/porkbun_client.py`, its one
home, so `./install` ships them with the `porkbun` directory.

Both delete Porkbun's parking placeholders (records pointing at `pixie.porkbun.com`) and
never touch NS records.

`dot-claude/skills/porkbun/` (`/porkbun`) writes one record from a prompt:
`porkbun.py create A auth.example.com 192.0.2.1`, or `update` or `delete` with the same
arguments, and `list` prints the records at a name and under it. It finds the domain the name
belongs to with Porkbun's `/domain/listAll`, uses the same dry-run, `--apply` and preflight
steps, and owns the client the other two import. `porkbun-create`, `porkbun-update`, `porkbun-delete` and
`porkbun-list` are the same skill with the verb fixed: each points at `porkbun/PROCEDURE.md`,
the one procedure all five follow.

Tests: `python3 -m unittest test_configure_domain_email test_configure_domain_eninesites test_porkbun`.

## Users on a hand-built host

`dot-claude/skills/linux-create-user/` (`/linux-create-user`) creates one account on a remote
host over `ssh <host> sudo -n bash -s`, the way a hand-built host's history made them. The
uid decides the kind: 1000 and above is a person (bash, `/home/<name>`, optional sudo with a
`NOPASSWD` drop-in, SSH keys from `github.com/<id>.keys`), below 1000 a service account
(system, nologin, `/var/lib/<name>`, gid equal to uid, optional GitHub deploy key with a
`Host <key>.github.com` alias). It reads the host, prints the plan, and writes only with
`--apply`, then reads the host again to verify. It never renumbers an account and only adds.
A host under racecar's Ansible rail gets its accounts from the `machine` role instead.

Tests: `python3 -m unittest test_linux_create_user`.

## Why the install needs no filter

`--strip-components=2` drops the archive's top directory and `dot-claude/`. A top-level
file such as this README strips to nothing and is skipped, so no member filter is needed,
and the same command works with GNU tar and macOS bsdtar. That holds only while
`dot-claude/` is the one directory at the top of the repo: a second one would be
extracted into `~/.claude` too.
