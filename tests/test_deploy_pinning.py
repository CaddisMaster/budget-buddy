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


# --- #305: the deployed version is OBSERVABLE, not just pinned ---------------
#
# #190 removed the cause of a silent stale deploy; it gave nobody a way to see
# which version is actually serving. These assert the other half: the image
# carries the version it was built as, and both workflows refuse to report
# success against a container that is not the release they just deployed.

DOCKERFILE = REPO_ROOT / "Dockerfile"


def _dockerfile():
    if not DOCKERFILE.exists():
        pytest.skip(_NOT_IN_IMAGE)
    return DOCKERFILE.read_text()


@pytest.mark.parametrize("variable", ["APP_VERSION", "APP_COMMIT"])
def test_the_image_declares_the_stamp_with_a_default(variable):
    """A bare `docker build .`, CI's docker-build job and the local override all
    pass no build arg. An undefaulted ARG interpolates to the empty string, and
    an empty version renders as an empty cell that looks exactly like a stamped
    build whose version went missing."""
    body = _dockerfile()
    assert re.search(rf"^ARG {variable}=\S", body, re.M), (
        f"Dockerfile must declare ARG {variable} with a non-empty default (#305)")
    assert re.search(rf"^ENV {variable}=", body, re.M), (
        f"ARG {variable} alone does not survive into the running container — "
        "a build arg is build-time only, so it needs a matching ENV")


@pytest.mark.parametrize("variable", ["APP_VERSION", "APP_COMMIT"])
def test_the_stamp_is_set_in_the_base_stage(variable):
    """⚠️ Placement, not presence. Only `base` is inherited by both `dev` and
    `prod`; an ENV added below `FROM base AS dev` would stamp the dev image and
    leave the shipped one reporting nothing — which no local run could catch,
    since local dev IS the dev stage."""
    body = _dockerfile()
    # The DIRECTIVE, not the words — anchored to line start for the same reason
    # the pg_dump ordering test below matches a command rather than a name. The
    # comment above these ARGs explains why they must sit in `base`, and it says
    # "FROM base AS dev" to do so, which a plain .index() finds first.
    env = re.search(rf"^ENV {variable}=", body, re.M).start()
    dev_stage = re.search(r"^FROM base AS dev\b", body, re.M).start()
    assert env < dev_stage, (
        f"ENV {variable} is set after the dev stage begins, so the prod image "
        "does not inherit it (#305)")


def test_the_release_builds_the_image_with_the_version_it_derived():
    """The stamp has to come from the same place the tags do. A hand-maintained
    second copy of the version is a copy that can disagree with the tag it
    ships under, which is precisely the confusion this issue exists to end."""
    if not RELEASE_WF.exists():
        pytest.skip(_NOT_IN_IMAGE)
    body = RELEASE_WF.read_text()
    assert "APP_VERSION=${{ steps.meta.outputs.version }}" in body, (
        "release.yml does not pass the derived version as a build arg (#305)")
    assert "APP_COMMIT=${{ steps.meta.outputs.sha_short }}" in body, (
        "release.yml does not pass the derived commit as a build arg (#305)")
    assert 'echo "sha_short=${sha_short}" >> "$GITHUB_OUTPUT"' in body, (
        "the meta step computes sha_short but does not publish it as an output")


@pytest.mark.parametrize("workflow", [RELEASE_WF, ROLLBACK_WF],
                         ids=["release", "rollback"])
def test_the_deploy_refuses_a_container_that_is_not_the_release(workflow):
    """Both workflows, for the same reason the pin test covers both: a rollback
    that cannot tell it rolled back is the case where guessing is least
    acceptable. `/healthz` returning 200 says the app is alive, never which
    version is alive — that distinction is the whole of #305."""
    if not workflow.exists():
        pytest.skip(_NOT_IN_IMAGE)
    body = workflow.read_text()
    assert "printenv APP_VERSION" in body, (
        f"{workflow.name} never asks the running container what it is (#305)")
    check = body.index("printenv APP_VERSION")
    assert "exit 1" in body[check:], (
        f"{workflow.name} reads the running version but does not fail on a "
        "mismatch — a check that cannot go red is decoration")


def test_the_release_checks_the_version_before_it_drops_anything():
    """⚠️ Ordering, and it is invisible. The after-pull migrations are DROPs
    (#277), so they run against whatever `up -d` actually left running. If the
    swap silently left the old image up, dropping tables it still SELECTs is the
    exact outage #277 existed to prevent — so the identity check has to come
    first, while the only thing that has happened is a container swap."""
    if not RELEASE_WF.exists():
        pytest.skip(_NOT_IN_IMAGE)
    body = RELEASE_WF.read_text()
    assert body.index("printenv APP_VERSION") < body.index("--phase after-pull")


# --- the rollback's one deliberate difference -------------------------------

def test_the_rollback_tolerates_an_image_that_predates_the_stamp():
    """⚠️ Asymmetry, stated because it looks like an oversight. `release.yml`
    demands an exact match — it always deploys an image it just built, so an
    unstamped one there is a real fault. Every image built before #305 reports
    nothing, and those are precisely the versions a rollback reaches for, so the
    same strictness would refuse to roll back to them *during an incident*. A
    wrong stamp stays a hard failure; only a missing one warns."""
    if not ROLLBACK_WF.exists():
        pytest.skip(_NOT_IN_IMAGE)
    body = ROLLBACK_WF.read_text()
    assert "::warning::" in body, (
        "rollback.yml must WARN on an unstamped image, not fail (#305)")
    assert "::error::" in body, (
        "rollback.yml must still fail on a version that does not match")
