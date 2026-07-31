"""#115 — announce a new release to every subscribed device.

The second caller of the #33 push stack, and the only one that is deliberately
NOT scoped to a single user: a release affects everybody, so this broadcasts to
every row of push_subscriptions. That is why it cannot live in pusher.py, which
never touches the DB and never sees a user id — same reason reminders.py exists.

Consent note, load-bearing: there is ONE push subscription per device and it now
covers two things. The Profile copy says so in the same breath as the bill
reminder it always promised (app/templates/profile.html). Sending release notes
against copy that only mentions bills would widen an existing consent silently —
if that section is ever reworded, this feature is part of what it must describe.

Idempotency is deliberately NOT tracked (Sean, 2026-07-31). `release: published`
fires once by construction, so the only double-send risk is a human re-running
the workflow, and the cost of that is one duplicate notification. reminder_log
exists because the DAILY job can re-fire on a schedule; this one cannot.
"""
import os
import re

import click
from flask import Blueprint
from flask.cli import with_appcontext

from app import pusher
from app.db import db_cursor

bp = Blueprint('announce', __name__)

# A lock-screen-sized body. The hard limit is Web Push's ~4KB payload ceiling,
# but a notification is read at a glance on a locked phone, and release notes run
# to paragraphs — so the useful cap is far below the technical one. Truncating
# here is what guarantees we never approach 4KB at all.
BODY_MAX_CHARS = 180

FALLBACK_BODY = "Updated — see what's new on your dashboard."

# Markdown noise to strip when flattening the notes to one line: heading hashes,
# list bullets and blockquote markers at the start of a line. Emphasis markers
# are left alone — they read as ordinary punctuation and stripping them would
# mangle words that legitimately contain an underscore.
_LINE_NOISE = re.compile(r'^\s*(?:#{1,6}\s*|[-*+]\s+|>\s?)', re.MULTILINE)


def build_release_notification(version, notes):
    """The pure core: {title, body, url} for one release announcement.

    `notes` is the GitHub Release body — free text a human wrote at release time,
    markdown, and potentially long. It is flattened to a single line and cut on a
    word boundary so the body reads as a sentence rather than a severed token.
    """
    flattened = _LINE_NOISE.sub('', notes or '')
    flattened = ' '.join(flattened.split())
    body = _truncate(flattened, BODY_MAX_CHARS) or FALLBACK_BODY
    return {
        'title': f'Budget Buddy {version} is live',
        'body': body,
        # The dashboard, because that is where the .whatsnew strip for this exact
        # release renders — the tap lands on the release's own summary.
        'url': '/',
    }


def _truncate(text, limit):
    """Cut to `limit` characters on a word boundary, adding an ellipsis only when
    something was actually removed."""
    if len(text) <= limit:
        return text
    # -1 leaves room for the ellipsis itself.
    clipped = text[:limit - 1]
    spaced = clipped.rsplit(' ', 1)[0]
    # A single word longer than the limit has no boundary to cut on; keep the
    # hard cut rather than returning an empty string.
    return (spaced or clipped).rstrip(' ,;:.') + '…'


def _all_subscriptions(cursor):
    """Every registered device, across every user. The one push query in this
    codebase that is intentionally not filtered by user_id."""
    cursor.execute('SELECT endpoint, p256dh, auth FROM push_subscriptions ORDER BY id')
    return [{'endpoint': r[0], 'p256dh': r[1], 'auth': r[2]}
            for r in cursor.fetchall()]


def broadcast_release(version, notes, *, logger=None):
    """Push the release announcement to every subscribed device.

    Gated on pusher.push_enabled() ALONE — the reminders.py shape. Returns the
    number of notifications sent. Never raises for a single bad device: each send
    is isolated, a permanently dead endpoint (404/410) is deleted, and a
    transient failure is left in place to be reached by the next release.
    """
    if not pusher.push_enabled():
        return 0

    with db_cursor() as cursor:
        subs = _all_subscriptions(cursor)
    if not subs:
        return 0

    payload = build_release_notification(version, notes)
    sent = 0
    dead = []
    for sub in subs:
        try:
            pusher.send_push(sub, payload)
            sent += 1
        except pusher.PushGone:
            dead.append(sub['endpoint'])
        except pusher.PushError as e:
            # Transient — keep the subscription. Unlike a reminder, a missed
            # release note is not worth any retry machinery.
            if logger:
                logger.warning('Release push failed for %s: %s', sub['endpoint'], e)

    if dead:
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                'DELETE FROM push_subscriptions WHERE endpoint = ANY(%s)', (dead,))

    return sent


@click.command('announce-release')
@click.option('--version', required=True, help='The released version, e.g. 0.4.0.')
@click.option('--notes', default=None,
              help='Release notes body. Defaults to the RELEASE_NOTES env var.')
@with_appcontext
def announce_release_command(version, notes):
    """`flask announce-release --version X` — notify every subscribed device.

    The notes arrive through the environment rather than the command line: the
    release workflow builds its remote command by string interpolation, and the
    body is free text, so it must never become part of a shell fragment.
    """
    from app import app

    if notes is None:
        notes = os.getenv('RELEASE_NOTES', '')
    sent = broadcast_release(version, notes, logger=app.logger)
    if not pusher.push_enabled():
        click.echo('Push is not configured; announced nothing.')
        return
    click.echo(f'Announced {version} to {sent} device(s).')
