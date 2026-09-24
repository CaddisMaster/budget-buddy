"""`scripts/check_criteria.py`: every promised Scenario is claimed (#358).

No network anywhere: the script's GitHub fetch is one function, and every test
here hands `run()` the bodies it would have fetched. The real-repo tests at the
bottom read this tree's own claims, which is how this file's markers for #358's
three scenarios are proven to be found at all.
"""
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_criteria.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "criteria.yml"


def _load():
    """Import the script by path — it is a standalone tool, not a package member."""
    spec = importlib.util.spec_from_file_location("check_criteria", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = _load()

ISSUE = """Some context.

```gherkin
Scenario: A quoted scenario from another issue
  Given it is only an illustration
```

## Acceptance criteria

```gherkin
Feature: Widgets

# A Gherkin comment at column 0 looks exactly like a markdown heading.
  Scenario: A widget posts once
    Given a widget

  Scenario Outline: A widget of <kind> renders
    Given a <kind> widget

  Scenario: A widget can be deleted
    Given a widget
```

## Out of scope

```gherkin
Scenario: Something explicitly not promised
```
"""

TITLES = ["A widget posts once", "A widget of <kind> renders", "A widget can be deleted"]


def _tree(tmp_path, feature="", test_py=""):
    features = tmp_path / "tests" / "features"
    features.mkdir(parents=True)
    if feature:
        (features / "w.feature").write_text(feature)
    if test_py:
        (tmp_path / "tests" / "test_w.py").write_text(test_py)
    return tmp_path


def _run(tmp_path, pr_body="", issues=None, **tree):
    lines = []
    code = cc.run(pr_body, {400: ISSUE} if issues is None else issues,
                  repo_root=_tree(tmp_path, **tree), out=lines.append)
    return code, "\n".join(lines)


# --- extracting the promise ---------------------------------------------------

def test_only_the_acceptance_criteria_section_counts():
    # The quoted scenario above the heading and the one under "Out of scope"
    # are not this issue's promise; the Gherkin comment is not a heading.
    assert cc.extract_criteria(ISSUE) == TITLES


def test_an_issue_form_heading_counts_too():
    # GitHub issue forms render a field label as `###`.
    body = "### Acceptance criteria\n\n```gherkin\nScenario: X happens\n```\n"
    assert cc.extract_criteria(body) == ["X happens"]


def test_an_issue_without_the_heading_promises_nothing():
    assert cc.extract_criteria("```gherkin\nScenario: Loose\n```") == []
    assert cc.extract_criteria(None) == []


def test_a_deeper_heading_stays_inside_the_section():
    body = ("## Acceptance criteria\n\n### Part one\n\n```gherkin\nScenario: A\n```\n"
            "\n## Next\n\n```gherkin\nScenario: B\n```\n")
    assert cc.extract_criteria(body) == ["A"]


def test_titles_match_loosely_on_case_space_and_a_full_stop():
    assert cc.normalize("  A Widget   posts once. ") == cc.normalize("a widget posts once")


# --- the three ways to claim ---------------------------------------------------

def test_feature_tags_claim_at_scenario_rule_and_feature_level(tmp_path):
    feature = """@issue-400
Feature: Widgets
  Scenario: A widget posts once
    Given x

  @issue-401
  Rule: Deleting
    Scenario: A widget can be deleted
      Given x

  @issue-402 @slow
  Scenario: Tagged alone
    Given x

  Scenario: After the tagged one
    Given x
"""
    claims = cc.feature_claims(_tree(tmp_path, feature=feature) / "tests" / "features")
    assert (400, "a widget posts once") in claims
    assert {(400, "a widget can be deleted"), (401, "a widget can be deleted")} <= claims
    assert (402, "tagged alone") in claims
    # A scenario-level tag does not leak onto the next scenario.
    assert (402, "after the tagged one") not in claims
    assert (401, "after the tagged one") in claims  # the Rule's tag still applies


def test_an_untagged_feature_scenario_claims_nothing(tmp_path):
    feature = "Feature: W\n  Scenario: A widget posts once\n    Given x\n"
    assert cc.feature_claims(_tree(tmp_path, feature=feature) / "tests" / "features") == set()


def test_a_criterion_marker_claims(tmp_path):
    test_py = '''import pytest

@pytest.mark.criterion(400, "A widget posts once")
@pytest.mark.parametrize("x", [1])
def test_a(x):
    pass

@pytest.mark.parametrize("x", [1])
def test_b(x):
    pass
'''
    claims = cc.pytest_claims(_tree(tmp_path, test_py=test_py) / "tests")
    assert claims == {(400, "a widget posts once")}


def test_a_non_literal_marker_is_an_error_not_a_silent_miss(tmp_path):
    test_py = 'import pytest\nT = "x"\n\n@pytest.mark.criterion(400, T)\ndef test_a():\n    pass\n'
    with pytest.raises(ValueError, match="two literals"):
        cc.pytest_claims(_tree(tmp_path, test_py=test_py) / "tests")


def test_a_pr_body_line_claims_a_hand_verified_criterion():
    body = ('Closes #400.\n\n- Verified by hand: #400 "A widget can be deleted" — docs only\n'
            'Verified by hand: 400 "missing hash is not a claim"\n')
    assert cc.hand_claims(body) == {(400, "a widget can be deleted")}


# --- #358's acceptance criteria -------------------------------------------------

@pytest.mark.criterion(358, "Criteria and tests are connected by something other than memory")
def test_every_scenario_claimed_passes_and_names_where(tmp_path):
    code, out = _run(
        tmp_path,
        pr_body='Verified by hand: #400 "A widget can be deleted" — manual',
        feature="@issue-400\nFeature: W\n  Scenario: A widget posts once\n    Given x\n",
        test_py='import pytest\n\n@pytest.mark.criterion(400, "A widget of <kind> renders")\n'
                'def test_a():\n    pass\n',
    )
    assert code == 0, out
    for title in TITLES:
        assert f"#400: ✓ {title}" in out


@pytest.mark.criterion(358, "A dropped criterion is visible")
def test_a_dropped_third_scenario_fails_and_is_named(tmp_path):
    code, out = _run(
        tmp_path,
        feature="@issue-400\nFeature: W\n  Scenario: A widget posts once\n    Given x\n"
                "  Scenario: A widget of <kind> renders\n    Given x\n",
    )
    assert code == 1, out
    assert '::error::#400: "A widget can be deleted" is not claimed' in out
    assert "#400: ✓ A widget posts once" in out


@pytest.mark.criterion(358, "Correcting a wrong specification stays cheap")
def test_correcting_the_issue_leaves_a_stale_claim_as_a_warning_only(tmp_path):
    # The spec said "posts once"; the implementation deliberately posts twice,
    # and the issue was corrected to say so. The old claim is still in the tree.
    corrected = ISSUE.replace("A widget posts once", "A widget posts twice")
    code, out = _run(
        tmp_path,
        issues={400: corrected},
        pr_body='Verified by hand: #400 "A widget posts twice"\n'
                'Verified by hand: #400 "A widget of <kind> renders"\n'
                'Verified by hand: #400 "A widget can be deleted"\n',
        feature="@issue-400\nFeature: W\n  Scenario: A widget posts once\n    Given x\n",
    )
    assert code == 0, out
    assert '::warning::#400: a claim names "a widget posts once"' in out
    assert "::error::" not in out


def test_a_pr_closing_no_issue_passes_and_says_so(tmp_path):
    code, out = _run(tmp_path, issues={})
    assert code == 0
    assert "closes no issue" in out


def test_an_issue_without_criteria_passes_and_says_so(tmp_path):
    code, out = _run(tmp_path, issues={401: "A bug.\n\n## Steps\n1. click"})
    assert code == 0
    assert "#401: no acceptance criteria" in out


def test_github_being_unreadable_fails_closed(monkeypatch, capsys):
    def boom(pr):
        raise RuntimeError("gh: HTTP 502")
    monkeypatch.setattr(cc, "fetch_from_github", boom)
    assert cc.main(["--pr", "1"]) == 2
    assert "::error::Could not read PR #1" in capsys.readouterr().out


# --- the real tree and the real workflow ----------------------------------------

def test_this_repos_own_claims_are_found():
    # Proves the collectors read this tree, including this file's own markers —
    # without it, every test above could pass against a scanner that finds
    # nothing in the real repo.
    claims = cc.pytest_claims(REPO_ROOT / "tests") | cc.feature_claims(
        REPO_ROOT / "tests" / "features"
    )
    assert (358, cc.normalize("A dropped criterion is visible")) in claims


@pytest.mark.skipif(not WORKFLOW.exists(), reason="workflow not present")
def test_the_workflow_runs_the_check_on_every_pr_and_on_body_edits():
    text = WORKFLOW.read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "types: [opened, edited, synchronize, reopened]" in code
    assert "python3 scripts/check_criteria.py --pr" in code
    # Not gated by ci.yml's fail-open classifier, and never skipped.
    assert "needs:" not in code and "if:" not in code
