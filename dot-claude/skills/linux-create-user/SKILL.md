---
name: linux-create-user
description: >
  Create one user on a remote Linux host over SSH, the way hand-built hosts made theirs: a
  person (uid 1000 and above: bash, /home/<name>, optional sudo with a NOPASSWD drop-in, SSH
  keys from github.com/<id>.keys) or a service account (uid below 1000: system, nologin,
  /var/lib/<name>, gid equal to uid, optional GitHub deploy key with a Host <key>.github.com
  alias). Picks the next free uid when none is given, and shows every default it picked.
  One deterministic script reads the host, plans, dry-runs, and with --apply writes, reads the host again and verifies. Never
  renumbers an existing account. Use for "create user <name> on <host>", "add a service
  account", "give <person> a login on <host>", or "set up a deploy user".
---

# Create a user on a remote host

One script does the work, from this machine, over `ssh <host> sudo -n bash -s`. Run it; do
not type `useradd` on the host by hand.

    python3 ~/.claude/skills/linux-create-user/create_user.py                       # help
    python3 ~/.claude/skills/linux-create-user/create_user.py [login@]host name [options]          # dry run
    python3 ~/.claude/skills/linux-create-user/create_user.py [login@]host name [options] --apply  # apply + verify

The uid decides the kind of account, as the host's own history did:

| uid | Kind | Made with | Options |
|---|---|---|---|
| 1000 and above | person | `useradd --uid N --user-group --create-home --shell /bin/bash` | `--github ID`, `--sudo` |
| below 1000 | service | `groupadd -r -g N`; `useradd -r -u N -g N -m -d /var/lib/<name> -s /usr/sbin/nologin` | `--deploy-key K` |

- `--github ID` adds the keys at `https://github.com/ID.keys` to `authorized_keys`, keeping the
  ones already there. A GitHub account with no keys is refused: the person could not log in.
- `--sudo` adds the `sudo` group and `/etc/sudoers.d/90-<name>` with `NOPASSWD`. The account has
  no password, so sudo that asks for one cannot be used. `visudo -cf` checks the file first.
- `--deploy-key K` makes `~/.ssh/id_ed25519_K` and an ssh config alias `Host K.github.com`, so
  `git clone git@K.github.com:<org>/<repo>.git` uses that key. The script prints the public key
  to add to the repo's deploy keys on GitHub; adding it there needs admin on the repo.
- `login@host` logs in as `login`, as in ssh; `name` is always the account to create. Without
  `login@`, ssh's own choice for the host is used and printed. The login needs passwordless sudo.
- `--uid N` sets the uid; `--service` instead makes a service account at the next free uid.
- The host may be any name for the machine. When ssh has no key stored under that name, the
  script finds the key the host presents under another name in `known_hosts`, connects with
  it, and prints which name it used. A key no name holds is refused with its fingerprint.

## Map the prompt

1. IF the prompt gives no arguments, THEN run the script with no arguments and show its output as the help.
2. Pass the host as the prompt writes it; `login@host` means log in as `login`, never the account to create.
3. IF the prompt gives no host, THEN ask in a plain message for the host as ssh knows it. End the turn.
4. IF the prompt gives no user name, THEN ask in a plain message for it. End the turn.
5. Pass `--uid`, `--service`, `--sudo`, `--github` or `--deploy-key` only when the prompt gives it.
6. Do not ask for anything else: the script picks a default for every other value and prints it.

**Why:** the host and the name have no default. Everything else does, and the dry run shows
each default it used, with its reason, before anything is written.

Defaults the script picks, and prints under "Defaults" in the dry run:

| Setting | Default |
|---|---|
| login | without `login@`, what ssh uses for the host: your ssh config's `User`, else your local username |
| uid | the account's own uid when the name exists; otherwise one past the highest uid in use in the kind's range (1000 and up, or 700 to 999 for a service), skipping a uid whose gid is taken; the system's own accounts, counting down from 999, are left out of "highest" |
| kind | without `--uid`, a person, unless `--service` or `--deploy-key` |
| sudo, ssh keys | a person gets neither without `--sudo` or `--github` |
| deploy key | a service gets none without `--deploy-key` |

## How to run it

1. Always run the dry run first, without `--apply`, and read every row.
2. IF every row is `ok`, THEN report that the account is already as asked and stop.
3. IF a row is `CONFLICT`, THEN report it to the owner and stop; do not work around it.
4. IF a row is `CREATE`, `ADD` or `EDIT`, THEN show the owner every row as a table.
5. IF the output has a "Defaults" block, THEN show it as a second table: setting, value, reason.
6. After you show the tables, ask the owner with AskUserQuestion: "Apply" or "Do not apply".
7. IF the owner wants a default changed, THEN rerun the dry run with that value given, and ask again.
8. Ask in the same turn as the dry run. Do not wait for the owner to type a request.
9. IF the owner chooses "Apply", THEN run the same command with `--apply`.
10. IF the owner chooses anything else, THEN stop and write nothing.
11. After `--apply`, paste the final output and the exit code.
12. IF the output ends with a deploy key, THEN show the owner the public key and the clone line.
13. IF the exit code is 2, THEN report the refusal message; do not work around it.

**Why:** `--apply` changes accounts and sudo rights on a shared host, and invoking the skill
does not approve rows the owner has not seen. An account is never renumbered: files owned by
the old uid would be orphaned and the services running as it would break.

Rows: `ok` already so, `CREATE` makes it, `ADD` adds a group membership or keys, `EDIT`
replaces the sudoers file (the note shows the old line), `CONFLICT` cannot be applied. Exit 0
conforms or was applied and verified, 1 changes pending, a conflict, or a failed verify, 2
could not decide (a bad argument, ssh, sudo, or the GitHub keys).

## What it leaves alone

It makes accounts only. Cloning a repo, a virtualenv, `/opt` ownership, `.aws` and `.env`
files belong to deploying a repo, not to a user. Removing an account, a group membership or a
key is done by hand: the script only adds.
