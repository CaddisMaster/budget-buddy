"""#529 — the release image is built for amd64 AND arm64.

The Droplet is amd64, but the `jupiter` VM is arm64, and the LXD containers the
Ansible roles are tested in (#530, #531, #534) run there. An amd64-only tag
cannot start on the VM, so the release must push a multi-arch index.

Building arm64 on an amd64 runner needs QEMU registered BEFORE buildx creates
its builder; set up the other way round, the builder has no arm64 emulator and
the build fails at the first `RUN`.

What a test cannot see is the pushed tag itself. That is checked once per
release with `docker buildx imagetools inspect` (RUNBOOK §6).
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_WF = REPO_ROOT / ".github/workflows/release.yml"
_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# Anchored to the directive, so a comment that mentions it is never a match.
_PLATFORMS = re.compile(r"^\s+platforms:\s*(?P<value>\S+)\s*$", re.M)
_QEMU = re.compile(r"^\s*-?\s*uses:\s*docker/setup-qemu-action@", re.M)
_BUILDX = re.compile(r"^\s*-?\s*uses:\s*docker/setup-buildx-action@", re.M)
_BUILD_PUSH = re.compile(r"^\s*-?\s*uses:\s*docker/build-push-action@", re.M)


def _build_push_platforms(text):
    """The `platforms:` value of the build-push step, as a set."""
    step = _BUILD_PUSH.search(text)
    assert step, "release.yml no longer uses docker/build-push-action"
    m = _PLATFORMS.search(text, step.end())
    assert m, "the build-push step names no platforms"
    return set(m.group("value").split(","))


@pytest.mark.skipif(not RELEASE_WF.exists(), reason=_NOT_IN_IMAGE)
def test_the_release_builds_amd64_and_arm64():
    assert _build_push_platforms(RELEASE_WF.read_text()) == {
        "linux/amd64",
        "linux/arm64",
    }


@pytest.mark.skipif(not RELEASE_WF.exists(), reason=_NOT_IN_IMAGE)
def test_qemu_is_registered_before_buildx_creates_its_builder():
    text = RELEASE_WF.read_text()
    qemu, buildx = _QEMU.search(text), _BUILDX.search(text)
    assert qemu, "release.yml builds arm64 on an amd64 runner without QEMU"
    assert buildx
    assert qemu.start() < buildx.start(), "setup-qemu-action must run before setup-buildx-action"


def test_a_platform_named_in_a_comment_is_not_read_as_the_setting():
    text = (
        "        uses: docker/build-push-action@abc # v7\n"
        "        with:\n"
        "          # platforms: linux/amd64,linux/arm64\n"
        "          platforms: linux/amd64\n"
    )
    assert _build_push_platforms(text) == {"linux/amd64"}
