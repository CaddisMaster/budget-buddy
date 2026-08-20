"""#272 — a password change signs out every other device.

Split out of #224, which asked whether there are session limits. There are — 365
days — but the answer that mattered is that nothing shortened it. `models.py`
used `UserMixin`'s default `get_id()`, so the session cookie identified a user by
**primary key alone** and carried nothing the server could invalidate. With
`login_user(user, remember=True)` unconditional (v10.13, so an installed PWA does
not re-prompt on launch) and no `REMEMBER_COOKIE_DURATION`, a cookie on a device
you no longer control authenticated for a year — and changing your password,
the one thing a worried user actually does, revoked none of it.

`users.session_token` (`sql/37`, #271) is now folded into the session id, and
`change_password` rotates it.

⚠️ Two properties are easy to get wrong in opposite directions, and both are
tested below:

1. **Fail CLOSED.** A cookie carrying a stale or malformed id must produce an
   ANONYMOUS request, never a 500. The pre-#272 cookie format is a bare `"42"`
   with no token at all, and `load_user` is called with whatever is in the
   cookie — including, after this ships, every outstanding cookie in existence.
2. **Do not sign out the device doing the change.** Rotating the token
   invalidates the acting session too, so `change_password` has to re-issue it.
   Getting this wrong logs you out every time you change your password, which
   reads as a bug rather than as security.
"""
import pytest
from conftest import PASSWORD, _login

from app.db import db_cursor
from app.models import User


def _token(user_id):
    with db_cursor() as cursor:
        cursor.execute("SELECT session_token FROM users WHERE id = %s", (user_id,))
        return str(cursor.fetchone()[0])


def _change_password(client, current, new):
    return client.post("/change-password", data={
        "current_password": current,
        "new_password": new,
    }, follow_redirects=False)


# --- The session id carries the token --------------------------------------

def test_get_id_carries_the_session_token(users):
    """Without this the cookie says only "user 42" and nothing is revocable."""
    user = User.get_by_id(users["a"]["id"])
    assert user.get_id() == f"{users['a']['id']}:{_token(users['a']['id'])}"


def test_a_loaded_user_exposes_its_token(users):
    for loader, arg in ((User.get_by_id, users["a"]["id"]),
                        (User.get_by_username, users["a"]["username"])):
        assert str(loader(arg).session_token) == _token(users["a"]["id"])


# --- Failing closed ---------------------------------------------------------

@pytest.mark.parametrize("cookie_id", [
    "",
    "notanumber",
    ":sometoken",
    "1:2:3",
])
def test_a_malformed_session_id_is_anonymous_not_an_error(app, cookie_id):
    """A raised exception here would be a 500 on every page for every logged-in
    user until they cleared their cookies — `load_user` runs before any route's
    own error handling. These shapes are rejected before any database lookup."""
    from app import load_user
    assert load_user(cookie_id) is None


@pytest.mark.parametrize("token", [
    "",
    "not-a-uuid",
    "1:2",
    "ü",                 # ⚠️ see below
    "étoken",
    "token\x00",
    "x" * 10000,
])
def test_a_bad_token_on_a_REAL_user_is_anonymous_not_an_error(users, token):
    """⚠️ The id here must EXIST, and that is the entire point of this test.

    Written against a made-up id, `User.get_by_id` returns None and `load_user`
    exits before it ever compares tokens — so every case below "passes" while
    the comparison itself is never executed. That is exactly what happened: the
    non-ASCII cases went green against a version where they raised.

    `secrets.compare_digest` raises `TypeError: comparing strings with non-ASCII
    characters is not supported` when given `str`. Reaching it with `"ü"` on a
    real user therefore raised, breaking the fail-closed contract. The fix is to
    compare BYTES; these cases are the regression test.
    """
    from app import load_user
    assert load_user(f"{users['a']['id']}:{token}") is None


def test_a_non_ascii_token_does_not_raise(users):
    """Stated separately from the parametrized set above so the failure mode has
    a name in the output. A TypeError here is a 500 on every authenticated page,
    not a failed login."""
    from app import load_user
    try:
        assert load_user(f"{users['a']['id']}:ü") is None
    except TypeError as exc:  # pragma: no cover - the regression itself
        pytest.fail(f"load_user must never raise; it raised {exc!r}")


