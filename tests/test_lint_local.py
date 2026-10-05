"""#264 — lint fails where it is cheap to fix, not four minutes away in CI.

`./test.sh` is documented as the one path every test run goes through, but it
ran pytest and nothing else. Ruff was absent from the dev container, so an
unused import was invisible locally and turned CI red. #263 orphaned four
imports in `goals.py`; the local suite was green, CI failed on F401, and the
whole pipeline re-ran for a one-line fix.

These are assertions about the FILES that drive the two runs, since nothing here
can start a container or a workflow. The load-bearing one is
`test_ci_reads_the_ruff_version_from_requirements_dev` (#426, which replaced
the two- then three-way pin comparison): pinning ruff locally while CI installs
whatever is newest gives back the very property this issue exists to establish —
that a green local run predicts a green remote one.

⚠️ Every test that reads a repo file SKIPS when the file is absent, naming
`.dockerignore`, per the #176 convention in `test_deploy_pinning.py`. **`test.sh`
is genuinely excluded from the shipped image**, and CI runs this suite inside
that image whenever the Dockerfile, requirements or tests change — which this
change touches, so that run really happens. `requirements-dev.txt` and
`.github/` are not excluded; they carry the guard for symmetry.
"""
import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

TEST_SH = REPO_ROOT / "test.sh"
REQUIREMENTS_DEV = REPO_ROOT / "requirements-dev.txt"
CI_WF = REPO_ROOT / ".github/workflows/ci.yml"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# `ruff==0.16.3`, ignoring any comment line that happens to mention ruff.
_REQ_PIN = re.compile(r"^ruff==(?P<version>\S+)\s*$", re.M)
# The `version:` input of the ruff action. Since #426 there must be NONE: CI
# reads requirements-dev.txt instead. Kept so the test can say so.
# Any ref, and an optional trailing comment: since #405 the action is pinned
# `@<sha> # v4.1.0`, and test_pinned_dependencies.py owns whether it is pinned.
_ACTION_WITH = re.compile(
    r"astral-sh/ruff-action@\S+[ \t]*(?:#[^\n]*)?\n\s*with:\s*\n(?P<inputs>(?:[ \t]+\S.*\n)+)")
# Any spelling of a ruff version pin a config file could carry: a requirement
# specifier, or the ruff-pre-commit repo (whose `rev:` is one). A `version:`
# input on the action is the third, matched inside its `with:` block only —
# `version:` alone is in dependabot.yml and half the actions in ci.yml.
_ANY_RUFF_PIN = re.compile(r"^\s*(?:ruff\s*(?:==|~=|>=|<=|!=)|.*ruff-pre-commit)", re.M)
_ACTION_VERSION = re.compile(r"^\s*version:", re.M)

# The step that proves the version CI ran is the pinned one. Its `run:` block
# is EXECUTED below rather than read, so a test cannot agree with a broken script.
_VERIFY_STEP = "Ruff ran the pinned version"


def _requirements_pin():
    m = _REQ_PIN.search(REQUIREMENTS_DEV.read_text())
    assert m, "requirements-dev.txt no longer pins ruff with a bare `ruff==<version>` line"
    return m.group("version")


def _ruff_action_inputs():
    m = _ACTION_WITH.search(CI_WF.read_text())
    assert m, "ci.yml no longer runs astral-sh/ruff-action with a `with:` block"
    return m.group("inputs")


def _step_run_block(name):
    """The body of a ci.yml step's `run: |` block, dedented."""
    lines = CI_WF.read_text().splitlines()
    at = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    run_at = next(i for i in range(at, len(lines)) if lines[i].strip() == "run: |")
    body = []
    for line in lines[run_at + 1:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body)


# --- The version pin ---------------------------------------------------------

@pytest.mark.skipif(not REQUIREMENTS_DEV.exists(), reason=_NOT_IN_IMAGE)
def test_ruff_is_pinned_for_the_dev_container():
    """Pinned with `==`, so the container bakes in a known version rather than
    drifting on the next rebuild."""
    assert _requirements_pin()


