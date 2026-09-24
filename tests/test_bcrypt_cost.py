"""Test users hash at bcrypt cost 4; the app itself keeps cost 12 (#384).

Two bcrypt hashes at cost 12 were ~0.37s of every ~0.45s behave scenario, and
every pytest test that creates users paid the same. The saving lives in
`tests/helpers.py::_create_user`, which both runners share.

⚠️ Flask-Bcrypt reads `BCRYPT_LOG_ROUNDS` ONCE, in `init_app`, and
`app/__init__.py` calls `Bcrypt(app)` at import. Setting that config key from a
fixture afterwards changes nothing and every other test stays green, which is
why the first test here reads the stored hash rather than trusting a setting.
Login still works at cost 4 because `check_password_hash` reads the cost out of
the stored hash; every `client_a` login proves that.
"""
import re
from pathlib import Path

import pytest

from app import bcrypt
from app.db import db_cursor
from tests.conftest import USER_A

OVERRIDE = Path(__file__).resolve().parent.parent / "docker-compose.override.yml"


def _cost(pw_hash):
    # A bcrypt hash is "$2b$<cost>$<salt+digest>".
    return int(pw_hash.split("$")[2])


@pytest.mark.criterion(384, "Test users are hashed at low cost")
def test_test_users_are_hashed_at_low_cost(users):
    with db_cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE username = %s", (USER_A,))
        row = cur.fetchone()
    assert row is not None, f"fixture user {USER_A} not found"
    assert _cost(row.password_hash) == 4, row.password_hash[:7]


@pytest.mark.criterion(384, "The app itself still hashes at full cost")
def test_the_app_still_hashes_at_full_cost():
    pw_hash = bcrypt.generate_password_hash("any-password").decode("utf-8")
    assert _cost(pw_hash) == 12, pw_hash[:7]


@pytest.mark.skipif(
    not OVERRIDE.exists(),
    reason="not present in the shipped image — .dockerignore excludes it",
)
def test_the_dev_container_reuses_time_wait_ports():
    """The price of a fast suite. A full run opens ~9,000 DB connections, each
    holding its local port in TIME_WAIT for 60s, against ~28,000 ports. At
    ~15s a run, the third back-to-back ./test.sh failed every connection with
    "Cannot assign requested address". Measured: without this sysctl TIME_WAIT
    climbed ~9,000 a run; with it, six back-to-back runs levelled off at ~11,000.

    Matched on non-comment lines only, so the comment explaining the setting
    cannot satisfy the test by itself."""
    lines = [
        ln for ln in OVERRIDE.read_text().splitlines()
        if not ln.lstrip().startswith("#")
    ]
    text = "\n".join(lines)
    assert re.search(r"^\s+sysctls:\s*$", text, re.M), "no sysctls: block"
    assert re.search(r"^\s+net\.ipv4\.tcp_tw_reuse:\s*1\s*$", text, re.M), (
        "docker-compose.override.yml must set net.ipv4.tcp_tw_reuse: 1 on web"
    )
