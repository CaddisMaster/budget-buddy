"""`HistoryRow` and the two queries that fill it positionally (#309, tranche 4).

History is the one place a database row reaches a template by POSITION rather
than by name. `_load_history` builds every row as `HistoryRow(*t, ...)`, so the
namedtuple's field order and the SELECT's column order are one unit: add a
column to the query without adding a field in the same place and every field
after it shifts by one. Nothing raises — the row still has the right number of
values, they are just the wrong ones — and Jinja renders whatever it is handed.

⚠️ There are TWO queries, not one. The paged SELECT fills the page, and the
page-1 pending pin (#210) runs a second SELECT through the same constructor. A
column added to one and not the other is the same silent shift, and it would
show only on pinned pending rows — the subset least likely to be in front of
whoever made the change.

The file this guards says all of that in a comment. `docs/gotchas.md`'s own
lesson is that a documented trap is not a guard, so this executes it: the
comment explains WHY, and these assertions are what fail when it stops being
true.

⚠️ Static, reading the source as text rather than running the queries. The
column list is inside an f-string interpolating a WHERE clause built at
runtime, so there is no execution path that yields it without a request and a
user — and the property under test is about the SOURCE anyway: whether two
literals and a tuple agree.
"""
import re
from pathlib import Path

from app.blueprints.transactions import HistoryRow

SOURCE = Path(__file__).resolve().parent.parent / "app" / "blueprints" / "transactions.py"

# The select list of every query that feeds HistoryRow: everything between
# `SELECT` and `FROM transactions t`.
#
# ⚠️ Anchored on `t.id, t.amount` rather than `t.id` alone. Three other queries
# in this module also read `FROM transactions t` — the two auto-categorize
# candidate loads and the CSV export — and a looser pattern picked up the
# cleanup ones, which open `t.id, t.description, t.amount`. The first cut of
# this file matched four queries and compared the wrong pair.
_HISTORY_SELECT = re.compile(
    r"SELECT\s+(?P<columns>t\.id,\s*t\.amount,.*?)\s+FROM\s+transactions\s+t", re.S)


def _output_names(select_list):
    """The column NAMES a cursor would report for one select list.

    `c.name AS category_name` is reported as `category_name`; a plain `t.amount`
    as `amount`. That is exactly the mapping `HistoryRow(*row)` depends on.
    """
    names = []
    for column in select_list.split(","):
        column = " ".join(column.split())
        alias = re.search(r"\bAS\s+(\w+)$", column, re.I)
        names.append(alias.group(1) if alias else column.rsplit(".", 1)[-1])
    return names


def _history_select_lists():
    return [m.group("columns") for m in _HISTORY_SELECT.finditer(SOURCE.read_text())]


def test_both_history_queries_are_found():
    """The precondition. If the regex stops matching — a query reformatted, a
    JOIN reordered — every assertion below passes vacuously against an empty
    list, which is the failure mode this whole file exists to prevent."""
    selects = _history_select_lists()
    for columns in selects:
        assert columns.rstrip().endswith("t.is_pending"), (
            "a matched select list does not end where HistoryRow's last "
            "query-supplied field does — the pattern is picking up the wrong "
            "query"
        )
    assert len(selects) == 2, (
        "expected exactly two SELECTs feeding HistoryRow (the paged query and "
        "the page-1 pending pin). If a query was added or reformatted, update "
        "this file deliberately rather than loosening the pattern."
    )


def test_the_paged_query_matches_the_row_shape_position_for_position():
    """The shift this guards against.

    `running_balance` is the one field the query does not supply — it is
    appended by the balance walk — so the query must match every field before
    it, in order.
    """
    columns = _output_names(_history_select_lists()[0])
    assert HistoryRow._fields[-1] == "running_balance", (
        "running_balance is expected to be the appended field; if the shape "
        "changed, this file needs rewriting rather than adjusting"
    )
    assert columns == list(HistoryRow._fields[:-1])


def test_the_pending_pin_query_matches_the_paged_one():
    """Asserted as an equality between the two queries, not against a copy of
    the column list. A literal here would be a third place to keep in step —
    and the first one to go stale, since nothing renders it."""
    paged, pinned = (_output_names(s) for s in _history_select_lists())
    assert pinned == paged


def test_a_column_added_to_only_one_query_would_be_caught():
    """Proves the guard can fail, rather than only that it passes.

    Runs the real comparison against a doctored copy of the paged select list —
    one extra column in the middle, which is precisely the edit the comment in
    `transactions.py` warns about and precisely the one that raises nothing at
    runtime.
    """
    paged = _history_select_lists()[0]
    doctored = paged.replace("t.description,", "t.description, t.notes,", 1)
    assert doctored != paged, "sanity: the doctored select list is unchanged"
    assert _output_names(doctored) != list(HistoryRow._fields[:-1])