@pytest.mark.criterion(426, "CI lints with the version requirements-dev.txt pins")
@pytest.mark.skipif(not CI_WF.exists(), reason=_NOT_IN_IMAGE)
def test_ci_reads_the_ruff_version_from_requirements_dev():
    """One pin, read — not two, compared.

    `astral-sh/ruff-action` installs the LATEST ruff when given no version, so a
    ruff release that tightens a rule would turn CI red against code `./test.sh`
    just passed — the failure #264 exists to remove. Until #426 CI carried its
    own `version:` literal, which Dependabot cannot see; six groups in a row
    went red on it (#285 … #419) and each needed the same hand-made commit.
    """
    inputs = _ruff_action_inputs()
    assert re.search(r"^\s*version-file:\s*requirements-dev\.txt\s*$", inputs, re.M), (
        "the ruff action must read its version from requirements-dev.txt"
    )
    assert not re.search(r"^\s*version:", inputs, re.M), (
        "ci.yml pins ruff a second time with `version:` — a copy Dependabot "
        "cannot update"
    )
    assert re.search(r"^\s*id:\s*ruff\s*$",
                     CI_WF.read_text().split("astral-sh/ruff-action@")[0].rsplit("- name:", 1)[1],
                     re.M), "the ruff step needs `id: ruff` so its version output can be read"


@pytest.mark.criterion(426, "CI lints with the version requirements-dev.txt pins")
@pytest.mark.skipif(not CI_WF.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.parametrize("ran, passes", [
    ("0.16.9", True),       # the pin
    ("0.16.10", False),     # `latest`, the action's documented fallback
    ("", False),            # the install failed and set no output
])
def test_ci_fails_unless_the_ruff_it_ran_is_the_pin(tmp_path, ran, passes):
    """⚠️ `version-file` FAILS OPEN: the action's own docs say that if parsing
    fails it "warns and falls back to `latest`". A warning in a green job is
    exactly the silent unpin #264 closed, so the step after it compares the
    version the action reports installing against the pin and fails otherwise.

    The step's script is run here, not read. Against a fixture pin of 0.16.9,
    so the rows do not need editing on a bump.
    """
    script = _step_run_block(_VERIFY_STEP)
    assert "RUFF_RAN" in script, "the verify step no longer reads the action's output"
    (tmp_path / "requirements-dev.txt").write_text("# a comment naming ruff==9.9.9\nruff==0.16.9\n")
    result = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path,
                            env={"PATH": "/usr/bin:/bin", "RUFF_RAN": ran},
                            capture_output=True, text=True)
    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@pytest.mark.skipif(not CI_WF.exists(), reason=_NOT_IN_IMAGE)
def test_the_verify_step_fails_with_no_pin(tmp_path):
    """A requirements file that stopped pinning ruff must fail the step, not
    compare an empty string with an empty output and pass."""
    (tmp_path / "requirements-dev.txt").write_text("pytest==9.1.1\n")
    result = subprocess.run(["bash", "-e", "-c", _step_run_block(_VERIFY_STEP)], cwd=tmp_path,
                            env={"PATH": "/usr/bin:/bin", "RUFF_RAN": ""},
                            capture_output=True, text=True)
    assert result.returncode != 0


