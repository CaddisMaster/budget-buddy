"""Assertions about claims the DOCS make, where a machine can check them.

Four stale-doc defects surfaced on 2026-08-24 (#288, #291, #292, #295). The
common factor was not carelessness — it was that **nothing executed the claim**.
`landing/index.html` is the least-covered file in the repo and the first thing a
visitor sees; `RUNBOOK.md`'s nginx snippet is only executed during a disaster.

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
not defensive noise. `.dockerignore` strips `*.md`, `CLAUDE.md` AND `landing/`,
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
LANDING = REPO_ROOT / "landing" / "index.html"
APEXCHARTS = REPO_ROOT / "app" / "static" / "apexcharts.min.js"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# Front-end libraries this project has retired. Naming one in a *tech stack
# declaration* is a factual error about what the app uses; naming it in prose
# ("Chart.js was retired with this change") is history and stays.
RETIRED_FRONTEND = {"chart.js"}


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
# The landing page — a tech stack nobody was checking
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LANDING.exists(), reason=_NOT_IN_IMAGE)
def test_no_landing_stack_tag_names_a_retired_library():
    """#291: the card advertised Chart.js for four releases after it was removed.

    Nothing could have caught it — no test reads this file, no CI job touches it,
    and it deploys outside the release workflow.
    """
    tags = re.findall(r'<span class="stack-tag">([^<]+)</span>', LANDING.read_text(encoding="utf-8"))

    assert len(tags) >= 5, f"found only {len(tags)} stack tags — the selector no longer matches"

    named = {t.strip().casefold() for t in tags}
    retired = named & RETIRED_FRONTEND
    assert not retired, (
        f"the landing page advertises retired {', '.join(sorted(retired))} — "
        "the app dropped it at 0.8.0 (#234) for ApexCharts"
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
