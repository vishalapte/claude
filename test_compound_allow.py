"""Tests for dot-claude/hooks/compound-command-allow.py. Run: python3 -m unittest test_compound_allow

The rules come from dot-claude/settings.json, the file ./install ships. Set
SETTINGS=~/.claude/settings.json to test the installed copy instead. Each `Bash(...)` rule is
turned into a command that matches it (`ls *` becomes `ls x`), and the test checks:

- every allow rule: `x && x` is approved;
- every allow rule ending in ` *`: the bare command (`ls`) is covered too, as in Claude Code;
- every ask and deny rule: `<allowed command> && <this command>` is not approved. The same
  rule is added to `allow` for this check, so the only thing that can stop approval is the
  hook reading `ask` and `deny`.

PARSER_APPROVED and PARSER_LEFT_TO_CLAUDE_CODE list commands whose answer does not depend on
the rules: pipes and `2>&1` the hook must approve, and `$(...)`, redirects to files, heredocs
and unclosed quotes it must never approve. README.md points here rather than repeating them.
"""

import json
import os
import subprocess
import tempfile
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOOK = Path(os.environ.get("HOOK", ROOT / "dot-claude" / "hooks" / "compound-command-allow.py"))
SETTINGS = Path(os.environ.get("SETTINGS", ROOT / "dot-claude" / "settings.json")).expanduser()
PERMISSIONS = json.loads(SETTINGS.read_text())["permissions"]
NOT_ALLOWED = "no-rule-allows-this"


def bash(key):
    """The glob inside every `Bash(...)` rule under `permissions.<key>`."""
    return [r[5:-1] for r in PERMISSIONS.get(key, []) if r.startswith("Bash(") and r.endswith(")")]


def sample(glob):
    """A command that matches `glob`, by construction: a trailing ` *` becomes ` x`, any other
    trailing or leading `*` matches nothing, and an inner `*` becomes `x`."""
    if glob.endswith(" *"):
        glob = glob[:-1] + "x"
    return glob.removesuffix("*").removeprefix("*").replace("*", "x")


def covered(command, globs):
    """The first glob `command` matches, reading `ls *` as covering a bare `ls`, or None."""
    return next((g for g in globs if fnmatchcase(command, g)
                 or (g.endswith(" *") and command == g[:-2])), None)


ALLOW, STOP = bash("allow"), bash("ask") + bash("deny")
ARGS = [g for g in ALLOW if g.endswith(" *") and not covered(sample(g), STOP)]
assert len(ARGS) >= 2, f"{SETTINGS} needs two allow rules ending in ` *` for the parser cases"
A, B = sample(ARGS[0]), sample(ARGS[1])
A_WORD = ARGS[0][:-2]

PARSER_APPROVED = [
    (f"{A} && {B}", "both parts allowed"),
    (f"{A} | {B}", "a pipe; both parts allowed"),
    (f"{A}; {B}", "both parts allowed"),
    (f"{A} 2>&1 | {B}", "`2>&1` only merges error output into normal output"),
    (f"{A} >/dev/null && {B}", "output thrown away, no file written"),
    (f"{A} 'a && b > c $(d)' && {B}", "inside single quotes, `&&`, `>` and `$(` are text"),
    (f'{A} "it\'s here" && {B}', "an apostrophe inside double quotes is text"),
    (f"{A} # && {NOT_ALLOWED}", "a `#` that starts a word begins a comment, which never runs"),
]

PARSER_LEFT_TO_CLAUDE_CODE = [
    (f"{A} && {NOT_ALLOWED}", "one part has no allow rule"),
    (f"{A_WORD}of && {B}", f"`{ARGS[0]}` does not cover `{A_WORD}of`"),
    (f"{A} $({NOT_ALLOWED})", "`$(...)` runs a second command no rule was checked against"),
    (f'{A} "$({NOT_ALLOWED})"', "`$(...)` still runs inside double quotes"),
    (f"{A} `{NOT_ALLOWED}`", "a backtick is the older spelling of `$(...)`"),
    (f"{A} <({NOT_ALLOWED})", "`<(...)` runs a command too"),
    (f"({NOT_ALLOWED}) && {A}", "parentheses run a command in a subshell"),
    (f"{A} > out.txt", "writes a file"),
    (f"{A} >> out.txt", "writes a file"),
    (f"{A} < f", "reads a file"),
    (f"{A} <<EOF\nb\nEOF", "a heredoc"),
    (f"{A} & {NOT_ALLOWED}", f"a single `&` starts `{NOT_ALLOWED}` as a second command"),
    (f"{A}\n{NOT_ALLOWED}", "a new line starts a new command"),
    (f"{A} a#b; {NOT_ALLOWED}", "a `#` inside a word is not a comment, so the second part runs"),
    (f'{A} "#"; {NOT_ALLOWED}', "a quoted `#` is not a comment either"),
    (f"{A} a\\ #b; {NOT_ALLOWED}", "`\\ ` makes the space part of the word, so this `#` is inside it"),
    (f"{A} ${{#x}}; {NOT_ALLOWED}", "`${#x}` is a length, not a comment"),
    (f"{A} it's && {B}", "the quote is never closed"),
]


class CompoundAllow(unittest.TestCase):
    def decide(self, command, permissions=PERMISSIONS):
        """The hook's decision for `command` under `permissions`: "allow", or None when it
        says nothing."""
        with tempfile.TemporaryDirectory() as scratch:
            home = Path(scratch)
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(json.dumps({"permissions": permissions}))
            out = subprocess.run(
                [str(HOOK)], input=json.dumps({"tool_input": {"command": command}, "cwd": str(home)}),
                env={"HOME": str(home), "PATH": os.environ["PATH"]}, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr, "", "the hook must not crash, even when it has no answer")
        return json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] if out.stdout.strip() else None

    def test_every_allow_rule(self):
        for glob in ALLOW:
            command = sample(glob)
            shadow = covered(command, STOP)
            with self.subTest(rule=glob, command=command, shadowed_by=shadow):
                self.assertEqual(self.decide(f"{command} && {command}"), None if shadow else "allow")

    def test_every_allow_rule_covers_the_bare_command(self):
        for glob in ALLOW:
            bare = glob[:-2]
            if not glob.endswith(" *") or covered(bare, STOP):
                continue
            with self.subTest(rule=glob, command=bare):
                self.assertEqual(self.decide(f"{bare} && {A}"), "allow")

    def test_every_ask_and_deny_rule(self):
        vetoed = {**PERMISSIONS, "allow": PERMISSIONS.get("allow", []) + [f"Bash({g})" for g in STOP]}
        for glob in STOP:
            command = sample(glob)
            with self.subTest(rule=glob, command=command):
                self.assertIsNone(self.decide(f"{A} && {command}", vetoed))

    def test_parser_approved(self):
        for command, why in PARSER_APPROVED:
            with self.subTest(command=command, why=why):
                self.assertEqual(self.decide(command), "allow")

    def test_parser_left_to_claude_code(self):
        for command, why in PARSER_LEFT_TO_CLAUDE_CODE:
            with self.subTest(command=command, why=why):
                self.assertIsNone(self.decide(command))


if __name__ == "__main__":
    unittest.main()
