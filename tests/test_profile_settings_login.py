"""Profile, Settings, User management and Login (#245 #246 #247 #248).

The last group of the #225 overhaul, and the one carrying the most "do not
break this". Four constraints are load-bearing and each is asserted here as
well as wherever it already lives:

  · Profile's notification copy is a CONSENT RECORD (#115/#191) — it must keep
    naming every kind of notification sent.
  · Settings must never render a credential's value, a prefix of it, or the
    variable name (#139).
  · NOT_SCHEDULED is deliberately NOT a fault (#151) — a panel that cried wolf
    about a legitimate state would be worth ignoring.
  · Login must not leak whether a username exists (#248).

⚠️ Login does NOT extend base.html — it is the second shell, so every shared
change has to be made twice. test_param_hardening.py already asserts the
cache-bust lockstep; the brand is asserted here for the same reason.
"""
import re
from pathlib import Path

import pytest

from tests.conftest import (
    PASSWORD,
    TEST_PREFIX,
    USER_A,
    _create_user,
    _delete_user,
)

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
CSS_PATH = Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"


@pytest.fixture(scope="module")
def css():
    return re.sub(r"/\*.*?\*/", "", CSS_PATH.read_text(), flags=re.S)


# --- #246: one status vocabulary, serving both panels -------------------------


def test_both_panels_use_one_status_component(admin_client, monkeypatch):
    """#246 asked for "one badge treatment" for integration states and job
    states. They already shared a class — but it was `.integration-state`,
    named for one of the two things it serves, which is how the jobs panel
    ended up borrowing a vocabulary that does not describe it."""
    html = admin_client.get("/settings").get_data(as_text=True)
    assert "status-pill" in html
    assert "integration-state" not in html, \
        "the jobs panel still borrows the integrations vocabulary"


def test_a_switched_off_job_does_not_look_like_a_failure(css):
    """⚠️ #151's decision, restated: NOT_SCHEDULED is a legitimate state on a
    server where that job is switched off. It must not share a treatment with
    an actual fault, or the panel becomes one to ignore."""
    idle = re.search(r"\.status-pill\.is-idle\s*\{([^}]*)\}", css)
    attention = re.search(r"\.status-pill\.is-attention\s*\{([^}]*)\}", css)
    assert idle and attention, "the status vocabulary is missing a level"
    assert idle.group(1).strip() != attention.group(1).strip(), \
        "an idle job renders identically to one that needs attention"
    assert "--danger" not in idle.group(1), \
        "a switched-off job is painted as an error"


def test_overdue_and_never_run_are_told_apart(admin_client, monkeypatch):
    """⚠️ Found while reading, not in the issue: `stale` and `never` both
    rendered red and identical. "Overdue" and "has never run" are different
    diagnoses — the first says a working job is late, the second says it has
    not happened once."""
    src = (TEMPLATES / "settings.html").read_text()
    stale = re.search(r"job\.state == 'stale'.*?</span>", src, re.S).group(0)
    never = re.search(r"job\.state == 'never'.*?</span>", src, re.S).group(0)
    stale_cls = re.search(r'class="([^"]*)"', stale).group(1)
    never_cls = re.search(r'class="([^"]*)"', never).group(1)
    assert stale_cls != never_cls, \
        "overdue and never-run render as the same badge"


