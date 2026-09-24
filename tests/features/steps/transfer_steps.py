"""Steps for transfers.feature (#389), the second area converted after schedules.

Thin calls into the same helpers the old pytest tests used. The Gherkin says
WHAT happens, and this file is the only place that knows HOW. Balances are
asserted as a CHANGE since the account was created, so the seeded $42.50
expense on every main account never has to appear in a scenario's arithmetic.

⚠️ Types and `_user` come from `tests/features/support.py`, imported before any
step below is defined. See that module for why the import order matters.
"""
from datetime import date

from behave import given, register_type, then, when

from app.helpers import GENERIC_ERROR
from tests.features.support import _pattern, _user
from tests.helpers import (
    account_balance,
    count_transactions_like,
    count_transfer_legs,
    create_account,
    create_transfer,
)

# Every transfer a scenario posts through the form carries this description,
# so "nothing was recorded" can be counted exactly.
POSTED = "behave-transfer"

# A group id no sequence will reach in a test database.
NO_SUCH_GROUP = 99999999


@_pattern(r'(?:their )?main account|"[^"]+"')
def _account_ref(text):
    return text


@_pattern(r"\d+(?:\.\d{2})?")
def _money(text):
    return float(text)


@_pattern(r'today|"[^"]*"')
def _on(text):
    return date.today().isoformat() if text == "today" else text.strip('"')


@_pattern(r"up|down")
def _direction(text):
    return text


register_type(Acct=_account_ref, Money=_money, On=_on, Dir=_direction)


def _account_id(context, who, ref):
    """"main account" / "their main account" is the seeded one; a quoted name
    is an account a Given step created."""
    if ref.endswith("main account"):
        return _user(context, who)["account_id"]
    return context.accounts[(who, ref.strip('"'))]


def _baseline(context, account_id):
    context.baseline.setdefault(account_id, account_balance(account_id))


def _body(context):
    return context.response.get_data(as_text=True)


# ── Given ───────────────────────────────────────────────────────────────────

@given('user {who:Who} has a second account {name:Acct}')
def given_second_account(context, who, name):
    user = _user(context, who)
    if not hasattr(context, "accounts"):
        context.accounts, context.baseline = {}, {}
    account_id = create_account(user["id"], name.strip('"'))
    context.accounts[(who, name.strip('"'))] = account_id
    _baseline(context, user["account_id"])
    _baseline(context, account_id)


@given("user {who:Who} has already transferred ${amount:Money} from {src:Acct} to {dst:Acct}")
def given_existing_transfer(context, who, amount, src, dst):
    src_id, dst_id = _account_id(context, who, src), _account_id(context, who, dst)
    group_id = create_transfer(_user(context, who)["id"], src_id, dst_id, amount,
                               date.today().isoformat())
    context.transfer = {"group_id": group_id, "from": src_id, "to": dst_id, "amount": amount}


# ── When ────────────────────────────────────────────────────────────────────

@when("user {who:Who} transfers ${amount:Money} from {src:Acct} to {dst:Acct} dated {on:On}")
def when_transferring(context, who, amount, src, dst, on):
    context.response = context.client.post("/transfers", data={
        "from_account": _account_id(context, who, src),
        "to_account": _account_id(context, who, dst),
        "amount": f"{amount:.2f}",
        "transfer_date": on,
        "description": POSTED,
    }, follow_redirects=True)


@when("user {who:Who} changes that transfer to ${amount:Money}")
def when_changing(context, who, amount):
    transfer = context.transfer
    context.response = context.client.post(
        f"/transfers/{transfer['group_id']}/edit",
        data={
            "from_account": transfer["from"],
            "to_account": transfer["to"],
            "amount": f"{amount:.2f}",
            "transfer_date": date.today().isoformat(),
        },
        follow_redirects=True,
    )


@when("user {who:Who} deletes that transfer")
def when_deleting(context, who):
    context.response = context.client.delete(
        f"/transfers/{context.transfer['group_id']}", follow_redirects=True)


@when("user {who:Who} opens the dashboard")
def when_opening_dashboard(context, who):
    context.response = context.client.get("/")


@when("user {who:Who} opens their transaction history")
def when_opening_history(context, who):
    context.response = context.client.get("/transactions")


@when("user {who:Who} opens a transfer that does not exist")
def when_opening_missing(context, who):
    context.response = context.client.get(f"/transfers/{NO_SUCH_GROUP}/edit")


# ── Then ────────────────────────────────────────────────────────────────────

@then("user {who:Who}'s {acct:Acct} has gone {direction:Dir} by ${amount:Money}")
def then_balance_moved(context, who, acct, direction, amount):
    account_id = _account_id(context, who, acct)
    change = round(account_balance(account_id) - context.baseline[account_id], 2)
    expected = amount if direction == "up" else -amount
    assert change == expected, f"{acct} moved by {change:+.2f}, expected {expected:+.2f}"


@then("user {who:Who}'s {acct:Acct} is unchanged")
def then_balance_unchanged(context, who, acct):
    account_id = _account_id(context, who, acct)
    change = round(account_balance(account_id) - context.baseline[account_id], 2)
    assert change == 0, f"{acct} moved by {change:+.2f}, expected no change"


@then('the transfer is refused with "{message}"')
def then_transfer_refused(context, message):
    assert context.response.status_code == 200, context.response.status_code
    assert message in _body(context), f"expected {message!r} on the page"


@then("no generic error is shown")
def then_no_generic_error(context):
    assert GENERIC_ERROR not in _body(context), "the generic error was shown"


@then("no transfer has been recorded for user {who:Who}")
def then_nothing_recorded(context, who):
    found = count_transactions_like(_user(context, who)["id"], POSTED)
    assert found == 0, f"expected no transfer rows, found {found}"


@then("that transfer has {count:d} sides")
def then_transfer_sides(context, count):
    found = count_transfer_legs(context.transfer["group_id"])
    assert found == count, f"expected {count} sides, found {found}"


@then("it is not found")
def then_not_found(context):
    status = context.response.status_code
    assert status == 404, f"expected 404, got {status}"


@then("the dashboard shows ${spent:Money} spent and ${earned:Money} earned")
def then_dashboard_figures(context, spent, earned):
    body = _body(context)
    assert f">${spent:,.2f}<" in body, f"spending ${spent:,.2f} not shown"
    assert f">${earned:,.2f}<" in body, f"income ${earned:,.2f} not shown"
    # Nor the transfer added to either figure, anywhere on the page.
    moved = context.transfer["amount"]
    for leaked in (spent + moved, earned + moved):
        assert f"{leaked:,.2f}" not in body, f"{leaked:,.2f} appears: the transfer was counted"


@then("the history shows that transfer with a Transfer badge and a link to it")
def then_history_shows_transfer(context):
    body = _body(context)
    # The nav's "Transfers" link contains the word too, so look for the badge.
    assert 'class="transfer-badge"' in body, "no transfer badge in the history"
    link = f"/transfers/{context.transfer['group_id']}"
    assert link in body, f"no link to {link} in the history"
