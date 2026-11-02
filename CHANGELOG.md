# Changelog

All notable changes to this configuration are recorded here, in the style of
[Keep a Changelog](https://keepachangelog.com). The version lives in `VERSION`.

## [Unreleased]

## 0.14.0 - 2026-11-02

### Added
- **`mail_eml.get_body(msg, type="text")`** returns a message's body as plain text, or as HTML
  with `type="html"`, picked as email's own `get_body` picks it, attachments skipped; `""` when
  there is none. `type="json"` returns every text part as a JSON list of
  `{"type", "content", "attachment", "filename", "depth"}`; `depth` counts the attached
  messages a part sits in. A test keeps `mail_eml`'s reading functions importable by name.

## 0.13.0 - 2026-11-01

### Added
- **`/enine-mail` and `skills/enine-mail/mail.py`** search, resolve, file and pin mail in a mail
  store, and draft and send mail through AWS SES:
  - `search` takes header flags and a Gmail-style `--query` under one grammar (`--from X` is
    `from:(X)`; `and` and `or` in any case). A term matches whole words; `word*` is a prefix.
    `filename:` reads only the files whose attachment headers can hold the term, and a subject
    search skips the files wicket's manifest rules out.
  - `resolve` and `pin` look a Message-ID up in wicket's manifest, then in each file's own
    header. `pin` takes the message-ids to lock.
  - `ingest` files a `.eml` byte for byte, and reports `exists` for a message already stored
    under any name.
  - `draft` builds, lists and shows drafts; `send N` shows draft N and `send N --apply` sends
    it with the draft's own From. SES sets the Date when it sends.
  - `ingest`, `pin` and `send` are dry runs until `--apply`.
  - The mail store, the owner-address file, the sender config, the drafts folder and the sent
    log each come from a flag (`--store`, `--identity`, `--senders`, `--drafts`, `--sent`) or an
    environment variable (`ENINE_MAIL_STORE`, `ENINE_MAIL_IDENTITY`, `ENINE_MAIL_SENDERS`,
    `ENINE_MAIL_DRAFTS`, `ENINE_MAIL_SENT`), the flag winning. None has a default; a verb that
    needs one that is not given refuses and names both. The display name comes from the
    sender config.

## 0.12.0 - 2026-11-01

### Added
- **`/enine-aws-ses` and `skills/enine-aws-ses/ses_identity.py <domain>`.** With `--profile`,
  it creates the SES identity, or sets it to 2048-bit Easy DKIM and MAIL FROM
  `hermes.<domain>`; without one it skips SES and says so. It prints the six Porkbun records
  SES needs (three DKIM CNAMEs, the MAIL FROM MX and SPF, DMARC) as `porkbun.py create`
  commands. Region and MAIL FROM follow the sender config (`--senders FILE` or
  `ENINE_MAIL_SENDERS`, no default); `--sender-profile` adds the domain there.

## 0.11.0 - 2026-11-01

### Added
- **`skills/enine-porkbun/` (`/enine-porkbun`) creates, updates and deletes Porkbun DNS
  records and reads the account.** `porkbun.py create|update|delete <TYPE> <name> [target]`
  finds the domain a name belongs to, dry-runs by default, and with `--apply` writes, re-reads
  the zone and runs Porkbun's preflight. `list` prints the account's domains, one row per name
  with its TLDs; `domain` prints the records at a name and under it. `update --match PREFIX`
  edits only the record whose value starts with PREFIX. `delete` with no target removes the
  one record of the type at the name; several is a `CONFLICT` unless `--all` is given.
  `/enine-porkbun-create`, `-update`, `-delete`, `-list` and `-domain` are the same skill with
  the verb fixed; all six follow `skills/enine-porkbun/PROCEDURE.md`.
- **A name holds one SPF and one DMARC record.** `create` of an SPF record attaches it to the
  one at the name: its missing terms are added, the record's `all` stays as set, and a record
  without one gets `-all` unless it uses `redirect=`. `create` of a DMARC record where another
  is held is a `CONFLICT` that `update --match v=DMARC1` replaces.
- **`porkbun.py capture <domain>`** copies the records a domain's live nameservers publish
  into Porkbun's zone, from Porkbun's scan. A scan cannot list a zone, so a name nobody
  guesses is not found.
- **`porkbun.py history`, `restore` and `check`.** `history` lists the zone's restore points;
  `restore <domain> <point>` puts one back (`--prune` also deletes what was added since),
  showing Porkbun's diff and dry run first and printing the command that undoes it. `check
  <domain> --intent move-nameservers|transfer-out|enable-dnssec` prints Porkbun's preflight.
- **`skills/enine-porkbun-fastmail/` (`/enine-porkbun-fastmail`) sets a domain's DNS for
  Fastmail:** the `fm1`, `fm2` and `fm3._domainkey` CNAMEs and Fastmail's include in the SPF
  record; `--mx` also edits the apex MX into Fastmail's two. It prints the steps left at
  Fastmail.
- **`--account NAME`** selects another Porkbun account, whose keys are in
  `~/.config/porkbun/credentials.d/NAME`; `~/.config/porkbun/credentials` is the default.
- **One plan in `skills/enine-porkbun/porkbun_client.py` writes for every Porkbun skill.** It
  writes name by name, each name's deletes first; a name holding a `CONFLICT` gets no writes;
  a refused write stops only its own name and is printed as `FAILED` with Porkbun's code,
  hint, the records in the way and request id. Every write carries an `Idempotency-Key`, and
  a write or read that meets a rate limit, a server error or no answer is resent up to three
  times. A dry run sends each planned create to Porkbun with `dryRun: true` and shows its
  verdict on the row.
- **Porkbun's placeholders are deleted wherever a skill writes:** records pointing at its
  parking hosts (`pixie.porkbun.com`, `uixie.porkbun.com`), MX to its forwarders
  (`fwd1`, `fwd2.porkbun.com`) and an SPF record naming only `include:_spf.porkbun.com`. The
  Forward Email skill, and Fastmail's with `--mx`, edit the forwarding MX into theirs in
  place. NS records are never placeholders.
- **`create` is `ok` when a record with the same value exists, whatever its TTL.** An `ALIAS`
  below the apex is held alone at its name, as a CNAME is (RFC 1034). Host values (`CNAME`,
  `ALIAS`, `MX`, `NS`, `SRV`) compare lowercase without the trailing dot.
- **`fe_porkbun.py` keeps another mail host's apex MX** as a `CONFLICT` that holds the apex;
  `--force` replaces it.
- **Porkbun sandbox keys.** A `pk1_sb_` key runs on Porkbun's test system: the run prints
  `SANDBOX:` and refuses any answer not marked `sandbox: true`. `test_porkbun_sandbox.py` runs
  `porkbun.py` there when `~/.config/porkbun/credentials.d/sandbox` holds one.
- **`test_porkbun.py` and `test_configure_domain_fastmail.py`** run the scripts against the
  Porkbun fake in `test_configure_domain_email.py`.

### Changed
- **The domain skills move under `enine-porkbun`:**
  `enine-configure-domain-email-via-forwardemail` is `enine-porkbun-forwardemail`, and
  `enine-configure-domain-eninesites` is `enine-porkbun-eninesites`. `./install` leaves the
  old directories in `~/.claude/skills/`.

## 0.10.0 - 2026-10-28

### Changed
- **`settings.json` denies `ExitPlanMode`.** Plan mode shows each plan as text and stops;
  shift+tab leaves plan mode.

## 0.9.1 - 2026-10-23

### Fixed
- **`create_user.py --service` picks the next uid after the host's own services.** A run of
  held ids that reaches 999, the system accounts that `useradd -r` and `systemd-sysusers`
  allocate downward from 999, is left out of the highest service uid.

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
