#!/usr/bin/env python3
"""Exit 0 only if the push URL in argv[1] is a GitHub repo whose owner is in the org list.

The list is private data, kept out of this repo in ~/.claude/orgs.json, e.g.
["my-org", "my-github-user"]. Anything short of that proof exits non-zero, including a
missing or malformed list and any exception, so the absence of the config protects.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ORGS = Path(os.environ.get("ORG_BOUNDARY_FILE", Path.home() / ".claude" / "orgs.json"))
GITHUB_HOSTS = {"github.com", "ssh.github.com"}  # ssh.github.com: SSH over port 443
URL = re.compile(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)[:/](?:\d+/)?([^/]+)/[^/]+?(?:\.git)?/?$")


def github(host: str) -> bool:
    if host.lower() in GITHUB_HOSTS:
        return True
    ssh = subprocess.run(["ssh", "-G", host], capture_output=True, text=True).stdout
    resolved = dict(line.split(" ", 1) for line in ssh.splitlines() if " " in line)
    return resolved.get("hostname", "").lower() in GITHUB_HOSTS


orgs = json.loads(ORGS.read_text(encoding="utf-8"))
if not (isinstance(orgs, list) and orgs and all(isinstance(o, str) and o for o in orgs)):
    sys.exit(f"org_boundary: {ORGS} must be a non-empty JSON list of GitHub owners")
m = URL.match(sys.argv[1])
if not (m and github(m[1])):
    sys.exit(f"org_boundary: {sys.argv[1]} is not a GitHub repo")
if m[2].lower() not in {o.lower() for o in orgs}:
    sys.exit(f"org_boundary: owner {m[2]!r} is not in {ORGS}")
