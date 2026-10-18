# Changelog

All notable changes to this configuration are recorded here, in the style of
[Keep a Changelog](https://keepachangelog.com). The version lives in `VERSION`.

## [Unreleased]

## 0.7.0 - 2026-10-18

### Added
- **`CLAUDE.md` has a `## Threads` section.** One thread is current; each other thread has
  its own file, `.rclocal/threads/<tag>.md`, so every file has one writer. `[new: tag]`,
  `[tag]`, `now [tag]` and `done [tag]` open, file to, switch to and close a thread; parking
  phrases ("table", "later", "for now") open one too. A question or idea that changes no
  files gets a background agent that writes its answer into the thread's file and returns
  one line. A question about something the agent showed gets a short answer and does not
  change the current step; only an instruction does.
- **`CLAUDE.md` has `## Replies` and `## Approval` sections.** Replies lead with the verdict,
  show at most 5 items, keep my words, and explain any term that is not plain English.
  Approval separates roger (acknowledgement: "copy", "ok") from wilco (execute: "go",
  "engage"); a question asks to understand, and each reply opens with "Roger" or "Wilco".
  Nothing merges into main without a wilco for it. "Sitrep?" reports the task in progress,
  or else the tasks that remain in each thread.

## 0.6.0 - 2026-10-16

### Added
- **`skills/enine-configure-domain-eninesites/`** sets `ALIAS` records for a domain and its
  `www` to `eninesites.com`, or to the host `--to` names, replacing the web record each
  name holds. It always dry-runs first and asks the owner before `--apply` writes. It
  reuses the email skill's Porkbun client.
- **`test_configure_domain_eninesites.py`** runs the script against the Porkbun fake in
  `test_configure_domain_email.py`.

## 0.5.0 - 2026-10-16

### Added
- **`skills/enine-configure-domain-email-via-forwardemail/`, a skill and stdlib script that
  add a domain to Forward Email and set its Porkbun DNS.** The records come from the
  `zone_file` Forward Email's API returns for the domain, not from a list kept here. Other
  apex MX records are replaced, Forward Email's include is merged into an existing SPF
  record rather than adding a second one, DKIM and DMARC values are replaced, other apex TXT
  records are kept, and a CNAME that would share a name with another type is reported, not
  applied. Porkbun's parking placeholders are deleted; NS records never are. A plain run is
  a dry run, and the skill asks the owner before it runs `--apply`, which writes, re-reads
  the zone, runs Porkbun's preflight and asks Forward Email to verify. Keys are read from
  `~/.config/forwardemail/credentials` and `~/.config/porkbun/credentials`, and a file
  readable by others is refused.
- **`test_configure_domain_email.py`** runs the script against in-memory Forward Email and
  Porkbun fakes.

## 0.4.0 - 2026-10-15

### Added
- **`skills/omarchy-display-sizing/`, a skill and stdlib script that size an Omarchy
  display.** It derives the monitor scale from the panel's physical width (130 points per
  inch, which is 1.25 on a 27" iMac at 3840x2160). It then holds bar icons and height, the
  GTK interface font, the GTK text factor and Ghostty's font to targets, each through its own
  control, and verifies them live. The defaults are the owner's sizes in screen pixels:
  25px bar icons, 16px Chrome tab text and 23.3px Ghostty text. It does not use
  `omarchy display text size`, which moves the shell font, GTK and terminal together and
  cannot reach those sizes. `--check` audits without changing anything, and a second apply
  changes nothing. It finds the Hyprland session itself, so it runs over SSH.
- **`test_display_sizing.py`** pins the defaults to those screen-pixel sizes, pins the scale
  rounding the script restates from Omarchy, and runs the apply against a fake desktop.

## 0.3.0 - 2026-09-26

### Added
- **`hooks/compound-command-allow.py`, moved here from racecar and rewritten as one stdlib
  Python file.** It answers Claude Code's prompt for a compound command (`a && b`, `a | b`)
  when every part matches an `allow` rule, since Claude Code asks for those even when each
  part is allowed alone. It is user configuration rather than a racecar standard. Five fixes
  over racecar's copy:
  - it reads `deny` and `ask`; it looked for a `disallow` key, which does not exist;
  - it says nothing when a part runs a command it cannot see (`$(...)`, a backtick,
    `<(...)`) or redirects to or from a file, where it used to approve `ls $(anything)`;
  - it no longer crashes on an unclosed quote;
  - it no longer cuts a command short at every `#`;
  - a rule such as `ls *` now covers a bare `ls`, as it does in Claude Code.
- **`test_compound_allow.py` tests the rules in `dot-claude/settings.json`**, so a rule is
  tested as soon as it is added. For each `ask` and `deny` rule, the test also allows the same
  command, so it fails if the hook ever stops reading `ask` and `deny`.

### Changed
- **`CLAUDE.md` no longer tells an agent to stop at a runbook and one line.** "Commits and
  pushes are mine" stays, and so does "approving a prompt is a per-action act".

## 0.2.0 - 2026-09-26

### Added
- **`./install` installs `~/.claude` from a checkout**, so what lands is what you are looking
  at, unpushed changes included. It replaces each file under `dot-claude/` whole, first saving
  the previous `settings.json` and `CLAUDE.md` in `~/.claude/backups/`, and warns when
  `orgs.json` is missing. `--dry-run` lists what it would replace. The download still works
  for a machine with no clone.
- **`CLAUDE.md` carries a placeholder for repo installs.** racecar's `./install` replaces it
  with the `@RACECAR.md` import; without racecar it stays an inert comment, so the base config
  never depends on racecar being present.
- **The README says to re-run each repo's `./install` after installing this one.** Replacing
  `settings.json` removes the hooks those installs wrote, and nothing reports it, because the
  hooks that would notice are the ones removed.

## 0.1.1 - 2026-09-26

### Fixed
- **Deleting a remote branch is refused by the `pre-push` hook, not by `settings.json`
  rules.** The two rules for it (`git push * :*` and the `git -C` form) never matched: Claude
  Code reads a trailing `:*` as its old prefix syntax, so they matched only a literal `*`. The
  spelling that does match prints a warning on every start. git marks every deletion with an
  all-zero sha, so the hook refuses `:branch`, `--delete` and `-d` alike, including through an
  alias or a script. The `--delete` rules stay, as a refusal before git runs.

## 0.1.0 - 2026-09-24

### Added
- **The base Claude Code configuration** under `dot-claude/`: permissions, the auto-mode
  policy, UI preferences and generic commands, installed into `~/.claude` by a one-command
  download.
- **A `pre-push` hook that refuses any push outside the GitHub owners** listed in the private
  `~/.claude/orgs.json`, and every push when that file is missing or malformed.
