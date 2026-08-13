"""#33 — the daily job: materialize what's due, then say what needs attention.

Three responsibilities, deliberately in that order, and only the last two are
gated on push being configured.

1. MATERIALIZE. Until now, run_due_schedules()/run_due_transfers() fired only on
   a page load, so a user who didn't log in got nothing posted — which quietly
   understated their digest, their forecast and the Money agent's view of their
   own data. This job runs them server-side for every user with an active
   schedule, ending that. The login-triggered runners stay: they cost nothing
   and still give an instant catch-up mid-day.

   ⚠️ This makes the FOR UPDATE locks in those runners genuinely load-bearing.
   They already existed for two concurrent page loads; now a page load can race
   the scheduler thread as well. Do not remove them.

   ⚠️ It also means this job MUST NOT be gated on push configuration. Wiring it
   behind VAPID (or, as the scheduler used to be, behind a Resend key) would
   mean a missing third-party key silently reverts the materialization
   invariant. app/__init__.py starts the scheduler on ENABLE_DIGEST_SCHEDULER
   alone for exactly this reason.

2. REMIND. Enumerate every occurrence due TOMORROW and push one notification per
   bill to the user's devices. A bill due Tuesday is most useful to know about
   on Monday evening.

3. ALERT ON WHAT POSTED (#191). Some bills are due on a fixed day for a figure
   that changes every month — electricity is the filed example — so the row that
   posts carries LAST month's amount until somebody corrects it. Pass 2 cannot
   serve this: it fires the evening before, quoting that same stale figure.

   ⚠️ This pass reads the LEDGER rather than the schedules, which is the whole
   design. run_due_schedules() also fires on three page-load paths, so opening
   the app in the morning posts the bill and advances next_due — a schedule-side
   query would then find nothing, on exactly the days the user was engaged. The
   posted transaction is the durable evidence, and transactions.schedule_id
   (sql/35) is what makes it traceable back to the schedule.

Structured like digests.send_weekly_digests(): its own app context, no
current_user (it runs on the scheduler thread), and per-user try/except so one
bad row never aborts the batch.
"""
from datetime import date, timedelta

import click
from flask import Blueprint
from flask.cli import with_appcontext

from app import pusher
from app.db import db_cursor

bp = Blueprint('reminders', __name__)

# How far ahead to look. 1 = "due tomorrow", the evening-before reminder.
# reminder_log is keyed per occurrence, so widening this does NOT cause the same
# bill to re-notify on each of the days it sits inside the window.
REMINDER_LEAD_DAYS = 1

# How far BACK the posted-bill pass looks (#191). More than one day so that a
# day this job did not run is recovered rather than lost — the claim in
# reminder_log is per transaction, so a row already alerted on stays quiet for
# the rest of the window.
POSTED_LOOKBACK_DAYS = 3


def _users_with_schedules(cursor):
    """Every user id owning at least one active schedule or transfer schedule.
    Finished schedules (#32) are excluded — they can never post again, so a user
    whose only schedule has ended has nothing to materialize."""
    cursor.execute("""
        SELECT DISTINCT user_id FROM (
            SELECT user_id FROM schedules
            WHERE is_active = true AND (end_date IS NULL OR next_due <= end_date)
            UNION
            SELECT user_id FROM transfer_schedules
            WHERE is_active = true AND (end_date IS NULL OR next_due <= end_date)
        ) AS s
        ORDER BY user_id
    """)
    return [r[0] for r in cursor.fetchall()]


def materialize_all_users(*, logger=None):
    """Run both due-runners for every user with active schedules. Returns the
    number of users processed without error. Never gated on push/mail config."""
    from app.blueprints.schedules import run_due_schedules
    from app.blueprints.transfers import run_due_transfers

    with db_cursor() as cursor:
        user_ids = _users_with_schedules(cursor)

    done = 0
    for user_id in user_ids:
        try:
            run_due_schedules(user_id)
            run_due_transfers(user_id)
            done += 1
        except Exception as e:  # one user's bad data must not stop the batch
            if logger:
                logger.exception('Materialize failed for user %s: %s', user_id, e)
    return done


