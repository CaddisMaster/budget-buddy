"""/admin/backup hardening (issue #13).

One authenticated GET returns the entire database as plaintext SQL — the
highest-value single endpoint in the application. These tests pin the access
rules and the audit trail. The rate limit itself is disabled under test (see
conftest), so its registration is asserted rather than its behaviour.
"""
import logging
from unittest.mock import patch

from tests.conftest import USER_ADMIN


class _FakeCompleted:
    """Stands in for subprocess.run's result so no real pg_dump is spawned."""

    def __init__(self, returncode=0, stdout=b"-- fake dump\n"):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = b""


def test_non_admin_gets_403_not_a_redirect(client_a):
    """Previously this flashed 'Access denied' and bounced to the dashboard.
    A download endpoint is not somewhere a user wanders by accident, so a
    refusal should be a refusal."""
    response = client_a.get("/admin/backup")
    assert response.status_code == 403
    # Specifically not a redirect.
    assert response.status_code not in (301, 302, 303, 307, 308)


def test_anonymous_is_redirected_to_login(anon_client):
    response = anon_client.get("/admin/backup")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_non_admin_never_reaches_pg_dump(client_a):
    """The guard must run before anything is spawned — a 403 that still shelled
    out would be a resource-exhaustion path even though no data is returned."""
    with patch("app.blueprints.admin.subprocess.run") as run:
        response = client_a.get("/admin/backup")
    assert response.status_code == 403
    run.assert_not_called()


def test_admin_can_download_and_it_is_logged(admin_client, caplog):
    """A full-database export previously left no trace at all. Without a log
    line, a compromise that exfiltrated everything would be invisible after
    the fact."""
    with caplog.at_level(logging.INFO):
        with patch("app.blueprints.admin.subprocess.run",
                   return_value=_FakeCompleted()) as run:
            response = admin_client.get("/admin/backup")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert "attachment;" in response.headers["Content-Disposition"]
    run.assert_called_once()

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "backup: database exported" in logged
    assert USER_ADMIN in logged


def test_failed_dump_does_not_leak_details(admin_client):
    """pg_dump failing should flash the generic message and redirect, not
    surface stderr or an exit code to the browser."""
    with patch("app.blueprints.admin.subprocess.run",
               return_value=_FakeCompleted(returncode=1, stdout=b"")):
        response = admin_client.get("/admin/backup", follow_redirects=False)

    assert response.status_code == 302
    assert b"pg_dump" not in response.data
    assert b"exit" not in response.data.lower()


def test_backup_is_rate_limited():
    """Real use is a manual click every so often. The limiter is disabled under
    test, so assert the decorator registered a limit on this endpoint rather
    than trying to exhaust it."""
    from app import limiter

    marked = {name.rsplit(".", 1)[-1] for name in limiter._marked_for_limiting}
    assert "backup_database" in marked, "no rate limit registered on /admin/backup"
