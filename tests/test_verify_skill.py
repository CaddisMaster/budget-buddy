"""#267 — the `verify` skill's teardown cannot silently rot again.

The skill carried a hand-maintained copy of `conftest.py::_delete_user`'s table
list. `sql/36` (#265) dropped `forecasts` and `goal_coach`; `conftest.py` was
updated and the copy was not. The block runs under `-v ON_ERROR_STOP=1`, so it
aborted on the first missing table and **tore down nothing** — leaving the
throwaway `__verify__` user and its data in the dev database, while looking like
an error you might scroll past.

The old note said "Mirrors `tests/conftest.py::_delete_user` … keep them in
step", which is a documented trap rather than a guard: staying in step required
whoever dropped a table to remember a file in `.claude/`. The fix is structural
— the skill now CALLS `_delete_user` — and these tests hold that shape:

1. the skill delegates and carries no `DELETE` of its own, so there is no second
   copy to drift;
2. every table the one remaining copy names still exists in `sql/schema.sql`, so
   the next migration that drops a table fails here instead of in a teardown
   nobody was watching.

⚠️ (2) is the one that generalises. (1) only says the duplicate is gone; (2) is
what would actually have caught #265's drop at the source.

`_delete_user` moved from `conftest.py` to `tests/helpers.py` in #356, so that
behave shares it. The history above keeps the old path because it was true.
"""
import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

SKILL = REPO_ROOT / ".claude/skills/verify/SKILL.md"
HELPERS = REPO_ROOT / "tests/helpers.py"
SCHEMA = REPO_ROOT / "sql/schema.sql"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# `DELETE FROM <table>` inside _delete_user.
_DELETE = re.compile(r"DELETE FROM (\w+)")
# `CREATE TABLE [IF NOT EXISTS] [public.]<table>`, quoted or bare.
# ⚠️ The `public.` qualifier is not optional decoration — schema.sql writes every
# table that way (it is pg_dump output), and a pattern without it matches nothing
# and makes this test fail listing EVERY table as missing rather than passing
# vacuously. That is the right way round, but it is worth knowing which failure
# you are looking at.
_CREATE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?\"?(\w+)\"?", re.I)


def _delete_user_body():
    """The function's own source, found by the parser.

    ⚠️ This used to slice from `def _delete_user(` to the next `@pytest.fixture`,
    which held only because a fixture happened to follow it in `conftest.py`.
    `helpers.py` has no fixtures, so that slice would have run to the end of the
    file and swept every later helper's SQL into the "teardown" — ast bounds it
    to the function whatever comes next.
    """
    src = HELPERS.read_text()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_delete_user":
            return ast.get_source_segment(src, node)
    raise AssertionError("tests/helpers.py no longer defines _delete_user")


def _teardown_tables():
    tables = _DELETE.findall(_delete_user_body())
    assert tables, "helpers._delete_user no longer issues any DELETE FROM"
    return tables


# --- The one remaining copy must name only real tables ---------------------

@pytest.mark.skipif(not (HELPERS.exists() and SCHEMA.exists()), reason=_NOT_IN_IMAGE)
def test_the_teardown_only_names_tables_that_exist():
    """⚠️ The load-bearing one.

    Stated against `sql/schema.sql` rather than against a live database on
    purpose: it then fails in CI, in the pull request that drops the table, and
    does not depend on whether anyone's dev database happens to have had the
    migration applied. A dropped table now breaks a test with a name that says
    what to do, instead of a teardown that quietly stops tearing down.
    """
    schema_tables = {t.lower() for t in _CREATE.findall(SCHEMA.read_text())}
    assert schema_tables, "sql/schema.sql no longer declares any CREATE TABLE"
    named = {t.lower() for t in _teardown_tables()}
    missing = sorted(named - schema_tables)
    assert not missing, (
        f"helpers._delete_user deletes from {missing}, which sql/schema.sql does "
        "not declare — a dropped table will make teardown fail silently"
    )


@pytest.mark.skipif(not HELPERS.exists(), reason=_NOT_IN_IMAGE)
def test_the_teardown_still_ends_with_the_user_row():
    """FK-safe order means children first and `users` last. A reordering that
    put `users` earlier would fail against the real FKs, but only when a user
    actually had rows — which is exactly the case a passing suite can miss."""
    body = _delete_user_body()
    assert body.rindex("DELETE FROM users") > body.rindex("DELETE FROM account")
    assert body.index("DELETE FROM transactions") < body.index("DELETE FROM account")


# --- The skill must not grow a second copy ---------------------------------

@pytest.mark.skipif(not SKILL.exists(), reason=_NOT_IN_IMAGE)
def test_the_verify_skill_delegates_its_teardown():
    body = SKILL.read_text()
    assert "_delete_user" in body, (
        "the verify skill no longer calls helpers._delete_user for teardown"
    )


@pytest.mark.skipif(not SKILL.exists(), reason=_NOT_IN_IMAGE)
def test_the_verify_skill_carries_no_table_list_of_its_own():
    """The actual fix, asserted as the absence of the thing that rotted.

    ⚠️ Prose in the skill still *mentions* the old `DELETE FROM` block to explain
    why it went away, so this cannot simply grep for the words. It checks for a
    runnable list — several DELETEs naming different tables — which is what a
    reintroduced copy would look like and what an explanatory sentence is not.
    """
    tables = {t.lower() for t in _DELETE.findall(SKILL.read_text())}
    assert len(tables) < 3, (
        f"the verify skill has grown its own teardown table list again ({sorted(tables)}) "
        "— call helpers._delete_user instead, so there is only one copy to maintain"
    )
