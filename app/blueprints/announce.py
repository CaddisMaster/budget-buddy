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

⚠️ The body is a FIXED line and does not summarise the release (#131, Sean,
2026-07-31 — reversing #115's "the text comes from the Release notes body",
before it ever deployed). The detail lives on the dashboard's What's-new strip,
which is where the notification points. Do not reintroduce a notes argument: the
release body is free text and release.yml builds its remote command by string
interpolation, so carrying it required a base64 hop, a truncator and a 4KB
ceiling to worry about. Sending nothing is what deletes that whole surface
rather than guarding it.
"""
import click
from flask import Blueprint
from flask.cli import with_appcontext

from app import pusher
from app.db import db_cursor

bp = Blueprint('announce', __name__)

# The same on every release. Short enough to read at a glance on a lock screen,
# and evergreen — nothing here has to be re-authored per release.
BODY = "Check out what's new in the app!"


def build_release_notification(version):
    """The pure core: {title, body, url} for one release announcement.

    Only the version varies. See the module docstring for why the release notes
    deliberately do not reach this payload.

    ⚠️ The title deliberately does NOT name the app (#133). Chrome renders its own
    attribution line under the title — "from Budget Buddy" on an installed PWA,
    sourced from manifest.json's `name` — which nothing here emits and nothing
    here can suppress. Naming the app again in the title is what made the phrase
    appear twice. Read the two together, not the title alone.
    """
    return {
        'title': f'Version {version} is live',
        'body': BODY,
        # The dashboard, because that is where the .whatsnew strip for this exact
        # release renders — the tap lands on the release's own summary.
        'url': '/',
    }


def _all_subscriptions(cursor):
    """Every registered device, across every user. The one push query in this
    codebase that is intentionally not filtered by user_id."""
    cursor.execute('SELECT endpoint, p256dh, auth FROM push_subscriptions ORDER BY id')
    return [{'endpoint': r[0], 'p256dh': r[1], 'auth': r[2]}
            for r in cursor.fetchall()]


def broadcast_release(version, *, logger=None):
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

    payload = build_release_notification(version)
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
@click.option('--version', required=True, help='The released version, e.g. 0.4.1.')
@with_appcontext
def announce_release_command(version):
    """`flask announce-release --version X` — notify every subscribed device.

    The version is the only input, and it comes from the workflow's own tag
    rather than from anything a human typed at release time.
    """
    from app import app

    sent = broadcast_release(version, logger=app.logger)
    if not pusher.push_enabled():
        click.echo('Push is not configured; announced nothing.')
        return
    click.echo(f'Announced {version} to {sent} device(s).')
