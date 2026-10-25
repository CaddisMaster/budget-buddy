"""`HistoryRow` and the two queries that fill it (#309, tranche 4; #491).

Until #491 History was the one place a database row reached a template by
POSITION: `HistoryRow(*t, ...)`, so a column added to the query shifted every
field after it, silently. `history_row()` now builds it by column NAME
(`tests/test_named_rows.py` proves that), so order no longer matters, but the
NAMES still have to agree: a query column HistoryRow does not know, or a field
no query supplies, is a TypeError on the first request that reaches it.

⚠️ There are TWO queries, not one. The paged SELECT fills the page, and the
page-1 pending pin (#210) runs a second SELECT through the same constructor. A
column added to one and not the other is the same silent shift, and it would
fail only on pinned pending rows — the subset least likely to be in front of
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

from app.services.history import HistoryRow

SOURCE = Path(__file__).resolve().parent.parent / "app" / "services" / "history.py"

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
    as `amount`. Those are the names `history_row()` matches on.
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


def test_the_paged_query_supplies_exactly_the_row_fields():
    """`running_balance` is the one field the query does not supply — the
    balance walk appends it — so the query must name every other field, and
    nothing else. Compared as sets: since #491 the order is free."""
    columns = _output_names(_history_select_lists()[0])
    assert HistoryRow._fields[-1] == "running_balance", (
        "running_balance is expected to be the appended field; if the shape "
        "changed, this file needs rewriting rather than adjusting"
    )
    assert sorted(columns) == sorted(HistoryRow._fields[:-1])


def test_the_pending_pin_query_matches_the_paged_one():
    """Asserted as an equality between the two queries, not against a copy of
    the column list. A literal here would be a third place to keep in step —
    and the first one to go stale, since nothing renders it."""
    paged, pinned = (_output_names(s) for s in _history_select_lists())
    assert sorted(pinned) == sorted(paged)


def test_a_column_added_to_only_one_query_would_be_caught():
    """Proves the guard can fail, rather than only that it passes.

    Runs the real comparison against a doctored copy of the paged select list —
    one extra column HistoryRow has no field for.
    """
    paged = _history_select_lists()[0]
    doctored = paged.replace("t.description,", "t.description, t.notes,", 1)
    assert doctored != paged, "sanity: the doctored select list is unchanged"
    assert sorted(_output_names(doctored)) != sorted(HistoryRow._fields[:-1])
