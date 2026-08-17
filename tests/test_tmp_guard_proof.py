"""TEMPORARY — proof that the in-image CI step now runs on a tests/ change.

This file is deliberately the defect #218 is about: it reads a repo file that
`.dockerignore` strips (`*.md`), with NO `skipif` guard. It therefore

  - PASSES in the dev container, where the bind mount serves the file, and
  - FAILS inside the shipped image, where the file does not exist.

That asymmetry is the point. A test that failed in both places would prove
nothing about the in-image step in particular — the ordinary Tests job would go
red first and the run would tell us nothing new.

Before this pull request, a `tests/`-only change left `image=false`, so the
in-image step never ran and this file would have gone green on the PR. If CI is
red on "Test suite runs inside the shipped image", the filter change works.

Removed in the next commit — this must NOT survive into main.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_tmp_proof_reads_a_dockerignored_file():
    changelog = REPO_ROOT / "CHANGELOG.md"
    assert changelog.exists(), (
        "CHANGELOG.md is absent — this is the shipped image, and an unguarded "
        "real-file test fails here exactly as #218 describes"
    )
