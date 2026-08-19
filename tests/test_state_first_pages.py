"""State first, form behind a disclosure (#239 #240 #241 #242 #243).

Five pages shared one shape: an "Add X" form stacked ABOVE the "Existing X"
list, so the thing you use least occupied the top of the page. #223 made
exactly this complaint about Home ("it opens with furniture, not the answer")
and the fix there was to lead with the state and demote the input. The five
issues each say the decision should be made once and applied to all five, so
the shared property is asserted once, here — the per-page work is tested in
each page's own file.

⚠️ Transfer is deliberately NOT held to "the form comes last" for its *one-off*
transfer form. `/transfers` exists to move money now; that form is the page's
purpose, not its furniture. Only the *automatic* transfer form — the one that
sat above its own table — is demoted here.

⚠️ The disclosure renders OPEN when the collection is empty and CLOSED when it
is not. A new user with nothing yet must not meet a page that is empty except
for a collapsed form; this mirrors the AI cards' empty-state rule, and it is
the half of the change most likely to be lost in a later edit.
"""
import re
from datetime import date, timedelta

import pytest

from tests.conftest import (
    create_account,
    create_goal,
    create_schedule,
    create_transfer_schedule,
)

SOON = date.today() + timedelta(days=7)


def _seed_nothing(users):
    """accounts/categories: the shared fixture already seeds one of each."""


def _seed_goal(users):
    create_goal(users["a"]["id"], users["a"]["account_id"], 1000)


def _seed_schedule(users):
    create_schedule(users["a"]["id"], users["a"]["account_id"], 25, "monthly",
                    SOON)


def _seed_transfer_schedule(users):
    other = create_account(users["a"]["id"], "state-first-dest")
    create_transfer_schedule(users["a"]["id"], users["a"]["account_id"], other,
                             50, "monthly", SOON)


# (path, id of the collection container, a marker unique to the ADD form,
#  what to seed so the collection is non-empty)
# The marker is the form's hx-post target, which is what identifies it as the
# add form rather than one of the page's other forms.
PAGES = [
    ("/accounts", "account-rows", 'hx-post="/accounts"', _seed_nothing),
    ("/categories", "category-rows", 'hx-post="/categories"', _seed_nothing),
    ("/goals", "goal-list", 'hx-post="/goals"', _seed_goal),
    ("/scheduled", "schedule-rows", 'hx-post="/scheduled"', _seed_schedule),
    ("/transfers", "transfer-schedule-rows", 'hx-post="/transfers/recurring"',
     _seed_transfer_schedule),
]

IDS = [p[0] for p in PAGES]


def _details_around(html, marker):
    """The <details> element enclosing `marker`, or None.

    Scans back from the marker for the nearest opening <details> tag and
    forward for its close, so this is about real nesting rather than the two
    strings merely both appearing on the page.
    """
    at = html.find(marker)
    if at == -1:
        return None
    opens = list(re.finditer(r"<details\b[^>]*>", html[:at]))
    if not opens:
        return None
    end = html.find("</details>", at)
    if end == -1:
        return None
    return html[opens[-1].start():end]


@pytest.mark.parametrize("path,list_id,marker,seed", PAGES, ids=IDS)
def test_the_add_form_sits_behind_a_disclosure(
        client_a, users, path, list_id, marker, seed):
    seed(users)
    html = client_a.get(path).get_data(as_text=True)
    panel = _details_around(html, marker)
    assert panel is not None, f"{path}: the add form is not inside a <details>"
    open_tag = panel.split(">")[0]
    assert "add-panel" in open_tag, \
        f"{path}: the disclosure is not the shared .add-panel component"
    assert "<summary" in panel, f"{path}: the disclosure has no summary to click"


@pytest.mark.parametrize("path,list_id,marker,seed", PAGES, ids=IDS)
def test_what_i_have_comes_before_the_form_for_a_new_one(
        client_a, users, path, list_id, marker, seed):
    """The whole shared decision, in one assertion."""
    seed(users)
    html = client_a.get(path).get_data(as_text=True)
    listing = html.find(f'id="{list_id}"')
    form = html.find(marker)
    assert listing != -1, f"{path}: no #{list_id} on the page"
    assert form != -1, f"{path}: add form not found"
    assert listing < form, \
        f"{path}: the add form still comes before the {list_id} listing"


@pytest.mark.parametrize("path,list_id,marker,seed", PAGES, ids=IDS)
def test_the_disclosure_is_closed_when_there_is_something_to_show(
        client_a, users, path, list_id, marker, seed):
    seed(users)
    html = client_a.get(path).get_data(as_text=True)
    panel = _details_around(html, marker)
    assert panel is not None
    assert " open" not in panel.split(">")[0], \
        f"{path}: the form is expanded even though the page has content"


@pytest.mark.parametrize("path,list_id,marker,seed", PAGES, ids=IDS)
def test_the_disclosure_is_open_when_there_is_nothing_yet(
        admin_client, path, list_id, marker, seed):
    """⚠️ The case that must NOT be a collapsed form on an otherwise blank
    page — that is a dead end for a new user.

    `admin_client` is the only fixture whose user owns no data at all (it is
    deliberately independent of the `users` fixture), which is exactly the
    state a brand-new account is in. Two pages suppress their add form
    entirely without prerequisites — Goals needs an account, Transfer needs
    two — and those guards predate this change, so the form is simply absent
    rather than collapsed.
    """
    html = admin_client.get(path).get_data(as_text=True)
    panel = _details_around(html, marker)
    if panel is None:
        assert marker not in html, \
            f"{path}: an add form is present but not inside the disclosure"
        pytest.skip(f"{path} renders no add form without its prerequisites")
    assert " open" in panel.split(">")[0], \
        f"{path}: a user with nothing yet meets a collapsed form"
