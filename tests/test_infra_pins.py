"""#530 — the Ansible control node is pinned exactly, and in one place. #577 — this
repo is a tenant of droplet-infra, and holds no copy of what moved there.

`infra/requirements-infra.txt` pins ansible-core and `infra/ansible/requirements.yml`
pins the collections, so a floating version would make the host layer's pinned SHA
mean different things on different days.

The same "one pin only" rule ruff follows (tests/test_lint_local.py): a second
ansible-core pin anywhere in the repo is a second answer to "which version?".

`infra/` is excluded from the image, so these skip there.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INFRA = REPO_ROOT / "infra"
REQS = INFRA / "requirements-infra.txt"
COLLECTIONS = INFRA / "ansible/requirements.yml"
_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes infra/"

pytestmark = pytest.mark.skipif(not INFRA.exists(), reason=_NOT_IN_IMAGE)

_EXACT = re.compile(r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$")
# A collection entry's version: exactly X.Y.Z, quoted or not. Anchored to the
# key, so a comment mentioning a version is never read as one.
_COLLECTION = re.compile(r"^\s*-\s*name:\s*(?P<name>\S+)", re.M)
_VERSION = re.compile(r"^\s+version:\s*\"?(?P<v>[^\"\s#]+)\"?", re.M)
# Any spelling of an ansible-core pin a requirements or config file could carry.
_ANY_ANSIBLE_PIN = re.compile(r"^\s*ansible(?:-core)?\s*(?:==|~=|>=|<=|!=|<|>)", re.M)


def _requirement_lines(text):
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def unpinned_requirements(text):
    return [line for line in _requirement_lines(text) if not _EXACT.match(line)]


def collection_versions(text):
    """{name: version} for each collection entry, version None if absent."""
    out = {}
    names = list(_COLLECTION.finditer(text))
    for i, m in enumerate(names):
        end = names[i + 1].start() if i + 1 < len(names) else len(text)
        v = _VERSION.search(text, m.end(), end)
        out[m.group("name")] = v.group("v") if v else None
    return out


def test_ansible_core_is_pinned_exactly():
    lines = _requirement_lines(REQS.read_text())
    assert any(line.startswith("ansible-core==") for line in lines)
    assert unpinned_requirements(REQS.read_text()) == []


def test_every_collection_is_pinned_exactly():
    versions = collection_versions(COLLECTIONS.read_text())
    assert versions, "found no collections — is the pattern still matching?"
    loose = {n: v for n, v in versions.items() if not (v and re.fullmatch(r"\d+\.\d+\.\d+", v))}
    assert loose == {}, f"collections not pinned to an exact X.Y.Z: {loose}"


def test_ansible_core_is_pinned_in_one_file_only():
    hits = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".txt", ".toml", ".cfg", ".yml", ".yaml", ".in"}:
            continue
        if any(part in {".git", ".venv", "venv", "collections", "node_modules", "vendor"} for part in path.parts):
            continue
        if _ANY_ANSIBLE_PIN.search(path.read_text(errors="ignore")):
            hits.append(path.relative_to(REPO_ROOT).as_posix())
    assert hits == ["infra/requirements-infra.txt"]


def _names(text):
    return {line.split("==")[0].lower() for line in _requirement_lines(text)}


def _pip_entry_directories(text):
    """The `directory:` of each pip block in a dependabot.yml, read as text."""
    blocks = re.split(r"^\s*-\s*package-ecosystem:\s*", text, flags=re.M)[1:]
    return [
        re.search(r'directory:\s*"([^"]+)"', block).group(1)
        for block in blocks
        if block.startswith("pip")
    ]


@pytest.mark.criterion(553, "The control node installs only pinned packages")
def test_the_control_node_installs_only_pinned_packages():
    # ansible-core's own requirements, so they cannot float underneath it.
    assert {"jinja2", "pyyaml", "cryptography", "packaging", "resolvelib"} <= _names(REQS.read_text())

    script = (INFRA / "test.sh").read_text()
    assert 'pip" install -q --no-deps -r' in script
    assert 'pip" check' in script
    # Over an old venv, a leftover package would satisfy pip check.
    assert "-m venv --clear" in script


@pytest.mark.criterion(553, "Dependabot proposes bumps for the control node")
def test_dependabot_proposes_bumps_for_the_control_node():
    config = (REPO_ROOT / ".github/dependabot.yml").read_text()
    assert "/infra" in _pip_entry_directories(config)


def test_the_production_check_names_a_python_ansible_core_can_run_on():
    # #567: the Mac's default python3 is too old for ansible-core 2.21.
    readme = (INFRA / "README.md").read_text()
    assert "python3 -m venv" not in readme
    assert "python3.12\" -m venv" in readme
    assert "--no-deps -r ../requirements-infra.txt && ../.venv/bin/pip check" in readme


@pytest.mark.parametrize("line", ["ansible-core>=2.21", "ansible-core", "ansible-core~=2.21.0"])
def test_a_loose_requirement_is_caught(line):
    assert unpinned_requirements(line + "\n") == [line]


def test_a_pip_entry_for_another_directory_is_not_mistaken_for_infra():
    text = (
        "updates:\n"
        '  - package-ecosystem: pip\n    directory: "/"\n'
        '  - package-ecosystem: docker\n    directory: "/infra"\n'
    )
    assert _pip_entry_directories(text) == ["/"]


def test_a_collection_without_an_exact_version_is_caught():
    text = (
        "collections:\n"
        "  - name: community.general   # version: 1.2.3 in a comment is not a pin\n"
        "  - name: ansible.posix\n"
        '    version: ">=2.0.0"\n'
    )
    assert collection_versions(text) == {"community.general": None, "ansible.posix": ">=2.0.0"}


# --- a tenant of droplet-infra (#577) -------------------------------------------

MOVED = (
    "ansible/roles/droplet_host",
    "ansible/roles/droplet_export",
    "ansible/roles/www_site",
    "ansible/roles/budget_buddy_backup/files/backup-export",
    "ansible/host.yml",
    "terraform",
)
PIN = INFRA / "host-role.pin"


@pytest.mark.criterion(577, "Budget Buddy holds no copy of the shared layer")
@pytest.mark.parametrize("path", MOVED)
def test_a_moved_path_stays_gone(path):
    """Two copies is the failure this move exists to end. The host layer lives in
    droplet-infra, and www's role in seandesmet.com, which has already changed its copy."""
    assert not (INFRA / path).exists(), f"infra/{path} moved out in #576; change it there"


