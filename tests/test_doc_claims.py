"""Assertions about claims the DOCS make, where a machine can check them.

Four stale-doc defects surfaced on 2026-08-24 (#288, #291, #292, #295). The
common factor was not carelessness — it was that **nothing executed the claim**.
`RUNBOOK.md`'s nginx snippet, for instance, is only executed during a disaster.

⚠️ The landing-page stack-tag test lived here until #299 and went with the page
to `CaddisMaster/seandesmet.com`. Do not re-add it: this repo no longer holds
the file, so the assertion could only ever skip.

So this file is the executable consumer those claims never had. It follows
`test_ci_postgres_probe.py`: parse the artifact as TEXT and assert a property of
it. Nothing here runs a workflow, reaches the Droplet, or renders a page.

⚠️ **SCOPE IS DELIBERATELY STRUCTURED CLAIMS, NEVER PROSE.** Every assertion
below targets something with a shape — a path in a fenced block, a `stack-tag`
span, a `/etc/letsencrypt/live/…` path, a version number. A test that fails when
someone rewords a paragraph is worse than no test at all: it trains the next
person to delete it, and takes the useful assertions with it. If you cannot
express the claim without a fuzzy match, it does not belong here.

⚠️ **EVERY TEST SKIPS WHEN ITS FILE IS ABSENT, naming `.dockerignore`.** This is
not defensive noise. `.dockerignore` strips `*.md` and `CLAUDE.md`,
and the suite runs inside the shipped image whenever `tests/` changes (#218) —
so in that run almost everything here is missing. A test that reads a repo file
must never assert the image is wrong when it is right. That exact defect reached
`main` twice (#176, then #218).

⚠️ Note what is deliberately NOT asserted: that `seandesmet.com-0001` never
appears in `RUNBOOK.md`. Naming a retired lineage while recounting the incident
is correct and useful. Using it as a live config path is the defect. The regex
below draws exactly that line.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
RUNBOOK = REPO_ROOT / "RUNBOOK.md"
APEXCHARTS = REPO_ROOT / "app" / "static" / "apexcharts.min.js"
HTMX = REPO_ROOT / "app" / "static" / "htmx.min.js"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# ---------------------------------------------------------------------------
# CLAUDE.md — the project map
# ---------------------------------------------------------------------------


def _project_map_paths():
    """Paths from the fenced block under `## Project map`, as repo-relative strings.

    The block nests one level: a top-level `app/` followed by two-space-indented
    children. Anything after `#` is a comment, not part of the path.
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")

    section = re.search(r"^## Project map\b(.*?)^## ", text, re.M | re.S)
    assert section, "CLAUDE.md has no '## Project map' section"

    fenced = re.search(r"```\n(.*?)```", section.group(1), re.S)
    assert fenced, "the '## Project map' section has no fenced block"

    paths, parent = [], ""
    for line in fenced.group(1).split("\n"):
        without_comment = line.split("#", 1)[0].rstrip()
        name = without_comment.strip()
        if not name:
            continue
        if len(without_comment) - len(without_comment.lstrip()) == 0:
            parent = name if name.endswith("/") else ""
            paths.append(name)
        else:
            paths.append(parent + name)
    return paths


@pytest.mark.skipif(not CLAUDE_MD.exists(), reason=_NOT_IN_IMAGE)
def test_every_path_in_the_project_map_exists():
    paths = _project_map_paths()

    # Presence assertion: without it a parser that silently matched nothing
    # would make this test pass while checking zero paths.
    assert len(paths) >= 10, f"parsed only {len(paths)} paths — the parser is broken, not the map"

    missing = [p for p in paths if not (REPO_ROOT / p).exists()]
    assert not missing, (
        "CLAUDE.md's project map names paths that do not exist: "
        + ", ".join(sorted(missing))
    )


# ---------------------------------------------------------------------------
# RUNBOOK.md — the document read during an incident
# ---------------------------------------------------------------------------

