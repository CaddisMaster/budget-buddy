"""#305 — the running version is observable, so a deploy can be verified.

The gap this closes: nothing the app served said which version it was running,
so "did the deploy land?" reached for a different improvised handle every
release. `css_v` proved nothing at `0.4.1` and `0.7.0` (neither release touched
a static asset) and worked at `0.8.0` only because the front-end overhaul
happened to rewrite the stylesheet. Meanwhile #190's real failure — production
sitting three releases stale with `/healthz` green — stayed unobservable.

⚠️ The version is read from the environment, and `APP_VERSION` is baked into the
image by a build arg rather than supplied from the Droplet's `.env`. `TAG` there
records what compose was *told* to pull; this records what the image *is*. The
distinction is the entire point, so do not "simplify" the reader to look at TAG.

⚠️ Every test here sets `APP_VERSION`/`APP_COMMIT` EXPLICITLY. The dev container
is built from the same Dockerfile and therefore carries the `dev` defaults, so a
test that assumes "unset" would be asserting the ambient build rather than the
rule.
"""
import json

import pytest

from app.helpers import DEV_VERSION, app_commit, app_version


@pytest.fixture
def clean_env(monkeypatch):
    """Neither variable set — the state of a bare `docker build .`."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("APP_COMMIT", raising=False)
    return monkeypatch


# --- the pure rule ---------------------------------------------------------

def test_an_unstamped_build_reports_dev(clean_env):
    """CI's docker-build job and a bare `docker build .` pass no build arg. The
    fallback has to be a real string: a None here reaches Jinja, renders as an
    empty cell, and looks exactly like a stamped build whose version went
    missing."""
    assert app_version() == DEV_VERSION
    assert app_commit() == DEV_VERSION


def test_a_stamped_build_reports_what_it_was_built_as(clean_env):
    clean_env.setenv("APP_VERSION", "0.9.0")
    clean_env.setenv("APP_COMMIT", "a5ecf27")
    assert app_version() == "0.9.0"
    assert app_commit() == "a5ecf27"


def test_a_blank_or_whitespace_stamp_reads_as_dev(clean_env):
    """`--build-arg APP_VERSION=` sets the variable to the empty string rather
    than leaving it unset, so a truthiness check is not enough on its own. An
    empty version rendering as an empty cell is the failure this prevents."""
    for blank in ("", "   ", "\t"):
        clean_env.setenv("APP_VERSION", blank)
        clean_env.setenv("APP_COMMIT", blank)
        assert app_version() == DEV_VERSION
        assert app_commit() == DEV_VERSION


def test_the_stamp_is_stripped(clean_env):
    """A trailing newline survives some build-arg plumbing and would render as a
    ragged cell."""
    clean_env.setenv("APP_VERSION", " 0.9.0\n")
    assert app_version() == "0.9.0"


# --- the rendered panel ----------------------------------------------------

def test_admin_sees_the_running_version_and_commit(admin_client, clean_env):
    clean_env.setenv("APP_VERSION", "0.9.0")
    clean_env.setenv("APP_COMMIT", "a5ecf27")

    body = admin_client.get("/settings").get_data(as_text=True)

    assert "0.9.0" in body
    assert "a5ecf27" in body


def test_the_panel_says_when_a_build_is_unstamped(admin_client, clean_env):
    """A local `docker compose up` renders this page too, and a blank cell there
    would read as a fault in the panel rather than as an unreleased build.

    ⚠️ Asserted on the EXPLANATION, not on `"dev" in body` — three letters that
    appear inside plenty of ordinary words, so that check would pass against a
    page with no Deployment card at all. This phrase renders only through the
    `dev_build` branch, so it also catches the flag going undefined (Jinja
    renders an unknown name as an empty string rather than raising)."""
    body = admin_client.get("/settings").get_data(as_text=True)
    assert "built locally, not from a release" in body


def test_a_non_admin_cannot_see_the_running_version(client_a, clean_env):
    """Same boundary as the Integrations panel directly above it (#139): which
    build is running is deployment detail, and every row on this page is
    admin-only."""
    clean_env.setenv("APP_VERSION", "0.9.0-secret")

    response = client_a.get("/settings", follow_redirects=True)

    assert "0.9.0-secret" not in response.get_data(as_text=True)


def test_an_anonymous_visitor_is_redirected_to_login(anon_client, clean_env):
    clean_env.setenv("APP_VERSION", "0.9.0-secret")

    response = anon_client.get("/settings")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --- the leak boundary -----------------------------------------------------

def test_healthz_still_says_nothing_about_the_version(anon_client, clean_env):
    """⚠️ Load-bearing, and it contradicts what `CLAUDE.md` proposed for years.
    `/healthz` is the one endpoint guaranteed reachable by anyone, so it is the
    last place to name a version — `main.healthz`'s docstring and
    `admin.integration_status`'s first rule both say so. This states that as a
    test rather than as prose, because "add the version to /healthz" is an
    obvious-looking improvement that a future session would otherwise make.

    Asserted against the raw body, not the parsed keys: a version smuggled into
    the `status` string would satisfy a `"version" not in body` key check."""
    clean_env.setenv("APP_VERSION", "0.9.0-secret")

    response = anon_client.get("/healthz")

    assert response.status_code == 200
    assert "0.9.0-secret" not in response.get_data(as_text=True)
    assert set(json.loads(response.data)) == {"status", "database"}
