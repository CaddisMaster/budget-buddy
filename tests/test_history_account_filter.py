"""#222 — History can be filtered by account.

History filtered on month and description only. An account filter is a third
filter, and the load-bearing part is that it reaches **every** query that reads
the ledger, not just the visible page:

- the COUNT behind the pager (miss it and the row count and page count describe
  a different set than the rows do),
- the paged SELECT (the rows you see),
- the *seed* SELECT that sums every filtered row older than the page — miss it
  and the running balance is the net of ALL accounts while the rows shown are
  one account's, which reads as a arithmetic bug rather than a filter bug,
- the page-1 pending-pin SELECT (#210), which is a separate query,
- and the CSV export, which builds its own WHERE clause in a second place.

⚠️ The export is the one that can drift silently: `export_transactions()`
duplicated the filter-building code rather than sharing it, so a filter added to
`_load_history` alone would produce a CSV that disagrees with the page it was
downloaded from. `_history_where()` is now the single builder and both call it.

Ownership needs no guard here: every WHERE starts `t.user_id = %s`, so another
user's `account_id` selects nothing rather than leaking. The isolation test
below states that as a property.
"""
import pytest
from conftest import create_account, create_transaction

from app.blueprints.transactions import PER_PAGE, _load_history
from app.db import get_db_connection


def _ledger(html):
    """Just the table body.

    ⚠️ Scoping matters here: the account <select> lists EVERY account by name,
    so an unscoped `"Filter Savings" not in html` fails against a page that is
    filtering perfectly correctly — and the mirror ("both names present" for the
    ignored-parameter test) passes vacuously off the dropdown without any row
    being rendered at all. Both assertions are only meaningful over the rows.
    """
    return html.split('id="txn-rows"')[1].split("</tbody>")[0]


