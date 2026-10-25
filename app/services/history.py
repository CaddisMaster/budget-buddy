"""The History ledger: the filtered, paged rows and their running balance
(#489, from the transactions blueprint).

load_history() is the one reader behind the History table, and history_where()
is the one WHERE builder its four queries and the CSV export share. Both take
explicit arguments: the request's filters are parsed by helpers.history_filters(),
and each blueprint that re-renders the History <tbody> (transactions after an
inline edit, transfers after a transfer edit) renders it itself.

⚠️ The paged query and the seed SUM behind the running balance share an
ORDER BY that defines "older", and the pending pin is applied in PYTHON after
the balance walk (docs/gotchas.md, "The History pending-pin is sorted in
PYTHON, never in SQL"). Moved verbatim.
"""
import math
from collections import namedtuple

from app.db import db_cursor

# The History row shape: the 13-column history SELECT plus the per-page
# running balance appended in load_history. Field names mirror the SELECT's
# column names, and history_row() builds it from those NAMES (#491), so the
# SELECT's column order does not matter. A column added to the SELECT but not
# here raises TypeError at the first request rather than shifting fields.
HistoryRow = namedtuple('HistoryRow', (
    'id amount description category_name account_name transaction_date '
    'transaction_type is_recurring frequency is_adjustment is_transfer '
    'transfer_group_id is_pending running_balance'
))


def history_row(row, running_balance):
    """One HistoryRow from a history SELECT row, matched by column name.

    It used to be `HistoryRow(*row, running_balance)`: built by position, so a
    column added to the SELECT shifted every field after it, and nothing raised.
    """
    return HistoryRow(**row._asdict(), running_balance=running_balance)

PER_PAGE = 25


def history_where(user_id, selected_month, search, account_id):
    """Build the shared History WHERE clause: (clause, params).

    ⚠️ ONE builder, FIVE queries. `load_history` runs four of them (the COUNT
    behind the pager, the paged SELECT, the seed SUM behind the running balance,
    and the page-1 pending pin) and `export_transactions` runs the fifth. The
    export used to duplicate this logic inline, which made "the CSV disagrees
    with the page it came from" a one-line-omission away — add a filter here and
    every reader gets it.

    The clause always opens with `t.user_id = %s`, so an `account_id` belonging
    to somebody else selects nothing rather than leaking; the filter needs no
    ownership guard of its own.
    """
    filters = ["t.user_id = %s"]
    params = [user_id]
    if selected_month:
        year, month = selected_month.split('-')
        filters.append("EXTRACT(YEAR FROM t.transaction_date) = %s AND EXTRACT(MONTH FROM t.transaction_date) = %s")
        params.extend([year, month])
    if search:
        filters.append("t.description ILIKE %s")
        params.append(f'%{search}%')
    if account_id:
        filters.append("t.account_id = %s")
        params.append(account_id)
    return "WHERE " + " AND ".join(filters), params


def load_history(user_id, selected_month, search, page, account_id=None,
                  per_page=PER_PAGE):
    """Return (rows_with_running_balance, total, total_pages) for one page of a
    user's transaction history under the given filters."""
    offset = (page - 1) * per_page
    where_clause, params = history_where(user_id, selected_month, search, account_id)
    with db_cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM transactions t {where_clause}", params)
        total = cursor.fetchone()[0]
        total_pages = math.ceil(total / per_page) if total > 0 else 1
        cursor.execute(f"""
            SELECT t.id, t.amount, t.description, c.name AS category_name, a.account_name,
                t.transaction_date, t.transaction_type,
                t.is_recurring, t.frequency, t.is_adjustment,
                t.is_transfer, t.transfer_group_id, t.is_pending
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN account a ON t.account_id = a.account_id
            {where_clause}
            ORDER BY t.transaction_date DESC, t.id DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])
        rows = cursor.fetchall()
        # Seed the page's balance walk with the signed net of every filtered row
        # OLDER than this slice, so balances connect across pages instead of
        # restarting at 0 (with filters active the column reads as the running
        # net of the matching rows).
        cursor.execute(f"""
            SELECT COALESCE(SUM(CASE WHEN older.transaction_type = 'income'
                                     THEN older.amount ELSE -older.amount END), 0) AS base
            FROM (
                SELECT t.transaction_type, t.amount
                FROM transactions t
                {where_clause}
                ORDER BY t.transaction_date DESC, t.id DESC
                OFFSET %s
            ) older
        """, params + [offset + per_page])
        running_balance = cursor.fetchone().base
    transactions_with_balance = []
    for t in reversed(rows):
        if t.transaction_type == 'income':
            running_balance += t.amount
        else:
            running_balance -= t.amount
        transactions_with_balance.append(history_row(t, running_balance))
    transactions_with_balance.reverse()
    # Pin pending rows to the top of PAGE 1 (#86, widened by #210).
    #
    # ⚠️ The pin happens HERE, in Python, and deliberately NOT as an
    # `is_pending DESC` prefix on the ORDER BY above. Both queries above are
    # ordered by date, and the seed query defines "older than this page" as
    # "further down that same ordering" (it repeats the ORDER BY with OFFSET
    # offset+per_page). Prefixing either one would not merely reorder the
    # display — it would redefine which rows count as older, so the balance
    # would be wrong for the pinned rows AND for every row beneath them.
    #
    # ⚠️ The same reasoning is why pending rows are NOT excluded from the paged
    # query: they must stay in the walk, because a pending row counts normally
    # in every figure (settled 2026-07-29). Everything below happens AFTER the
    # walk, so no posted row's balance can move.
    #
    # #210: the pin used to be page-SCOPED — it sorted only the rows already
    # fetched, so a pending charge 40 days back sat at the top of page 4 and was
    # never seen from page 1. The premise that justified that ("a pending row is
    # entered when the charge happens, so it is on page 1 in practice") does not
    # hold once more than a page of newer rows exists. Page 1 now shows EVERY
    # pending row, and later pages show none — so each appears exactly once.
    pending_rows = []
    if page == 1:
        with db_cursor() as cursor:
            cursor.execute(f"""
                SELECT t.id, t.amount, t.description, c.name AS category_name, a.account_name,
                    t.transaction_date, t.transaction_type,
                    t.is_recurring, t.frequency, t.is_adjustment,
                    t.is_transfer, t.transfer_group_id, t.is_pending
                FROM transactions t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN account a ON t.account_id = a.account_id
                {where_clause} AND t.is_pending
                ORDER BY t.transaction_date DESC, t.id DESC
            """, params)
            # running_balance is None because the template renders an em dash for
            # a pending row and never reads it — a pinned row is out of date
            # order, so a balance in that cell would read as wrong.
            pending_rows = [history_row(r, None) for r in cursor.fetchall()]
    posted_rows = [t for t in transactions_with_balance if not t.is_pending]
    return pending_rows + posted_rows, total, total_pages
