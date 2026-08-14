"""#206 — two concurrent suite runs cannot corrupt each other.

`conftest.py` builds `TEST_PREFIX` from `PYTEST_XDIST_WORKER`, which separates
workers **within one run** and not two runs from each other. Two runs both spawn
`gw0..gw9`, get identical prefixes, and create and tear down the same users —
measured at 424 errors + 1 failure when the prefix was briefly hardcoded, and it
reads as flakiness rather than as contention.

The guard lives in `test.sh` because that is the one place every path already
goes through: an agent tool call, a terminal, and `runtests` all invoke it.
`runtests`' own check cannot do this job — it reads `pane_current_command` for a
single pane, so a bare `./test.sh` from an agent's shell leaves that pane looking
idle, and it is machine-local besides.

⚠️ **Every test here SKIPS when `test.sh` is absent, naming `.dockerignore`.**
`test.sh` is genuinely excluded from the shipped image, and CI runs this suite
inside that image whenever the Dockerfile or requirements change (#176). The dev
bind-mount puts the file back, so this can never fail locally — failing in the
image would assert the image is wrong when it is right. `test_deploy_pinning.py`
is the precedent.

⚠️ These tests never let `test.sh` reach a container. Each one holds the lock
first, so the script refuses and exits **before** any docker command — which is
both the behaviour under test and what makes it safe to invoke the suite's own
runner from inside the suite.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_SH = REPO_ROOT / "test.sh"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

pytestmark = pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)


def _lock_path():
    """The lock file test.sh uses, read from the script rather than duplicated.

    Hardcoding the path here would let the two drift and leave this file passing
    against a lock nothing takes.
    """
    for line in TEST_SH.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("LOCKFILE=") or stripped.startswith("LOCK_FILE="):
            value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            return value
    pytest.fail("test.sh does not define a LOCKFILE — the lock is what #206 added")


def _run_test_sh(env=None):
    """Invoke test.sh with the lock already held, so it refuses immediately."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        [str(TEST_SH), "-k", "__nothing_matches_this__"],
        capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT), env=merged,
    )


def test_test_sh_defines_a_lock_file():
    """The property, not the path — where the lock lives is an implementation
    detail, but that one exists at all is the whole fix."""
    assert _lock_path(), "test.sh must define a lock file"


def test_a_second_run_is_refused_while_the_lock_is_held(tmp_path):
    """The load-bearing one. A held lock must stop a second run dead."""
    lock = tmp_path / "held.lock"
    with open(lock, "w") as fh:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run_test_sh({"TEST_SH_LOCKFILE": str(lock)})

    assert result.returncode != 0, (
        "a second run must fail while one is in flight\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "in progress" in combined, combined


def test_the_refusal_never_reaches_docker(tmp_path):
    """⚠️ The refusal must come BEFORE anything expensive.

    A guard that refused only after building an image would still be correct and
    would still be useless — the point is to fail in milliseconds. Asserting the
    absence of docker output is weak on its own, so this also pins that the run
    was fast enough to have skipped a build.
    """
    lock = tmp_path / "held.lock"
    with open(lock, "w") as fh:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run_test_sh({"TEST_SH_LOCKFILE": str(lock)})

    combined = (result.stdout + result.stderr).lower()
    for marker in ("using the running web container", "throwaway", "building"):
        assert marker not in combined, (
            f"test.sh got as far as {marker!r} before refusing:\n{combined}"
        )


def test_the_lock_is_an_advisory_lock_not_a_marker_file():
    """⚠️ The trap, stated as a test, because the obvious implementation is wrong.

    A guard written as "if the lock file exists, refuse" survives `kill -9` and
    wedges every later run — which is precisely the failure that gets a guard
    deleted rather than fixed. An advisory lock on a descriptor is released by
    the kernel when the holder dies, whatever killed it, so there is no stale
    state to clean up and no `rm` in a trap to forget.

    Asserted against the script's text rather than by killing a run, because the
    kernel's behaviour is not what is in doubt — the implementation choice is.
    """
    text = TEST_SH.read_text()
    assert "flock" in text, "the lock must be an advisory flock, not a marker file"

    for antipattern in ("if [ -f", "if [[ -f", "if [ -e", "if [[ -e"):
        window = text[text.find("flock") - 400:text.find("flock") + 400] if "flock" in text else ""
        assert antipattern not in window, (
            f"{antipattern} near the lock suggests an existence check, which a "
            "killed run would leave wedged"
        )


def test_the_lock_survives_the_exec():
    """⚠️ `test.sh` ends in `exec docker compose …`, which REPLACES the shell.

    A lock taken and released inside the script would be gone before a single
    test ran — a guard that protects only the setup is no guard at all. An open
    file descriptor survives `exec`, so the lock must be taken on a descriptor
    (`exec 9>…` then `flock 9`) and simply never released; the kernel drops it
    when the exec'd process finally exits.
    """
    text = TEST_SH.read_text()
    assert re.search(r"exec\s+\d+\s*>", text), (
        "the lock must be held on a numbered file descriptor so it survives the "
        "final `exec docker compose …`"
    )
    assert not re.search(r"flock\s+-u", text), (
        "an explicit unlock defeats the point — the run must hold it to the end"
    )


def test_the_lock_path_is_overridable_for_testing():
    """The tests above pass their own lock path. If test.sh ever stops honouring
    an override they would silently start contending for the real lock — and
    would then interfere with the very run executing them."""
    text = TEST_SH.read_text()
    assert "TEST_SH_LOCKFILE" in text, (
        "test.sh must honour TEST_SH_LOCKFILE so tests can use their own lock"
    )
