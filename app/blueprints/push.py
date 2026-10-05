"""#33 — Web Push subscription management for the installed PWA.

Every route is user-scoped. The browser hands us the PushSubscription it got
from its push service, and we store or forget it. Since #437 a device also
reports itself SEEN from Home, and Profile can remove any of the user's devices. The sending side lives in
app/pusher.py (the network seam) and blueprints/reminders.py (the daily job).

Subscriptions are per DEVICE, not per user — a phone and a laptop are separate
endpoints, and a user may have several. The endpoint is globally unique, so
re-subscribing the same browser upserts; if a different user subscribes on a
browser that already has an endpoint stored, the row moves to them, which is
correct (the notification must follow whoever is signed in).
"""
import hashlib

import psycopg2
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from app.db import db_cursor
from app.helpers import GENERIC_ERROR
from app.pusher import push_enabled

bp = Blueprint('push', __name__)

# What Profile shows for a row with no label: every row that predates sql/39,
# and any device whose User-Agent names no platform we recognise.
UNKNOWN_DEVICE = 'Unknown device'

# Checked IN ORDER — every Chromium UA also says "Safari/", and Edge also says
# "Chrome/", so the most specific token must win. On iOS every browser is
# WebKit, but each still announces itself (CriOS, FxiOS, EdgiOS).
_BROWSERS = (
    (('Edg/', 'EdgiOS/', 'EdgA/'), 'Edge'),
    (('OPR/',), 'Opera'),
    (('SamsungBrowser/',), 'Samsung Internet'),
    (('Firefox/', 'FxiOS/'), 'Firefox'),
    (('Chrome/', 'CriOS/'), 'Chrome'),
    (('Safari/',), 'Safari'),
)
# iPhone/iPad before Macintosh: an iPhone UA says "like Mac OS X". Android
# before Linux, for the same reason. ⚠️ iPadOS reports itself as a Mac by
# default, so an iPad usually reads "Mac · Safari" — the browser's choice, not
# something a server can see through.
_PLATFORMS = (
    ('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'),
    ('CrOS', 'Chromebook'), ('Macintosh', 'Mac'), ('Windows', 'Windows'),
    ('Linux', 'Linux'),
)


def device_label(user_agent):
    """A short, human name for a device — "iPhone · Safari" — or None.

    #437. Derived HERE from the request's User-Agent, never accepted from the
    client: the label is shown back on Profile, so it must not be free text a
    page script chose. None means "nothing recognisable", and Profile renders
    that as UNKNOWN_DEVICE. Bounded to sql/39's varchar(64).

    ⚠️ When no browser token is present (an installed iOS home-screen app can
    report none), the platform alone is returned rather than a guessed browser.
    """
    ua = user_agent or ''
    platform = next((name for token, name in _PLATFORMS if token in ua), None)
    if platform is None:
        return None
    browser = next((name for tokens, name in _BROWSERS
                    if any(t in ua for t in tokens)), None)
    label = f'{platform} · {browser}' if browser else platform
    return label[:64]


def endpoint_fingerprint(endpoint):
    """A short hash Profile puts on each device row, so the page can mark
    "this device" by hashing its own endpoint — without the endpoint itself
    ever being written into the HTML."""
    return hashlib.sha256(endpoint.encode()).hexdigest()[:16]


def _subscription_from_request():
    """Pull {endpoint, p256dh, auth} out of the posted JSON, or (None, error).

    The browser's PushSubscription serialises as {endpoint, keys:{p256dh, auth}}.
    Everything is validated here rather than trusted — this is a POST body.
    """
    data = request.get_json(silent=True) or {}
    endpoint = (data.get('endpoint') or '').strip()
    keys = data.get('keys') or {}
    p256dh = (keys.get('p256dh') or '').strip()
    auth = (keys.get('auth') or '').strip()
    if not endpoint or not p256dh or not auth:
        return None, 'Incomplete subscription'
    # A push endpoint is always an https URL from the browser's push service.
    if not endpoint.startswith('https://'):
        return None, 'Invalid endpoint'
    return {'endpoint': endpoint, 'p256dh': p256dh, 'auth': auth}, None