def _due_tomorrow(user_id, today, lead_days=REMINDER_LEAD_DAYS):
    """Every scheduled item falling in the reminder window for one user.

    Reuses main.upcoming_occurrences (which honours #32's end_date), so a bill
    that will never be charged is never reminded about. The walker's window is
    start-EXCLUSIVE, so window_start = today gives exactly the days after today.
    Returns [{source, source_id, description, amount, type, due}, ...].
    """
    from app.blueprints.main import upcoming_occurrences  # lazy: import cycle

    window_end = today + timedelta(days=lead_days)
    items = []
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT id, description, amount, transaction_type, next_due,
                   frequency, anchor_day, second_day, end_date
            FROM schedules
            WHERE is_active = true AND user_id = %s AND next_due <= %s
              AND (end_date IS NULL OR next_due <= end_date)
        """, (user_id, window_end))
        for (sid, desc, amount, ttype, next_due, freq, anchor,
             second, end_date) in cursor.fetchall():
            for due in upcoming_occurrences(next_due, freq, anchor, second,
                                            today, window_end,
                                            end_date=end_date):
                items.append({'source': 'schedule', 'source_id': sid,
                              'description': (desc or '').strip() or 'Scheduled item',
                              'amount': float(amount), 'type': ttype, 'due': due})

        cursor.execute("""
            SELECT ts.id, ts.description, ts.amount, ts.next_due, ts.frequency,
                   ts.anchor_day, ts.second_day, ts.end_date,
                   af.account_name AS from_account_name,
                   at.account_name AS to_account_name
            FROM transfer_schedules ts
            JOIN account af ON ts.from_account_id = af.account_id
            JOIN account at ON ts.to_account_id = at.account_id
            WHERE ts.is_active = true AND ts.user_id = %s AND ts.next_due <= %s
              AND (ts.end_date IS NULL OR ts.next_due <= ts.end_date)
        """, (user_id, window_end))
        for (tsid, desc, amount, next_due, freq, anchor, second, end_date,
             from_name, to_name) in cursor.fetchall():
            label = (desc or '').strip() or f'Transfer {from_name} → {to_name}'
            for due in upcoming_occurrences(next_due, freq, anchor, second,
                                            today, window_end,
                                            end_date=end_date):
                items.append({'source': 'transfer', 'source_id': tsid,
                              'description': label, 'amount': float(amount),
                              'type': 'transfer', 'due': due})

    items.sort(key=lambda i: (i['due'], i['description']))
    return items


def _claim(cursor, user_id, item):
    """Try to claim this occurrence for sending. True if we got it, False if a
    previous run already did. The UNIQUE row IS the lock — it survives the
    container restart a deploy causes, which a process-local set would not."""
    cursor.execute("""
        INSERT INTO reminder_log (user_id, source, source_id, occurrence_date)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, source, source_id, occurrence_date) DO NOTHING
        RETURNING id
    """, (user_id, item['source'], item['source_id'], item['due']))
    return cursor.fetchone() is not None


def _subscriptions(cursor, user_id):
    cursor.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions "
                   "WHERE user_id = %s ORDER BY id", (user_id,))
    return [{'endpoint': r[0], 'p256dh': r[1], 'auth': r[2]}
            for r in cursor.fetchall()]


def _notification(item):
    """The payload the service worker's push handler renders."""
    when = 'tomorrow' if item['due'] == date.today() + timedelta(days=1) \
        else item['due'].isoformat()
    verb = 'due' if item['type'] != 'income' else 'expected'
    return {
        'title': f"{item['description']} — ${item['amount']:,.2f}",
        'body': f"{verb.capitalize()} {when}",
        'url': '/scheduled' if item['source'] == 'schedule' else '/transfers',
    }


def _deliver(user_id, subs, payload, logger):
    """Push one payload to every device the user has. Returns (sent, surviving
    subscriptions).

    A 404/410 means that browser install is gone for good, so the row is deleted
    and dropped from the working list. Anything else is transient and the
    subscription is KEPT — tomorrow's run will reach it.
    """
    sent = 0
    dead = []
    for sub in subs:
        try:
            pusher.send_push(sub, payload)
            sent += 1
        except pusher.PushGone:
            dead.append(sub['endpoint'])
        except pusher.PushError as e:
            if logger:
                logger.warning('Push failed for user %s: %s', user_id, e)
    if dead:
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ANY(%s)", (dead,))
        subs = [s for s in subs if s['endpoint'] not in dead]
    return sent, subs


