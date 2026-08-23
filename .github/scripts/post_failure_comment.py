"""
TEMPORARY debugging aid - see the "Post failure diagnostics as a commit
comment" step in .github/workflows/ci.yml for why this exists. Delete
both this file and that step once CI is green again; this is not meant
to be a permanent part of the pipeline.

Posts the tail of pytest's output as a commit comment via the plain
GitHub REST API (readable at
GET /repos/{repo}/commits/{sha}/comments), since the raw Actions step
log is only reachable via a signed Azure Blob Storage URL that isn't
reachable from wherever this failure is being investigated.
"""

from __future__ import annotations

import json
import os
import urllib.request

MAX_CHARS = 60_000


def main() -> None:
    log_path = "pytest-output.log"
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as f:
            output = f.read()
        output = output[-MAX_CHARS:]
    else:
        output = "(no pytest-output.log captured)"

    python_version = os.environ.get("MATRIX_PYTHON_VERSION", "?")
    calendar_extras = os.environ.get("MATRIX_CALENDAR_EXTRAS", "?")
    body = (
        f"### CI failure diagnostics: py{python_version}, "
        f"calendar-extras={calendar_extras}\n\n"
        f"```\n{output}\n```"
    )

    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    token = os.environ["GITHUB_TOKEN"]

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/commits/{sha}/comments",
        data=json.dumps({"body": body}).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(resp.status, resp.read().decode()[:500])


if __name__ == "__main__":
    main()