def test_the_pre_272_cookie_format_is_rejected(users):
    """⚠️ Deliberately uses a REAL user's id, and that is the whole point.

    Every outstanding cookie carries a bare `"<id>"` the moment this ships, so
    this is the normal path on deploy day rather than an edge case. Written
    against an id that does NOT exist — the obvious version — the assertion
    passes whether or not the format is rejected, because a missing user returns
    None anyway. It would have gone green against the pre-#272 loader, which is
    exactly the vacuous pass it is here to avoid.
    """
    from app import load_user
    uid = users["a"]["id"]
    assert load_user(f"{uid}:{_token(uid)}") is not None   # the token form works
    assert load_user(str(uid)) is None                     # the bare id does not


def test_a_stale_token_no_longer_loads_the_user(users):
    """The property the whole change rests on."""
    from app import load_user
    uid = users["a"]["id"]
    stale = load_user(f"{uid}:{_token(uid)}")
    assert stale is not None                      # the CURRENT token works
    with db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE users SET session_token = gen_random_uuid() WHERE id = %s", (uid,))
    assert load_user(f"{uid}:{stale.session_token}") is None


# --- The behaviour ----------------------------------------------------------

def test_a_password_change_rotates_the_token(users, client_a):
    uid = users["a"]["id"]
    before = _token(uid)
    resp = _change_password(client_a, PASSWORD, "brand-new-password-1")
    assert resp.status_code == 302
    assert _token(uid) != before


def test_another_device_is_signed_out(app, users, client_a):
    """The acceptance criterion, driven through two real clients.

    `client_a` changes the password; the second client, logged in beforehand and
    holding its own cookie, must land on the login page rather than the app.
    """
    other = app.test_client()
    _login(other, users["a"]["username"])
    assert other.get("/transactions").status_code == 200      # signed in before

    _change_password(client_a, PASSWORD, "brand-new-password-2")

    after = other.get("/transactions")
    assert after.status_code == 302
    assert "/login" in after.headers["Location"]


def test_the_device_doing_the_change_stays_signed_in(users, client_a):
    """⚠️ The mirror of the test above, and the one that is easy to lose.

    Rotating the token invalidates the ACTING session too, so `change_password`
    re-issues it. Without that, changing your password logs you out every single
    time — which reads as a bug, and is the kind of regression a test asserting
    only "other devices are signed out" would happily allow.
    """
    resp = _change_password(client_a, PASSWORD, "brand-new-password-3")
    assert resp.status_code == 302
    assert "/login" not in resp.headers["Location"]
    assert client_a.get("/transactions").status_code == 200


def test_a_rejected_password_change_rotates_nothing(users, client_a):
    """A wrong current password must not sign anybody out — otherwise the form
    becomes a denial-of-service against your own other devices."""
    uid = users["a"]["id"]
    before = _token(uid)
    _change_password(client_a, "definitely-not-the-password", "brand-new-password-4")
    assert _token(uid) == before
    assert client_a.get("/transactions").status_code == 200


def test_a_too_short_password_rotates_nothing(users, client_a):
    uid = users["a"]["id"]
    before = _token(uid)
    _change_password(client_a, PASSWORD, "short")
    assert _token(uid) == before


def test_logging_in_normally_still_works_afterwards(app, users, client_a):
    """The new password works, and the fresh session carries the current token."""
    _change_password(client_a, PASSWORD, "brand-new-password-5")
    fresh = app.test_client()
    _login(fresh, users["a"]["username"], "brand-new-password-5")
    assert fresh.get("/transactions").status_code == 200


def test_one_users_change_does_not_touch_another(users, client_a, client_b):
    """Isolation, asserted the way every other feature file does."""
    b_before = _token(users["b"]["id"])
    _change_password(client_a, PASSWORD, "brand-new-password-6")
    assert _token(users["b"]["id"]) == b_before
    assert client_b.get("/transactions").status_code == 200
