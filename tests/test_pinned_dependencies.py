"""#405 — workflows run only the code they were reviewed with.

A `uses: owner/action@v7` runs whatever commit the tag points at when the job
starts, and whoever controls the tag can move it. Two of these actions receive
secrets (`docker/login-action` the ghcr token, `anthropics/claude-code-action`
the Claude OAuth token). The Dockerfile's `FROM python:3.14-slim` had the same
shape. Every reference is now pinned: actions to a full commit SHA with the
release as a trailing comment (`@<sha> # v7.0.1`, the form Dependabot keeps
updated), the base image to a digest.

These tests make a NEW tag-pinned line fail the suite, so the property does not
depend on whoever adds the next workflow remembering it.

⚠️ `.github/` and the Dockerfile are read from the repo; each test skips when
its file is absent, as in the shipped image (see test_deploy_pinning.py).
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GITHUB_DIR = REPO_ROOT / ".github"
DOCKERFILE = REPO_ROOT / "Dockerfile"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# A `uses:` KEY, as a list item or a plain key. Anchored to the start of the line
# so a `# uses: …` comment explaining the rule is never mistaken for one.
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$", re.M)
_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
_VERSION_COMMENT = re.compile(r"#\s*v\d\S*")

_FROM = re.compile(r"^FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.M | re.I)
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def unpinned_actions(text):
    """Every `uses:` in `text` not pinned to a full SHA with a version comment."""
    bad = []
    for m in _USES.finditer(text):
        ref, rest = m.group("ref"), m.group("rest")
        if ref.startswith("./"):
            continue  # an action in this repo, reviewed with the workflow
        if ref.startswith("docker://"):
            if not _DIGEST.search(ref):
                bad.append(ref)
            continue
        if not _PINNED.match(ref) or not _VERSION_COMMENT.search(rest):
            bad.append(ref + rest)
    return bad


def unpinned_images(dockerfile_text):
    """Every FROM naming a registry image without a digest. A FROM naming an
    earlier stage (`FROM base AS dev`) is this file's own build, not a pull."""
    stages, bad = set(), []
    for m in _FROM.finditer(dockerfile_text):
        image = m.group("image")
        if image not in stages and not _DIGEST.search(image):
            bad.append(image)
        if m.group("stage"):
            stages.add(m.group("stage"))
    return bad


def _workflow_files():
    return sorted(GITHUB_DIR.rglob("*.yml")) + sorted(GITHUB_DIR.rglob("*.yaml"))


@pytest.mark.skipif(not GITHUB_DIR.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(405, "Every workflow action is pinned to a full commit SHA")
def test_every_workflow_action_is_pinned_to_a_full_commit_sha():
    found, bad = 0, {}
    for path in _workflow_files():
        text = path.read_text()
        found += len(_USES.findall(text))
        if offenders := unpinned_actions(text):
            bad[str(path.relative_to(REPO_ROOT))] = offenders
    # A pattern that stopped matching would find nothing and pass. There were
    # 22 `uses:` lines when this was written; demand the scan still sees them.
    assert found >= 20, f"only {found} `uses:` lines found — is the pattern still matching?"
    assert not bad, (
        "pin each action to a full commit SHA with its release as a comment, "
        f"e.g. `@<40-hex sha> # v7.0.1` (#405): {bad}")


@pytest.mark.criterion(405, "A workflow that uses an action by tag fails the suite")
@pytest.mark.parametrize("line", [
    "      - uses: actions/checkout@v7",
    "        uses: anthropics/claude-code-action@v1",
    "      - uses: actions/checkout@main",
    # A short SHA is ambiguous and can be made to collide.
    "      - uses: actions/checkout@3d3c42e",
    # Pinned, but nobody reading the diff can tell which release it is.
    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "      - uses: docker://alpine:3.20",
])
def test_an_action_used_by_tag_is_caught(line):
    assert unpinned_actions(line + "\n"), f"not caught: {line!r}"


@pytest.mark.parametrize("line", [
    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
    "      - uses: ./.github/actions/local",
    "      # uses: actions/checkout@v7  (a comment, not a key)",
])
def test_a_pinned_or_local_action_passes(line):
    assert not unpinned_actions(line + "\n")


@pytest.mark.skipif(not DOCKERFILE.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(405, "The base image is pinned by digest")
def test_the_base_image_is_pinned_by_digest():
    text = DOCKERFILE.read_text()
    assert _FROM.search(text), "the Dockerfile has no FROM line the pattern can see"
    assert not unpinned_images(text), (
        f"pin the base image by digest, `image:tag@sha256:…` (#405): {unpinned_images(text)}")


def test_an_undigested_base_image_is_caught_and_a_stage_is_not():
    assert unpinned_images("FROM python:3.14-slim AS base\nFROM base AS dev\n") == [
        "python:3.14-slim"]
