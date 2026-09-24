# Changelog

All notable changes to this configuration are recorded here, in the style of
[Keep a Changelog](https://keepachangelog.com). The version lives in `VERSION`.

## [Unreleased]

## 0.1.0 - 2026-09-24

### Added
- **The base Claude Code configuration** under `dot-claude/`: permissions, the auto-mode
  policy, UI preferences and generic commands, installed into `~/.claude` by a one-command
  download.
- **A `pre-push` hook that refuses any push outside the GitHub owners** listed in the private
  `~/.claude/orgs.json`, and every push when that file is missing or malformed.