def test_backup_export_is_nowhere_in_this_repos_roles():
    # A copy under another path would pass the check above.
    assert [p for p in (INFRA / "ansible/roles").rglob("backup-export") if p.is_file()] == []


def test_host_role_is_pinned_to_droplet_infra_by_full_sha():
    text = PIN.read_text()
    assert re.search(r"^repo=https://github\.com/CaddisMaster/droplet-infra\.git$", text, re.M)
    assert re.search(r"^sha=[0-9a-f]{40}$", text, re.M)


def test_the_roles_path_reaches_the_pinned_host_layer():
    cfg = (INFRA / "ansible/ansible.cfg").read_text()
    assert "roles_path = ./roles:./vendor/droplet-infra/infra/ansible/roles" in cfg
    assert "infra/ansible/vendor/" in (REPO_ROOT / ".gitignore").read_text().splitlines()


def test_the_vendored_copy_is_kept_out_of_the_image():
    # It's fetched next to the roles, so a local build would otherwise carry it.
    assert "infra/" in (REPO_ROOT / ".dockerignore").read_text().splitlines()


def test_the_app_playbook_never_runs_the_host_layer():
    # #576: tenants pin the host layer for testing only.
    play = (INFRA / "ansible/app.yml").read_text()
    for role in ("droplet_host", "droplet_export", "www_site"):
        assert role not in play


def test_the_app_roles_never_manage_the_pull_key():
    tasks = (INFRA / "ansible/roles/budget_buddy_backup/tasks/main.yml").read_text()
    assert "authorized_key" not in tasks
    assert "backup-export" not in tasks.replace("budget_buddy_backup_url_file", "")
