"""Connection-credential precedence (issue #15).

The application should connect as the least-privileged role available, but must
keep working in an environment that has not created one yet. That fallback is
what makes adopting `budget_app` a deliberate change to .env rather than a
breaking one, so it is worth pinning.

The role's actual restrictions are enforced by Postgres, not by this code, and
are verified against a real database — see RUNBOOK. CI has no `budget_app`
role, so asserting them here would only prove the fallback fired.
"""
from unittest.mock import patch

from app import db


def _captured_connect_kwargs(env):
    """Call get_db_connection with a given environment, return the kwargs it
    passed to psycopg2."""
    with patch.dict("os.environ", env, clear=True):
        with patch("app.db.psycopg2.connect") as connect:
            db.get_db_connection()
    return connect.call_args.kwargs


BASE = {
    "DB_HOST": "db",
    "DB_PORT": "5432",
    "DB_NAME": "budget",
    "DB_USER": "superuser_account",
    "DB_PASSWORD": "superuser_pw",
}


def test_uses_app_role_when_configured():
    kwargs = _captured_connect_kwargs(
        {**BASE, "DB_APP_USER": "budget_app", "DB_APP_PASSWORD": "app_pw"}
    )
    assert kwargs["user"] == "budget_app"
    assert kwargs["password"] == "app_pw"


def test_falls_back_to_db_user_when_app_role_is_unset():
    """An environment that has not run sql/30_app_role.sql yet must be
    completely unaffected."""
    kwargs = _captured_connect_kwargs(BASE)
    assert kwargs["user"] == "superuser_account"
    assert kwargs["password"] == "superuser_pw"


def test_empty_app_user_falls_back_rather_than_connecting_anonymously():
    """`DB_APP_USER=` in a .env file yields an empty string, not an absent key.
    Treating that as 'configured' would attempt a connection with no username,
    which fails in a way that looks like a database outage rather than a
    configuration error."""
    kwargs = _captured_connect_kwargs({**BASE, "DB_APP_USER": "", "DB_APP_PASSWORD": ""})
    assert kwargs["user"] == "superuser_account"
    assert kwargs["password"] == "superuser_pw"


def test_other_connection_parameters_are_untouched():
    kwargs = _captured_connect_kwargs({**BASE, "DB_APP_USER": "budget_app"})
    assert kwargs["host"] == "db"
    assert kwargs["port"] == "5432"
    assert kwargs["dbname"] == "budget"
