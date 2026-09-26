#!/usr/bin/env python3
"""PreToolUse hook: auto-allow a compound Bash command (`a && b`, `a | b`, `a; b`) when every
part matches an `allow` rule and no part matches a `deny` or `ask` rule.

Claude Code prompts for a compound command even when each part is allowed on its own; this
hook answers that prompt. When it is not sure, it prints nothing and Claude Code decides as it
would without the hook: prompt, block, or allow. README.md, "Auto-approving compound
commands", lists what should pass and what should not, and test_compound_allow.py holds each
example.

Rules come from ~/.claude/settings.json and the project's .claude/settings.json and
.claude/settings.local.json. `Bash(ls *)` is read as the glob `ls *`, matched against the
start of each part, and covers a bare `ls`, as Claude Code's own matching does.
"""

import json
import shlex
import sys
from fnmatch import fnmatchcase
from pathlib import Path

PUNCT = "();<>|&\n"
SPLIT = {"&&", "||", ";", "|", "&", "|&", "\n"}
HARMLESS = {(">&", "1"), (">&", "2"), (">", "/dev/null"), (">>", "/dev/null")}


def rules(cwd, key):
    """The Bash patterns under `permissions.<key>` in every settings file that applies."""
    found = []
    for path in (Path.home() / ".claude/settings.json", cwd / ".claude/settings.json",
                 cwd / ".claude/settings.local.json"):
        try:
            entries = json.loads(path.read_text()).get("permissions", {}).get(key, [])
        except (OSError, ValueError, AttributeError):
            continue
        for rule in entries:
            if isinstance(rule, str) and rule.startswith("Bash(") and rule.endswith(")"):
                found.append(rule[5:-1].removesuffix("*").removesuffix(":"))
    return found


def without_comments(cmd):
    """cmd with its comments cut, or None when it runs a command substitution.

    shlex drops quotes, so the two things that depend on them are read here first. A `#`
    starts a comment only at the start of an unquoted word, so `a#b`, `"#"` and `${#x}` are
    text. `$(` or a backtick runs a command unless it is single-quoted or escaped.
    """
    out, single, double, word_start, i = [], False, False, True, 0
    while i < len(cmd):
        c = cmd[i]
        if single:
            single = c != "'"
        elif c == "\\":
            out.append(cmd[i:i + 2])
            i, word_start = i + 2, False
            continue
        elif c == "`" or cmd.startswith("$(", i):
            return None
        elif double:
            double = c != '"'
        elif c == "#" and word_start:
            while i < len(cmd) and cmd[i] != "\n":
                i += 1
            continue
        elif c in "'\"":
            single, double = c == "'", c == '"'
        out.append(c)
        word_start = not (single or double) and c in " \t\n;&|()<>"
        i += 1
    return "".join(out)


def parts(cmd):
    """The command split at its operators, or None when it is not sure what would run.

    Unsure means a command substitution, a subshell, a file redirect other than HARMLESS, a
    heredoc, or an unclosed quote.
    """
    cmd = without_comments(cmd)
    if cmd is None:
        return None
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=PUNCT)
    lexer.whitespace_split, lexer.whitespace, lexer.commenters = True, " \t\r", ""
    try:
        tokens = list(lexer)
    except ValueError:  # an unclosed quote
        return None
    found, words = [], []
    pairs = zip(tokens, tokens[1:] + [""])
    for token, following in pairs:
        if token in SPLIT:
            found.append(words)
            words = []
        elif (token, following) in HARMLESS:
            next(pairs)  # the redirect's target goes with it
        elif set(token) <= set(PUNCT):
            return None
        else:
            words.append(token)
    found.append(words)
    return [" ".join(w) for w in found if w]


def matches(part, patterns):
    # The appended space lets `ls *` cover a bare `ls` but not `lsof`.
    return any(fnmatchcase(part + " ", pattern + "*") for pattern in patterns)


def main():
    try:
        event = json.load(sys.stdin)
        command = event["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        return
    cwd = Path(event.get("cwd") or ".")
    allow = rules(cwd, "allow")
    stop = rules(cwd, "deny") + rules(cwd, "ask")
    split = parts(command) if allow and command else None
    if split and all(matches(p, allow) and not matches(p, stop) for p in split):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Every part of the compound command matches an allow "
                                        "rule, and none a deny or ask rule",
        }}))


if __name__ == "__main__":
    main()
