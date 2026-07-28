"""#33 — Web Push subscription management for the installed PWA.

Two routes, both user-scoped: the browser hands us the PushSubscription it got
from its push service, and we store or forget it. The sending side lives in
app/pusher.py (the network seam) and blueprints/reminders.py (the daily job).

Subscriptions are per DEVICE, not per user — a phone and a laptop are separate
endpoints, and a user may have several. The endpoint is globally unique, so
re-subscribing the same browser upserts; if a different user subscribes on a
browser that already has an endpoint stored, the row moves to them, which is
correct (the notification must follow whoever is signed in).
"""
import psycopg2
from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.db import db_cursor
from app.helpers import GENERIC_ERROR
from app.pusher import push_enabled

bp = Blueprint('push', __name__)


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
                INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (endpoint) DO UPDATE
                    SET user_id = EXCLUDED.user_id,
                        p256dh  = EXCLUDED.p256dh,
                        auth    = EXCLUDED.auth
            """, (current_user.id, sub['endpoint'], sub['p256dh'], sub['auth']))
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
