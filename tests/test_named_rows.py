"""Database rows are read and built by NAME, never by position (#491).

`db_cursor()` hands back `NamedTupleCursor` rows, but a namedtuple still
answers `row[1]` and still unpacks — so a SELECT gaining or reordering a column
silently shifted every field after it at each site that read it by position.
Nothing raised: the row still had the right number of values, they were just
the wrong ones, and Jinja renders whatever it is handed.

Two halves, one per acceptance criterion:

1. `HistoryRow`, the one row that reached a template built positionally
   (`HistoryRow(*t, running_balance)`), is now built from the row's field
   NAMES. Shown with a row whose columns arrive in a different order.
2. A scan of `app/` for the positional shapes, so a new one cannot slip back
   in. Each detector is proven to fire on a doctored snippet first, because a
   scan that matches nothing passes against any tree.

⚠️ The scan is a heuristic over the AST, not a type checker. It catches the
shapes that actually occurred: an integer index of 1 or more on a row-named
variable (`row[1]`, `r[2]`), a tuple unpack straight off `fetchone()` or
`fetchall()`, and a `*row` splat into a call. A row renamed to something
unusual and then indexed would get past it; the rule in `docs/gotchas.md` is
what covers that.

`row[0]` is deliberately allowed: `cursor.fetchone()[0]` on a single-column
query (`RETURNING id`, `COUNT(*)`) has no second column to shift.
"""
import ast
from collections import namedtuple
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.history import HistoryRow, history_row

APP = Path(__file__).resolve().parent.parent / "app"

# Variable names that hold a database row in app/. Every positional read #491
# found used one of these.
ROW_NAMES = {"row", "r", "t", "srow", "cat"}
FETCHES = {"fetchone", "fetchall"}


def _is_fetch(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in FETCHES)


def positional_reads(source):
    """(line, description) for every positional row read in `source`."""
    found = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in ROW_NAMES
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int) and node.slice.value >= 1):
            found.append((node.lineno, f"{node.value.id}[{node.slice.value}]"))
        elif (isinstance(node, (ast.For, ast.comprehension))
              and isinstance(node.target, ast.Tuple) and _is_fetch(node.iter)):
            found.append((getattr(node, "lineno", node.iter.lineno),
                          f"tuple unpack over {node.iter.func.attr}()"))
        elif (isinstance(node, ast.Assign) and _is_fetch(node.value)
              and any(isinstance(t, ast.Tuple) for t in node.targets)):
            found.append((node.lineno, f"tuple unpack of {node.value.func.attr}()"))
        elif isinstance(node, ast.Call):
            for arg in node.args:
                if (isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name)
                        and arg.value.id in ROW_NAMES):
                    found.append((node.lineno, f"*{arg.value.id} into a call"))
    found.extend(_unpacks_through_a_name(tree))
    return found


def _unpacks_through_a_name(tree):
    """The same tuple unpack one assignment away (#513).

    `due = cursor.fetchall()` then `for (sid, amount, ...) in due` is exactly
    as positional as unpacking the call directly, and the shapes above missed
    it in both due-runners. Scoped per function: a name only counts if THIS
    function bound it from a fetch, so a parameter or a helper's return value
    of the same name (`build_budget_report(spend_rows)`, `category_spending()`'s
    plain tuples built by name) is not flagged.
    """
    found = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fetched = {t.id for node in ast.walk(func)
                   if isinstance(node, ast.Assign) and _is_fetch(node.value)
                   for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(func):
            if (isinstance(node, (ast.For, ast.comprehension))
                    and isinstance(node.target, ast.Tuple)
                    and isinstance(node.iter, ast.Name) and node.iter.id in fetched):
                found.append((getattr(node, "lineno", node.iter.lineno),
                              f"tuple unpack over {node.iter.id} (a fetch)"))
            elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                  and node.value.id in fetched
                  and any(isinstance(t, ast.Tuple) for t in node.targets)):
                found.append((node.lineno, f"tuple unpack of {node.value.id} (a fetch)"))
    return found


