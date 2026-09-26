# Changelog

All notable changes to this configuration are recorded here, in the style of
[Keep a Changelog](https://keepachangelog.com). The version lives in `VERSION`.

## [Unreleased]

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