# A certbot lineage that carries a `-000N` suffix. Certbot mints these when it is
# re-run with a different `-d` set, so which one is live changes over time — that
# makes any hardcoded one a claim with an expiry date.
_VERSIONED_LINEAGE = re.compile(r"/etc/letsencrypt/live/([A-Za-z0-9.\-]*-\d{4})/")


@pytest.mark.skipif(not RUNBOOK.exists(), reason=_NOT_IN_IMAGE)
def test_runbook_hardcodes_no_versioned_certbot_lineage():
    """#292/#295: the DR snippet named `seandesmet.com-0001`, which was DELETED.

    §"Full rebuild" says to write the site files from §3. Copying a block that
    points at a lineage which no longer exists means nginx fails to start —
    during a rebuild, while the site is already down.

    The fix is not to update the path to today's lineage; that just resets the
    expiry. It is to name the command that reads the real one.
    """
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "certbot certificates" in text, (
        "RUNBOOK.md no longer tells you how to list the lineages on the box — "
        "that command is what makes a hardcoded path unnecessary"
    )

    hardcoded = sorted(set(_VERSIONED_LINEAGE.findall(text)))
    assert not hardcoded, (
        "RUNBOOK.md hardcodes versioned certbot lineage(s): "
        + ", ".join(hardcoded)
        + ". Which lineage is live changes whenever certbot is re-run with a "
        "different -d set, so name `certbot certificates` instead of a path."
    )


# ---------------------------------------------------------------------------
# The vendored chart library — a version and a licence, both load-bearing
# ---------------------------------------------------------------------------


def _documented_apexcharts_version():
    match = re.search(r"ApexCharts (\d+)\.(\d+)\.(\d+)", CLAUDE_MD.read_text(encoding="utf-8"))
    assert match, "CLAUDE.md no longer states a pinned ApexCharts version"
    return tuple(int(g) for g in match.groups())


@pytest.mark.skipif(
    not (CLAUDE_MD.exists() and APEXCHARTS.exists()), reason=_NOT_IN_IMAGE
)
def test_the_vendored_chart_library_is_the_version_the_docs_claim():
    major, minor, patch = _documented_apexcharts_version()
    banner = f"ApexCharts v{major}.{minor}.{patch}"

    assert banner in APEXCHARTS.read_text(encoding="utf-8", errors="replace"), (
        f"CLAUDE.md documents {banner}, but app/static/apexcharts.min.js does not "
        "carry that version banner — the doc and the artifact have drifted"
    )


@pytest.mark.skipif(not CLAUDE_MD.exists(), reason=_NOT_IN_IMAGE)
def test_the_pinned_chart_library_is_still_an_mit_release():
    """ApexCharts went dual-licensed at 5.x, and 6.x ships a watermark enforcer.

    `CLAUDE.md` carries this as a written warning ("do not upgrade blind").
    A written warning did not stop the last four defects in this file's history,
    so it is an assertion now.
    """
    major = _documented_apexcharts_version()[0]
    assert major < 5, (
        f"CLAUDE.md pins ApexCharts {major}.x — 5.x+ is dual-licensed and 6.x ships "
        "a watermark enforcer. 4.7.0 is the last MIT release. If this bump is "
        "deliberate, the licence question has to be answered first."
    )


# ---------------------------------------------------------------------------
# The other vendored bundle — htmx had no pinned version at all (#309, t5)
# ---------------------------------------------------------------------------


def _documented_htmx_version():
    match = re.search(r"htmx (\d+)\.(\d+)\.(\d+)", CLAUDE_MD.read_text(encoding="utf-8"))
    assert match, "CLAUDE.md no longer states a pinned htmx version"
    return tuple(int(g) for g in match.groups())


