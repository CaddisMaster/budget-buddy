"""Every acceptance-criteria scenario a PR's issues promise must be claimed (#358).

Feature issues carry Gherkin acceptance criteria, and until this check the
criteria and the tests were connected only by whoever read the issue that day.
A dropped scenario closed with its PR and left no trace but prose in a closed
issue.

For each issue the PR closes, this reads the `Scenario:` titles under the
issue's "Acceptance criteria" heading, and requires each to be CLAIMED by one of:

  * a `.feature` scenario under tests/features/ with that title, tagged
    `@issue-<n>` (on the scenario, its Rule, or its Feature);
  * a pytest test decorated `@pytest.mark.criterion(<n>, "<title>")`;
  * a line in the PR body: `Verified by hand: #<n> "<title>" — <why>`,
    for a criterion that is a statement about docs or process rather than
    something a test can hold.

Titles match case-insensitively with whitespace collapsed. Anything unclaimed
fails the check and is named.

⚠️ CORRECTING A WRONG SPEC IS MEANT TO BE CHEAP. Issues here are regularly
wrong on contact with the code (#243, #246, #326, #314). Fix the ISSUE, then
re-run the check: it reads the issue at run time, and a claim left pointing at a
scenario that no longer exists is reported as a warning, never a failure.
Editing an issue does not trigger a run by itself — re-run the workflow.

⚠️ FAILS CLOSED. If GitHub cannot be read, the check errors rather than
passing — a check that silently does not run is the #281 failure this must not
repeat. A PR that closes no issue (Dependabot), or closes only issues without
criteria (bugs), passes and says so.

Standard library only: it runs on a bare CI runner, before anything installs.
Usage: python scripts/check_criteria.py --pr <number>   (needs `gh`, GH_TOKEN)
"""
import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SCENARIO = re.compile(r"^\s*(?:Scenario(?: Outline| Template)?|Example):\s*(.+?)\s*$")
_ISSUE_TAG = re.compile(r"@issue-(\d+)\b")
_HAND = re.compile(r'^\s*(?:[-*]\s+)?Verified by hand:\s*#(\d+)\s+"([^"]+)"', re.I | re.M)


def normalize(title):
    """Case-insensitive, whitespace-collapsed, trailing full stop ignored."""
    return " ".join(title.split()).rstrip(".").casefold()


def extract_criteria(body):
    """The Scenario titles under the issue's "Acceptance criteria" heading.

    Only that section counts: issues quote other issues' Gherkin to explain
    themselves (#358 quotes #324's), and those quoted scenarios are not this
    issue's promise. The section runs to the next heading of the same or a
    higher level. A `#` inside a code fence is a Gherkin comment, not a heading.
    """
    titles = []
    in_fence = False
    section_level = None
    for line in (body or "").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            heading = _HEADING.match(line)
            if heading:
                level = len(heading.group(1))
                if section_level is not None and level <= section_level:
                    section_level = None
                if section_level is None and heading.group(2).lower().startswith(
                    "acceptance criteria"
                ):
                    section_level = level
                continue
        if section_level is not None and in_fence:
            scenario = _SCENARIO.match(line)
            if scenario:
                titles.append(scenario.group(1))
    return titles


def feature_claims(features_dir):
    """{(issue, normalized title)} claimed by tagged `.feature` scenarios."""
    claims = set()
    for path in sorted(Path(features_dir).rglob("*.feature")):
        feature_tags, rule_tags, pending = set(), set(), set()
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if line.startswith("@"):
                pending |= set(_ISSUE_TAG.findall(line))
            elif line.startswith("Feature:"):
                feature_tags, pending = pending, set()
            elif line.startswith("Rule:"):
                rule_tags, pending = pending, set()
            else:
                scenario = _SCENARIO.match(line)
                if scenario:
                    for issue in feature_tags | rule_tags | pending:
                        claims.add((int(issue), normalize(scenario.group(1))))
                    pending = set()
    return claims


def _is_criterion_marker(node):
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "criterion"


