"""The due-runners: turn every schedule whose day has come into real ledger rows.

A schedule (and a recurring transfer) is a template, not a ledger row. Each
runner materializes one transaction (or one paired transfer) per due date,
catching up if several are overdue, then advances next_due past today. Both
fire on page loads for the signed-in user AND from the daily job for every
user (docs/gotchas.md, "Due-runners fire TWO ways").

⚠️ The due-row SELECTs are `FOR UPDATE`, and that lock is load-bearing: page
loads race each other and the scheduler thread. The race scenarios in
tests/features/schedule_materialization.feature and test_transfer_schedules.py
go red without it.

They lived in the schedules and transfers blueprints until #489, so Home, the
transactions page and the daily job imported those blueprints (two of them
lazily, to dodge a cycle) to reach them.
"""
from datetime import date

from app.db import db_cursor
from app.services.recurrence import compute_next_due


def run_due_schedules(user_id):
    """Generate plain transactions for every active schedule whose next_due has
    arrived, catching up (loop) if several occurrences are overdue, then advance
    next_due past today. Scoped to the given user."""
    today = date.today()
    with db_cursor(commit=True) as cursor:
        # FOR UPDATE serializes concurrent runs (gunicorn serves requests on
        # multiple threads — e.g. two tabs loading /dashboard and /transactions
        # at once). Without it, both runs read next_due before either advances
        # it and each materializes the same occurrence. The second run blocks
        # here until the first commits, re-evaluates next_due <= today against
        # the updated row, and finds nothing due.
        cursor.execute("""
            SELECT id, amount, description, category_id, account_id,
                   transaction_type, frequency, anchor_day, second_day, next_due,
                   end_date
            FROM schedules
            WHERE is_active = true AND next_due <= %s AND user_id = %s
              AND (end_date IS NULL OR next_due <= end_date)
            FOR UPDATE
        """, (today, user_id))
        due = cursor.fetchall()
        for s in due:
            # #32: nothing materializes past the schedule's last day. Note this
            # also covers the nastiest case for free — a schedule whose next_due
            # went stale AND whose end_date has since passed gives zero
            # iterations, so the catch-up loop back-fills nothing on the next
            # login rather than posting months of charges that never happened.
            stop = min(today, s.end_date) if s.end_date is not None else today
            cur_due = s.next_due
            while cur_due <= stop:
                # #191 — schedule_id is stamped on EVERY materialized row, not
                # just a variable one. The link is what makes a posted row
                # traceable back to the thing that posted it; the schedule's
                # is_variable_amount flag decides only whether the daily job says
                # anything about it. Deciding here instead would mean flipping the
                # flag later could not see the rows already posted.
                cursor.execute("""
                    INSERT INTO transactions
                        (amount, description, category_id, account_id,
                         transaction_date, transaction_type, schedule_id, user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (s.amount, s.description, s.category_id, s.account_id, cur_due,
                      s.transaction_type, s.id, user_id))
                cur_due = compute_next_due(
                    cur_due, s.frequency, anchor_day=s.anchor_day, second_day=s.second_day)
            cursor.execute(
                "UPDATE schedules SET next_due = %s WHERE id = %s AND user_id = %s",
                (cur_due, s.id, user_id))


def run_due_transfers(user_id):
    """Materialize a paired transfer for every active recurring transfer whose
    next_due has arrived, catching up (loop) if several occurrences are overdue,
    then advance next_due past today. Scoped to the given user. The twin of
    run_due_schedules()."""
    today = date.today()
    with db_cursor(commit=True) as cursor:
        # FOR UPDATE serializes concurrent runs (same race as run_due_schedules:
        # two threads read next_due before either advances it and each posts the
        # same paired transfer). The second run blocks until the first commits,
        # re-evaluates next_due <= today, and finds nothing due.
        cursor.execute("""
            SELECT id, amount, description, from_account_id, to_account_id,
                   frequency, anchor_day, second_day, next_due, end_date
            FROM transfer_schedules
            WHERE is_active = true AND next_due <= %s AND user_id = %s
              AND (end_date IS NULL OR next_due <= end_date)
            FOR UPDATE
        """, (today, user_id))
        due = cursor.fetchall()
        for s in due:
            label = s.description or 'Transfer'
            # #32: nothing materializes past the schedule's last day — including
            # the stale-next_due catch-up case (zero iterations, no back-fill).
            stop = min(today, s.end_date) if s.end_date is not None else today
            cur_due = s.next_due
            while cur_due <= stop:
                cursor.execute("SELECT nextval('transfer_group_seq')")
                gid = cursor.fetchone()[0]
                # Expense leg out of the From account.
                cursor.execute(
                    "INSERT INTO transactions (amount, description, transaction_date, "
                    "account_id, transaction_type, is_transfer, transfer_group_id, user_id) "
                    "VALUES (%s, %s, %s, %s, 'expense', true, %s, %s)",
                    (s.amount, label, cur_due, s.from_account_id, gid, user_id))
                # Income leg into the To account.
                cursor.execute(
                    "INSERT INTO transactions (amount, description, transaction_date, "
                    "account_id, transaction_type, is_transfer, transfer_group_id, user_id) "
                    "VALUES (%s, %s, %s, %s, 'income', true, %s, %s)",
                    (s.amount, label, cur_due, s.to_account_id, gid, user_id))
                cur_due = compute_next_due(
                    cur_due, s.frequency, anchor_day=s.anchor_day, second_day=s.second_day)
            cursor.execute(
                "UPDATE transfer_schedules SET next_due = %s WHERE id = %s AND user_id = %s",
                (cur_due, s.id, user_id))
