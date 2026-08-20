"""#281 — CI's Postgres wait must probe TCP, not the unix socket.

The `postgres:16` entrypoint runs initdb against a TEMPORARY server started with
`-c listen_addresses=''` (socket only, no TCP), stops it with `pg_ctl -m fast -w
stop`, then starts the real one. So `pg_isready` with no `-h` reports READY
against a server that is about to disappear, the wait loop breaks early, and the
schema load lands in the shutdown window:

    FATAL:  the database system is shutting down

That is what turned the push to main for #280 red. The window is sub-second, so
it fails perhaps one run in many and a re-run goes green — which is precisely why
the guard is a test rather than a comment. The temp server never listens on TCP
at all, so a TCP probe cannot see it: the race is removed, not narrowed.

Nothing here can run a workflow, so these are assertions about the FILE that
drives it.

⚠️ Skips when the file is absent, naming `.dockerignore`, matching
`test_deploy_pinning.py`. `.github/` is not currently excluded from the shipped
image, so this guard is for symmetry — but the suite runs inside that image
whenever the Dockerfile, requirements or tests change (#218), and a test that
reads a repo file must never assert the image is wrong when it is right.

⚠️ Deliberately parsed as TEXT, not with `yaml.safe_load`. PyYAML is not in
`requirements.txt` or `requirements-dev.txt` — it resolves locally but is not
guaranteed inside the shipped image, and an ImportError there would be exactly
the class of failure the note above is about.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WF = REPO_ROOT / ".github/workflows/ci.yml"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

STEP_NAME = "Start Postgres and load the schema"

# Every pg_isready call, with the rest of its line, so the flags can be read.
_PROBE = re.compile(r"^\s*.*?\bpg_isready\b(?P<flags>[^\n]*)$", re.M)


def _step_body():
    """The one step's `run:` block, isolated from the rest of the workflow.

    Sliced from its `- name:` to the next step at the same indentation. Matching
    the whole file instead would let a `-h` anywhere in ci.yml — including in a
    comment, or in one of the service-container health checks this test is not
    about — satisfy an assertion about THIS step.
    """
    text = CI_WF.read_text()
    start = text.find(f"- name: {STEP_NAME}")
    assert start != -1, (
        f"ci.yml no longer has a step named {STEP_NAME!r}; this guard is "
        "asserting about a step that moved or was renamed (#281)")
    nxt = re.compile(r"^      - name: ", re.M).search(text, start + 1)
    return text[start:nxt.start() if nxt else len(text)]


@pytest.mark.skipif(not CI_WF.exists(), reason=_NOT_IN_IMAGE)
def test_the_readiness_probe_does_not_use_the_unix_socket():
    """The whole of #281. Stated as a property of every probe in the step rather
    than as one expected string: a socket probe is the bug whatever else the
    line does, and adding a second unguarded probe would reintroduce it."""
    body = _step_body()
    probes = _PROBE.findall(body)
    assert probes, "the step no longer waits for Postgres at all (#281)"

    socket_probes = [p for p in probes if not re.search(r"-h[= ]?\S", p)]
    assert not socket_probes, (
        "pg_isready without -h probes the unix socket, where the initdb temp "
        "server answers READY just before it is shut down, so the schema load "
        f"fails with 'the database system is shutting down' (#281): {socket_probes}")


@pytest.mark.skipif(not CI_WF.exists(), reason=_NOT_IN_IMAGE)
def test_an_exhausted_wait_fails_instead_of_loading_anyway():
    """A bounded loop that falls through on exhaustion runs the schema load
    against a database that never came up, so the step fails reporting whatever
    psql happened to say. That is how #281 reached the log as one confusing line
    rather than a timeout naming the real problem."""
    body = _step_body()
    assert "exit 1" in body, (
        "the wait loop has no failure path — on exhaustion it falls through and "
        "loads the schema anyway, hiding the real cause behind a psql error (#281)")