def _push_pass(items_for_user, payload_for, *, today, logger, name):
    """The spine both push passes share: every user with a device, their items,
    one claim per item, one notification per device.

    Factored out when the second pass arrived (#191) rather than copied, because
    the parts that are easy to get subtly wrong — claim BEFORE send, per-user
    isolation, pruning dead devices mid-batch — are exactly the parts that must
    not drift between the two.
    """
    if not pusher.push_enabled():
        return 0

    with db_cursor() as cursor:
        cursor.execute("SELECT DISTINCT user_id FROM push_subscriptions ORDER BY user_id")
        user_ids = [r[0] for r in cursor.fetchall()]

    sent = 0
    for user_id in user_ids:
        try:
            items = items_for_user(user_id, today)
            if not items:
                continue
            with db_cursor() as cursor:
                subs = _subscriptions(cursor, user_id)
            if not subs:
                continue

            for item in items:
                # Claim first, in its own committed transaction: if the send
                # then fails we do NOT retry it, which is the right trade here —
                # a duplicate notification is worse than a missed one, and both
                # of these are stale by the next run anyway.
                with db_cursor(commit=True) as cursor:
                    if not _claim(cursor, user_id, item):
                        continue
                delivered, subs = _deliver(user_id, subs, payload_for(item), logger)
                sent += delivered
        except Exception as e:  # never let one user break the batch
            if logger:
                logger.exception('%s failed for user %s: %s', name, user_id, e)
    return sent


def send_due_reminders(*, today=None, logger=None):
    """Push one notification per bill due within the reminder window, to every
    device the user has registered. Idempotent per occurrence. Returns the
    number of notifications sent. No-op when push isn't configured."""
    return _push_pass(_due_tomorrow, _notification,
                      today=today or date.today(), logger=logger,
                      name='Reminders')


def _posted_variable_bills(user_id, today, lookback=POSTED_LOOKBACK_DAYS):
    """Every transaction a variable-amount schedule has posted recently, for one
    user. Returns [{source, source_id, description, amount, due}, ...] where
    source_id is a TRANSACTION id (see sql/35 for why the claim key differs).

    This reads the LEDGER, not the schedules, and that is the whole design (#191).
    run_due_schedules() fires on three page-load paths as well as the daily job,
    so by the time this runs, next_due has usually already moved past the
    occurrence — a schedule-side query would find nothing on exactly the days the
    user opened the app. The posted row is the durable evidence that it happened.
    """
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT t.id, t.description, t.amount, t.transaction_date
            FROM transactions t
            JOIN schedules s ON t.schedule_id = s.id AND s.user_id = t.user_id
            WHERE t.user_id = %s
              AND s.is_variable_amount = true
              AND t.transaction_date > %s
              AND t.transaction_date <= %s
            ORDER BY t.transaction_date, t.id
        """, (user_id, today - timedelta(days=lookback), today))
        return [{'source': 'posted', 'source_id': tid,
                 'description': (desc or '').strip() or 'Scheduled bill',
                 'amount': float(amount), 'due': posted_on}
                for tid, desc, amount, posted_on in cursor.fetchall()]


def _posted_notification(item):
    """The payload for "this bill posted with last month's figure"."""
    return {
        'title': f"{item['description']} posted — ${item['amount']:,.2f}",
        'body': "That's the amount from last time. Check it and update it if it changed.",
        # NOT /transactions/<id>/edit: that route returns an HTMX <tr> fragment,
        # which is not a page a notification can open.
        'url': '/transactions',
    }


def send_posted_bill_alerts(*, today=None, logger=None):
    """Push one alert per variable-amount bill that has posted in the lookback
    window, to every device the user has registered. Idempotent per transaction.
    Returns the number of notifications sent. No-op when push isn't configured."""
    return _push_pass(_posted_variable_bills, _posted_notification,
                      today=today or date.today(), logger=logger,
                      name='Posted-bill alerts')


def run_daily_tasks(*, today=None):
    """The scheduler entry point. Materialize for everyone, then notify.

    Order matters: materializing first means both notification passes read a
    ledger that is already up to date — and for the posted-bill pass that is not
    merely tidy, it is what lets a bill posted by this very job be alerted on the
    same run. Returns (users_materialized, reminders_sent, alerts_sent).
    """
    from app import app  # local import to avoid an import cycle at module load
    from app.jobs import DAILY, record_job_run
    today = today or date.today()
    users = materialize_all_users(logger=app.logger)
    sent = send_due_reminders(today=today, logger=app.logger)
    alerts = send_posted_bill_alerts(today=today, logger=app.logger)
    summary = (f'materialized {users} user(s), sent {sent} reminder(s), '
               f'{alerts} bill alert(s)')
    app.logger.info('Daily tasks: %s', summary)
    # #151 — recorded AFTER the work, so /settings reports a job that finished
    # rather than one that started. Never raises; see app/jobs.py.
    record_job_run(DAILY, summary)
    return users, sent, alerts


@click.command('run-daily')
@with_appcontext
def run_daily_command():
    """`flask run-daily` — run the daily materialize + notify pass now."""
    users, sent, alerts = run_daily_tasks()
    click.echo(f'Materialized {users} user(s); sent {sent} reminder(s); '
               f'{alerts} bill alert(s).')
