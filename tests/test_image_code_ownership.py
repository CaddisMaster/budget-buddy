"""#497 — the shipped image's code is owned by root, and CI proves it.

The Dockerfile used to `COPY --chown=appuser:appuser . .`, so the process
serving the app owned, and could rewrite, the code it serves: a compromise could
persist by editing it. The fix is a plain `COPY`, and the guard is a step in
CI's docker-build job that runs the BUILT image as its default user and fails
if anything under /app is writable.

Nothing here can run Docker, so these are assertions about the FILE that drives
the check — that the step exists, runs on every PR, and checks the property
rather than something adjacent to it. That the step itself fails on a bad image
was shown by hand on the PR, against the old image and a mutant.

⚠️ Parsed as TEXT, and skipped when the file is absent, for the reasons
`test_ci_postgres_probe.py` gives.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WF = REPO_ROOT / ".github/workflows/ci.yml"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

JOB = "  docker-build:"
STEP_NAME = "Container cannot write its own code"


def _step_body():
    """The step's text, sliced from its `- name:` to the next step, after
    checking it sits inside the docker-build job (the only job that has the
    image)."""
    text = CI_WF.read_text()
    job = text.find(JOB)
    assert job != -1, "ci.yml no longer has a docker-build job"
    start = text.find(f"- name: {STEP_NAME}")
    assert start != -1, (
        f"ci.yml no longer has a step named {STEP_NAME!r}; nothing proves the "
        "shipped image's code is read-only to the app (#497)")
    next_job = re.compile(r"^  [a-z][\w-]*:\s*$", re.M).search(text, job + len(JOB))
    assert start > job and (next_job is None or start < next_job.start()), (
        f"{STEP_NAME!r} is not inside the docker-build job, which is the only "
        "job holding the built image")
    nxt = re.compile(r"^      - name: ", re.M).search(text, start + 1)
    return text[start:nxt.start() if nxt else len(text)]


@pytest.mark.skipif(not CI_WF.exists(), reason=_NOT_IN_IMAGE)
def test_the_check_runs_on_every_pr():
    """A `paths`-style gate would skip it on exactly the PR that adds
    `--chown` back, if that PR touched nothing the gate watched."""
    body = _step_body()
    assert not re.search(r"^\s+if:", body, re.M), (
        "the ownership check is gated by an `if:`; it must run on every build (#497)")


@pytest.mark.skipif(not CI_WF.exists(), reason=_NOT_IN_IMAGE)
def test_the_check_runs_as_the_images_default_user():
    """The property is "the user the app runs as cannot write here". Run as
    root, `find -writable` matches everything and `-w` is always true, so a
    `-u root` would turn the check into noise — or, inverted, into a pass."""
    body = _step_body()
    runs = re.findall(r"docker run[^\n]*", body)
    assert runs, "the step no longer runs the image"
    as_other = [r for r in runs if re.search(r"\s(-u|--user)[\s=]", r)]
    assert not as_other, f"the check must run as the image's default user: {as_other}"


@pytest.mark.skipif(not CI_WF.exists(), reason=_NOT_IN_IMAGE)
def test_the_check_covers_ownership_and_every_writable_path():
    body = _step_body()
    assert re.search(r"stat [^\n]*/app /app/app", body), (
        "the step no longer checks who owns /app and /app/app (#497)")
    assert "find /app -writable" in body, (
        "the step no longer searches all of /app for paths the app user can "
        "write; checking only the two directories misses a writable file (#497)")
    assert body.count("exit 1") >= 3, (
        "each check needs its own failure path: the positive control, "
        "ownership, and writability (#497)")
