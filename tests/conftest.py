"""Shared pytest fixtures for the route and data-isolation tests.

These tests run against the *dev* Postgres container (the same DB the local app
uses). To avoid touching real data, every fixture creates dedicated test users
behind a recognizable prefix and tears them — and only them — down afterwards.

This file holds only what is pytest's own: the per-worker prefix and the
fixtures. The plain helpers — user setup and FK-safe teardown, every `create_*`
and `fetch_*` — live in `tests/helpers.py` (#356), which behave shares.
"""
import os
from datetime import date

import pytest

from app import app as flask_app
from app import limiter
from tests.helpers import PASSWORD, _create_user, _delete_user, _login, _seed_basic_data, _wait_for_db

# Prefix keeps test rows obvious and easy to sweep if a run aborts mid-way.
#
# ⚠️ It is per-WORKER, and that is what makes `-n auto` safe. Every worker is a
# separate process running the same fixtures against the SAME database. With one
# shared prefix they would all create, mutate and tear down the same three users:
# worker 1's teardown deletes worker 2's fixtures mid-test, inserts collide on
# the unique username, and the failures look like flaky tests rather than a
# harness bug.
#
# pytest-xdist exports the worker id as PYTEST_XDIST_WORKER ("gw0", "gw1", ...).
# It is absent on a serial run, so the prefix is then exactly "__pytest__" and
# behaviour is byte-identical to before parallelism existed.
#
# Everything downstream derives from this — including the sweep that makes an
# aborted run recoverable — so nothing else needs to know about workers. Do NOT
# hardcode "__pytest__" anywhere; build names from TEST_PREFIX.
TEST_PREFIX = "__pytest__" + os.environ.get("PYTEST_XDIST_WORKER", "")
USER_A = TEST_PREFIX + "user_a"
USER_B = TEST_PREFIX + "user_b"
USER_ADMIN = TEST_PREFIX + "admin"


@pytest.fixture(scope="session")


def app():
    _wait_for_db()
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False  # no token plumbing in tests
    if not flask_app.secret_key:
        flask_app.secret_key = "pytest-secret"
    limiter.enabled = False  # the 60/min cap would trip a fast test run
    return flask_app


@pytest.fixture


def users(app):
    # Sweep any leftovers from a previously aborted run, then build fresh.
    _delete_user(USER_A)
    _delete_user(USER_B)
    a_id = _create_user(USER_A, PASSWORD)
    b_id = _create_user(USER_B, PASSWORD)
    data = {
        "a": {"id": a_id, "username": USER_A, **_seed_basic_data(a_id, "A")},
        "b": {"id": b_id, "username": USER_B, **_seed_basic_data(b_id, "B")},
    }
    yield data
    _delete_user(USER_A)
    _delete_user(USER_B)


@pytest.fixture


def anon_client(app):
    return app.test_client()


@pytest.fixture


def client_a(app, users):
    client = app.test_client()
    _login(client, USER_A)
    return client


@pytest.fixture


def client_b(app, users):
    client = app.test_client()
    _login(client, USER_B)
    return client


@pytest.fixture


def admin_client(app):
    """A logged-in admin. Deliberately independent of the `users` fixture — the
    admin-only routes are about the is_admin flag, not about owning data."""
    _delete_user(USER_ADMIN)
    _create_user(USER_ADMIN, PASSWORD, is_admin=True)
    client = app.test_client()
    _login(client, USER_ADMIN)
    yield client
    _delete_user(USER_ADMIN)


# ── The forecast clock (#309, tranche 8b) ────────────────────────────────────

# Mid-month, and every month has one. `compute_forecast()`'s "still to land"
# window is the days strictly after today up to month end, so the day has to be
# short of the shortest month's last day — the 15th clears 28 with room.
FROZEN_DAY = 15


@pytest.fixture


def forecast_today(monkeypatch):
    """Freeze the clock `compute_forecast()` reads; yield the date it now sees.

    ⚠️ Why this exists. `app/blueprints/forecasts.py` calls `date.today()`
    directly, so its tests could not choose a date and five of them opened with

        if today.day >= days_in_month:
            pytest.skip("last day of month — no remaining-this-month window")

    which silently stopped running the forecast arithmetic on the last day of
    every month — roughly twelve days a year, and precisely the boundary the
    month-end arithmetic is most likely to be wrong at. Found on 2026-08-31,
    when the suite reported 1232 passed / 9 skipped against the 1237 / 4 the
    tranche-8a PR had recorded three days earlier.

    The suite already knew the answer: `compute_goal_projection`,
    `recent_months`, `_report_months`, `compute_digest_facts` and
    `build_seed_plan` all take an injectable `today=`. Only the forecast does
    not, so the seam is applied from outside here rather than by widening a
    signature the review is not otherwise touching.

    Patching the module's `date` NAME (not `datetime.date` globally) keeps the
    blast radius to this one module: `_remaining_scheduled` compares the frozen
    value against real `date` rows from psycopg2, which a subclass supports.

    ⚠️ Rows dated by the DATABASE (the `users` fixture's seeded transaction
    defaults to `CURRENT_DATE`) are still on the real clock, so a test that
    needs its own month-to-date spend must date it from the value yielded here.
    """
    frozen = date.today().replace(day=FROZEN_DAY)

    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return frozen

    monkeypatch.setattr("app.blueprints.forecasts.date", _FrozenDate)
    return frozen
