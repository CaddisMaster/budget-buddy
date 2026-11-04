"""`--dist loadgroup` really does keep a group on one worker (#588).

The sweep tests rely on `xdist_group("scheduler_sweep")`, and losing it raises
nothing: pytest 9.2.0 with xdist 3.8.0 spread them across every worker, and they
failed at random with a different set each run. So this runs a real two-worker
session in a subprocess and reads which workers ran the tests.

It asserts the behaviour rather than the `@group` node ID suffix, because xdist's
own fix for this changes how the group reaches the scheduler.
"""
import os
import re
import subprocess
import sys

GROUPED_TESTS = """\
import pytest

pytestmark = pytest.mark.xdist_group("together")


@pytest.mark.parametrize("n", range(12))
def test_grouped(n):
    pass
"""


def test_loadgroup_keeps_a_group_on_one_worker(tmp_path):
    (tmp_path / "test_a.py").write_text(GROUPED_TESTS)
    (tmp_path / "test_b.py").write_text(GROUPED_TESTS)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-n", "2", "--dist", "loadgroup", "-v",
         "-p", "no:cacheprovider", "--rootdir", str(tmp_path), str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, env=env, timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "24 passed" in result.stdout, result.stdout
    workers = set(re.findall(r"^\[(gw\d+)\]", result.stdout, re.MULTILINE))
    assert len(workers) == 1, (
        f"The group ran on {sorted(workers)}, so --dist loadgroup is not grouping "
        "and the sweep tests will fail at random. See docs/testing.md (#588)."
    )
