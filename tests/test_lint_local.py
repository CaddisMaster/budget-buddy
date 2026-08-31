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


# --- One name for the shared harness (#309, tranche 8b) ----------------------

TESTS_DIR = Path(__file__).resolve().parent

# `from conftest import ...` / `import conftest` at the start of a line. The
# dotted form is deliberately NOT matched: `from tests.conftest import` is the
# correct spelling and every file should hit the second pattern below.
_BARE_CONFTEST = re.compile(r"^(?:from conftest import|import conftest\b)", re.M)
_DOTTED_CONFTEST = re.compile(r"^from tests\.conftest import", re.M)


def _test_sources():
    """Every test module's text, keyed by filename. conftest.py itself is
    excluded — it does not import itself."""
    return {path.name: path.read_text()
            for path in sorted(TESTS_DIR.glob("test_*.py"))}


def test_conftest_is_imported_under_exactly_one_name():
    """⚠️ Two spellings of one import load the module TWICE.

    `pythonpath = .` (pytest.ini) puts the repo root on `sys.path`, so
    `tests.conftest` resolves; pytest separately inserts `tests/` itself, so a
    bare `conftest` also resolves. They are the SAME FILE and two different
    module objects — proven rather than argued, when this was found:

        conftest        id=...846064  file=/app/tests/conftest.py
        tests.conftest  id=...343344  file=/app/tests/conftest.py
        same object? False
        create_transaction same func? False

    Nine files used the bare form and thirty-seven the dotted one.

    Nothing was broken by it, because every value conftest defines at module
    scope is derived deterministically from the environment — `TEST_PREFIX` is
    the same string in both copies. That is exactly why it needs a test rather
    than a comment: it is invisible until conftest grows module-level state
    that is not, and then the two copies disagree silently. The live footgun is
    narrower and available today: `monkeypatch.setattr("tests.conftest.X", ...)`
    patches one copy, and a file that imported the bare name keeps the other.

    ⚠️ The floor is what stops this going vacuous. A regex that matched nothing
    would make `assert not bare` true against a suite that had stopped
    importing the harness at all.
    """
    sources = _test_sources()
    assert len(sources) > 40, (
        f"only found {len(sources)} test modules — the glob is broken, not the "
        "suite. Without this floor the assertions below check nothing."
    )

    dotted = sorted(n for n, text in sources.items() if _DOTTED_CONFTEST.search(text))
    assert len(dotted) > 20, (
        f"only {len(dotted)} files import `tests.conftest` — the regex no longer "
        "matches the form it is meant to accept, so the check below is vacuous."
    )

    bare = sorted(n for n, text in sources.items() if _BARE_CONFTEST.search(text))
    assert not bare, (
        f"{bare} import the shared harness as `conftest` rather than "
        "`tests.conftest`. Both resolve, and loading it under two names creates "
        "two module objects from one file — see this test's docstring."
    )
