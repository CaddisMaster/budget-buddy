"""Steps shared by more than one feature (#389). Imported by nothing.

behave executes every file in this directory, so a step shared between feature
areas must be defined exactly once, here — never imported from one step file
into another. Types and helpers come from `tests/features/support.py`.
"""
from behave import given

from tests.features.support import _user
from tests.helpers import _login


@given("user {who:Who} is signed in")
def given_signed_in(context, who):
    client = context.app.test_client()
    _login(client, _user(context, who)["username"])
    context.client = client