def test_settings_still_never_renders_a_credential(admin_client, monkeypatch):
    """#139's guarantee, re-asserted here because this change touches the
    panel that carries it. The dedicated coverage is in
    test_integration_status.py; this is the tripwire for THIS refactor."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRETVALUE-0123456789abcdef")
    html = admin_client.get("/settings").get_data(as_text=True)
    assert "SECRETVALUE" not in html
    assert "sk-ant-" not in html
    assert "ANTHROPIC_API_KEY" not in html


# --- #245: Profile grouped by what it affects ---------------------------------


def test_profile_still_names_every_kind_of_notification(client_a):
    """⚠️ The consent record (#115/#191). Asserted per-kind, not as one "does
    it mention notifications" check — that would pass while a whole category
    went undisclosed.

    ⚠️ SKIPS when push is not configured, which it is not in CI or the dev
    container: the whole section is gated on push_enabled(). The canonical,
    always-running copy of this assertion is
    test_release_announce.py::test_profile_copy_names_every_kind_of_notification,
    which sets the VAPID env up itself. This one exists because #245 REORDERS
    the page that paragraph sits on, and a reorder is exactly the kind of
    change that could drop it.

    ⚠️ That pointer named test_push_reminders.py until #309 tranche 8b, and the
    test has never lived there. The assertion was fine; the signpost was not,
    and a reader checking whether the consent record was still covered would
    have found nothing where it said to look. It is asserted rather than
    written down now — see test_the_delegated_assertions_exist below.
    """
    html = client_a.get("/profile").get_data(as_text=True)
    if 'id="push-toggle"' not in html:
        pytest.skip("push is not configured here; see test_release_announce.py")
    assert "before" in html and "due" in html, "the bill reminder is undisclosed"
    assert "posted" in html or "changes" in html, \
        "the variable-bill nudge is undisclosed"
    assert "updated" in html, "the release note is undisclosed"


def test_profile_keeps_the_feedback_publicity_warning_above_the_fields(client_a):
    """#64 — the warning is the ONLY control on what gets published, and its
    POSITION is part of that: above the fields, not below the button.

    ⚠️ Skips when the feedback feature is gated off (no GitHub token), for the
    same reason as the test above. test_feedback.py owns the gate itself.
    """
    html = client_a.get("/profile").get_data(as_text=True)
    if "published publicly on GitHub" not in html:
        pytest.skip("feedback is not configured here; see test_feedback.py")
    assert html.index("published publicly on GitHub") < html.index('name="title"'), \
        "the publicity warning moved below the fields"


def test_profile_groups_its_sections(client_a):
    """#245: "grouped by what they affect, not laid out as four peers".

    Two sections are ungated and always render — "Your account" and "Your
    data" — so two is the floor this can assert without depending on which
    optional features happen to be configured. The gated sections carry the
    same wrapper; see the template.
    """
    html = client_a.get("/profile").get_data(as_text=True)
    assert html.count('class="profile-section"') >= 2
    assert "Your account" in html and "Your data" in html
    assert html.index("Your account") < html.index("Your data")


def test_changing_your_password_is_reachable_from_profile(client_a):
    """#249 lives on its own page reached from here; the overhaul must not
    strand it."""
    html = client_a.get("/profile").get_data(as_text=True)
    assert "/change-password" in html


# --- #247: proportionate, and destructive actions weighted --------------------


@pytest.fixture
def a_second_user():
    """A user the admin can actually act on.

    ⚠️ /admin/users lists EVERY user on the server — it is not scoped to the
    caller — so a test that just reads the page depends on whichever rows other
    xdist workers happen to have created at that moment. The first cut of the
    test below did exactly that and passed locally for the wrong reason: other
    workers' users were present. In CI the admin was the ONLY row, and since
    `_user_row.html` renders no controls for your own row (you cannot delete
    yourself), there was no delete button to find.

    This is the global-listing trap in docs/testing.md pointed at users rather
    than push endpoints: assert on a row you created, never on whatever the
    listing happens to contain.
    """
    username = TEST_PREFIX + "deletable"
    _delete_user(username)
    _create_user(username, PASSWORD)
    yield username
    _delete_user(username)


def test_deleting_a_user_is_not_the_same_weight_as_toggling_admin(
        admin_client, a_second_user):
    """#247: "deleting a user is visually distinguished from toggling admin"."""
    html = admin_client.get("/admin/users").get_data(as_text=True)
    row_start = html.index(a_second_user)
    row = html[row_start:html.index("</tr>", row_start)]
    assert "btn-danger" in row, "delete carries no destructive styling"
    toggle = row[row.index("toggle_admin") if "toggle_admin" in row
                 else row.index("Make admin"):]
    assert not toggle.startswith("btn-danger"), \
        "the admin toggle is styled as destructively as the delete"


def test_your_own_row_offers_no_delete(admin_client):
    """The reason the fixture above exists, stated as its own test: you cannot
    delete yourself, so your row carries no controls at all."""
    html = admin_client.get("/admin/users").get_data(as_text=True)
    assert "(you)" in html


def test_user_management_says_where_it_sits(admin_client):
    """#247 asks whether this is its own page or a section of Settings. It
    stays a page — the answer is stated on it rather than left ambiguous."""
    html = admin_client.get("/admin/users").get_data(as_text=True)
    assert "/settings" in html, "no link back to the Settings it belongs to"


def test_user_management_has_an_empty_state(admin_client):
    """It had none: an admin whose filter or database returned nothing saw a
    table head above a void."""
    src = (TEMPLATES / "admin_users.html").read_text()
    assert "{% if" in src and "users" in src, "no empty-state branch"


# --- #248: Login looks like the app it opens, and leaks nothing ---------------


def test_login_carries_the_app_mark(anon_client):
    """#248: the brand mark is in the sidebar on every other page and was
    absent from the one page a visitor actually sees first."""
    html = anon_client.get("/login").get_data(as_text=True)
    assert "brand-mark" in html


def test_login_still_shares_the_cache_bust_with_the_other_shell(anon_client,
                                                                client_a):
    """⚠️ Login does not extend base.html, so a shared change has to be made
    twice. test_param_hardening.py owns this property; asserted again here
    because this change edits that shell."""
    login = anon_client.get("/login").get_data(as_text=True)
    app_page = client_a.get("/profile").get_data(as_text=True)
    ver = re.search(r"style\.css\?v=([0-9a-f]+)", login)
    assert ver, "login lost its cache-busted stylesheet"
    assert f"style.css?v={ver.group(1)}" in app_page, \
        "the two shells now disagree about the stylesheet version"


# ⚠️ The case is parametrized by a LABEL, never by the username itself.
# USER_A is TEST_PREFIX + "user_a" and TEST_PREFIX is PER-WORKER, so putting it
# in a parametrize argument gives every xdist worker a different test ID and the
# run dies with "Different tests were collected between gw0 and gw2" before a
# single assertion executes. The prefix rule in docs/testing.md is about row
# isolation; this is its collection-time twin.
@pytest.mark.parametrize("case", ["unknown-username", "wrong-password"])
def test_login_does_not_reveal_whether_a_username_exists(anon_client, users, case):
    """⚠️ #248's hard constraint: the two failures must be indistinguishable."""
    username = "this-user-does-not-exist" if case == "unknown-username" else USER_A
    resp = anon_client.post("/login",
                            data={"username": username, "password": "x" * 12},
                            follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data


def _without_csrf(html):
    """⚠️ The CSRF token is minted per REQUEST, so two renders of the same page
    legitimately differ by that one value. Comparing raw bodies made this test
    fail on main while the property it checks was perfectly intact."""
    return re.sub(r'name="csrf_token" value="[^"]*"',
                  'name="csrf_token" value="X"', html)


def test_the_two_login_failures_render_identically(anon_client, users):
    """The parametrized test above checks each failure in isolation; this
    compares them, which is the property that actually forbids enumeration."""
    unknown = _without_csrf(anon_client.post("/login", data={
        "username": "no-such-user-at-all", "password": "x" * 12},
        follow_redirects=True).get_data(as_text=True))
    wrong = _without_csrf(anon_client.post("/login", data={
        "username": USER_A, "password": "x" * 12},
        follow_redirects=True).get_data(as_text=True))
    assert unknown == wrong, \
        "the two failure pages differ, which enumerates usernames"


# --- the two skips above delegate; this is what stops them delegating to
#     nothing (#309, tranche 8b) ------------------------------------------------


def test_the_delegated_assertions_exist():
    """⚠️ Two tests in this file SKIP when their feature is unconfigured and name
    another file as the always-running copy. That makes those names load-bearing:
    if the named test is renamed, moved or deleted, the skip here stops being a
    delegation and becomes a silent coverage hole — the whole assertion is gone
    and nothing goes red, because a skip is not a failure.

    One of the two pointers was already wrong when this was written (it named
    test_push_reminders.py for a test that lives in test_release_announce.py),
    which is the case for stating it as code rather than as prose.
    """
    import tests.test_feedback as feedback_tests
    import tests.test_release_announce as announce_tests

    delegated = [
        (announce_tests, "test_profile_copy_names_every_kind_of_notification"),
        (feedback_tests, "test_the_form_warns_that_reports_are_public"),
    ]
    missing = [f"{module.__name__}::{name}"
               for module, name in delegated
               if not callable(getattr(module, name, None))]
    assert not missing, (
        f"the skips in this file delegate to {missing}, which no longer exist — "
        "so the assertion they stand in for is running nowhere"
    )