# --- criterion 1: HistoryRow -------------------------------------------------

@pytest.mark.criterion(491, "A reordered SELECT cannot shift a history row's fields")
def test_a_reordered_select_still_builds_the_right_history_row():
    """The query's columns, in REVERSE order. Built positionally this row would
    carry is_pending in `id` and the id in `is_pending`; built by name, every
    field lands where it belongs."""
    values = {
        "id": 7, "amount": Decimal("12.50"), "description": "Coffee",
        "category_name": "Food", "account_name": "Checking",
        "transaction_date": date(2026, 10, 1), "transaction_type": "expense",
        "is_recurring": False, "frequency": None, "is_adjustment": False,
        "is_transfer": False, "transfer_group_id": None, "is_pending": True,
    }
    reordered = namedtuple("Row", list(reversed(values)))(**values)

    built = history_row(reordered, Decimal("99.00"))

    assert built._asdict() == {**values, "running_balance": Decimal("99.00")}


def test_a_column_history_row_does_not_know_fails_loudly():
    """The other half of building by name: a column added to the SELECT but not
    to HistoryRow is an error at the first request, not a silent shift."""
    Row = namedtuple("Row", HistoryRow._fields[:-1] + ("notes",))
    row = Row(*range(len(Row._fields)))
    with pytest.raises(TypeError, match="notes"):
        history_row(row, None)


# --- criterion 2: the scan ---------------------------------------------------

@pytest.mark.parametrize("snippet, expected", [
    ("x = row[1]", "row[1]"),
    ("d = {r[0]: r[2] for r in rows}", "r[2]"),
    ("for a, b in cursor.fetchall():\n    pass", "tuple unpack over fetchall()"),
    ("x = [a for a, b in cursor.fetchall()]", "tuple unpack over fetchall()"),
    ("a, b = cursor.fetchone()", "tuple unpack of fetchone()"),
    ("HistoryRow(*t, 0)", "*t into a call"),
    # #513: one assignment away, inside a function.
    ("def f(c):\n    due = c.fetchall()\n    for (a, b) in due:\n        pass",
     "tuple unpack over due (a fetch)"),
    ("def f(c):\n    rows = c.fetchall()\n    return {a: b for a, b in rows}",
     "tuple unpack over rows (a fetch)"),
    ("def f(c):\n    row = c.fetchone()\n    a, b = row", "tuple unpack of row (a fetch)"),
])
def test_each_positional_shape_is_detected(snippet, expected):
    """Proves the scan below can fail, one shape at a time."""
    assert [desc for _, desc in positional_reads(snippet)] == [expected]


@pytest.mark.parametrize("snippet", [
    "n = cursor.fetchone()[0]",
    "x = row.amount",
    "first = row[0]",
    "end = period[1]",
    "rows = cursor.fetchall()",
    # #513's lookalikes: plain tuples built by name, not fetched here.
    "def build(spend_rows):\n    for cid, total in spend_rows:\n        pass",
    "def f(c):\n    rows = category_spending(c)\n    return [n for n, t in rows]",
    "def f(c):\n    rows = c.fetchall()\n    for r in rows:\n        print(r.amount)",
])
def test_reads_by_name_and_single_columns_are_not_flagged(snippet):
    assert positional_reads(snippet) == []


@pytest.mark.criterion(491, "No multi-column row is read by index")
def test_no_multi_column_row_is_read_by_position_in_app():
    files = sorted(APP.rglob("*.py"))
    assert files, "found no Python files under app/ — the scan would pass vacuously"
    offenders = [f"{path.relative_to(APP.parent)}:{line}: {desc}"
                 for path in files
                 for line, desc in positional_reads(path.read_text())]
    assert offenders == [], (
        "read these rows by name (row.amount), not by position:\n  "
        + "\n  ".join(offenders))
