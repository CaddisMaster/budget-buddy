"""Steps for the weekly digest feature (#483).

Every network seam the send reaches is stubbed, and the keys it checks are set
to fakes. ⚠️ The dev container carries a REAL `ANTHROPIC_API_KEY`, so a seam
left unstubbed here is a real, billed model call. All of it is restored by
cleanups.
"""
import os

from behave import given, then, when

import app.ai as ai
import app.mailer as mailer
from app.ai import ParseError, _Digest
from app.blueprints.digests import send_weekly_digests
from app.scheduler import _in_app_context
from tests.features.support import _user, race
from tests.helpers import _connection


def _address(context, who):
    return f"{_user(context, who)['username']}@test.dev"


def _no_agent(*args, **kwargs):
    # The money agent has its own try/except in the send; skipping it keeps
    # the scenario to one model seam and the email.
    raise ParseError("agent offline in the digest scenarios")


def _stub_the_outside_world(context):
    """Fake keys, a canned model reply, and a mailer that records instead of
    sending. Idempotent within a scenario."""
    if hasattr(context, "sent_to"):
        return
    context.sent_to = []

    def record_send(api_key, to, subject, html, reply_to):
        context.sent_to.append(to)
        return {"id": f"msg-{len(context.sent_to)}"}

    stubs = {
        (ai, "_call_digest_model"): lambda *a, **k: _Digest(summary="Your week.", tips=["Save"]),
        (ai, "_call_agent_model"): _no_agent,
        (mailer, "_call_resend"): record_send,
    }
    for (module, name), stub in stubs.items():
        context.add_cleanup(setattr, module, name, getattr(module, name))
        setattr(module, name, stub)

    for key in ("ANTHROPIC_API_KEY", "RESEND_API_KEY"):
        previous = os.environ.get(key)
        os.environ[key] = "test-key"
        if previous is None:
            context.add_cleanup(os.environ.pop, key, None)
        else:
            context.add_cleanup(os.environ.__setitem__, key, previous)


@given("user {who:Who} is opted in to the weekly digest")
def given_opted_in(context, who):
    conn = _connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET email = %s, weekly_digest = true, last_digest_sent_on = NULL "
        "WHERE id = %s", (_address(context, who), _user(context, who)["id"]))
    conn.commit()
    cur.close()
    conn.close()


@when("user {who:Who} saves the Profile form with the digest ticked again")
def when_saves_profile_again(context, who):
    response = context.client.post("/profile/settings", data={
        "email": _address(context, who), "weekly_digest": "on"})
    assert response.status_code == 302, response.status_code


def _as_scheduled(context):
    """The digest wrapped exactly as the scheduler wraps it: inside the app's
    context, which each thread pushes for itself (#490)."""
    return _in_app_context(context.app, send_weekly_digests)


@when("the weekly digest job runs")
def when_digest_runs(context):
    _stub_the_outside_world(context)
    _as_scheduled(context)()


@when("the weekly digest job runs twice at the same time")
def when_digest_runs_twice(context):
    _stub_the_outside_world(context)
    race(context, [_as_scheduled(context), _as_scheduled(context)])


@then("user {who:Who} has been sent {count:d} digest")
def then_sent_digests(context, who, count):
    address = _address(context, who)
    sent = context.sent_to.count(address)
    assert sent == count, f"{address} was sent {sent} digests: {context.sent_to!r}"
