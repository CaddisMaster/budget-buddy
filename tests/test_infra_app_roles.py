"""#531 — what the app roles must never do, readable without an LXD container.

`infra/test.sh` builds the roles for real, on the VM only. These hold the two rules
whose failure would be worst on production, in the run CI makes:

- release.yml owns `.env` and `docker-compose.yml`. A role writing either would show
  every release as drift, and a real run would put production back on its copy.
- the site file's upload limit is what lets a phone screenshot reach the app (#447).
- the ping URL is a secret, and the two backup scripts' path lists must agree (#532).

`infra/` is excluded from the image, so these skip there.
"""
import re
from pathlib import Path

import pytest

ROLES = Path(__file__).resolve().parent.parent / "infra/ansible/roles"
APP_ROLES = ("budget_buddy_app", "www_site", "budget_buddy_backup")
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


BACKUP_FILES = ROLES / "budget_buddy_backup/files"


def _tar_paths(text):
    """The absolute paths a script's `tar czf` is given, one per continued line."""
    return re.findall(r"^\s+(/(?:opt|etc)/\S+?)(?:\s+\\|\s+2>>\S+;\s+then)?\s*$", text, flags=re.M)


def test_both_backup_scripts_archive_the_same_paths():
    # Dropping one is invisible: the tarball still builds and passes gzip -t.
    export = _tar_paths((BACKUP_FILES / "backup-export").read_text())
    backup = _tar_paths((BACKUP_FILES / "budget-buddy-backup").read_text())
    assert "/etc/nginx/sites-enabled" in export
    assert export == backup


def test_a_path_missing_from_one_script_is_noticed():
    a = "tar czf - \\\n  /opt/x/.env \\\n  /etc/nginx/y\n"
    b = "tar czf - \\\n  /opt/x/.env\n"
    assert _tar_paths(a) != _tar_paths(b)


def test_the_forced_command_is_bound_to_the_pull_key():
    defaults = (ROLES / "budget_buddy_backup/defaults/main.yml").read_text()
    assert 'command="/usr/local/bin/backup-export",restrict,' in defaults
    assert "jupiter-backup" in defaults


@pytest.mark.criterion(532, "The ping URL is never in git")
def test_the_ping_url_is_never_in_git():
    role = ROLES / "budget_buddy_backup"
    tasks = (role / "tasks/main.yml").read_text()
    assert forbidden_targets(tasks) == []
    assert "budget_buddy_backup_url.stat.exists" in tasks
    for path in role.rglob("*"):
        if path.is_file():
            assert "hc-ping.com/" not in path.read_text(), path
