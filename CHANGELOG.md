# Changelog

All notable changes to this configuration are recorded here, in the style of
[Keep a Changelog](https://keepachangelog.com). The version lives in `VERSION`.

## [Unreleased]

## 0.12.0 - 2026-10-28

### Changed
- **Plan mode no longer asks to proceed.** `settings.json` denies `ExitPlanMode`, which
  removes the tool, so Claude shows each revised plan as text and stops instead of asking
  "Would you like to proceed?" after every revision. Leave plan mode with shift+tab.

## 0.11.0 - 2026-10-25

### Added
- **`skills/enine-porkbun-fastmail/` (`/enine-porkbun-fastmail`) sets a Porkbun domain's DNS
  for Fastmail.** `fm_porkbun.py` holds `fm1`, `fm2` and `fm3._domainkey` to Fastmail's DKIM
  CNAMEs and adds `include:spf.messagingengine.com` to the one SPF record, so Fastmail can
  send as the domain while its mail keeps arriving where it does. `--mx` also edits the apex
  MX into Fastmail's two in place. It ends by printing the steps at Fastmail its API cannot
  do: add the domain, add the sending identities, and check a test message for `dkim=pass`.
- **The Porkbun skills take `--account NAME`** to work in another Porkbun account, whose keys
  are in `~/.config/porkbun/credentials.d/NAME`, the same two lines as
  `~/.config/porkbun/credentials`, which stays the default. A name the account does not hold
  is refused with the other accounts named, and the procedure asks the owner which to use.
- **`/enine-porkbun-domain` prints a domain's records**, the job `/porkbun-list` did.
- **`porkbun.py update --match PREFIX` edits only the record whose value starts with
  PREFIX**, in any case. An apex TXT holds SPF beside site verifications, so `update TXT
  example.com "v=spf1 ..." --match v=spf1` changes the SPF record and nothing else.
- **`test_configure_domain_fastmail.py`** runs the Fastmail script against the Porkbun fake.

### Changed
- **The Porkbun skills are renamed to one prefix, `enine-porkbun`.** `/porkbun` is now
  `/enine-porkbun`, and its verb skills `/enine-porkbun-create`, `-update`, `-delete`,
  `-list` and `-domain`. `enine-configure-domain-email-via-forwardemail` is now
  `enine-porkbun-forwardemail`, and `enine-configure-domain-eninesites` is now
  `enine-porkbun-eninesites`. The scripts keep their names. `./install` does not remove the
  old directories from `~/.claude/skills/`; delete them by hand.
- **`list` prints the account's domains**, one row per name with its TLDs, where it printed a
  domain's records.
- **Every Porkbun skill writes through one plan in `porkbun_client.py`.** `porkbun.py`,
  `configure_eninesites.py` and `fe_porkbun.py` each had their own plan and apply, in three
  write orders. Each now works out its records and asks for create, update and delete;
  deletes are applied first, so Porkbun never refuses a record that clashes with one about
  to go, and an update edits its record in place. A name holding a `CONFLICT` gets no writes,
  so a record is never deleted without the one meant to replace it.
- **An `ALIAS` below the apex is held alone at its name, as a CNAME is**, because Porkbun
  serves it as one (RFC 1034).
- **`create` is `ok` when a record with the same value exists, whatever its TTL**, where a
  different `--ttl` made a second record with the same value. A TTL is changed with `update`.
- **`configure_eninesites.py` keeps a web record's TTL when it edits it into the `ALIAS`**,
  where it set 600.

## 0.10.1 - 2026-10-23

### Fixed
- **`create_user.py --service` picks the next uid after the host's own services**, not after
  the system's accounts. Ubuntu's `useradd -r` and `systemd-sysusers` take uids counting down
  from 999, so "one past the highest in 700..999" was 1000 and every `--service` was refused
  with "no free uid left". A run of held ids reaching 999 is now left out of "highest".

## 0.10.0 - 2026-10-21

### Added
- **`skills/porkbun/` (`/porkbun`) creates, updates or deletes one Porkbun DNS record, or
  lists the records at a name.** `porkbun.py` finds the domain a name belongs to, dry-runs by
  default, and with `--apply` writes, re-reads the zone and runs Porkbun's preflight; the
  skill asks before it applies. A bare `/porkbun` shows the help and asks for the verb, the
  name, the type and the target, before the credentials are read.
- **`skills/porkbun-{create,update,delete,list}/`** are `/porkbun` with the verb fixed. Each
  points at `skills/porkbun/PROCEDURE.md`, the one procedure all five skills follow.
- **`test_porkbun.py`** runs the script against the Porkbun fake in
  `test_configure_domain_email.py`, which gains an `argv()` hook for scripts whose first
  argument is not the domain.

### Fixed
- **`ALIAS` and `NS` values are compared as host names**, lowercase and without the trailing
  dot, as `CNAME`, `MX` and `SRV` already were (`porkbun_client.HOST_TYPES`).

### Changed
- **The Porkbun client moves to `skills/porkbun/porkbun_client.py`**, out of the email
  skill's `fe_porkbun.py`, which keeps only Forward Email. The email and eninesites skills
  import it from there.

## 0.9.0 - 2026-10-21

### Added
- **`skills/linux-create-user/` (`/linux-create-user`) creates one user on a remote host over
  SSH**, as the host's own history made them: a person (uid 1000 and above) with optional sudo
  and GitHub keys, or a service account (uid below 1000) with an optional GitHub deploy key.
  Without a uid it picks the next free one, and it prints every default it used. It dry-runs
  by default; `--apply` writes and verifies. An existing account is never renumbered. A host
  name ssh does not know is matched by its host key to a name `known_hosts` does know.
- **`test_linux_create_user.py`** runs the script against a fake host and checks every script
  it sends with `bash -n`.

## 0.8.0 - 2026-10-18

### Added
- **The `commit-msg` hook refuses merge commits, Claude trailers, and a bump line that is not last.** It runs for every commit Claude Code makes, in any repo.
- **`settings.json` asks before a command moves a branch, and denies `gh pr merge` and `git commit -n`.** `attribution` stops the co-author and session trailers and the PR line, and `pull.ff=only` keeps pulls fast-forward.

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
