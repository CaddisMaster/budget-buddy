"""Steps shared by more than one feature (#389). Imported by nothing.

behave executes every file in this directory, so a step shared between feature
areas must be defined exactly once, here — never imported from one step file
into another. Types and helpers come from `tests/features/support.py`.
"""
from behave import given, then

from tests.features.support import _user
from tests.helpers import _connection, _login


@given("user {who:Who} is signed in")
def given_signed_in(context, who):
    client = context.app.test_client()
    _login(client, _user(context, who)["username"])
    context.client = client


@given("user {who:Who} is an admin and signed in")
def given_admin_signed_in(context, who):
    # The scenario's own user, promoted — after_scenario deletes it as usual.
    conn = _connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_admin = true WHERE id = %s", (_user(context, who)["id"],))
    conn.commit()
    cur.close()
    conn.close()
    context.execute_steps(f"Given user {who} is signed in")


# Read after `support.race()`, which collects every exception a racer raised.
@then("no run failed")
def then_no_run_failed(context):
    assert context.errors == [], f"runs raised: {context.errors!r}"
