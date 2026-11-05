"""#601: a failed pg_dump must stop the deploy, not look like a backup.

`pg_dump | gzip > file` reports gzip's status. Without pipefail a failed dump
exits 0 and leaves a small, valid gzip, so `set -e` and `gzip -t` both pass and
the step prints "backup ok" over nothing.

These run the step's own lines out of release.yml under a stub `docker`, rather
than asserting that a string is present or running a copy that could be right
while the workflow stays wrong.
"""
import gzip
import os
import re
import subprocess
from pathlib import Path

import pytest

RELEASE_WF = Path(__file__).resolve().parent.parent / ".github/workflows/release.yml"

pytestmark = pytest.mark.skipif(
    not RELEASE_WF.exists(), reason="not in the shipped image: .dockerignore excludes it")


def _dump_step():
    """The lines from `DUMP=` to the `echo "backup ok`, unescaped from the
    workflow's double-quoted ssh argument, which is what the remote shell receives.
    The echo, not the phrase: the comment above the dump says "backup ok" too."""
    m = re.search(r'^\s*DUMP=.*?echo \\"backup ok[^\n]*$', RELEASE_WF.read_text(), re.M | re.S)
    assert m, "release.yml no longer has the backup step this test runs"
    return m.group(0).replace('\\"', '"').replace('\\$', '$')


def _run(tmp_path, stub_body):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "docker"
    stub.write_text(f"#!/bin/sh\n{stub_body}\n")
    stub.chmod(0o755)
    return subprocess.run(
        ["bash", "-c", "set -e\n" + _dump_step()],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}",
             "DB_USER": "owner", "DB_NAME": "budget"},
    )


@pytest.mark.criterion(601, "A failed dump stops the deploy")
def test_a_failed_dump_stops_the_deploy(tmp_path):
    result = _run(tmp_path, 'echo "pg_dump: error: connection failed" >&2; exit 1')
    assert result.returncode != 0, "a failed pg_dump did not stop the backup step"
    assert "backup ok" not in result.stdout


@pytest.mark.criterion(601, "A good dump still passes")
def test_a_good_dump_still_passes(tmp_path):
    # How a real pg_dump 16 ends: `\unrestrict` comes AFTER the completion line.
    result = _run(tmp_path, r"""printf '%s\n' '-- PostgreSQL database dump' 'COPY rows' \
        '-- PostgreSQL database dump complete' '--' '' '\unrestrict abc123'""")
    assert result.returncode == 0, result.stderr
    assert "backup ok" in result.stdout
    dumps = list((tmp_path / "backups").glob("pre-deploy-*.sql.gz"))
    assert len(dumps) == 1
    assert b"PostgreSQL database dump complete" in gzip.decompress(dumps[0].read_bytes())


@pytest.mark.criterion(603, "A dump that ends early stops the deploy")
def test_a_dump_that_ends_early_stops_the_deploy(tmp_path):
    """Exit 0 and a valid gzip, so only the missing completion line can catch it."""
    result = _run(tmp_path, "printf '%s\\n' '-- PostgreSQL database dump' 'COPY rows'")
    assert result.returncode != 0, "a dump with no completion line passed the backup step"
    assert "backup ok" not in result.stdout
