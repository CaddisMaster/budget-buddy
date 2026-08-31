"""Route smoke + auth tests.

Smoke: every main page returns 200 for a logged-in user (catches template/query
regressions). Auth: EVERY route bounces anonymous users to /login unless it is
on the public allowlist, and the login form accepts good credentials / rejects
bad ones.
"""
import re

import pytest

from app import app as flask_app
from tests.conftest import USER_A

# The main pages, for the render smoke test below. This one is deliberately a
# hand-picked list — it is about which pages are worth rendering, not about
# coverage — and it is NOT what guards authentication; see PUBLIC_ROUTES.
PROTECTED_PAGES = [
    "/",
    "/transactions",
    "/transactions/new",
    "/scheduled",
    "/categories",
    "/accounts",
    "/budgets",
    "/transfers",
    "/goals",
    "/profile",
]


# ⚠️ The allowlist is the PUBLIC routes, not the protected ones (#309, tranche
# 8b). Every endpoint the app registers is required to bounce an anonymous
# request unless it is named here, so a new route is protected-by-default as far
# as this file is concerned and opening one up is a visible edit to this list
# with a reason beside it. The list it replaces ran the other way round and
# covered 10 of 79 method/route pairs — 13% — so a route that shipped without
# `@login_required` would have been asserted by nothing at all.
#
# Nothing was actually unguarded when this was written: all 79 pairs were
# checked and only these four answered. The point is that nothing could have
# noticed if that changed.
PUBLIC_ROUTES = {
    ("GET", "main.healthz"): "the liveness probe — a monitor cannot log in (#11)",
    ("GET", "auth.login"): "the login form itself",
    ("POST", "auth.login"): "the login form itself",
    ("GET", "main.service_worker"): "the browser re-fetches the SW outside any session",
    # Two legacy-URL stubs that redirect BEFORE auth, documented as such in
    # analytics.py and main.dashboard: `/` enforces auth after the hop, so an
    # anonymous visitor still lands on /login, one redirect later.
    ("GET", "analytics.analytics"): "legacy URL — redirects to /, which enforces auth",
    ("GET", "main.dashboard"): "legacy URL — redirects to /, which enforces auth",
}

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _fill(rule):
    """A concrete path for a rule, so parameterised routes can be requested.

    The ids are never real, which is the point: `@login_required` runs before
    any ownership lookup, so an anonymous request must be turned away before the
    id is ever used. A route that 404s on the bogus id instead of redirecting is
    exactly the ordering mistake this test is looking for.
    """
    return re.sub(r"<[^>]+>", "1", re.sub(r"<int:[^>]+>", "1", str(rule)))


def _every_route():
    """(method, endpoint, path) for every rule the app registers."""
    return sorted(
        (method, rule.endpoint, _fill(rule.rule))
        for rule in flask_app.url_map.iter_rules()
        if rule.endpoint != "static"
        for method in rule.methods
        if method in _METHODS
    )


@pytest.mark.parametrize("path", PROTECTED_PAGES)
def test_logged_in_user_gets_200(client_a, path):
    response = client_a.get(path)
    assert response.status_code == 200


def test_every_route_requires_login_unless_it_is_on_the_allowlist(anon_client):
    """⚠️ Derived from the URL map, not from a list of paths someone remembered.

    This is the `AI_SURFACES` / `THEME_TOKENS` shape pointed at authentication:
    a hand-maintained list of protected pages can only ever fail for a member
    somebody added, so it goes quieter as the app grows rather than red.
    """
    routes = _every_route()
    assert len(routes) > 60, (
        f"only {len(routes)} method/route pairs found — the URL map scan is "
        "broken, not the app. Without this floor the loop below checks nothing."
    )

    open_to_anonymous = []
    for method, endpoint, path in routes:
        if (method, endpoint) in PUBLIC_ROUTES:
            continue
        response = anon_client.open(path, method=method)
        location = response.headers.get("Location", "")
        if not (response.status_code == 302 and "/login" in location):
            open_to_anonymous.append(
                f"{method} {path} ({endpoint}) -> {response.status_code} {location}")

    assert not open_to_anonymous, (
        "these routes answered an anonymous request instead of redirecting to "
        "/login; if that is deliberate, add it to PUBLIC_ROUTES with a reason:\n"
        + "\n".join(open_to_anonymous)
    )


