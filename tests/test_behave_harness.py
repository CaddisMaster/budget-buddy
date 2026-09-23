"""#356 — the behave scenarios run, fail loudly, and cannot run as nothing.

Each test here is one of the pilot's acceptance criteria, made mechanical:

- `./test.sh` runs the scenarios before pytest, in the slot ruff uses, and a
  failing scenario stops the run;
- CI runs them too — it calls pytest directly, so `test.sh` alone is not enough;
- a run that selects ZERO scenarios fails rather than passing;
- behave's rows can never be torn down by pytest, or the other way round;
- behave does not ship in the production image.

⚠️ The zero-scenario tests RUN the wrapper, in a subprocess, against features
written into `tmp_path`, rather than asserting on its source. A guard that is
only read is a guard nobody has seen fail; these watch it fail. A subprocess
because behave's step registry is process-global — two in-process runs with
different step files would collide on the second.

Every test reading a repo file skips when it is absent, naming `.dockerignore`:
`test.sh` is stripped from the shipped image, where the suite also runs.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import TEST_PREFIX

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_SH = REPO_ROOT / "test.sh"
CI = REPO_ROOT / ".github/workflows/ci.yml"
ENVIRONMENT = REPO_ROOT / "tests/features/environment.py"
REQUIREMENTS_DEV = REPO_ROOT / "requirements-dev.txt"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"


# ---------------------------------------------------------------------------
# The wrapper cannot pass by running nothing
# ---------------------------------------------------------------------------

_STEPS = (
    "from behave import given, then\n"
    "@given('all is well')\n"
    "def given_well(context): pass\n"
    "@then('it {outcome}')\n"
    "def then_outcome(context, outcome):\n"
    "    assert outcome == 'passes', outcome\n"
)


def _features(tmp_path, feature_text):
    (tmp_path / "steps").mkdir()
    (tmp_path / "steps" / "steps.py").write_text(_STEPS)
    (tmp_path / "under_test.feature").write_text(feature_text)
    return tmp_path


def _run(features_dir, *args):
    return subprocess.run(
        [sys.executable, "-m", "tests.run_behave", str(features_dir), *args],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )


def test_a_passing_scenario_passes(tmp_path):
    """The positive control. Without it, every test below could pass because the
    wrapper fails on EVERYTHING — which would read as all guards holding."""
    result = _run(_features(tmp_path, (
        "Feature: f\n"
        "  Scenario: s\n"
        "    Given all is well\n"
        "    Then it passes\n"
    )))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_failing_scenario_fails_the_run(tmp_path):
    result = _run(_features(tmp_path, (
        "Feature: f\n"
        "  Scenario: s\n"
        "    Given all is well\n"
        "    Then it fails\n"
    )))
    assert result.returncode != 0
    assert "ZERO scenarios" not in result.stderr, "failed for the wrong reason"


def test_a_filter_that_selects_nothing_fails(tmp_path):
    """⚠️ The one behave gets wrong on its own. Measured on 1.3.3: every
    scenario filtered out exits 0, reporting "0 scenarios passed"."""
    result = _run(_features(tmp_path, (
        "Feature: f\n"
        "  Scenario: s\n"
        "    Given all is well\n"
        "    Then it passes\n"
    )), "--tags=@matches-nothing")
    assert result.returncode != 0
    assert "ZERO scenarios" in result.stderr, result.stderr


def test_a_feature_with_no_scenarios_fails(tmp_path):
    result = _run(_features(tmp_path, "Feature: f\n  Just a description.\n"))
    assert result.returncode != 0
    assert "ZERO scenarios" in result.stderr, result.stderr


def test_a_scenario_inside_a_rule_is_counted(tmp_path):
    """A feature written entirely under `Rule:` blocks must count as having run.

    The wrapper counts via `walk_scenarios()`, which descends into Rules;
    counting `feature.scenarios` instead is the tempting shortcut, and that
    would read this feature as zero scenarios and fail it for nothing."""
    result = _run(_features(tmp_path, (
        "Feature: f\n"
        "  Rule: r\n"
        "    Scenario: s\n"
        "      Given all is well\n"
        "      Then it passes\n"
    )))
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# test.sh runs them, in the right place
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)
def test_test_sh_runs_behave_inside_the_lock_and_before_the_exec():
    """Asserted as POSITIONS. A behave call placed after `exec` would satisfy a
    substring assertion and never run, and one placed before the `flock` would
    run unguarded against a concurrent suite."""
    text = TEST_SH.read_text()
    lock = text.index("flock -n 9")
    behave = text.index("python -m tests.run_behave")
    final_exec = text.rindex('exec $RUNNER "${PYTEST_ARGS[@]}"')
    assert lock < behave < final_exec


@pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)
def test_test_sh_stops_on_a_failing_scenario_and_has_an_escape_hatch():
    text = TEST_SH.read_text()
    block = text[text.index("python -m tests.run_behave") - 200:text.rindex("exec $RUNNER")]
    assert "if ! $RUNNER python -m tests.run_behave" in block
    assert "exit 1" in block
    assert "SKIP_BDD" in text


@pytest.mark.skipif(not TEST_SH.exists(), reason=_NOT_IN_IMAGE)
def test_test_sh_probes_the_container_for_behave():
    """Same reason #264 probes for ruff: a container predating the dependency
    falls through to the rebuilding path instead of failing every run."""
    probe = re.search(r"web_has_dev_deps\(\) \{(.*?)\n\}", TEST_SH.read_text(), re.S)
    assert probe and "behave" in probe.group(1)


# ---------------------------------------------------------------------------
# CI runs them, both places it runs the suite
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not CI.exists(), reason=_NOT_IN_IMAGE)
def test_the_ci_tests_job_runs_behave():
    """ci.yml calls pytest directly, never through test.sh — so without its own
    step, CI would stay green having run no scenario at all."""
    text = CI.read_text()
    job = text[text.index("    name: Tests\n"):text.index("    name: Migrations apply")]
    assert "run: python -m tests.run_behave" in job


@pytest.mark.skipif(not CI.exists(), reason=_NOT_IN_IMAGE)
def test_the_in_image_run_fails_on_a_failing_scenario():
    """That script is several commands in one `sh -c`. Without `set -e` the
    step reports only the last one, and a failing behave run would pass."""
    text = CI.read_text()
    step = text[text.index("- name: Test suite runs inside the shipped image"):]
    step = step[:step.index("- name: Confirm what the image test covered")]
    # Anchored to the directive's own line: the comment above it explains
    # `set -e` by name, and an unanchored search found the comment and passed
    # with the directive deleted.
    directive = re.search(r"^\s*set -e\s*$", step, re.M)
    assert directive, "the in-image script no longer sets -e"
    assert directive.start() < step.index("python -m tests.run_behave")


@pytest.mark.skipif(not CI.exists(), reason=_NOT_IN_IMAGE)
def test_behave_is_asserted_absent_from_the_shipped_image():
    text = CI.read_text()
    step = text[text.index("- name: Shipped image carries no test dependencies"):]
    step = step[:step.index("\n      - name:", 1)]
    assert re.search(r"for dep in .*\bbehave\b", step), step


# behave in COMMAND position: `python -m behave`, or `behave` as the first word
# after a separator, `$RUNNER`, a YAML `run:` or a compose service name.
# ⚠️ Anchored on purpose: the first cut matched any whitespace-bounded "behave"
# and failed on test.sh's own `echo "→ Skipping behave …"` — a guard that
# fires on prose gets deleted, so it must match invocations and nothing else.
_BARE_BEHAVE = re.compile(
    r"python3?\s+-m\s+behave\b|(?:^|[;&|]|\$RUNNER|\brun:|\bweb)\s*behave\b")


@pytest.mark.skipif(not (CI.exists() and TEST_SH.exists()), reason=_NOT_IN_IMAGE)
def test_nothing_invokes_bare_behave():
    """Bare `behave` exits 0 on a run that selects nothing — only the wrapper
    makes that a failure, so every invocation has to go through it."""
    for path in (TEST_SH, CI):
        for line in path.read_text().splitlines():
            code = line.split("#", 1)[0]
            assert not _BARE_BEHAVE.search(code), (
                f"{path.name}: `{line.strip()}` runs behave directly — use "
                "`python -m tests.run_behave`"
            )


@pytest.mark.skipif(not REQUIREMENTS_DEV.exists(), reason=_NOT_IN_IMAGE)
def test_behave_is_pinned():
    assert re.search(r"^behave==\d", REQUIREMENTS_DEV.read_text(), re.M)


# ---------------------------------------------------------------------------
# The two runners cannot collide
# ---------------------------------------------------------------------------


def _behave_prefix():
    tree = ast.parse(ENVIRONMENT.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "BEHAVE_PREFIX":
            return node.value.value
    raise AssertionError("environment.py no longer defines BEHAVE_PREFIX")


def test_behave_and_pytest_never_share_a_prefix():
    """Rows are owned by username prefix, and `_delete_user` matches whole
    names — but a sweep by `LIKE prefix%` is one edit away. Neither prefix may
    start the other, so no pytest name can ever be a behave name or vice versa.

    Read from the file rather than imported: behave loads `environment.py`
    itself, and an import here would be a second copy of it.
    """
    behave = _behave_prefix()
    # "__pytest__" + the xdist worker id — the base is the `__word__` part.
    pytest_base = re.match(r"__[a-z]+__", TEST_PREFIX).group()
    assert behave and pytest_base
    assert not behave.startswith(pytest_base)
    assert not pytest_base.startswith(behave)
