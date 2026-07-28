"""#33 — outbound Web Push (the bill-reminder send path).

The twin of app/mailer.py, and shaped identically on purpose: a single isolated
network seam (`_call_webpush`) that tests monkeypatch, a `push_enabled()` gate
twinned with mailer.mail_enabled() / helpers.ai_enabled(), and a PushError that
callers catch so one dead device never aborts a batch. The module NEVER touches
the DB and never sees a user id — the caller owns subscription storage.

The one thing this adds over the mailer: a push endpoint can be permanently
GONE (the user uninstalled the PWA, cleared site data, or the push service
expired the registration). That is reported as 404/410 and must be told apart
from a transient failure — retrying it forever accomplishes nothing, so
PushGone is a distinct exception the caller uses to DELETE the subscription.
"""
import json
import os

# VAPID identifies this application server to the push service. The subject is
# a contact URL (mailto: or https:) the push provider can use to reach the
# operator about a misbehaving sender; it is part of the spec, not optional.
VAPID_SUBJECT = os.getenv('VAPID_SUBJECT', 'mailto:noreply@budget.seandesmet.com')

# How many seconds the push service should hold the message for a device that is
# currently offline. A bill reminder is worthless once the bill is due, so it
# expires well inside a day rather than arriving stale.
PUSH_TTL = 12 * 60 * 60


class PushError(Exception):
    """Raised on any send failure (no keys, package missing, provider error).
    Callers catch it so one failed send never aborts the reminder batch."""


class PushGone(PushError):
    """The subscription is permanently dead (404/410) — the PWA was uninstalled,
    site data was cleared, or the registration expired. A subclass of PushError
    so a caller that doesn't care still catches it with one except; callers that
    DO care delete the stored subscription instead of retrying forever."""


def push_enabled():
    """True when Web Push is configured — i.e. both VAPID keys are present.
    Routes and templates gate the subscribe UI on this so the feature stays
    invisible until the keys are set (the mail_enabled() / ai_enabled()
    pattern). The app must run normally without it."""
    return bool(os.getenv('VAPID_PUBLIC_KEY') and os.getenv('VAPID_PRIVATE_KEY'))


def public_key():
    """The VAPID public key, handed to the browser so it can subscribe. This is
    NOT a secret — it is designed to be published to every client; only the
    private key signs."""
    return os.getenv('VAPID_PUBLIC_KEY', '')


def send_push(subscription, payload):
    """Send one notification to one device subscription.

    `subscription` is {endpoint, p256dh, auth}; `payload` is a dict the service
    worker's push handler reads. Raises PushGone if the endpoint is permanently
    dead, PushError on any other failure.
    """
    if not push_enabled():
        raise PushError('VAPID keys are not set')
    endpoint = (subscription or {}).get('endpoint')
    if not endpoint:
        raise PushError('Subscription has no endpoint')

    info = {
        'endpoint': endpoint,
        'keys': {'p256dh': subscription.get('p256dh'),
                 'auth': subscription.get('auth')},
    }
    return _call_webpush(info, json.dumps(payload),
                         os.getenv('VAPID_PRIVATE_KEY'), VAPID_SUBJECT)


def _call_webpush(subscription_info, data, private_key, subject):
    """The single network call to the push service — isolated so tests stub it
    without sending anything (CI has no keys). Wraps any SDK, network, or
    missing-package error in PushError, and a dead endpoint in PushGone."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError as e:  # package missing from the image
        raise PushError(f'pywebpush unavailable: {e}') from e

    try:
        return webpush(
            subscription_info=subscription_info,
            data=data,
            vapid_private_key=private_key,
            vapid_claims={'sub': subject},
            ttl=PUSH_TTL,
        )
    except WebPushException as e:
        # 404 = the endpoint never existed; 410 Gone = it was valid and has been
        # retired. Both mean "stop sending here", as opposed to a 429/5xx which
        # is worth another go tomorrow.
        status = getattr(getattr(e, 'response', None), 'status_code', None)
        if status in (404, 410):
            raise PushGone(f'subscription gone ({status})') from e
        raise PushError(str(e)) from e
    except Exception as e:  # network, malformed key, anything else
        raise PushError(str(e)) from e
