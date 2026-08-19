"""The dead AI cache tables are gone (#236, #262).

`forecasts` died with #232 (Home's four AI surfaces became one panel) and
`goal_coach` with #262 (the Goal Coach removal). Both cached narrative only —
every figure was recomputed on read — so there was nothing to migrate.

⚠️ These assert against the LIVE database, not against sql/schema.sql. A test
that greps the schema file proves only that a file was edited; the thing that
matters is whether the migration actually ran against the database the suite
talks to.
"""
import psycopg2
import pytest

from app.db import get_db_connection

DEAD = ("forecasts", "goal_coach")
# The caches that are still alive, asserted alongside so a migration that drops
# too much fails here rather than in production.
ALIVE = ("insights", "agent_runs")


def _tables():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public'"
    )
    names = {r[0] for r in cur.fetchall()}
    cur.close()
    conn.close()
    return names


@pytest.mark.parametrize("table", DEAD)
def test_the_dead_cache_table_is_gone(table):
    assert table not in _tables(), f"{table} is still in the database"


@pytest.mark.parametrize("table", ALIVE)
def test_the_live_cache_tables_survive(table):
    """⚠️ Paired with the above on purpose. `insights` and `agent_runs` are the
    same SHAPE as the two that were dropped — same columns, same period key —
    so a migration written slightly too broadly takes them too, and nothing
    else in the suite would notice until a page 500'd."""
    assert table in _tables(), f"{table} was dropped and should not have been"


@pytest.mark.parametrize("table", DEAD)
def test_selecting_from_a_dropped_table_raises(table):
    """The property behind the absence check: any code still querying these
    fails loudly rather than reading an empty table that quietly exists."""
    conn = get_db_connection()
    cur = conn.cursor()
    with pytest.raises(psycopg2.errors.UndefinedTable):
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
    conn.rollback()
    cur.close()
    conn.close()


def test_the_schema_file_agrees_with_the_database():
    """schema.sql is the only artifact that builds a database from nothing, so
    it has to agree — a migration that edits one and not the other leaves a
    fresh clone with tables production no longer has."""
    from pathlib import Path
    schema = (Path(__file__).resolve().parents[1] / "sql" / "schema.sql").read_text()
    for table in DEAD:
        assert f"CREATE TABLE public.{table}" not in schema
    for table in ALIVE:
        assert f"CREATE TABLE public.{table}" in schema
