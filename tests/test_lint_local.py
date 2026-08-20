"""#264 — lint fails where it is cheap to fix, not four minutes away in CI.

`./test.sh` is documented as the one path every test run goes through, but it
ran pytest and nothing else. Ruff was absent from the dev container, so an
unused import was invisible locally and turned CI red. #263 orphaned four
imports in `goals.py`; the local suite was green, CI failed on F401, and the
whole pipeline re-ran for a one-line fix.

These are assertions about the FILES that drive the two runs, since nothing here
can start a container or a workflow. The load-bearing one is
`test_the_two_ruff_pins_agree`: pinning ruff locally while CI installs whatever
is newest gives back the very property this issue exists to establish — that a
green local run predicts a green remote one.

⚠️ Every test that reads a repo file SKIPS when the file is absent, naming
`.dockerignore`, per the #176 convention in `test_deploy_pinning.py`. **`test.sh`
is genuinely excluded from the shipped image**, and CI runs this suite inside
that image whenever the Dockerfile, requirements or tests change — which this
change touches, so that run really happens. `requirements-dev.txt` and
`.github/` are not excluded; they carry the guard for symmetry.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

TEST_SH = REPO_ROOT / "test.sh"
REQUIREMENTS_DEV = REPO_ROOT / "requirements-dev.txt"
CI_WF = REPO_ROOT / ".github/workflows/ci.yml"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# `ruff==0.16.3`, ignoring any comment line that happens to mention ruff.
_REQ_PIN = re.compile(r"^ruff==(?P<version>\S+)\s*$", re.M)
# The `version:` input of the ruff action, which is the only pinned version in
# ci.yml's lint job.
_ACTION_PIN = re.compile(r"astral-sh/ruff-action@v\d+\s*\n\s*with:\s*\n(?:\s*#.*\n)*\s*version:\s*(?P<version>\S+)")


def _requirements_pin():
    m = _REQ_PIN.search(REQUIREMENTS_DEV.read_text())
    assert m, "requirements-dev.txt no longer pins ruff with a bare `ruff==<version>` line"
    return m.group("version")


def _action_pin():
    m = _ACTION_PIN.search(CI_WF.read_text())
    assert m, "ci.yml's ruff-action step no longer carries a `version:` input"
    return m.group("version")


# --- The version pins ------------------------------------------------------

@pytest.mark.skipif(not REQUIREMENTS_DEV.exists(), reason=_NOT_IN_IMAGE)
def test_ruff_is_pinned_for_the_dev_container():
    """Pinned with `==`, so the container bakes in a known version rather than
    drifting on the next rebuild."""
    assert _requirements_pin()


@pytest.mark.skipif(not (REQUIREMENTS_DEV.exists() and CI_WF.exists()), reason=_NOT_IN_IMAGE)
def test_the_two_ruff_pins_agree():
    """⚠️ The load-bearing one.

    `astral-sh/ruff-action` installs the LATEST ruff when given no `version:`.
    Left that way, a ruff release that adds or tightens a rule turns CI red
    against code the local run just passed — which is the failure #264 exists to
    remove, reintroduced through the half nobody was looking at. Stated as an
    equality between the two files rather than as a literal version, so bumping
    ruff means changing both and the test does not need editing.
    """
    assert _requirements_pin() == _action_pin(), (
        "requirements-dev.txt and .github/workflows/ci.yml pin different ruff "
        "versions — a local run no longer predicts CI"
    )


# --- test.sh -----------------------------------------------------------------

@pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)
def test_test_sh_runs_ruff_before_pytest():
    """Order, not mere presence.

    ⚠️ Asserted as two positions in the file rather than as "ruff appears
    somewhere": a `ruff check` placed after the pytest invocation would satisfy
    a substring assertion and never run, because the script ends in `exec`.
    """
    body = TEST_SH.read_text()
    lint_at = body.find("python -m ruff check")
    pytest_at = body.find('exec $RUNNER "${PYTEST_ARGS[@]}"')
    assert lint_at != -1, "test.sh no longer runs ruff"
    assert pytest_at != -1, "test.sh no longer execs pytest"
    assert lint_at < pytest_at, "ruff must run BEFORE the exec that replaces the shell"


@pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)
def test_a_lint_failure_stops_the_run():
    """Option A of the three in #264: fail fast, do not warn and continue.

    A warning that can be ignored is a warning that will be ignored — which is
    how the defect reached CI to begin with. The non-zero exit is the behaviour;
    without it this whole change is decorative.
    """
    body = TEST_SH.read_text()
    assert "if ! $RUNNER python -m ruff check; then" in body
    assert "exit 1" in body.split("python -m ruff check")[1]


@pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)
def test_there_is_an_escape_hatch():
    """`SKIP_LINT=1` — so a known stray import cannot block the test signal you
    actually wanted mid-iteration, which is the one real cost of failing fast."""
    body = TEST_SH.read_text()
    assert "SKIP_LINT" in body
    assert 'if [ -n "${SKIP_LINT:-}" ]; then' in body


@pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)
def test_the_container_probe_covers_ruff_too():
    """A container built before this change has pytest but not ruff.

    Probing only pytest would send such a container down the "use the live one"
    path and then fail on the ruff invocation, turning a self-healing staleness
    into a hard error on every run. The script already knows how to repair this
    — fall through to the throwaway path, which rebuilds.
    """
    body = TEST_SH.read_text()
    assert "web_has_dev_deps" in body
    probe = body.split("web_has_dev_deps() {")[1].split("}")[0]
    assert "import pytest" in probe
    assert "ruff" in probe


@pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)
def test_the_concurrent_run_lock_is_untouched():
    """#206's flock is the guard that actually holds, and #264 runs a command
    before the `exec` for the first time. The lock is still taken on fd 9 before
    anything expensive and still never released, so it is held across the ruff
    run and inherited by the exec'd pytest exactly as before."""
    body = TEST_SH.read_text()
    assert "exec 9> \"$LOCKFILE\"" in body
    assert "flock -n 9" in body
    lock_at = body.find("flock -n 9")
    lint_at = body.find("python -m ruff check")
    assert lock_at < lint_at, "the lock must be taken before ruff runs, not after"