@pytest.mark.skipif(not (CLAUDE_MD.exists() and HTMX.exists()), reason=_NOT_IN_IMAGE)
def test_the_vendored_htmx_is_the_version_the_docs_claim():
    """The same claim ApexCharts has carried since #234, for the bundle beside it.

    ⚠️ htmx is the harder of the two to check by eye: the minified file carries
    NO banner comment — the first bytes are `var htmx=function(){` — so opening
    it tells you nothing about which release it is. The version lives in the
    runtime config object instead, which is why this asserts on that shape
    rather than on a header.

    #309 found this file pinned nowhere: `CLAUDE.md` said "vendored
    `htmx.min.js`" with no version, and no licence shipped beside it, while its
    neighbour had both plus two tests. The asymmetry was the finding — see
    test_design_system.py for the licence half.
    """
    major, minor, patch = _documented_htmx_version()
    stamp = f'version:"{major}.{minor}.{patch}"'

    assert stamp in HTMX.read_text(encoding="utf-8", errors="replace"), (
        f"CLAUDE.md documents htmx {major}.{minor}.{patch}, but "
        "app/static/htmx.min.js does not carry that version stamp — the doc "
        "and the artifact have drifted"
    )


# --- seam names referenced in prose actually exist (#309, tranche 3) ----------

# Names deliberately referenced while absent. Each entry is a seam that was
# REMOVED, named by a test asserting it stayed removed — so the reference is the
# point, not a mistake. Adding to this list should be a deliberate act.
DELIBERATELY_ABSENT_SEAMS = {
    # Removed with the Goal Coach (#262); tests/test_goal_coach_removed.py
    # asserts app.ai no longer defines it.
    "_call_coach_model",
}

# Whole-identifier match. The negative lookbehind stops a longer identifier
# that merely CONTAINS the prefix — the sdk-call-shape test module's own name is
# the live example — from reading as a seam reference. Requiring a letter after
# the prefix stops a glob written with a wildcard from matching.
_SEAM_REFERENCE = re.compile(r"(?<![A-Za-z0-9])_call_[a-z][a-z0-9_]*")


def test_every_seam_name_mentioned_anywhere_actually_exists():
    """A `_call_*` name in a comment or docstring must resolve to a real seam.

    The isolated `_call_*()` seams are the app's whole story about testing
    anything that touches a network, so they get named constantly — in module
    docstrings, in the comments explaining a call's arguments, and in the tests
    that stub them. Those references are the map a reader follows, and nothing
    executed them: #309 found a test docstring pointing at the budget-review
    seam under a name it has never had.

    This is a structured claim in the sense this file means it — an identifier
    either resolves or it does not, so there is no fuzzy match and no way for a
    reworded paragraph to fail it.

    ⚠️ **This docstring deliberately spells out no example of a dangling name,
    and neither should any fix it prompts.** A file test that scans source for a
    pattern will find that pattern in the prose explaining the rule — which is
    how the first cut of this test failed on itself, and how an unrelated
    Dockerfile test once failed on a correct file by matching its own comment.
    Name the seam that exists, describe the wrong one.

    ⚠️ Needs no `.dockerignore` skip, unlike everything above: it reads `.py`
    files, which are genuinely in the shipped image. `*.md` is what gets
    stripped.
    """
    defined = set()
    for path in (REPO_ROOT / "app").rglob("*.py"):
        defined.update(re.findall(r"^def (_call_[a-z0-9_]+)", path.read_text(),
                                  re.MULTILINE))
    assert "_call_ask_model" in defined, "sanity: found no seam definitions at all"

    dangling = {}
    for directory in ("app", "tests"):
        for path in (REPO_ROOT / directory).rglob("*.py"):
            for name in _SEAM_REFERENCE.findall(path.read_text()):
                if name in defined or name in DELIBERATELY_ABSENT_SEAMS:
                    continue
                dangling.setdefault(name, set()).add(
                    str(path.relative_to(REPO_ROOT)))

    assert not dangling, (
        "these _call_* names are referenced but defined nowhere in app/ — "
        "either the name is wrong or the seam was removed and the reference "
        "should join DELIBERATELY_ABSENT_SEAMS: "
        + "; ".join(f"{name} ({', '.join(sorted(files))})"
                    for name, files in sorted(dangling.items()))
    )
