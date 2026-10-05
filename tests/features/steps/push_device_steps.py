"""Steps for push_devices.feature (#437).

Push is "configured" by setting the two VAPID variables for the scenario only;
`context.add_cleanup` puts back whatever was there. Nothing is ever sent — no
step reaches `pusher._call_webpush`.

⚠️ `push_subscriptions.endpoint` is globally UNIQUE, so every endpoint is built
from the behave prefix. behave runs serially and never alongside pytest, but a
literal shared with a pytest test would still be one row between them.

⚠️ Types and `_user` come from `tests/features/support.py`, imported before any
step below is defined. See that module for why the import order matters.
"""
import os
import re
from html import unescape

from behave import given, then, when

from tests.features.support import _user
from tests.helpers import _connection

PREFIX = "__behave__"

IPHONE_SAFARI = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) "
                 "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 "
                 "Mobile/15E148 Safari/604.1")


def _endpoint(name):
    return f"https://push.example/{PREFIX}{name}"


def _sql(query, params=()):
    conn = _connection()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall() if cur.description else None
    conn.commit()
    cur.close()
    conn.close()
    return rows


def _add_device(context, who, label, seen_today):
    user = _user(context, who)
    endpoint = _endpoint(f"{who}-{len(getattr(context, 'devices', []))}")
    (row,) = _sql(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, device_label, last_seen_at) "
        "VALUES (%s, %s, 'p256dh-x', 'auth-x', %s, CASE WHEN %s THEN now() END) RETURNING id",
        (user["id"], endpoint, label, seen_today))
    device = {"who": who, "id": row[0], "endpoint": endpoint}
    context.devices = getattr(context, "devices", []) + [device]
    return device


def _device_of(context, who):
    return next(d for d in context.devices if d["who"] == who)


def _count(context, who):
    (row,) = _sql("SELECT count(*) FROM push_subscriptions WHERE user_id = %s",
                  (_user(context, who)["id"],))
    return row[0]


# ── Given ───────────────────────────────────────────────────────────────────

@given("push is configured")
def given_push_configured(context):
    saved = {k: os.environ.get(k) for k in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY")}

    def restore():
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    context.add_cleanup(restore)
    os.environ["VAPID_PUBLIC_KEY"] = "test-public"
    os.environ["VAPID_PRIVATE_KEY"] = "test-private"


@given("user {who:Who} has a device that was never seen since tracking began")
def given_unseen_device(context, who):
    _add_device(context, who, None, False)


@given('user {who:Who} has a device called "{label}" seen today')
def given_named_device(context, who, label):
    _add_device(context, who, label, True)


# ── When ────────────────────────────────────────────────────────────────────

@when("user {who:Who} subscribes a device from an iPhone")
def when_subscribes_from_iphone(context, who):
    context.response = context.client.post(
        "/push/subscribe",
        json={"endpoint": _endpoint(f"{who}-iphone"),
              "keys": {"p256dh": "p256dh-x", "auth": "auth-x"}},
        headers={"User-Agent": IPHONE_SAFARI})
    assert context.response.status_code == 200, context.response.get_data(as_text=True)


def _report_seen(context, endpoint):
    context.response = context.client.post("/push/seen", json={"endpoint": endpoint},
                                           headers={"User-Agent": IPHONE_SAFARI})
    assert context.response.status_code == 200, context.response.get_data(as_text=True)


@when("that device reports itself seen")
def when_device_seen(context):
    _report_seen(context, context.devices[-1]["endpoint"])


@when("a device user {who:Who} never registered reports itself seen")
def when_unknown_device_seen(context, who):
    _report_seen(context, _endpoint(f"{who}-never-registered"))


@when("user {owner:Who}'s device reports itself seen through user {who:Who}'s session")
def when_other_users_device_seen(context, owner, who):
    _report_seen(context, _device_of(context, owner)["endpoint"])


@when("user {who:Who} opens Profile")
def when_opens_profile(context, who):
    context.response = context.client.get("/profile")
    assert context.response.status_code == 200


@when("user {who:Who} opens Home")
def when_opens_home(context, who):
    context.response = context.client.get("/")
    assert context.response.status_code == 200


@when("user {who:Who} removes that device")
def when_removes_device(context, who):
    device = context.devices[-1]
    context.response = context.client.post(f"/push/devices/{device['id']}/remove")


@when("user {who:Who} tries to remove user {owner:Who}'s device")
def when_removes_other_users_device(context, who, owner):
    device = _device_of(context, owner)
    context.response = context.client.post(f"/push/devices/{device['id']}/remove")


# ── Then ────────────────────────────────────────────────────────────────────

_DEVICE = re.compile(r'<li class="push-device"[^>]*>(.*?)</li>', re.S)
_TAGS = re.compile(r"<[^>]+>")


def _listed_devices(html):
    """Each device row on Profile, as its visible text with whitespace folded."""
    return [" ".join(unescape(_TAGS.sub(" ", body)).split())
            for body in _DEVICE.findall(html)]


@then("user {who:Who}'s Profile lists a device called \"{label}\"")
def then_profile_lists(context, who, label):
    html = context.client.get("/profile").get_data(as_text=True)
    rows = _listed_devices(html)
    assert any(label in row for row in rows), rows


@then('it lists "{label}", last seen today')
def then_lists_seen_today(context, label):
    rows = _listed_devices(context.response.get_data(as_text=True))
    assert any(label in row and "Last seen today" in row for row in rows), rows


@then('it lists "{label}", not seen since tracking began')
def then_lists_never_seen(context, label):
    rows = _listed_devices(context.response.get_data(as_text=True))
    assert any(label in row and "Not seen since tracking began" in row for row in rows), rows


@then("the server answers that it knows the device")
def then_known(context):
    assert context.response.get_json() == {"ok": True, "known": True}, context.response.get_json()


@then("the server answers that it does not know the device")
def then_unknown(context):
    assert context.response.get_json() == {"ok": True, "known": False}, context.response.get_json()


@then("the device's last seen time is today")
def then_seen_today(context):
    (row,) = _sql("SELECT last_seen_at::date = CURRENT_DATE FROM push_subscriptions WHERE id = %s",
                  (context.devices[-1]["id"],))
    assert row[0] is True


@then("user {who:Who} has no registered devices")
def then_no_devices(context, who):
    assert _count(context, who) == 0


@then("user {who:Who} still has {n:d} registered device")
def then_still_has(context, who, n):
    assert _count(context, who) == n


@then("user {who:Who}'s device was never seen since tracking began")
def then_still_unseen(context, who):
    (row,) = _sql("SELECT last_seen_at FROM push_subscriptions WHERE id = %s",
                  (_device_of(context, who)["id"],))
    assert row[0] is None


@then("the response is {code:d}")
def then_response_code(context, code):
    assert context.response.status_code == code, context.response.status_code


_NUDGE = re.compile(r'<[^>]*\bid="push-nudge"[^>]*>')


@then("the page carries the device prompt, hidden until the browser decides")
def then_carries_prompt(context):
    tags = _NUDGE.findall(context.response.get_data(as_text=True))
    assert len(tags) == 1, tags
    assert re.search(r"\bhidden\b", tags[0]), tags[0]


@then("the page carries no device prompt")
def then_no_prompt(context):
    html = context.response.get_data(as_text=True)
    # Positive control: this IS Home, with push configured, so an absent prompt
    # means the gate held rather than that the page failed to render.
    assert 'id="ask-panel"' in html or "whatsnew" in html
    assert not _NUDGE.search(html)