@bp.route('/push/subscribe', methods=['POST'])
@login_required
def subscribe():
    if not push_enabled():
        # Not an error the user can act on — the server simply isn't configured
        # for push. Say so plainly rather than storing a subscription nothing
        # will ever send to.
        return jsonify({'ok': False, 'error': 'Push is not configured'}), 503

    sub, error = _subscription_from_request()
    if error:
        return jsonify({'ok': False, 'error': error}), 400

    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO push_subscriptions
                    (user_id, endpoint, p256dh, auth, device_label, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (endpoint) DO UPDATE
                    SET user_id      = EXCLUDED.user_id,
                        p256dh       = EXCLUDED.p256dh,
                        auth         = EXCLUDED.auth,
                        device_label = EXCLUDED.device_label,
                        last_seen_at = now()
            """, (current_user.id, sub['endpoint'], sub['p256dh'], sub['auth'],
                  device_label(request.headers.get('User-Agent'))))
    except psycopg2.Error:
        current_app.logger.exception('push subscribe failed')
        return jsonify({'ok': False, 'error': GENERIC_ERROR}), 500
    return jsonify({'ok': True})


@bp.route('/push/unsubscribe', methods=['POST'])
@login_required
def unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get('endpoint') or '').strip()
    if not endpoint:
        return jsonify({'ok': False, 'error': 'No endpoint'}), 400
    try:
        with db_cursor(commit=True) as cursor:
            # Scoped to the caller: a user can only ever forget their own device,
            # never someone else's by guessing an endpoint.
            cursor.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = %s AND user_id = %s",
                (endpoint, current_user.id))
    except psycopg2.Error:
        current_app.logger.exception('push unsubscribe failed')
        return jsonify({'ok': False, 'error': GENERIC_ERROR}), 500
    return jsonify({'ok': True})


@bp.route('/push/seen', methods=['POST'])
@login_required
def seen():
    """#437 — Home reports that this browser's subscription is still alive.

    TOUCH ONLY: it refreshes `last_seen_at` and `device_label` on the caller's
    own row for that endpoint, and creates nothing. `known` tells the page
    whether such a row exists; when it does not, Home shows its prompt.

    ⚠️ Deliberately NOT an upsert through /push/subscribe. That would quietly
    re-add a device the user had just removed from Profile's list, and the
    whole point of #437 is that nothing about a device's state changes without
    the user seeing it.
    """
    if not push_enabled():
        return jsonify({'ok': False, 'error': 'Push is not configured'}), 503
    data = request.get_json(silent=True) or {}
    endpoint = (data.get('endpoint') or '').strip() if isinstance(data, dict) else ''
    if not endpoint.startswith('https://'):
        return jsonify({'ok': False, 'error': 'Invalid endpoint'}), 400
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute("""
                UPDATE push_subscriptions
                   SET last_seen_at = now(), device_label = %s
                 WHERE endpoint = %s AND user_id = %s
                RETURNING id
            """, (device_label(request.headers.get('User-Agent')), endpoint, current_user.id))
            known = cursor.fetchone() is not None
    except psycopg2.Error:
        current_app.logger.exception('push seen failed')
        return jsonify({'ok': False, 'error': GENERIC_ERROR}), 500
    return jsonify({'ok': True, 'known': known})


@bp.route('/push/devices/<int:sub_id>/remove', methods=['POST'])
@login_required
def remove_device(sub_id):
    """#437 — forget one of the user's devices from Profile's list.

    The device's browser is not told (nothing can reach it but a push). If it
    is still alive, the next time it opens Home its /push/seen answers
    `known: false` and it shows the prompt — so a removal is never silently
    undone, and never silently final either.
    """
    with db_cursor() as cursor:
        cursor.execute("SELECT 1 FROM push_subscriptions WHERE id = %s AND user_id = %s",
                       (sub_id, current_user.id))
        if cursor.fetchone() is None:
            abort(404)
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM push_subscriptions WHERE id = %s AND user_id = %s",
                           (sub_id, current_user.id))
    except psycopg2.Error:
        current_app.logger.exception('push device remove failed')
        flash(GENERIC_ERROR)
        return redirect(url_for('auth.profile'))
    flash('Device removed. It will get no more notifications.')
    return redirect(url_for('auth.profile'))
