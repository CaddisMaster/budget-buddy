"""Write-side ownership guards for posted foreign-key ids (#489).

Every form that posts a category_id or account_id checks it belongs to the
user BEFORE the write. Transactions, schedules and the statement import all use
this one guard; it lived in the transactions blueprint until #489, so the other
two imported a blueprint to reach it.
"""


def validate_category_account(cursor, user_id, category_id, account_id):
    """Ownership guard for write-side FK ids (v10.1.1 IDOR fix). For each
    non-null id, confirm the row belongs to user_id; return a list of error
    strings for any that don't, so callers fold it into their existing
    validation-error path. Mirrors the budgets `/budgets/set` guard, but for
    the transaction + schedule forms (which previously trusted the posted ids).
    Shared by transactions, schedules and the statement import."""
    errors = []
    if category_id is not None:
        cursor.execute("SELECT 1 FROM categories WHERE id = %s AND user_id = %s",
                       (category_id, user_id))
        if cursor.fetchone() is None:
            errors.append('Invalid category')
    if account_id is not None:
        cursor.execute("SELECT 1 FROM account WHERE account_id = %s AND user_id = %s",
                       (account_id, user_id))
        if cursor.fetchone() is None:
            errors.append('Invalid account')
    return errors
