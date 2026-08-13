"""#115 — the release announcement broadcast.

No real Web Push calls: pusher._call_webpush is monkeypatched, the same seam
test_push_reminders.py stubs, and the VAPID keys are set per-test so
push_enabled() flips deliberately rather than depending on the environment.

⚠️ This is the ONE push path that is not scoped to a user, which makes it the one
place a global SELECT can see another xdist worker's rows. Every assertion here
is therefore written against THIS worker's own endpoints (built from TEST_PREFIX)
rather than against a total count, and the failing-seam stubs fail SELECTIVELY by
endpoint — a stub that raised PushGone unconditionally would delete a parallel
worker's subscriptions and fail a file nobody touched.
"""
import json

import pytest

import app.pusher as pusher
from app.blueprints.announce import (
    BODY,
    broadcast_release,
    build_release_notification,
)
from app.db import get_db_connection
from tests.conftest import TEST_PREFIX

# ⚠️ Shares one worker with every other file that drives a global sweep (#157).
# broadcast_release() is THE deliberately not-user-scoped push path — it writes
# to every row of push_subscriptions by design — so it reaches other workers'
# devices, and a PushGone from this file's seam would delete their rows.
pytestmark = pytest.mark.xdist_group("scheduler_sweep")

ENDPOINT_A1 = f"https://push.example/{TEST_PREFIX}a1"
ENDPOINT_A2 = f"https://push.example/{TEST_PREFIX}a2"
ENDPOINT_B1 = f"https://push.example/{TEST_PREFIX}b1"


# --- helpers ----------------------------------------------------------------

def _enable_push(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-private")


def _disable_push(monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)


def _capture(monkeypatch, fail_for=None):
    """Stub the network seam; return the list it records sends into.

    `fail_for` maps an endpoint to the exception it should raise. Failures are
    keyed by endpoint on purpose — see the module docstring.
    """
    fail_for = fail_for or {}
    sent = []

    def fake(subscription_info, data, private_key, subject):
        endpoint = subscription_info["endpoint"]
        if endpoint in fail_for:
            raise fail_for[endpoint]
        sent.append({"endpoint": endpoint, "payload": json.loads(data)})
        return {"status_code": 201}

    monkeypatch.setattr(pusher, "_call_webpush", fake)
    return sent


def _add_subscription(user_id, endpoint):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
        "VALUES (%s, %s, 'p256dh-x', 'auth-x') "
        "ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id",
        (user_id, endpoint))
    conn.commit()
    cur.close()
    conn.close()


def _endpoint_exists(endpoint):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
    found = cur.fetchone() is not None
    cur.close()
    conn.close()
    return found


def _endpoints_sent(sent):
    return {s["endpoint"] for s in sent}


# --- the pure builder -------------------------------------------------------

def test_title_names_the_version():
    note = build_release_notification("0.4.1")
    assert note["title"] == "Version 0.4.1 is live"


def test_title_does_not_name_the_app():
    """#133 — Chrome renders its OWN attribution line ("from Budget Buddy",
    from manifest.json's `name`) under the title, and nothing in this app can
    suppress it. Naming the app in the title too is what printed the phrase
    twice on the lock screen. This is the whole point of the change, and it is
    invisible in the payload — the duplicate only shows on a real device."""
    title = build_release_notification("0.4.1")["title"]
    assert "Budget Buddy" not in title


def test_tap_target_is_the_dashboard():
    # Where the .whatsnew strip for this release renders.
    assert build_release_notification("0.4.1")["url"] == "/"


def test_body_is_the_fixed_line():
    assert build_release_notification("0.4.1")["body"] == BODY
    assert BODY == "Check out what's new in the app!"


def test_only_the_version_varies_between_releases():
    """#131 — the body is evergreen. Nothing is re-authored per release, so the
    only difference between two releases' notifications is the version."""
    a = build_release_notification("0.4.1")
    b = build_release_notification("9.9.9")
    assert a["body"] == b["body"]
    assert a["url"] == b["url"]
    assert a["title"] != b["title"]


def test_the_builder_takes_no_release_text():
    """⚠️ The load-bearing one. Release notes are free text and release.yml builds
    its remote command by interpolation — carrying that text is what needed a
    base64 hop and a truncator. Not accepting it at all is what deleted that
    surface, so a second argument reappearing here is a real regression."""
    import inspect

    params = list(inspect.signature(build_release_notification).parameters)
    assert params == ["version"]


def test_payload_is_tiny():
    # Web Push tops out around 4KB. A fixed line cannot approach it.
    payload = json.dumps(build_release_notification("0.4.1"))
    assert len(payload.encode("utf-8")) < 256


# --- the broadcast ----------------------------------------------------------

def test_does_nothing_when_push_is_unconfigured(monkeypatch, users):
    _disable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(users["a"]["id"], ENDPOINT_A1)

    assert broadcast_release("0.4.1") == 0
    assert sent == []  # the seam is never reached


