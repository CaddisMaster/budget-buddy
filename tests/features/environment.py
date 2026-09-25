"""behave's harness for tests/features/ (#356) — conftest.py's fixtures, for behave.

The data helpers are shared with pytest through `tests/helpers.py`; only the
Flask test-client layer is rebuilt here, as behave hooks:

    before_all       conftest's `app` fixture       — wait for the DB, build the app
    before_scenario  `users`                        — two fresh users, seeded
    after_scenario   `users` teardown               — FK-safe delete, own rows only

`client_a` is not built here: a scenario that needs a signed-in user says so in
a step ("Given user A is signed in", in `steps/`), which keeps the actor visible
in the Gherkin and spares every other scenario a bcrypt round trip. ⚠️ Steps
must not import from this file — behave loads it itself, so an import would
load it a second time as a separate module.

⚠️ THE PREFIX IS BEHAVE'S OWN, NEVER PYTEST'S. `conftest.TEST_PREFIX` is
`__pytest__` + the xdist worker id. Reusing it would make a behave run and a
pytest run create and tear down the same users — the collision `test.sh`'s
header measured at 424 errors when the prefix was briefly hardcoded, and which
reads as flakiness rather than contention. `test_behave_harness.py` asserts
neither prefix can start the other.

⚠️ Behave runs SERIALLY — no workers — so one fixed prefix is enough here. If a
sharding layer is ever written, this prefix has to become per-shard exactly the
way conftest's became per-worker.
"""
from app import app as flask_app
from app import limiter
from tests.helpers import (
    PASSWORD,
    _create_user,
    _delete_user,
    _seed_basic_data,
    _wait_for_db,
    refuse_a_database_that_holds_users,
)

BEHAVE_PREFIX = "__behave__"
USER_A = BEHAVE_PREFIX + "user_a"
USER_B = BEHAVE_PREFIX + "user_b"


def before_all(context):
    _wait_for_db()
    # A user present before the first scenario means this is a real database,
    # not the one test.sh just built (#400). Raising here aborts the whole run.
    refuse_a_database_that_holds_users()
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False  # no token plumbing in tests
    if not flask_app.secret_key:
        flask_app.secret_key = "behave-secret"
    limiter.enabled = False
    context.app = flask_app


def before_scenario(context, scenario):
    # Sweep leftovers from an aborted run, then build fresh — as `users` does.
    _delete_user(USER_A)
    _delete_user(USER_B)
    a_id = _create_user(USER_A, PASSWORD)
    b_id = _create_user(USER_B, PASSWORD)
    context.users = {
        "a": {"id": a_id, "username": USER_A, **_seed_basic_data(a_id, "A")},
        "b": {"id": b_id, "username": USER_B, **_seed_basic_data(b_id, "B")},
    }


def after_scenario(context, scenario):
    _delete_user(USER_A)
    _delete_user(USER_B)

