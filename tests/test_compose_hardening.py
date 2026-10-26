"""#498 — web and worker run locked down, and db is left alone.

`docker-compose.yml` gives `web` and `worker` a read-only root filesystem, no
capabilities, no-new-privileges, and PID and memory limits. These assertions
exist so a key cannot be dropped silently: nothing else would notice, because
the app runs exactly the same without any of them.

`db` must carry NONE of them. Any edit to its definition recreates the database
container on the next `up -d`, and install_compose.sh refuses that release, so
Postgres hardening is a scheduled operation of its own (#500).

⚠️ Parsed as TEXT, not with PyYAML, which is not in the shipped image (see
`test_ci_postgres_probe.py`). The reader below understands exactly this file's
shape: top-level blocks, two-space service blocks, and the one `<<:` merge of
the `x-locked-down` anchor. A layout it cannot read fails loudly rather than
passing, because every lookup asserts that what it found is non-empty.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
OVERRIDE = REPO_ROOT / "docker-compose.override.yml"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

HARDENED = ("web", "worker")
HARDENING_KEYS = ("read_only", "tmpfs", "cap_drop", "security_opt", "pids_limit", "mem_limit")


def _block(text, name, indent):
    """The lines under `<indent>name:` up to the next key at that indent."""
    m = re.search(rf"^{' ' * indent}{re.escape(name)}:[^\n]*\n", text, re.M)
    assert m, f"no `{name}:` at indent {indent} in docker-compose.yml"
    end = re.compile(rf"^ {{0,{indent}}}\S", re.M).search(text, m.end())
    return text[m.end():end.start() if end else len(text)]


def _keys(block, indent):
    """{key: value text} for the keys of a block at `indent`, a value being the
    rest of its line plus any more-indented lines under it."""
    out, key = {}, None
    for line in block.splitlines():
        m = re.match(rf"^ {{{indent}}}([\w<-]+):\s*(.*)$", line)
        if m:
            key = m.group(1)
            out[key] = m.group(2)
        elif key and line.startswith(" " * (indent + 1)):
            out[key] += "\n" + line.strip()
    return out


def _service(text, name):
    """A service's effective keys, with `<<: *locked-down` resolved."""
    keys = _keys(_block(_block(text, "services", 0), name, 2), 4)
    assert keys, f"service {name!r} has no keys the reader could see"
    if keys.pop("<<", "").strip() == "*locked-down":
        anchor = _keys(_block(text, "x-locked-down", 0), 2)
        assert anchor, "the x-locked-down anchor has no keys the reader could see"
        keys = {**anchor, **keys}
    return keys


def _missing_hardening(text):
    """Every `service: key` that is absent or wrong, so a failure names both."""
    problems = []
    for name in HARDENED:
        k = _service(text, name)
        checks = {
            "read_only": k.get("read_only", "").strip() == "true",
            "tmpfs": "/tmp" in k.get("tmpfs", ""),
            "cap_drop": "ALL" in k.get("cap_drop", ""),
            "security_opt": "no-new-privileges:true" in k.get("security_opt", ""),
            "pids_limit": k.get("pids_limit", "").strip().isdigit(),
            "mem_limit": bool(re.fullmatch(r"\d+[mg]", k.get("mem_limit", "").strip())),
        }
        problems += [f"{name}: {key}" for key, ok in checks.items() if not ok]
    return problems


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(498, "web and worker run locked down")
def test_web_and_worker_run_locked_down():
    problems = _missing_hardening(COMPOSE.read_text())
    assert not problems, f"hardening missing from docker-compose.yml: {problems}"


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(498, "a hardening key removed from compose fails the suite")
def test_a_removed_hardening_key_is_named_with_its_service():
    """Mutations of the real file, so this proves the check above can fail and
    that its failure names both the service and the key."""
    text = COMPOSE.read_text()
    assert "  read_only: true\n" in text, "the anchor's read_only line moved; update this test"
    no_read_only = text.replace("  read_only: true\n", "", 1)
    assert {"web: read_only", "worker: read_only"} <= set(_missing_hardening(no_read_only))

    web = _block(_block(text, "services", 0), "web", 2)
    unmerged = text.replace(web, web.replace("    <<: *locked-down\n", ""), 1)
    # Only what the mutation ADDED, so a real gap elsewhere fails the test
    # above rather than muddying this one.
    added = set(_missing_hardening(unmerged)) - set(_missing_hardening(text))
    assert {"web: read_only", "web: cap_drop"} <= added
    assert not [p for p in added if p.startswith("worker")], \
        "unmerging web's anchor must not be reported against worker"


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
def test_db_carries_none_of_the_hardening():
    """Adding any of these to db would make the next release recreate the
    database container, which install_compose.sh refuses. That is #500's
    scheduled window, never a side effect of this file."""
    db = _service(COMPOSE.read_text(), "db")
    leaked = [key for key in HARDENING_KEYS if key in db]
    assert not leaked, f"db carries hardening keys, which would recreate it: {leaked}"


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
def test_gunicorn_opens_no_control_socket_in_the_read_only_home():
    """gunicorn 26 opens a control socket in $HOME by default, and $HOME is
    read-only under this file."""
    command = _service(COMPOSE.read_text(), "web")["command"]
    assert '"gunicorn"' in command and '"--no-control-socket"' in command, command


@pytest.mark.skipif(not OVERRIDE.exists(), reason=_NOT_IN_IMAGE)
def test_dev_keeps_the_lockdown_and_lifts_only_memory_and_noexec():
    """Dev running locked down is what shows the hardening breaks nothing before
    a release. It may lift exactly two things — the memory cap (the suite runs
    in this container) and /tmp's noexec (a test runs a stub from tmp_path) —
    and must not quietly switch the rest off."""
    text = OVERRIDE.read_text()
    for forbidden in ("read_only", "cap_add", "cap_drop", "security_opt",
                      "pids_limit", "privileged"):
        assert not re.search(rf"^\s+{forbidden}:", text, re.M), \
            f"docker-compose.override.yml sets {forbidden}, undoing #498 in dev"
