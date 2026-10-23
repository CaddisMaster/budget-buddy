#!/usr/bin/env python3
"""Prove `schema.sql` builds what its migrations build (#492).

`sql/schema.sql` builds every fresh database (dev, CI, `budget_test`, a rebuilt
server). The numbered migrations build production. They cannot be compared by
replaying the chain from empty (see schema.sql's header), so CI compares the
one pair that CAN be built: the merge base's schema.sql with this pull request's
migrations applied (what production becomes), against HEAD's schema.sql loaded
fresh (what every new database becomes). Any difference is drift.

Before this, CI only checked that schema.sql was TOUCHED alongside a new
migration. Any edit passed: a typo'd type, a missing constraint, or a CHECK
spelled differently from the migration's, which #314 showed is the difference
between protecting a column and protecting nothing.

⚠️ **Catalog facts compared as SETS, deliberately not a `pg_dump` text diff:**

* **Column ORDER is ignored.** A migration can only append a column
  (`ALTER TABLE … ADD COLUMN`), while schema.sql may declare it anywhere, and
  nothing reads a row by position (#491). A text diff fails on every one.
* **Names are compared**, constraint and index names included. A later
  migration that drops a constraint does it by name, so a name that differs
  between production and a fresh database is a real defect, not noise.
* **Grants are compared.** A migration that creates a table but forgets to
  grant the app role on it passes everywhere else and fails in production at
  the first query.
* **`schema_migrations` is excluded.** `migrate.py` creates it; schema.sql
  does not, by design.
* **Views, functions, triggers, enum types and extensions are compared too**,
  though the schema holds none today, so the first migration to add one is
  checked rather than passing unseen.

Roles are cluster-wide, so both databases must live in the same cluster (they
do in CI). Connects with the DB_* environment, like `scripts/migrate.py`.

Usage::

    python scripts/check_schema_matches.py --migrated budget --fresh freshcheck
"""

import argparse
import os
import sys

import psycopg2

IGNORED_TABLES = ('schema_migrations',)

# Every query returns rows of plain values; each row is one fact. The first
# column names the object the fact belongs to, for the report.
QUERIES = {
    'table': """
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    """,
    'column': """
        SELECT c.relname || '.' || a.attname,
               format_type(a.atttypid, a.atttypmod),
               CASE WHEN a.attnotnull THEN 'NOT NULL' ELSE 'NULL' END,
               COALESCE(pg_get_expr(d.adbin, d.adrelid), '')
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND a.attnum > 0 AND NOT a.attisdropped
    """,
    'constraint': """
        SELECT c.relname || '.' || k.conname, pg_get_constraintdef(k.oid)
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
    """,
    'index': """
        SELECT i.indexname, i.indexdef
        FROM pg_indexes i
        WHERE i.schemaname = 'public'
    """,
    'sequence': """
        SELECT s.sequencename, s.data_type::text, s.start_value::text,
               s.increment_by::text, s.min_value::text, s.max_value::text,
               s.cycle::text
        FROM pg_sequences s
        WHERE s.schemaname = 'public'
    """,
    'grant': """
        SELECT c.relname || ' to ' ||
               CASE WHEN g.grantee = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(g.grantee) END,
               g.privilege_type
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) g
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S')
    """,
    # None of the kinds below exists in the schema today. They are here so the
    # first migration to add one is compared too, rather than passing unseen.
    'view': """
        SELECT c.relname, c.relkind::text, pg_get_viewdef(c.oid)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm')
    """,
    'function': """
        SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
               pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
    """,
    'trigger': """
        SELECT c.relname || '.' || t.tgname, pg_get_triggerdef(t.oid)
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal
    """,
    'type': """
        SELECT t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE n.nspname = 'public'
        GROUP BY t.typname
    """,
    'extension': """
        SELECT e.extname FROM pg_extension e WHERE e.extname <> 'plpgsql'
    """,
}


def _ignored(name):
    """True for a fact about a table the comparison skips. Names are
    `table`, `table.column`, `table.constraint`, `table to role`, or an index
    or sequence name, which by Postgres convention starts with its table's."""
    return any(name == t or name.startswith((t + '.', t + ' ', t + '_'))
               for t in IGNORED_TABLES)


def describe(cursor):
    """{kind: set of fact tuples} for the database `cursor` is connected to."""
    facts = {}
    for kind, sql in QUERIES.items():
        cursor.execute(sql)
        facts[kind] = {tuple(row) for row in cursor.fetchall()
                       if not _ignored(row[0])}
    return facts


def compare(migrated, fresh):
    """Lines describing every difference between two `describe()` results;
    empty when they match. Pure, so the report is testable without Postgres."""
    lines = []
    for kind in QUERIES:
        only_migrated = migrated[kind] - fresh[kind]
        only_fresh = fresh[kind] - migrated[kind]
        for fact in sorted(only_migrated):
            lines.append(f'{kind} {fact[0]}: migrations build {_detail(fact)}, '
                         f'schema.sql {_counterpart(fact, only_fresh)}')
        for fact in sorted(only_fresh):
            if not _has_counterpart(fact, only_migrated):
                lines.append(f'{kind} {fact[0]}: schema.sql builds {_detail(fact)}, '
                             'the migrations do not')
    return lines


def _detail(fact):
    return ' | '.join(str(v) for v in fact[1:]) if len(fact) > 1 else 'it'


def _has_counterpart(fact, others):
    return any(other[0] == fact[0] for other in others)


def _counterpart(fact, others):
    """How schema.sql's version of the same named object differs, if it has one."""
    matches = [other for other in others if other[0] == fact[0]]
    if not matches:
        return 'does not'
    return 'builds ' + ' / '.join(_detail(m) for m in sorted(matches))


def _connect(dbname):
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=dbname,
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
    )


def check(migrated_db, fresh_db):
    """Describe both databases and return compare()'s lines."""
    described = []
    for dbname in (migrated_db, fresh_db):
        connection = _connect(dbname)
        try:
            with connection.cursor() as cursor:
                described.append(describe(cursor))
        finally:
            connection.close()
    return compare(*described)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prove schema.sql builds what its migrations build (#492).")
    parser.add_argument('--migrated', required=True,
                        help='database built from the previous schema.sql plus the migrations')
    parser.add_argument('--fresh', required=True,
                        help="database built from this tree's schema.sql alone")
    args = parser.parse_args(argv)
    lines = check(args.migrated, args.fresh)
    if lines:
        print('schema.sql does not build what the migrations build:')
        for line in lines:
            print(f'  {line}')
        print('A fresh database (dev, CI, a rebuilt server) would differ from production.')
        return 1
    print('schema.sql matches what the migrations build.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
