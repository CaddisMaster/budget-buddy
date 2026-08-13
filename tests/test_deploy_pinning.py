"""#190 — the deployed version is pinned, and an unpinned command fails loudly.

`docker-compose.yml` used to resolve the app image as `${TAG:-latest}`. On the
Droplet that default was a live foot-gun: deploys pull the exact version tag and
never `latest`, so the local `latest` is stale by construction, and a hand-run
`docker compose up -d` moved production back three releases with no signal at
all — container healthy, `/healthz` 200, uptime check green.

Nothing here can reach the Droplet, so these are assertions about the FILES that
drive it: the default is gone, and both workflows write the version they deploy
into `.env` so a bare `up -d` reproduces the running deployment.

⚠️ Every test that reads a repo file SKIPS when the file is absent, naming
`.dockerignore` — `docker-compose*.yml` and `.env.*` are genuinely excluded from
the shipped image, and CI runs this suite inside that image whenever the
Dockerfile or requirements change (#176). Failing there would assert the image is
wrong when it is right. `.github/` is not excluded, so the workflow tests carry
the same guard only for symmetry.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
RELEASE_WF = REPO_ROOT / ".github/workflows/release.yml"
ROLLBACK_WF = REPO_ROOT / ".github/workflows/rollback.yml"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# The one line that matters, isolated from every comment mentioning TAG. The ref
# is matched to end-of-line rather than as `\S+`: the mandatory-variable message
# contains spaces, and a `\S+` here would simply fail to match the correct file.
_IMAGE_LINE = re.compile(r"^\s*image:\s*ghcr\.io/\S+?budget-buddy:(?P<ref>.+?)\s*$",
                         re.M)


def _image_ref():
    m = _IMAGE_LINE.search(COMPOSE.read_text())
    assert m, "docker-compose.yml no longer declares the ghcr image on one line"
    return m.group("ref")


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
def test_the_image_tag_has_no_default():
    """The whole of #190. `${TAG:-latest}` reads like a harmless convenience and
    is the bug itself, so this states the property rather than the string: any
    `:-` default at all, whatever it points at, silently selects an image nobody
    named."""
    ref = _image_ref()
    assert ":-" not in ref, (
        f"the app image is {ref} — a default makes an unpinned `docker compose "
        "up -d` choose an image instead of failing (#190)")


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
def test_an_unset_tag_is_an_error_that_names_the_variable():
    """The other half: no default is only useful if the failure is legible.
    `${TAG:?...}` errors naming TAG; a bare `${TAG}` would interpolate to the
    empty string and pull `budget-buddy:`, which fails somewhere far less
    obvious."""
    ref = _image_ref()
    assert "${TAG:?" in ref, f"expected a mandatory ${{TAG:?…}}, got {ref}"


@pytest.mark.skipif(not ENV_EXAMPLE.exists(), reason=_NOT_IN_IMAGE)
def test_the_env_template_carries_a_tag_line():
    """Local dev never runs this image — the override builds from source — but
    compose interpolates every file before merging, so a `.env` without a TAG
    line breaks `./test.sh` and `docker compose ps` alike. The template is the
    only thing that tells a fresh clone that."""
    assert re.search(r"^TAG=", ENV_EXAMPLE.read_text(), re.M), (
        ".env.example must carry a TAG= line — without one a fresh clone's "
        "compose commands fail before anything starts")


@pytest.mark.parametrize("workflow", [RELEASE_WF, ROLLBACK_WF],
                         ids=["release", "rollback"])
def test_the_deploy_pins_the_version_into_env(workflow):
    """Both workflows, not just the deploy. A rollback that does not rewrite the
    pin leaves the box naming the version it just rolled AWAY from, so the next
    bare `up -d` rolls production forward again — the same failure, pointed the
    other way."""
    if not workflow.exists():
        pytest.skip(_NOT_IN_IMAGE)
    body = workflow.read_text()
    assert "grep -v '^TAG=' .env > .env.tmp" in body, (
        f"{workflow.name} does not rewrite the TAG pin in .env (#190)")
    assert "printf 'TAG=%s\\n' '${VERSION}'" in body, (
        f"{workflow.name} does not write the deployed version into .env")


@pytest.mark.parametrize("workflow", [RELEASE_WF, ROLLBACK_WF],
                         ids=["release", "rollback"])
def test_the_pin_is_written_to_a_file_that_is_already_private(workflow):
    """`.env` holds every production secret, and a plain `>` redirect creates the
    temp file at the umask default. The mode has to be set BEFORE the write, not
    after — this is the hole that once left the prod `.env` world-readable, by a
    different route."""
    if not workflow.exists():
        pytest.skip(_NOT_IN_IMAGE)
    body = workflow.read_text()
    chmod = body.index("chmod 600 .env.tmp")
    write = body.index("grep -v '^TAG=' .env > .env.tmp")
    assert chmod < write, (
        f"{workflow.name} writes .env.tmp before chmod'ing it — the secrets are "
        "briefly at the umask default")


def test_the_release_pins_before_it_runs_any_other_compose_command():
    """Ordering, stated because it is invisible: the pre-deploy `pg_dump` runs a
    bare `docker compose exec`, which cannot resolve the image at all until the
    pin exists. Pinning after the backup would fail the deploy on the FIRST run
    against an unpinned .env — precisely the run that introduces the pin."""
    if not RELEASE_WF.exists():
        pytest.skip(_NOT_IN_IMAGE)
    body = RELEASE_WF.read_text()
    # The COMMAND, not the word: a comment above the pin explains why it comes
    # first and mentions pg_dump, which an `.index("pg_dump")` would find.
    assert body.index("mv .env.tmp .env") < body.index("docker compose exec -T db pg_dump")