def _mark_pending(transaction_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE transactions SET is_pending = true WHERE id = %s", (transaction_id,))
    conn.commit()
    cur.close()
    conn.close()


@pytest.fixture
def two_accounts(users):
    """A second and third account for user A, each with its own rows.

    The `users` fixture already seeded one 42.50 expense on A's first account;
    filtering to either account below therefore also proves that row is
    excluded.
    """
    uid = users["a"]["id"]
    checking = create_account(uid, "Filter Checking")
    savings = create_account(uid, "Filter Savings")
    # Distinct amounts so a wrong row is identifiable, not merely a wrong count.
    create_transaction(uid, checking, 100, "2026-02-01", "expense")
    create_transaction(uid, checking, 200, "2026-02-02", "income")
    create_transaction(uid, savings, 700, "2026-02-03", "expense")
    return {"uid": uid, "checking": checking, "savings": savings}


def test_only_the_selected_accounts_rows_are_returned(two_accounts):
    rows, total, _pages = _load_history(
        two_accounts["uid"], None, "", 1, account_id=two_accounts["checking"])
    assert total == 2
    assert {float(r.amount) for r in rows} == {100.0, 200.0}
    assert {r.account_name for r in rows} == {"Filter Checking"}


def test_the_running_balance_nets_only_the_selected_account(two_accounts):
    """The seed query must carry the filter too.

    Without it the walk starts from the net of every account and the top row's
    balance is wrong by the other accounts' total — the rows look right, so the
    defect presents as bad arithmetic.
    """
    rows, _total, _pages = _load_history(
        two_accounts["uid"], None, "", 1, account_id=two_accounts["checking"])
    # Newest first: +200 income (Feb 2), then -100 expense (Feb 1).
    assert float(rows[0].running_balance) == pytest.approx(100.0)
    assert float(rows[1].running_balance) == pytest.approx(-100.0)


def test_the_balance_connects_across_pages_under_an_account_filter(users):
    """The seed query's OFFSET walk, with a filter that is not month or search."""
    uid = users["a"]["id"]
    acct = create_account(uid, "Filter Paged")
    other = create_account(uid, "Filter Noise")
    # 30 expenses of 1.00 on the filtered account; the noise account carries a
    # single large row that must not enter the walk at any point.
    for i in range(30):
        create_transaction(uid, acct, 1, f"2026-04-{i + 1:02d}", "expense")
    create_transaction(uid, other, 5000, "2026-04-15", "expense")

    page1, total, pages = _load_history(uid, None, "", 1, account_id=acct)
    page2, _, _ = _load_history(uid, None, "", 2, account_id=acct)
    assert total == 30 and pages == 2
    assert len(page1) == PER_PAGE and len(page2) == 30 - PER_PAGE
    # Page 1's top row is the full net of the filtered account: -30.00.
    assert float(page1[0].running_balance) == pytest.approx(-30.0)
    # Continuity across the page break, and the oldest row is its own amount.
    assert float(page1[-1].running_balance) == pytest.approx(
        float(page2[0].running_balance) - 1.0)
    assert float(page2[-1].running_balance) == pytest.approx(-1.0)


def test_the_account_filter_composes_with_month_and_search(users):
    uid = users["a"]["id"]
    acct = create_account(uid, "Filter Combo")
    other = create_account(uid, "Filter Combo Other")
    create_transaction(uid, acct, 11, "2026-05-01", "expense")     # wanted
    create_transaction(uid, acct, 12, "2026-06-01", "expense")     # wrong month
    create_transaction(uid, other, 13, "2026-05-02", "expense")    # wrong account

    rows, total, _pages = _load_history(uid, "2026-05", "seed", 1, account_id=acct)
    assert total == 1
    assert float(rows[0].amount) == pytest.approx(11.0)


def test_a_pending_row_on_another_account_is_not_pinned(users, client_a):
    """The page-1 pin runs its own SELECT — the filter has to reach that one too."""
    uid = users["a"]["id"]
    acct = create_account(uid, "Filter Pin")
    other = create_account(uid, "Filter Pin Other")
    create_transaction(uid, acct, 21, "2026-07-01", "expense")
    _mark_pending(create_transaction(uid, other, 22, "2026-07-02", "expense"))

    rows, total, _pages = _load_history(uid, None, "", 1, account_id=acct)
    assert total == 1
    assert [float(r.amount) for r in rows] == [21.0]


# --- Route surface ---------------------------------------------------------

def test_the_route_renders_only_the_selected_account(two_accounts, client_a):
    resp = client_a.get(f"/transactions?account={two_accounts['checking']}")
    assert resp.status_code == 200
    rows = _ledger(resp.get_data(as_text=True))
    assert "Filter Checking" in rows
    assert "Filter Savings" not in rows


def test_the_filter_chip_names_the_account(two_accounts, client_a):
    """#237's chips state what is filtered; a third filter joins them."""
    resp = client_a.get(f"/transactions?account={two_accounts['checking']}")
    html = resp.get_data(as_text=True)
    assert "filter-chip" in html
    chips = html.split('class="filter-chips"')[1].split("</div>")[0]
    assert "Filter Checking" in chips


def test_the_account_select_offers_the_users_accounts(two_accounts, client_a):
    resp = client_a.get("/transactions")
    html = resp.get_data(as_text=True)
    assert 'name="account"' in html
    select = html.split('name="account"')[-1].split("</select>")[0]
    assert "Filter Checking" in select and "Filter Savings" in select
    assert "All accounts" in select


def test_a_non_numeric_account_param_is_ignored(two_accounts, client_a):
    """parse_int_param: a raw string against an int column would be a 500."""
    resp = client_a.get("/transactions?account=notanumber")
    assert resp.status_code == 200
    # Ignored, not applied — both accounts' ROWS are still listed. Asserted over
    # the tbody, since both names appear in the dropdown either way.
    rows = _ledger(resp.get_data(as_text=True))
    assert "Filter Checking" in rows and "Filter Savings" in rows


def test_another_users_account_id_reveals_nothing(users, client_a):
    """Every WHERE is user-scoped, so a foreign id selects zero rows."""
    resp = client_a.get(f"/transactions?account={users['b']['account_id']}")
    assert resp.status_code == 200
    assert "txn-b" not in resp.get_data(as_text=True)


def test_the_tbody_swap_preserves_the_account_filter(two_accounts, client_a):
    """render_history_tbody() reads the filters off the request itself."""
    resp = client_a.get(f"/transactions/rows?account={two_accounts['checking']}")
    assert resp.status_code == 200
    # The fragment IS the tbody — no dropdown to confuse the absence assertion.
    html = resp.get_data(as_text=True)
    assert "Filter Checking" in html
    assert "Filter Savings" not in html


# --- Export ----------------------------------------------------------------

def test_the_export_honours_the_account_filter(two_accounts, client_a):
    """The CSV must agree with the page it was downloaded from."""
    resp = client_a.get(f"/transactions/export?account={two_accounts['checking']}")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Filter Checking" in body
    assert "Filter Savings" not in body
    assert "700" not in body


def test_the_export_link_carries_a_search_with_no_month(two_accounts, client_a):
    """Regression: the href concatenated `?month=` then `&search=`, so with a
    search and NO month it emitted `/transactions/export&search=…` — not a query
    string at all. Flask routed it to the export view with no args, and the CSV
    silently ignored the filter the user could see applied on the page."""
    resp = client_a.get("/transactions?search=seed")
    html = resp.get_data(as_text=True)
    assert "/transactions/export&" not in html
    assert "/transactions/export?search=seed" in html


def test_the_export_link_carries_every_active_filter(two_accounts, client_a):
    acct = two_accounts["checking"]
    resp = client_a.get(f"/transactions?month=2026-02&search=seed&account={acct}")
    html = resp.get_data(as_text=True)
    export_href = [seg for seg in html.split('href="') if "/transactions/export" in seg][0]
    export_href = export_href.split('"')[0]
    assert "month=2026-02" in export_href
    assert "search=seed" in export_href
    assert f"account={acct}" in export_href
