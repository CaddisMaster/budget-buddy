"""Steps for production_logs.feature (#404).

Every scenario reads what `app.logs.handler`, the real handler production
writes through, actually formats. The handler's stream is pointed at a buffer
for the scenario and restored afterwards. ⚠️ Never `caplog.at_level()` or a
`setLevel()` here: lowering the level to capture a line is exactly how the
backup audit line went unlogged in production while its test passed.

Faults and extra log lines are injected with cleanups registered on the
context, so a failing step cannot leave a patched app behind for the next
scenario.

⚠️ Types and `_user` come from `tests/features/support.py`, imported before any
step below is defined. See that module for why the import order matters.
"""
import io
import logging
import re
from datetime import date
from unittest.mock import patch

from behave import given, then, when

from app import logs
from tests.features.support import _user
from tests.helpers import _connection

# The prefix app.logs.FORMAT writes: [time] [pid] [LEVEL] [request id] name: …
# Traceback lines carry no prefix and are skipped when checking IDs.
_PREFIX = re.compile(r"^\[[^\]]+\] \[\d+\] \[[A-Z]+\] \[([^\]]+)\] ")


class _FakeDump:
    """Stands in for subprocess.run's result, so no real pg_dump is spawned."""
    returncode = 0
    stdout = b"-- fake dump\n"
    stderr = b""


def _log(context):
    """The scenario's log buffer, attached to the real handler on first use."""
    if not hasattr(context, "log"):
        # Production's root logger is at Python's default, WARNING. Anything
        # lower here would pass INFO lines the real app drops. behave's own
        # log capture does exactly that; tests/run_behave.py turns it off.
        root = logging.getLogger().getEffectiveLevel()
        assert root == logging.WARNING, (
            f"the root logger is at {logging.getLevelName(root)}, not WARNING, so "
            "this scenario cannot see a dropped INFO line. Run it through "
            "`python -m tests.run_behave`, which disables behave's log capture."
        )
        context.log = io.StringIO()
        previous = logs.handler.setStream(context.log)
        context.add_cleanup(logs.handler.setStream, previous)
    return context.log


def _request(context, send):
    """Run one request; keep its response, its ID and the lines it logged."""
    log = _log(context)
    start = log.tell()
    response = send()
    text = log.getvalue()[start:]
    visit = {
        "response": response,
        "id": response.headers.get("X-Request-ID"),
        "text": text,
        "ids": [m.group(1) for m in map(_PREFIX.match, text.splitlines()) if m],
    }
    context.visits = getattr(context, "visits", []) + [visit]
    return visit


def _patch(context, target, **kwargs):
    patcher = patch(target, **kwargs)
    patcher.start()
    context.add_cleanup(patcher.stop)


def _assert_all_carry(visit, at_least):
    assert visit["id"], "the response carried no X-Request-ID header"
    assert len(visit["ids"]) >= at_least, (
        f"expected at least {at_least} log line(s), got:\n{visit['text']}"
    )
    assert set(visit["ids"]) == {visit["id"]}, (
        f"lines carry {visit['ids']}, the response says {visit['id']}:\n{visit['text']}"
    )


# ── Given ───────────────────────────────────────────────────────────────────

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


@given("the dashboard logs two lines of its own while it renders")
def given_dashboard_logs_two_lines(context):
    functions = context.app.view_functions
    original = functions["main.index"]

    def noisy(*args, **kwargs):
        # One through app.logger, one through a child logger, which reaches
        # the handler only by propagation.
        context.app.logger.info("behave: the dashboard is rendering")
        logging.getLogger("app.behave").info("behave: still rendering")
        return original(*args, **kwargs)

    functions["main.index"] = noisy
    context.add_cleanup(functions.__setitem__, "main.index", original)


@given("saving a transaction fails with an unexpected error")
def given_saving_fails(context):
    # Not a psycopg2.Error, so the handler's own except does not catch it. It
    # fires after the form has been read, which is the realistic place for it.
    _patch(context, "app.blueprints.transactions.validate_category_account",
           side_effect=RuntimeError("behave: saving failed"))
    # TESTING re-raises into the test client; production renders a 500 and
    # logs it. The scenario is about the production path.
    config = context.app.config
    previous = config.get("PROPAGATE_EXCEPTIONS")
    config["PROPAGATE_EXCEPTIONS"] = False
    context.add_cleanup(config.__setitem__, "PROPAGATE_EXCEPTIONS", previous)


# ── When ────────────────────────────────────────────────────────────────────

@when("user {who:Who} exports the database")
def when_exports(context, who):
    _patch(context, "app.blueprints.admin.subprocess.run", return_value=_FakeDump())
    visit = _request(context, lambda: context.client.get("/admin/backup"))
    assert visit["response"].status_code == 200, visit["response"].status_code


@when("user {who:Who} opens the dashboard twice")
def when_opens_dashboard_twice(context, who):
    for _ in range(2):
        visit = _request(context, lambda: context.client.get("/"))
        assert visit["response"].status_code == 200, visit["response"].status_code


@when('user {who:Who} adds a transaction with the memo "{memo}"')
def when_adds_transaction(context, who, memo):
    user = _user(context, who)
    _request(context, lambda: context.client.post("/transactions/new", data={
        "description": memo,
        "amount": "12.34",
        "transaction_date": date.today().isoformat(),
        "account_id": user["account_id"],
        "category_id": user["category_id"],
        "transaction_type": "expense",
    }))


# ── Then ────────────────────────────────────────────────────────────────────

@then('the log says "{text}"')
def then_log_says(context, text):
    assert text in context.log.getvalue(), context.log.getvalue()


@then("every line the export logged carries its request ID")
def then_export_lines_carry_id(context):
    _assert_all_carry(context.visits[-1], at_least=1)


@then("each visit's lines all carry that visit's request ID")
def then_each_visit_carries_its_id(context):
    for visit in context.visits:
        _assert_all_carry(visit, at_least=2)


@then("the two visits have different request IDs")
def then_visits_differ(context):
    first, second = (visit["id"] for visit in context.visits)
    assert first != second, first


@then("the response is a server error")
def then_server_error(context):
    status = context.visits[-1]["response"].status_code
    assert status == 500, status


@then("the log carries the traceback and the request's ID")
def then_traceback_and_id(context):
    visit = context.visits[-1]
    text = visit["text"]
    assert "Traceback (most recent call last)" in text, text
    assert "RuntimeError: behave: saving failed" in text, text
    error_lines = [line for line in text.splitlines() if "[ERROR]" in line]
    assert error_lines, text
    assert all(f"[{visit['id']}]" in line for line in error_lines), text


@then('the log never mentions "{text}"')
def then_log_never_mentions(context, text):
    assert text not in context.log.getvalue(), context.log.getvalue()
