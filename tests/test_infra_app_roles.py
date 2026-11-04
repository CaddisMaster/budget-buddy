"""#531 — what the app roles must never do, readable without an LXD container.

`infra/test.sh` builds the roles for real, on the VM only. These hold the two rules
whose failure would be worst on production, in the run CI makes:

- release.yml owns `.env` and `docker-compose.yml`. A role writing either would show
  every release as drift, and a real run would put production back on its copy.
- the site file's upload limit is what lets a phone screenshot reach the app (#447).
- the ping URL is a secret (#532).

`backup-export` and its key moved to droplet-infra (#576). `infra/test.sh` compares
its `configs` list with the backup script's, because only there are both files present.

`infra/` is excluded from the image, so these skip there.
"""
import re
from pathlib import Path

import pytest

ROLES = Path(__file__).resolve().parent.parent / "infra/ansible/roles"
APP_ROLES = ("budget_buddy_app", "budget_buddy_backup")
_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes infra/"

pytestmark = pytest.mark.skipif(not ROLES.exists(), reason=_NOT_IN_IMAGE)

_WRITE_TARGET = re.compile(r"^\s*(?:dest|path):\s*(?P<target>.+)$", re.M)
_FORBIDDEN = re.compile(r"\.env\b|docker-compose\.yml|_url_file\s*}}\s*\"?$|healthchecks-url")


def written_paths(text):
    """Every `dest:` or `path:` in a task file that isn't a read-only stat."""
    tasks = re.split(r"^- name:", text, flags=re.M)
    return [
        m.group("target")
        for task in tasks
        if "ansible.builtin.stat:" not in task
        for m in _WRITE_TARGET.finditer(task)
    ]


def forbidden_targets(text):
    return [t for t in written_paths(text) if _FORBIDDEN.search(t)]


@pytest.mark.parametrize("role", APP_ROLES)
def test_the_role_writes_nothing_release_yml_owns(role):
    task_files = list((ROLES / role / "tasks").glob("*.yml"))
    assert task_files, f"found no tasks for {role}"
    for path in task_files:
        assert forbidden_targets(path.read_text()) == [], path


def test_the_detector_finds_a_write_and_ignores_a_stat():
    written = (
        "- name: Look\n  ansible.builtin.stat:\n    path: /opt/budget-buddy/.env\n"
        "- name: Ship it\n  ansible.builtin.copy:\n    src: x\n    dest: /opt/budget-buddy/docker-compose.yml\n"
    )
    assert forbidden_targets(written) == ["/opt/budget-buddy/docker-compose.yml"]


def test_the_budget_site_keeps_the_upload_limit_on_the_import_route_alone():
    site = (ROLES / "budget_buddy_app/files/budget-buddy").read_text()
    assert site.count("client_max_body_size") == 1
    assert re.search(r"location = /transactions/import \{\s*client_max_body_size 16m;", site)


@pytest.mark.criterion(532, "The ping URL is never in git")
def test_the_ping_url_is_never_in_git():
    role = ROLES / "budget_buddy_backup"
    tasks = (role / "tasks/main.yml").read_text()
    assert forbidden_targets(tasks) == []
    assert "budget_buddy_backup_url.stat.exists" in tasks
    for path in role.rglob("*"):
        if path.is_file():
            assert "hc-ping.com/" not in path.read_text(), path
