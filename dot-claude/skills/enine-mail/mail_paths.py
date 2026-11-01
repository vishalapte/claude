"""Where mail.py reads and writes: five paths, each from a flag or an environment variable.

The one home for them. None has a built-in default: the consumer that installs the skill sets
the variables, or the caller passes the flags, and a verb that needs a path neither gives
refuses rather than guess one. A flag wins over its variable; an empty variable is unset.
"""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from mail_store import Refusal

# name: (flag, environment variable, metavar, what it is)
NAMES = {
    "store": ("--store", "ENINE_MAIL_STORE", "DIR", "the mail store"),
    "identity": ("--identity", "ENINE_MAIL_IDENTITY", "FILE",
                 "the owner-address file, JSON {\"owner_addresses\": [...]}"),
    "senders": ("--senders", "ENINE_MAIL_SENDERS", "FILE", "the sender config (TOML)"),
    "drafts": ("--drafts", "ENINE_MAIL_DRAFTS", "DIR", "the drafts folder"),
    "sent": ("--sent", "ENINE_MAIL_SENT", "FILE", "the sent log (JSON Lines)"),
}


@dataclass(frozen=True)
class Paths:
    """The five paths as resolved; None where neither the flag nor the variable gives one."""

    store: Path | None = None
    identity: Path | None = None
    senders: Path | None = None
    drafts: Path | None = None
    sent: Path | None = None

    def need(self, verb, *names):
        """Refuse, naming each missing path's flag and variable, unless all of `names` are set."""
        missing = [name for name in names if getattr(self, name) is None]
        if missing:
            how = "; ".join(f"{NAMES[n][3].split(',')[0]}: pass {NAMES[n][0]} {NAMES[n][2]} or set {NAMES[n][1]}"
                            for n in missing)
            raise Refusal(f"{verb} needs {how}")
        return self


def resolve(args, environ=None):
    """The paths from parsed arguments (an attribute per name, when given) and the environment."""
    environ = os.environ if environ is None else environ
    found = {}
    for name, (_, variable, _, _) in NAMES.items():
        value = getattr(args, name, None) or environ.get(variable) or None
        found[name] = Path(value).expanduser() if value else None
    return Paths(**found)


def add_flags(parser, names, *, suppress=False):
    """Add the flags for `names`. On a verb's parser `suppress` leaves the top-level value alone
    when the flag is not repeated after the verb."""
    for name in names:
        flag, variable, metavar, what = NAMES[name]
        extra = {"default": argparse.SUPPRESS} if suppress else {}
        parser.add_argument(flag, dest=name, metavar=metavar, help=f"{what} (or ${variable})", **extra)
