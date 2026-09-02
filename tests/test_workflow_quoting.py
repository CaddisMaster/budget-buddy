"""The deploy workflows must not execute their own comments (#348).

Both `release.yml` and `rollback.yml` hand their remote script to `ssh` as a
single DOUBLE-QUOTED argument. Inside double quotes bash performs command
substitution regardless of a leading `#` — a `#` in that string is a comment to
the *remote* shell and plain text to the local one. So a backtick pair anywhere
in it, comment or not, runs on the GitHub runner at deploy time and its output
is spliced into the command that is then sent to the Droplet.

That is not hypothetical: the `v0.9.0` deploy log carries

    /home/runner/work/_temp/….sh: line 31: /healthz: No such file or directory

from an unescaped ``/healthz`` inside a comment in `release.yml`. Harmless only
because `/healthz` is not a command. `rollback.yml` had sixteen unescaped
backticks including ``printenv``, which splices the step's whole environment —
`SSH_KEY` among it — into the string sent over the wire, and being multi-line it
breaks out of the `#` comment it sits in on the remote side.

The convention that prevents this (`\\`` inside the string) was already used
three times in `release.yml`; it was followed unevenly, which is exactly the
kind of rule that wants a test rather than a comment.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# `.github/` is NOT stripped by .dockerignore (proven by #333, which found
# .github/pull_request_template.md inside the shipped image), so these files are
# readable in-image too and this module needs no skipif guard.


def remote_command_regions(text):
    """Every remote command string in a workflow, as (start_line, end_line, text).

    A region opens on a line ending `"set -e` — the opening quote of the ssh
    argument — and closes at the first later line ending in an UNescaped `"`.
    The escaped-quote test is what keeps ordinary body lines like
    ``echo \\"pinned …\\"`` from being mistaken for the end of the string.
    """
    lines = text.splitlines()
    regions = []
    i = 0
    while i < len(lines):
        if lines[i].rstrip().endswith('"set -e'):
            start = i
            j = i + 1
            while j < len(lines):
                stripped = lines[j].rstrip()
                if stripped.endswith('"') and not stripped.endswith('\\"'):
                    break
                j += 1
            regions.append((start + 1, j + 1, "\n".join(lines[start:j + 1])))
            i = j
        i += 1
    return regions


def all_regions():
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for start, end, body in remote_command_regions(path.read_text()):
            found.append((path.name, start, end, body))
    return found


REGIONS = all_regions()
IDS = [f"{name}:{start}-{end}" for name, start, end, _ in REGIONS]


def test_the_scan_actually_found_the_remote_commands():
    """Anti-vacuity. Every assertion below is 'no match found in this region',
    which a region-finder that silently returned nothing would satisfy forever —
    the shape of guard this repo has already been bitten by twice.
    """
    assert len(REGIONS) >= 3, (
        f"expected the ssh remote-command strings in release.yml (2) and "
        f"rollback.yml (1); found {len(REGIONS)}: {IDS}"
    )
    names = {name for name, _, _, _ in REGIONS}
    assert {"release.yml", "rollback.yml"} <= names, names
    for name, start, end, body in REGIONS:
        assert "cd /opt/budget-buddy" in body, (
            f"{name}:{start}-{end} does not look like a remote command string"
        )


@pytest.mark.parametrize("name,start,end,body", REGIONS, ids=IDS)
def test_no_unescaped_backtick_in_a_remote_command(name, start, end, body):
    """A bare backtick pair here runs on the RUNNER, not the Droplet."""
    bare = [m.group(0).replace("\n", " ") for m in
            re.finditer(r"(?<!\\)`[^`]*(?<!\\)`", body)]
    assert not bare, (
        f"{name}:{start}-{end} carries {len(bare)} unescaped backtick pair(s), "
        f"which bash runs locally before ssh sends the string: {bare}. "
        f"Escape them as \\` — the convention already used elsewhere in the "
        f"same string."
    )


@pytest.mark.parametrize("name,start,end,body", REGIONS, ids=IDS)
def test_no_unescaped_command_substitution_in_a_remote_command(name, start, end, body):
    """`$(…)` is the same defect in the other syntax.

    Clean today, and asserted so it stays that way — the fix for the backticks
    would otherwise leave the identical hole one keystroke away. Note `${VAR}`
    is deliberately NOT covered: the workflows interpolate their own env vars
    into the string on purpose, which is why the pattern is anchored to `$(`.
    """
    bare = [m.group(0) for m in re.finditer(r"(?<!\\)\$\([^)]*\)", body)]
    assert not bare, (
        f"{name}:{start}-{end} carries unescaped command substitution {bare}, "
        f"which bash runs locally before ssh sends the string. Escape as \\$(…)."
    )
