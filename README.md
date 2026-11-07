# claude

A Claude Code configuration, installed into `~/.claude`: the settings and guardrails every
session runs under, and a set of skills that do real work, such as managing DNS at Porkbun,
setting a domain up for AWS SES, and searching, filing and sending mail.

Everything that installs lives under `dot-claude/`. The tests and this README stay in the
repo.

| Area | What it does | Where |
|---|---|---|
| [Settings and guardrails](#settings-and-guardrails) | Permissions, git hooks that refuse unsafe pushes and commits, auto-approval of safe compound commands | `dot-claude/settings.json`, `dot-claude/git-hooks/`, `dot-claude/hooks/` |
| [Porkbun](#porkbun) | Create, update, delete and read DNS records; capture, undo and preflight a zone | `dot-claude/skills/enine-porkbun*/` |
| [Domain setups](#domain-setups) | One run sets a domain's DNS for Forward Email, Fastmail or a website host | `enine-porkbun-forwardemail/`, `-fastmail/`, `-eninesites/` |
| [AWS SES](#aws-ses) | Configure an SES identity and print the records it needs | `dot-claude/skills/enine-aws-ses/` |
| [Mail](#mail) | Search, resolve, file and pin stored mail; draft and send through SES; rank DMARC reports | `dot-claude/skills/enine-mail/`, `dot-claude/skills/enine-mail-dmarc/` |
| [Hosts and desktops](#hosts-and-desktops) | Create Linux accounts over SSH; size an Omarchy desktop's display | `linux-create-user/`, `omarchy-display-sizing/` |
| [Commands](#commands) | `/lint` and `/review` | `dot-claude/commands/` |

## How the tools behave

Every script that changes something follows the same rules, so one habit covers them all:

- **A dry run by default.** Without `--apply` a script reads, prints exactly what it would
  change, and writes nothing. Each skill shows those rows and asks before it applies them.
- **Rows you can read.** `ok` already as asked, `CREATE`, `EDIT` (with the old value),
  `DELETE`, `CONFLICT` (cannot be applied, with the reason), `FAILED` (refused by the service).
- **Exit codes.** `0` done or nothing to do; `1` changes pending, a conflict, or something
  wrong found; `2` refused, such as a bad argument or a missing credential.
- **Credentials stay out of the repo.** Each section's **Credentials** names the file a tool
  reads. A key file holds `KEY=VALUE` lines and must be mode 600; a tool refuses one that
  others can read, and no tool prints a key.

## Install

### From a checkout

    ./install            # --dry-run lists what would be replaced

It installs what is in the checkout, unpushed changes included. It copies every file under
`dot-claude/` into `~/.claude`, first saving the current `settings.json` and `CLAUDE.md` in
`~/.claude/backups/`.

### Without a clone

    curl -fsSL https://github.com/vishalapte/claude/archive/refs/heads/main.tar.gz \
      | tar -xz -C ~/.claude --strip-components=2

`--strip-components=2` drops the archive's top directory and `dot-claude/`, so a top-level
file such as this README strips to nothing and is skipped. The same command works with GNU
tar and macOS bsdtar. It holds only while `dot-claude/` is the one directory at the top of
the repo; a second one would be extracted into `~/.claude` too.

### What an install replaces

Both ways replace `settings.json` whole. Any other repo that writes into it, such as
racecar, must run its own `./install` again afterwards; those installs are safe to re-run.
Credentials, history, projects and memory in `~/.claude` are never touched, and file modes
are kept, so `statusline-command.sh` stays executable.

## Settings and guardrails

`settings.json` holds the permissions (what runs, what asks, what is denied), the auto-mode
policy, the status line and UI preferences.

### Git hooks

The hooks apply only to commands Claude Code runs, never to your own terminal:
`settings.json` points git at them through `env` (`GIT_CONFIG_*` setting `core.hooksPath`).
Every hook name other than `pre-push` and `commit-msg` is a symlink to `_dispatch`, which runs
the repo's own hook, so pre-commit and the rest keep working.

### Pushes

- A push goes only to a GitHub owner listed in `~/.claude/orgs.json`. That file is private
  and never in this repo; copy `orgs.example.json` there and list your owners. With no file,
  or a malformed one, every push is refused.
- A push that deletes a remote ref is refused, however it is spelled (`:branch`, `--delete`,
  `-d`): git marks every deletion the same way.
- Skipping or redirecting hooks (`--no-verify`, `core.hooksPath`, `GIT_CONFIG_*`) is denied.
- `gh` has no hook point: public gists, public repo creation and visibility changes are
  denied in `settings.json`; other `gh` writes ask.

### Commits

- In any repo, a commit Claude Code makes is refused when it is a merge commit, carries a
  `Co-Authored-By: Claude` or `Claude-Session:` trailer, or has a `Bump version to X.Y.Z.`
  line that is not the message's last line.
- `git commit -n` is denied like `--no-verify`.
- `pull.ff=only` makes a pull that cannot fast-forward fail instead of merging.
- Commands that move a branch (merge, reset, rebase, cherry-pick, revert, `branch -f` and the
  like) ask first; `gh pr merge` is denied.

### Compound commands

Claude Code asks before `a && b` or `a | b` even when each part is allowed.
`hooks/compound-command-allow.py` approves such a command when every part matches an `allow`
rule and none matches an `ask` or `deny` rule; otherwise it stays silent and Claude Code
decides as usual. It never approves a part it cannot see, such as `$(...)`.

### Plan mode

`ExitPlanMode` is denied, so plan mode shows each plan as text and stops; leave it with
shift+tab.

## Porkbun

`/enine-porkbun` drives one script, `skills/enine-porkbun/porkbun.py`. With no arguments it
prints its help. `/enine-porkbun-create`, `-update`, `-delete`, `-list` and `-domain` are the
same skill with the verb fixed; all follow `skills/enine-porkbun/PROCEDURE.md`.

### Verbs

| Verb | Does |
|---|---|
| `create <TYPE> <name> <target>` | adds the record; `ok` when the same value is there |
| `update <TYPE> <name> <target> [--match P]` | edits the one record of that type at the name, or the one whose value starts with `P` |
| `delete <TYPE> <name> [target \| --all]` | removes the record holding `target`; with none, the one record of the type; several need `--all` |
| `list` | the account's domains, one row per name with its TLDs |
| `domain <name> [--type T]` | the records at a name and under it |
| `capture <domain>` | copies the records a domain's live nameservers publish into Porkbun's zone, before the nameservers move |
| `history <domain>` / `restore <domain> <point>` | the zone's restore points, and putting one back (`--prune` also deletes what was added since) |
| `check <domain> --intent I` | Porkbun's preflight for `move-nameservers`, `transfer-out` or `enable-dnssec` |

### What every write guarantees

- **One SPF and one DMARC record per name** (RFC 7208, RFC 7489). `create` of an SPF record
  attaches it to the one there: missing terms are added, the record's `all` stays as set, and
  a record without one gets `-all`. `create` of a DMARC record where another exists is a
  `CONFLICT` that `update --match v=DMARC1` replaces.
- **A CNAME stands alone at its name** (RFC 1034), and an `ALIAS` below the apex counts as one.
- **Porkbun's placeholders are removed**: records pointing at its parking page
  (`pixie.porkbun.com`, `uixie.porkbun.com`), MX to its email forwarders (`fwd1`,
  `fwd2.porkbun.com`) and an SPF record naming only `include:_spf.porkbun.com`. NS records are
  never touched.
- **A refused write stops only its own name**, printed as `FAILED` with Porkbun's code, hint,
  the records in the way and the request id. Writes run name by name, deletes first.
- **Safe retries.** Every write carries an `Idempotency-Key`, and a write or read that meets a
  rate limit, a server error or no answer is resent up to three times.
- **Porkbun checks the dry run too.** Each planned create is sent with `dryRun: true` and
  Porkbun's verdict is shown on the row.

### Credentials

| Account | File | Keys |
|---|---|---|
| the default | `~/.config/porkbun/credentials` | `PORKBUN_API_KEY`, `PORKBUN_SECRET_API_KEY` |
| any other, chosen with `--account NAME` | `~/.config/porkbun/credentials.d/NAME` | the same two |

    mkdir -p ~/.config/porkbun
    printf 'PORKBUN_API_KEY=pk1_...\nPORKBUN_SECRET_API_KEY=sk1_...\n' > ~/.config/porkbun/credentials
    chmod 600 ~/.config/porkbun/credentials

Every skill that writes Porkbun records reads these files: the ones above, the domain setups
and `enine-aws-ses`'s printed commands. Porkbun also needs API access turned on for each
domain (porkbun.com > Domain Management); a run without it says so and names the domain.

### Sandbox

A key starting `pk1_sb_` runs on Porkbun's test system instead of the live zones. The output
starts `SANDBOX:`, and any answer not marked sandbox is refused, so a sandbox key can never
change a real zone.

## Domain setups

Each of these works out a domain's records from the service that uses them and writes them
through the same Porkbun client, with the same dry run and guarantees. All three read the
[Porkbun credentials](#credentials) and take `--account NAME`.

### Forward Email

`/enine-porkbun-forwardemail` (`fe_porkbun.py <domain>`) adds the domain to Forward Email and
holds its DNS to the records in Forward Email's own zone file: MX, SPF (merged), DKIM, return
path, DMARC, verification and autodiscovery. Another mail host's MX is kept and shown as a
`CONFLICT`; `--force` replaces it. It ends with Forward Email's own verification.

**Credentials:** `~/.config/forwardemail/credentials`, mode 600, holding
`FORWARDEMAIL_API_KEY=...`, the API token from your Forward Email account.

### Fastmail

`/enine-porkbun-fastmail` (`fm_porkbun.py <domain>`) sets Fastmail's three DKIM CNAMEs and its
include in the SPF record, so Fastmail can send as the domain; `--mx` also moves the apex MX
to Fastmail. It prints the steps left at Fastmail.

**Credentials:** none for Fastmail. Its API cannot add a domain, so the script never calls it.

### eninesites

`/enine-porkbun-eninesites` (`configure_eninesites.py <domain>`) points the domain and `www`
at `eninesites.com` with `ALIAS` records, or at the host `--to` names.

**Credentials:** Porkbun only.

## AWS SES

`/enine-aws-ses` (`skills/enine-aws-ses/ses_identity.py <domain>`) sets a domain up to send
through SES, or confirms that it already is.

### What it sets

1. **At SES**: it creates the domain's identity, or sets it to 2048-bit Easy DKIM and MAIL
   FROM `hermes.<domain>`. Without `--profile` it says the SES half was skipped.
2. **At Porkbun**: it reads the zone first, then gives each record SES needs one row: the three
   DKIM CNAMEs (one row), the MAIL FROM MX, the MAIL FROM SPF, and DMARC with `p=reject`.

   | in the zone | provided | row |
   |---|---|---|
   | no | yes | `CREATE`: its `porkbun.py create` command prints |
   | no | no | `ERROR` |
   | yes | matches | `CONFIRM` |
   | yes | differs | `PROMPT`: the update prints, delete the zone's and create the provided |

   With no DKIM tokens given, the DKIM row confirms the three the zone holds in AWS form. When
   every row is `CONFIRM` it prints `CONFIRMED: <domain>`. The script writes nothing at Porkbun;
   the skill runs the printed commands on approval, and a `PROMPT` row defaults to doing nothing.

### Region and MAIL FROM

They come from the domain's entry in the [sender config](#sender-config), else `us-east-1`
and `hermes`; `--region` and `--mail-from` override them. A domain the sender config does not
list gets an entry there, shown in a dry run and appended on `--apply`: `mail_from`, `region` when
it is not the default, `# sends_for = []`, and the profile line commented out, holding the profile
most listed domains use. With no
sender config set, or no file at its path, nothing is added (the file is never created), and a domain already listed is flagged; its entry is never
changed. After it creates records, the skill adds the domain this way without asking.

### Credentials

| For | Where |
|---|---|
| SES | an AWS CLI profile in `~/.aws/config` and `~/.aws/credentials`, passed as `--profile NAME`; it must be allowed to read and change SES identities in the domain's account |
| the records | the [Porkbun credentials](#credentials), read by the script to see the zone and by the printed commands to write it |

The two halves never share a credential. Without `--profile` the SES half is skipped; the zone
is always read, so the Porkbun credentials are required.

## Mail

`/enine-mail` drives `skills/enine-mail/mail.py`, which works a mail store on disk and sends
through SES. It is standard-library Python only.

### Verbs

| Verb | Does | Writes |
|---|---|---|
| `search` | finds stored mail by `--from --to --cc --bcc --subject --after --before`, or a Gmail-style `--query`, newest first | nothing |
| `resolve <message-id>` | prints the stored `.eml` for a Message-ID | nothing |
| `ingest <file.eml>...` | moves a `.eml` into the store byte for byte (`--copy` keeps the source); `exists` when it is already stored under any name | with `--apply` |
| `pin <message-id>...` | locks each stored `.eml` with the macOS immutable flag; `--check` reports unlocked ones, `--unpin` unlocks | with `--apply` |
| `draft` | builds draft N, the exact bytes SES will send, and shows it; with no arguments, lists drafts and sent mail | a draft only |
| `send N` | shows draft N as it will go; `send N --apply` sends it through `aws sesv2 send-email` | with `--apply` |
| `dmarc` | reads every DMARC report in the store and ranks threats, holes and statistics; `/enine-mail-dmarc` runs it | nothing |

### Search

A term matches whole words, as in Gmail: `oci` finds "OCI card" but not "Social", and `art*`
is a prefix. An address or domain still matches whole, since `@` and `.` separate words. The
flags and `--query` share one grammar: words side by side are ANDed, `or` and `and` in any
case combine them, `-word` excludes, quotes make a phrase, and `--from X` is `from:(X)`.

### Sending

`send N --apply` takes only a draft number, so what goes out is the draft that was shown. The
draft is marked `sending` before SES is called, so a retry cannot send it twice, and each send
adds one line to the sent log. The From header, display name included, is delivered as
drafted; SES sets the Date.

### DMARC

`dmarc` picks likely reports by subject, sender and content type, never by the address they
came to, then reads each for what it holds; a message those headers do not mark is not read.
An aggregate report (RFC 7489) is XML, zipped, gzipped or bare, read from its bytes whatever its label says; a
failure report (RFC 6591) is a multipart/report with a `message/feedback-report` part. A
report kept in two mailboxes counts once. The output runs threats, failure reports, holes,
then statistics by domain, reporter, month and sender.

- **Threat**: a source IP that failed DMARC for a domain. `high` when a receiver delivered
  it, `medium` when every message was quarantined or rejected, `low` when the same IP also
  passed for the domain. Recent means within `--days` (default 30).
- **Hole**: `p=none`, `pct` below 100, `sp=none`, a legitimate sender whose mail passed on SPF
  alone (a forward of it fails), or a domain with no report for 60 days. A policy is as
  published on the last report's date. A hole whose newest evidence is over 60 days old is not
  reported, nor is a sender whose newest passing mail passed DKIM: it may be fixed since.
- Exit 1 on a recent medium or high threat, a recent failure report, or a high hole.

Each report is untrusted: decompressed size is capped at 50 MB, and an attachment that will
not open is counted as unparseable without hiding the others in the same message.

### Environment variables

The skill holds no path and no sender. Each of five paths comes from a flag or an environment
variable, the flag winning. None has a default: a verb that needs one that is not given
refuses and names both.

| Variable | Flag | Holds | Needed by |
|---|---|---|---|
| `ENINE_MAIL_STORE` | `--store DIR` | the mail store | `search`, `resolve`, `ingest`, `pin`, `dmarc`; a reply's threading |
| `ENINE_MAIL_IDENTITY` | `--identity FILE` | the owner-address file | `ingest`, to tell sent mail from received |
| `ENINE_MAIL_SENDERS` | `--senders FILE` | the sender config | `draft` and `send` with message arguments; `enine-aws-ses` |
| `ENINE_MAIL_DRAFTS` | `--drafts DIR` | the drafts folder | `draft`, `send` |
| `ENINE_MAIL_SENT` | `--sent FILE` | the sent log, one JSON line per send | listing drafts, `send N --apply`; a reply's threading |

Set them in `env` in `~/.claude/settings.json`, so every Claude session finds them:

    "env": {
      "ENINE_MAIL_STORE": "/Users/alice/mail",
      "ENINE_MAIL_IDENTITY": "/Users/alice/mail/identity.json",
      "ENINE_MAIL_SENDERS": "/Users/alice/.config/mail/senders.toml",
      "ENINE_MAIL_DRAFTS": "/Users/alice/.local/share/mail/drafts",
      "ENINE_MAIL_SENT": "/Users/alice/.local/share/mail/sent.jsonl"
    }

An install of this repo replaces `settings.json` whole, so the repo that uses the skill writes
these in its own install.

### The store

    <store>/<account>/archive/<counterparty-domain>/<YYYY-MM>/<hash>.eml
    <store>/<account>/manifest/<YYYY>.jsonl        optional, one JSON line per message

When an account has a manifest, keyed by Message-ID, `resolve`, `pin` and subject searches
answer from it instead of opening every file.

### The owner-address file

JSON listing your own addresses, matched exactly and never by domain:

    {"owner_addresses": ["alice@example.com", "alice@example.org"]}

### Sender config

TOML, one entry per domain you send from. A setting missing from the entry comes from
`[defaults]`, and `{domain}` is replaced with the sending domain. `profile` has no default.

    [defaults]
    region = "us-east-1"
    name = "Alice Example"
    bcc = "alice@example.com"

    [domains."example.com"]
    profile = "ses-example"        # the AWS profile that sends
    mail_from = "hermes"           # read by enine-aws-ses
    reply_to = "alice@{domain}"

A domain not listed, or marked `deprecated = true`, is refused.

### Credentials

`send` calls `aws sesv2 send-email` with the domain's `profile` and `region` from the sender
config. That profile lives in `~/.aws/config` and `~/.aws/credentials` and must be allowed to
send email through SES in the domain's account. Nothing else in the skill needs a credential.

### As a library

`skills/enine-mail/mail_eml.py` reads `.eml` files for other programs: `decode_hdr`,
`msg_date`, `html_to_text`, `body_text`, `account_dirs` and `get_body(msg, type="text")`,
which returns a message's body as plain text, as HTML with `type="html"`, or every text part
as a JSON list with `type="json"` (`{"type", "content", "attachment", "filename", "depth"}`,
where `depth` counts the attached messages a part sits in).

## Hosts and desktops

### Linux users

`/linux-create-user` (`create_user.py [login@]host name`) creates one account on a remote host
over `ssh <host> sudo -n bash -s`. A uid of 1000 or more is a person (bash, `/home/<name>`,
optional sudo, SSH keys from `github.com/<id>.keys`); below 1000 a service account (nologin,
`/var/lib/<name>`, optional GitHub deploy key). It reads the host, prints the plan, writes only
with `--apply`, and reads the host again to verify. It never renumbers an account.

**Access:** your SSH config and keys reach the host, and the login account has passwordless
sudo there (`sudo -n`). A host key ssh cannot find under any name is refused, with its fingerprint
and the command that trusts it once.

### Omarchy display sizing

`/omarchy-display-sizing` (`display_sizing.py`) holds an Omarchy desktop's display sizes to
one set of targets for 27" iMacs: monitor scale, bar, GTK font and text factor, and Ghostty's
font. It derives the scale from the panel's physical width, so a 2560x1440 panel gets 1.0 and
a 3840x2160 one 1.25. `--check` audits and changes nothing. It runs over SSH as the desktop
user:

    ssh imac01 python3 .claude/skills/omarchy-display-sizing/display_sizing.py --check

**Access:** SSH as the desktop user; it changes only that user's settings.

## Commands

- **`/lint`** runs pylint and black in audit mode, reports the findings, and asks before
  fixing anything.
- **`/review`** reviews code for bugs, flaws and quality issues and runs benchmarks. It is
  read-only.

## Tests

    python3 -m unittest discover -p 'test_*.py'

| Tests | Cover |
|---|---|
| `test_git_boundary`, `test_compound_allow` | the push and commit hooks; the compound-command hook against every rule in `settings.json` |
| `test_porkbun`, `test_configure_domain_email`, `test_configure_domain_fastmail`, `test_configure_domain_eninesites` | the Porkbun scripts, against a Porkbun fake |
| `test_porkbun_sandbox` | `porkbun.py` itself on Porkbun's sandbox, when `~/.config/porkbun/credentials.d/sandbox` holds a `pk1_sb_` key; skipped otherwise |
| `test_aws_ses` | `ses_identity.py`, against a fake SES |
| `test_mail`, `test_mail_search`, `test_mail_send` | the mail verbs, in a temporary store, with the `aws` call replaced |
| `test_linux_create_user`, `test_display_sizing` | the host and desktop scripts |

No test reads your mail, configuration or credentials, or sends anything. Every address in
them is a placeholder (`example.com`, `alice`, `192.0.2.x`).
