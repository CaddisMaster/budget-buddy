"""#433 — the deploy and rollback workflows ship docker-compose.yml themselves.

Before this the file only reached the Droplet by a hand scp from the Mac, so
nothing tied it to the image. At 0.12.0 the Droplet still held the previous
release's file (#432).

Nothing here can reach the Droplet. The first half EXECUTES
`scripts/install_compose.sh`, the part that runs there, in a temp directory with
a stub `docker` on PATH, so the hash check, the atomic rename and the db guard
are tested as shipped rather than read. The second half checks the workflows
call it before their first compose command.

`scripts/` and `.github/` are both in the shipped image (.dockerignore strips
neither), so no skipif guard is needed.
"""
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install_compose.sh"
RELEASE_WF = REPO_ROOT / ".github/workflows/release.yml"
ROLLBACK_WF = REPO_ROOT / ".github/workflows/rollback.yml"

OLD = "services:\n  web:\n    image: old\n"
NEW = "services:\n  web:\n    image: new\n"

# Answers the three docker calls the script makes, from environment variables
# each test sets. STUB_WANT empty with STUB_CONFIG_FAILS mimics `config` erroring.
STUB_DOCKER = """#!/bin/sh
case "$*" in
  "compose ps -q db") printf '%s\\n' "$STUB_DB_ID" ;;
  *"config --hash db")
    [ -n "$STUB_CONFIG_FAILS" ] && { echo "bad compose file" >&2; exit 15; }
    printf 'db %s\\n' "$STUB_WANT" ;;
  inspect*) printf '%s\\n' "$STUB_HAVE" ;;
  *) echo "unexpected docker call: $*" >&2; exit 99 ;;
esac
"""


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def droplet(tmp_path):
    """A deploy dir holding the old file and a freshly staged new one."""
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "docker-compose.yml").write_text(OLD)
    (deploy / ".docker-compose.yml.new").write_text(NEW)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB_DOCKER)
    stub.chmod(0o755)
    return deploy, bin_dir


def _install(droplet, expected=None, mode="release", db_id="", want="h1", have="h1",
             config_fails=False):
    deploy, bin_dir = droplet
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "STUB_DB_ID": db_id, "STUB_WANT": want, "STUB_HAVE": have,
           "STUB_CONFIG_FAILS": "1" if config_fails else ""}
    return subprocess.run(
        ["sh", "-s", "--", expected or _sha(NEW), "0.13.0", mode],
        stdin=SCRIPT.open(), cwd=deploy, env=env, capture_output=True, text=True)


def _live(droplet):
    return (droplet[0] / "docker-compose.yml").read_text()


def _staged_left(droplet):
    return (droplet[0] / ".docker-compose.yml.new").exists()


@pytest.mark.criterion(433, "A release brings its own compose file")
def test_a_matching_file_replaces_the_old_one(droplet):
    result = _install(droplet, db_id="abc123")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _live(droplet) == NEW
    assert not _staged_left(droplet)
    assert f"installed docker-compose.yml {_sha(NEW)}" in result.stdout


@pytest.mark.criterion(433, "A wrong compose file fails the deploy")
def test_a_hash_mismatch_fails_and_installs_nothing(droplet):
    result = _install(droplet, expected=_sha(OLD))
    assert result.returncode == 1
    assert "hash mismatch" in result.stdout
    assert _live(droplet) == OLD
    assert not _staged_left(droplet), "a refused file must not linger to be picked up"


def test_a_missing_staged_file_fails(droplet):
    (droplet[0] / ".docker-compose.yml.new").unlink()
    result = _install(droplet)
    assert result.returncode == 1
    assert "did not arrive" in result.stdout
    assert _live(droplet) == OLD


def test_an_unknown_mode_is_refused(droplet):
    result = _install(droplet, mode="deploy")
    assert result.returncode == 2
    assert _live(droplet) == OLD


def test_a_release_that_would_recreate_db_is_refused(droplet):
    """The guard chosen for #433: a db change is a scheduled operation, never
    a side effect of a release, so the old file stays and nothing swaps."""
    result = _install(droplet, db_id="abc123", want="new-db-hash", have="old-db-hash")
    assert result.returncode == 1
    assert "recreate the database container" in result.stdout
    assert _live(droplet) == OLD
    assert not _staged_left(droplet)


def test_a_rollback_that_changes_db_warns_and_still_installs(droplet):
    """Mid-incident the file must still match the image; rollback.yml keeps db
    out of its `up` instead."""
    result = _install(droplet, mode="rollback", db_id="abc123",
                      want="new-db-hash", have="old-db-hash")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout
    assert _live(droplet) == NEW


def test_a_box_with_no_db_container_skips_the_guard(droplet):
    """A rebuilt box has nothing to recreate. The stub's differing hashes would
    fail the release if the guard ran."""
    result = _install(droplet, db_id="", want="x", have="y")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _live(droplet) == NEW


def test_an_unreadable_compose_file_is_not_blamed_on_db(droplet):
    """`config | cut` reports cut's status, so a failed config would otherwise
    come back as an empty hash and read as 'db changed'."""
    result = _install(droplet, db_id="abc123", config_fails=True)
    assert result.returncode == 1
    assert "could not read" in result.stdout
    assert "recreate" not in result.stdout
    assert _live(droplet) == OLD


@pytest.mark.criterion(433, "A release brings its own compose file")
def test_release_installs_the_file_before_its_first_compose_command():
    text = RELEASE_WF.read_text()
    install = text.index("< scripts/install_compose.sh")
    assert "sh -s -- '${EXPECTED}' '${VERSION}' release" in text
    # The checked-out file is what gets hashed and sent.
    assert 'EXPECTED="$(sha256sum docker-compose.yml' in text
    assert "< docker-compose.yml" in text
    assert install < text.index("docker compose"), \
        "a compose command runs before the release's compose file is in place"
    assert "docker compose up -d --remove-orphans" in text


@pytest.mark.criterion(433, "A rollback brings the rolled-back version's compose file")
def test_rollback_installs_the_target_tags_file_before_the_swap():
    text = ROLLBACK_WF.read_text()
    assert 'git show "v${VERSION}:docker-compose.yml"' in text
    assert "fetch-depth: 0" in text, "git show needs the tags"
    assert "sh -s -- '${EXPECTED}' '${VERSION}' rollback" in text
    assert text.index("< scripts/install_compose.sh") < text.index("docker compose")


def test_rollback_never_recreates_db_and_removes_orphans():
    """Rolling back past #402 drops `worker` from the file. Without
    --remove-orphans the new-image worker keeps scheduling alongside the old
    image's in-process scheduler, and every job runs twice."""
    text = ROLLBACK_WF.read_text()
    assert "grep -vx db" in text
    assert "docker compose up -d --no-deps --remove-orphans \\$SERVICES" in text


def _step(text, name):
    """One workflow step's block, from its `- name:` to the next step."""
    start = text.index(f"      - name: {name}")
    end = text.find("\n      - ", start + 1)
    return text[start:end if end != -1 else len(text)]


@pytest.mark.parametrize("workflow, name", [
    (RELEASE_WF, "Ship the release's docker-compose.yml"),
    (ROLLBACK_WF, "Ship that version's docker-compose.yml"),
])
def test_the_ship_step_is_unconditional(workflow, name):
    """An `if:` on the step would skip it and leave the old file in place,
    and every ordering check above would still pass."""
    step = _step(workflow.read_text(), name)
    assert "install_compose.sh" in step
    assert "\n        if:" not in step