def test_the_public_allowlist_names_only_routes_that_exist(app):
    """The other direction, and the one derivation cannot cover.

    A retired public route left in the allowlist is a standing exemption for an
    endpoint name that a future route could reuse. Discovery cannot notice that
    — only the list can — which is why both halves are here.
    """
    registered = {(method, endpoint) for method, endpoint, _path in _every_route()}
    stale = sorted(entry for entry in PUBLIC_ROUTES if entry not in registered)
    assert not stale, f"PUBLIC_ROUTES exempts routes the app no longer has: {stale}"


def test_the_allowlist_is_the_short_list(anon_client):
    """A guard that exempts everything guards nothing. Stated as a ceiling so
    that "just add it to PUBLIC_ROUTES" cannot quietly become the fix for a
    genuinely unprotected route."""
    assert len(PUBLIC_ROUTES) <= 8, (
        "the public allowlist has grown — each entry is a route serving "
        "anonymous requests, so this wants a deliberate look, not a bump"
    )
    assert all(reason.strip() for reason in PUBLIC_ROUTES.values()), \
        "every exemption carries a reason"


def test_login_with_valid_credentials_redirects(anon_client, users):
    response = anon_client.post(
        "/login",
        data={"username": USER_A, "password": "test-password-123"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" not in response.headers["Location"]


def test_login_with_bad_password_stays_on_login(anon_client, users):
    response = anon_client.post(
        "/login",
        data={"username": USER_A, "password": "wrong-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_dashboard_shows_hero_summary(client_a):
    """v10.6 Refresh — the dashboard (the home page since v10.13) renders the
    at-a-glance hero (net position + income/expenses) for a user with
    transactions."""
    response = client_a.get("/")
    assert response.status_code == 200
    assert b"Net position" in response.data
    # USER_A is seeded with a single $42.50 expense, so it surfaces in the hero.
    assert b"42.50" in response.data


def test_legacy_dashboard_url_redirects_home(client_a):
    """v10.13 merge — /dashboard became /; the stub keeps old bookmarks alive
    (the /analytics precedent)."""
    response = client_a.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_legacy_dashboard_redirect_carries_month_filter(client_a):
    response = client_a.get("/dashboard?month=2026-05")
    assert response.status_code == 302
    assert "month=2026-05" in response.headers["Location"]


def test_global_quick_add_button_renders(client_a):
    """v10.13 nav regroup — "Add transaction" left the sidebar for a persistent
    quick-add button in the topbar (FAB on mobile) present on every page."""
    response = client_a.get("/transactions")
    assert b'class="quick-add-btn"' in response.data
    assert b'href="/transactions/new"' in response.data


def test_logout_redirects_to_login(client_a):
    response = client_a.post("/logout")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_logout_rejects_get(client_a):
    """v10.10.1 — a GET logout is CSRF-able (any cross-site <img src="/logout">
    would log the user out), so the route is POST-only."""
    response = client_a.get("/logout")
    assert response.status_code == 405


def test_logout_control_asks_for_confirmation(client_a):
    """#35 — logout sits next to ordinary navigation and a mis-tap on the
    installed PWA costs a re-login on a touch keyboard, so the control confirms
    before it fires. The guard is on the form's submit, which keeps the route
    POST-only (see test_logout_rejects_get) and keeps the CSRF token on a real
    form POST rather than moving logout behind a GET-able confirmation page."""
    body = client_a.get("/").data.decode()
    form = re.search(r"<form[^>]*nav-logout[^>]*>", body)
    assert form, "logout form is missing from the nav"
    assert "onsubmit" in form.group(0)
    assert "confirm(" in form.group(0)
    assert 'method="post"' in form.group(0)