@pytest.mark.criterion(426, "A Dependabot ruff bump needs no hand-made follow-up")
@pytest.mark.skipif(not REQUIREMENTS_DEV.exists(), reason=_NOT_IN_IMAGE)
def test_requirements_dev_is_the_only_ruff_pin():
    """Replaces `test_all_three_ruff_pins_agree` (#309 tranche 10 → #426).

    That test compared three copies and was right every time it failed — the
    mechanism was the problem, not the guard. Dependabot's pip group edits
    `requirements-dev.txt` alone, so a bump is one edit only if nothing else
    names a version. The ruff-pre-commit hook was the third copy; pre-commit
    was not installed in the working clone, and `./test.sh` lints first (#264),
    so the hook was dropped rather than kept in step.

    Swept over every config file rather than the two known ones: a guard over a
    hand-maintained list only fails for the members someone remembered to add.
    ⚠️ The positive control is requirements-dev.txt itself — if the sweep stops
    matching its pin, it would pass against any number of copies.
    """
    candidates = [REQUIREMENTS_DEV, REPO_ROOT / "requirements.txt", PRE_COMMIT,
                  REPO_ROOT / "pyproject.toml", REPO_ROOT / "Dockerfile", TEST_SH,
                  *sorted((REPO_ROOT / ".github").rglob("*.yml"))]
    hits = {}
    for path in candidates:
        if path.exists():
            text = "\n".join(line for line in path.read_text().splitlines()
                              if not line.lstrip().startswith("#")) + "\n"
            found = _ANY_RUFF_PIN.findall(text)
            found += [f"ruff-action {v.strip()}" for block in _ACTION_WITH.finditer(text)
                      for v in _ACTION_VERSION.findall(block.group("inputs"))]
            if found:
                hits[str(path.relative_to(REPO_ROOT))] = found
    assert "requirements-dev.txt" in hits, (
        "the sweep no longer finds the real pin — it is broken, and would pass "
        "against any number of copies"
    )
    assert list(hits) == ["requirements-dev.txt"] and len(hits["requirements-dev.txt"]) == 1, (
        f"ruff is pinned in more than one place: {hits}. Dependabot updates "
        "requirements-dev.txt only, so every other copy goes stale on the next bump."
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

# `from conftest import ...` / `import conftest` at the start of a line — and the
# same for `helpers`, which took conftest's plain functions in #356 and has the
# same two-name hazard. The dotted form is deliberately NOT matched by the bare
# pattern: `from tests.<module> import` is the correct spelling.
_SHARED = r"(?:conftest|helpers)"
_BARE_SHARED = re.compile(rf"^(?:from {_SHARED} import|import {_SHARED}\b)", re.M)
_DOTTED_SHARED = re.compile(rf"^from tests\.{_SHARED} import", re.M)


def _test_sources():
    """Every test module's text, keyed by filename. conftest.py itself is
    excluded — it does not import itself."""
    return {path.name: path.read_text()
            for path in sorted(TESTS_DIR.glob("test_*.py"))}


def test_the_shared_harness_is_imported_under_exactly_one_name():
    """⚠️ Two spellings of one import load the module TWICE.

    Holds for `tests/conftest.py` and, since #356, `tests/helpers.py` — which
    now holds the functions this docstring's example names.

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

    # Counted over both modules together: most files moved from `tests.conftest`
    # to `tests.helpers` in #356, and a per-module floor would fail for that
    # reason rather than because the regex stopped matching.
    dotted = sorted(n for n, text in sources.items() if _DOTTED_SHARED.search(text))
    assert len(dotted) > 20, (
        f"only {len(dotted)} files import `tests.conftest`/`tests.helpers` — the "
        "regex no longer matches the form it is meant to accept, so the check "
        "below is vacuous."
    )

    bare = sorted(n for n, text in sources.items() if _BARE_SHARED.search(text))
    assert not bare, (
        f"{bare} import the shared harness as `conftest`/`helpers` rather than "
        "`tests.conftest`/`tests.helpers`. Both resolve, and loading a module "
        "under two names creates two module objects from one file — see this "
        "test's docstring."
    )


def test_the_shared_helpers_are_runner_neutral():
    """`tests/helpers.py` is shared by pytest and behave (#356), so it may not
    depend on either runner.

    ⚠️ The prefix is the one that matters. `TEST_PREFIX` is pytest's per-worker
    `__pytest__…`; behave builds its own. A helper that read it — or imported
    anything from `tests.conftest` — would let a behave run create or tear down
    rows under pytest's prefix, which is the collision `docs/testing.md` records
    as 424 errors when the prefix was briefly hardcoded. Helpers take the
    username as an argument instead.
    """
    tree = ast.parse((TESTS_DIR / "helpers.py").read_text())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "app.db" in imported, "found no imports at all — the walk is broken"
    assert "pytest" not in imported, "tests/helpers.py imports pytest"
    assert not {m for m in imported if m.endswith("conftest")}, (
        "tests/helpers.py imports from conftest, which is pytest's own"
    )

    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"TEST_PREFIX", "USER_A", "USER_B", "USER_ADMIN"}, (
        "a helper reads pytest's prefix — take the username as an argument"
    )


def test_nothing_imports_a_helper_through_conftest():
    """Every name imported from `tests.conftest` must be DEFINED there.

    `conftest.py` imports `PASSWORD`, `_login` and friends from `tests.helpers`,
    so `from tests.conftest import PASSWORD` resolves — by accident. It breaks
    the day conftest stops needing that name, in a file nobody touched. #356
    found six such imports, all inside test bodies, which is where a
    module-level search does not look; this walks every node.
    """
    conftest = ast.parse((TESTS_DIR / "conftest.py").read_text())
    defined = {node.name for node in conftest.body if isinstance(node, ast.FunctionDef)}
    defined |= {target.id for node in conftest.body if isinstance(node, ast.Assign)
                for target in node.targets}
    assert "TEST_PREFIX" in defined and "client_a" in defined, "conftest parse is broken"

    leaks = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "tests.conftest":
                leaks += [f"{path.name}:{node.lineno} {alias.name}"
                          for alias in node.names if alias.name not in defined]
    assert not leaks, (
        f"imported through conftest but defined elsewhere: {leaks} — import each "
        "from where it lives (`tests.helpers`, `app.db`, …)"
    )