def test_reaches_every_users_devices(monkeypatch, users):
    """The not-user-scoped property — the single thing that separates this query
    from every other one in the codebase."""
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(users["a"]["id"], ENDPOINT_A1)
    _add_subscription(users["a"]["id"], ENDPOINT_A2)
    _add_subscription(users["b"]["id"], ENDPOINT_B1)

    count = broadcast_release("0.4.1")

    # A parallel worker may own rows too, so assert on ours rather than a total.
    assert {ENDPOINT_A1, ENDPOINT_A2, ENDPOINT_B1} <= _endpoints_sent(sent)
    assert count >= 3


def test_every_device_gets_the_same_release_payload(monkeypatch, users):
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(users["a"]["id"], ENDPOINT_A1)
    _add_subscription(users["b"]["id"], ENDPOINT_B1)

    broadcast_release("0.4.1")

    ours = [s["payload"] for s in sent
            if s["endpoint"] in (ENDPOINT_A1, ENDPOINT_B1)]
    assert len(ours) == 2
    assert ours[0] == ours[1]
    assert ours[0]["title"] == "Version 0.4.1 is live"
    assert ours[0]["body"] == BODY


def test_a_dead_subscription_is_deleted(monkeypatch, users):
    _enable_push(monkeypatch)
    _add_subscription(users["a"]["id"], ENDPOINT_A1)
    _add_subscription(users["b"]["id"], ENDPOINT_B1)
    _capture(monkeypatch, fail_for={ENDPOINT_A1: pusher.PushGone("gone (410)")})

    broadcast_release("0.4.1")

    assert not _endpoint_exists(ENDPOINT_A1)
    assert _endpoint_exists(ENDPOINT_B1)


def test_a_transient_failure_keeps_the_subscription(monkeypatch, users):
    """The opposite of PushGone: a 503 today says nothing about tomorrow."""
    _enable_push(monkeypatch)
    _add_subscription(users["a"]["id"], ENDPOINT_A1)
    sent = _capture(monkeypatch, fail_for={ENDPOINT_A1: pusher.PushError("503")})

    broadcast_release("0.4.1")

    assert ENDPOINT_A1 not in _endpoints_sent(sent)
    assert _endpoint_exists(ENDPOINT_A1)


def test_one_bad_device_does_not_abort_the_batch(monkeypatch, users):
    _enable_push(monkeypatch)
    _add_subscription(users["a"]["id"], ENDPOINT_A1)
    _add_subscription(users["a"]["id"], ENDPOINT_A2)
    _add_subscription(users["b"]["id"], ENDPOINT_B1)
    sent = _capture(monkeypatch,
                    fail_for={ENDPOINT_A1: pusher.PushError("boom")})

    broadcast_release("0.4.1")

    assert {ENDPOINT_A2, ENDPOINT_B1} <= _endpoints_sent(sent)
    assert ENDPOINT_A1 not in _endpoints_sent(sent)


# --- the CLI entry point ----------------------------------------------------

def test_cli_sends_the_fixed_body(app, monkeypatch, users):
    """The version is the only thing the workflow passes in, and it comes from
    the release tag rather than from anything a human typed."""
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(users["a"]["id"], ENDPOINT_A1)

    result = app.test_cli_runner().invoke(args=["announce-release",
                                                "--version", "0.4.1"])

    assert result.exit_code == 0, result.output
    ours = [s for s in sent if s["endpoint"] == ENDPOINT_A1]
    assert ours[0]["payload"]["body"] == BODY
    assert ours[0]["payload"]["title"] == "Version 0.4.1 is live"


def test_cli_rejects_release_text(app, monkeypatch):
    """⚠️ --notes is gone deliberately (#131). If it ever comes back, the base64
    guard in release.yml has to come back with it."""
    _enable_push(monkeypatch)
    result = app.test_cli_runner().invoke(
        args=["announce-release", "--version", "0.4.1", "--notes", "anything"])
    assert result.exit_code != 0


def test_cli_says_so_when_push_is_unconfigured(app, monkeypatch):
    _disable_push(monkeypatch)
    result = app.test_cli_runner().invoke(args=["announce-release",
                                                "--version", "0.4.1"])
    assert result.exit_code == 0, result.output
    assert "not configured" in result.output


# --- consent ----------------------------------------------------------------

def test_profile_copy_names_every_kind_of_notification(client_a, monkeypatch):
    """⚠️ This copy IS the consent record. One switch now sends bill reminders,
    posted-bill nudges (#191) AND release notes, so the page has to say all
    three. Jinja fails silently, so an assertion is the only thing standing
    between a reworded template and a consent that was widened without telling
    anyone.

    Each kind is asserted separately: a single "does it mention notifications"
    check would pass while a whole category of message went undisclosed."""
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-private")

    html = client_a.get("/profile").get_data(as_text=True)

    assert "Bill reminders and app updates" in html
    assert "the evening before" in html                      # #33
    assert "amount changes has posted" in html               # #191
    assert "when Budget Buddy is updated" in html            # #115