def pytest_claims(tests_dir):
    """{(issue, normalized title)} claimed by `@pytest.mark.criterion(n, "title")`."""
    claims = set()
    for path in sorted(Path(tests_dir).rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not (isinstance(deco, ast.Call) and _is_criterion_marker(deco)):
                    continue
                args = deco.args
                if (
                    len(args) == 2
                    and isinstance(args[0], ast.Constant) and isinstance(args[0].value, int)
                    and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str)
                ):
                    claims.add((args[0].value, normalize(args[1].value)))
                else:
                    raise ValueError(
                        f"{path}:{deco.lineno}: criterion() takes two literals, "
                        "(issue_number, \"Scenario title\")"
                    )
    return claims


def hand_claims(pr_body):
    """{(issue, normalized title)} from `Verified by hand: #n "title"` lines."""
    return {(int(n), normalize(t)) for n, t in _HAND.findall(pr_body or "")}


def check(criteria, claims):
    """criteria: {issue: [title, ...]}. Returns (unclaimed, orphaned).

    unclaimed — (issue, title) promised and not claimed: a failure.
    orphaned  — (issue, normalized title) claimed for one of these issues but
                no longer promised by it: a warning, because it is what a
                corrected spec leaves behind.
    """
    unclaimed, promised = [], set()
    for issue, titles in sorted(criteria.items()):
        for title in titles:
            key = (issue, normalize(title))
            promised.add(key)
            if key not in claims:
                unclaimed.append((issue, title))
    orphaned = sorted(k for k in claims if k[0] in criteria and k not in promised)
    return unclaimed, orphaned


def _gh(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def fetch_from_github(pr):
    """(pr_body, {issue: issue_body}) for the issues the PR closes."""
    view = _gh("pr", "view", str(pr), "--json", "body,closingIssuesReferences")
    issues = {}
    for ref in view.get("closingIssuesReferences") or []:
        n = ref["number"]
        issues[n] = _gh("issue", "view", str(n), "--json", "body")["body"]
    return view.get("body") or "", issues


def run(pr_body, issue_bodies, repo_root=REPO_ROOT, out=print):
    """The whole check, given what GitHub returned. Returns the exit code."""
    if not issue_bodies:
        out("This PR closes no issue, so there are no criteria to check.")
        return 0
    criteria = {n: extract_criteria(b) for n, b in issue_bodies.items()}
    tests_dir = Path(repo_root) / "tests"
    claims = (
        feature_claims(tests_dir / "features")
        | pytest_claims(tests_dir)
        | hand_claims(pr_body)
    )
    for n, titles in sorted(criteria.items()):
        if not titles:
            out(f"#{n}: no acceptance criteria under an 'Acceptance criteria' heading.")
        for title in titles:
            mark = "✓" if (n, normalize(title)) in claims else "✗"
            out(f"#{n}: {mark} {title}")
    unclaimed, orphaned = check(criteria, claims)
    for n, title in orphaned:
        out(f"::warning::#{n}: a claim names \"{title}\", which the issue no longer "
            "specifies. Remove or rename the claim.")
    if unclaimed:
        for n, title in unclaimed:
            out(f"::error::#{n}: \"{title}\" is not claimed by any test.")
        out("")
        out("Claim each one with a `@issue-<n>` tagged .feature scenario, a")
        out('`@pytest.mark.criterion(<n>, "<title>")` test, or a PR-body line')
        out('`Verified by hand: #<n> "<title>" — <why>`. If the criterion was wrong,')
        out("correct the issue and re-run this check. See docs/testing.md.")
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check that a PR's issues' acceptance criteria are all claimed."
    )
    parser.add_argument("--pr", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        pr_body, issue_bodies = fetch_from_github(args.pr)
    except (RuntimeError, json.JSONDecodeError, KeyError) as err:
        print(f"::error::Could not read PR #{args.pr} or its issues from GitHub: {err}")
        return 2
    return run(pr_body, issue_bodies)


if __name__ == "__main__":
    sys.exit(main())
