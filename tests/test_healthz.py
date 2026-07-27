"""/healthz — the liveness/readiness probe (issue #11).

Consumed by the Docker healthcheck, the deploy job's post-deploy verification,
and uptime monitoring. Three properties matter and are asserted here: it works
without a session, it actually reaches the database rather than merely proving
gunicorn is listening, and it says nothing beyond up/down.
"""
import json

import psycopg2

from app.blueprints import main as main_bp


def test_healthz_anon_returns_200(anon_client):
    # No session, and crucially no redirect — a probe cannot log in, and a
    # 302 to /login would make the check assert the wrong thing entirely.
    response = anon_client.get("/healthz")
    assert response.status_code == 200
    assert "json" in response.mimetype

    body = json.loads(response.data)
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_healthz_reports_503_when_database_is_unreachable(anon_client, monkeypatch):
    """The point of the endpoint. Gunicorn accepting the connection proves the
    process is up; if Postgres is gone the app is not serviceable, and a 200
    here would be actively misleading to whatever is watching."""
    def _boom(*args, **kwargs):
        raise psycopg2.OperationalError("could not connect to server")

    monkeypatch.setattr(main_bp, "db_cursor", _boom)

    response = anon_client.get("/healthz")
    assert response.status_code == 503

    body = json.loads(response.data)
    assert body["status"] == "error"
    assert body["database"] == "unreachable"


def test_healthz_leaks_nothing_on_failure(anon_client, monkeypatch):
    """This is the one endpoint guaranteed reachable by anyone, so it is the
    last place to leak. The driver's message must not reach the response."""
    secret = "password authentication failed for user budget_app"

    def _boom(*args, **kwargs):
        raise psycopg2.OperationalError(secret)

    monkeypatch.setattr(main_bp, "db_cursor", _boom)

    response = anon_client.get("/healthz")
    assert response.status_code == 503
    assert secret.encode() not in response.data
    assert b"psycopg2" not in response.data
    assert b"Traceback" not in response.data

    # And nothing about the deployment either.
    body = json.loads(response.data)
    assert set(body) == {"status", "database"}


def test_healthz_is_exempt_from_the_rate_limit():
    """Monitoring polls this continuously from several sources — the Docker
    healthcheck, the deploy job, Uptime Kuma. If the limiter could throttle it,
    sustained polling would manufacture a false outage.

    The limiter is disabled under test, so this asserts the registration rather
    than the behaviour. Flask-Limiter records exemptions in `_route_exemptions`,
    keyed by the view's fully-qualified name."""
    from app import limiter

    exempt = {name.rsplit(".", 1)[-1] for name in limiter._route_exemptions}
    assert "healthz" in exempt
