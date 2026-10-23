"""Tests for scripts/check_schema_matches.py (#492).

Each test builds two real databases in the test cluster: one as the migrations
would leave it (a small "previous schema.sql" plus a migration), one as a
"schema.sql" builds it fresh. The comparison runs the script's own `main()`, so
these exercise the catalog queries, not a model of them.

The databases are scratch ones with a random suffix, never `budget_test`:
xdist workers run these concurrently, and the script compares WHOLE databases,
so anything another test left in a shared one would read as drift.
"""
import os
import re
import uuid
from contextlib import closing
from pathlib import Path

import psycopg2
import pytest

from scripts import check_schema_matches as check

PREVIOUS = "CREATE TABLE t (id serial PRIMARY KEY, amount numeric(10,2));"
CI_WF = Path(__file__).resolve().parent.parent / ".github/workflows/ci.yml"


def _admin():
    connection = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"))
    connection.autocommit = True  # CREATE/DROP DATABASE cannot run in a transaction
    return connection


@pytest.fixture
def build(capsys):
    """build(migrated_sql, fresh_sql) -> (exit code, printed report)."""
    names = []

    def _build(migrated_sql, fresh_sql):
        pair = [f"schemachk_{uuid.uuid4().hex[:12]}" for _ in range(2)]
        names.extend(pair)
        with closing(_admin()) as admin, admin.cursor() as cursor:
            for name in pair:
                cursor.execute(f"CREATE DATABASE {name}")
        for name, sql in zip(pair, (migrated_sql, fresh_sql), strict=True):
            with closing(psycopg2.connect(
                    host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
                    dbname=name, user=os.getenv("DB_USER"),
                    password=os.getenv("DB_PASSWORD"))) as connection:
                with connection, connection.cursor() as cursor:
                    cursor.execute(sql)
        code = check.main(["--migrated", pair[0], "--fresh", pair[1]])
        return code, capsys.readouterr().out

    yield _build
    with closing(_admin()) as admin, admin.cursor() as cursor:
        for name in names:
            cursor.execute(f"DROP DATABASE IF EXISTS {name}")


@pytest.mark.criterion(492, "schema.sql that disagrees with its migration fails CI")
def test_a_check_spelled_differently_from_its_migration_fails_naming_it(build):
    """#314's trap: `amount = amount` reads like a NaN guard and is not one,
    because Postgres numeric holds NaN equal to itself."""
    code, out = build(
        PREVIOUS + " ALTER TABLE t ADD CONSTRAINT t_amount_is_finite"
                   " CHECK (amount <> 'NaN'::numeric);",
        "CREATE TABLE t (id serial PRIMARY KEY, amount numeric(10,2)"
        " CONSTRAINT t_amount_is_finite CHECK (amount = amount));")
    assert code == 1
    assert "constraint t.t_amount_is_finite" in out
    assert "'NaN'::numeric" in out and "(amount = amount)" in out


@pytest.mark.criterion(492, "schema.sql that matches its migration passes")
def test_a_schema_sql_that_matches_its_migration_passes(build):
    code, out = build(
        PREVIOUS + " ALTER TABLE t ADD CONSTRAINT t_amount_is_finite"
                   " CHECK (amount <> 'NaN'::numeric);",
        "CREATE TABLE t (id serial PRIMARY KEY, amount numeric(10,2)"
        " CONSTRAINT t_amount_is_finite CHECK (amount <> 'NaN'::numeric));")
    assert (code, out.strip()) == (0, "schema.sql matches what the migrations build.")


def test_column_order_is_not_drift(build):
    """A migration can only append a column; schema.sql may declare it anywhere."""
    code, _ = build(PREVIOUS + " ALTER TABLE t ADD COLUMN note text;",
                    "CREATE TABLE t (id serial PRIMARY KEY, note text, amount numeric(10,2));")
    assert code == 0


def test_a_column_type_that_differs_fails(build):
    code, out = build(PREVIOUS + " ALTER TABLE t ADD COLUMN note varchar(40);",
                      "CREATE TABLE t (id serial PRIMARY KEY, amount numeric(10,2), note text);")
    assert code == 1
    assert "column t.note: migrations build character varying(40)" in out


def test_a_grant_the_migration_forgot_fails(build):
    """A table schema.sql grants on and the migration does not: in production
    the app role's first query on it is refused. PUBLIC stands in for the app
    role, so the test creates no cluster-wide role."""
    code, out = build(PREVIOUS,
                      PREVIOUS + " GRANT SELECT ON t TO PUBLIC;")
    assert code == 1
    assert "grant t to PUBLIC: schema.sql builds SELECT, the migrations do not" in out


def test_an_object_only_the_migrations_build_fails(build):
    code, out = build(PREVIOUS + " CREATE INDEX t_amount_idx ON t (amount);", PREVIOUS)
    assert code == 1
    assert "index t_amount_idx: migrations build" in out and "schema.sql does not" in out


def test_migrate_py_bookkeeping_is_not_drift(build):
    """`schema_migrations` (its table, sequence and key) comes from migrate.py,
    which schema.sql deliberately does not create."""
    code, _ = build(PREVIOUS + " CREATE TABLE schema_migrations"
                               " (id serial PRIMARY KEY, filename text UNIQUE);",
                    PREVIOUS)
    assert code == 0


def test_a_trigger_is_compared_too(build):
    """No trigger exists today; the first migration to add one must not pass
    unseen."""
    function = ("CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql"
                " AS $$ BEGIN RETURN NEW; END $$;")
    code, out = build(PREVIOUS + function
                      + " CREATE TRIGGER t_touch BEFORE UPDATE ON t"
                        " FOR EACH ROW EXECUTE FUNCTION touch();",
                      PREVIOUS + function)
    assert code == 1
    assert "trigger t.t_touch" in out


@pytest.mark.skipif(not CI_WF.exists(), reason="ci.yml is not in the image (.dockerignore)")
def test_ci_compares_the_migrated_database_with_a_fresh_one():
    """Everything above proves the script; this proves CI runs it, on the right
    pair, and only after the migrations have actually been applied."""
    job = CI_WF.read_text().split("\n  migrations:\n", 1)[1].split("\n  docker-build:", 1)[0]
    run = "python scripts/check_schema_matches.py --migrated budget --fresh freshcheck"
    assert run in job, "the migrations job no longer runs the schema comparison"
    assert job.index("python scripts/migrate.py\n") < job.index(run), \
        "the comparison runs before the pull request's migrations are applied"
    assert "-f sql/schema.sql" in job[:job.index(run)].split("CREATE DATABASE freshcheck", 1)[1], \
        "freshcheck is not built from HEAD's schema.sql before the comparison"


@pytest.mark.skipif(not CI_WF.exists(), reason="ci.yml is not in the image (.dockerignore)")
def test_a_change_to_the_checker_alone_runs_the_migrations_job():
    """The job's steps run only when the classifier sets sql=true. Without the
    checker in that filter, a pull request editing only the checker would skip
    the one step that runs it, and report success."""
    text = CI_WF.read_text()
    pattern = re.search(r"if matches '(\^\(sql/[^']*)'; then\n\s*sql=true", text).group(1)
    assert re.match(pattern, "scripts/check_schema_matches.py")
    assert not re.match(pattern, "scripts/release_prep.py"), "sanity: the filter is not match-all"
