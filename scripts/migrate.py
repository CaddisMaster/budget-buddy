#!/usr/bin/env python3
"""Apply pending sql/ migrations, tracking what has already run.

Deliberately standalone rather than a Flask CLI command, for two reasons:

1. It must connect as the **owning** role (``DB_USER``), not the app role.
   ``DB_APP_USER`` is the least-privilege ``budget_app`` role from
   ``sql/30_app_role.sql`` — DML only, no DDL — so migrations run through the
   app's own connection helper would fail the moment that role is adopted.
   Same reasoning as ``/admin/backup`` keeping ``DB_USER`` for ``pg_dump``.
2. Importing the app to run a migration means booting the very code whose
   schema assumptions may not hold yet.

⚠️ **The numbered files are forward-only deltas, not a replayable history.**
Replaying ``sql/01`` … from an empty database does NOT reproduce the schema —
``users`` is created only in ``schema.sql``, and several migrations reference
tables no numbered file creates. ``schema.sql`` is the only artifact that builds
a database from nothing; the numbered files only make sense applied in order on
top of the state that preceded them.

That is why ``--baseline`` exists. Production has ~30 migrations already applied
and no tracking table, so the first run there records them as applied **without
executing them** — running them would fail, and would be wrong even if it did not.

Usage::

    python scripts/migrate.py --status     # what is applied, what is pending
    python scripts/migrate.py --baseline   # ONE TIME on an existing database
    python scripts/migrate.py --dry-run    # what would be applied
    python scripts/migrate.py              # apply pending migrations
"""

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2

SQL_DIR = Path(__file__).resolve().parent.parent / 'sql'

# Numbered migrations only. schema.sql is the fresh-database artifact and is
# never applied as a migration — doing so on an existing database would try to
# recreate every table.
MIGRATION_RE = re.compile(r'^(\d+)_.*\.sql$')


def migration_files():
    """Every numbered migration, ordered by its numeric prefix.

    Sorted numerically, not lexically: '9_x.sql' must come before '10_x.sql',
    and a plain string sort puts it after.
    """
    found = []
    for path in SQL_DIR.iterdir():
        match = MIGRATION_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path.name))
    return [name for _, name in sorted(found)]


def connect():
    """Connect as the owning role. Never the app role — migrations need DDL."""
    missing = [v for v in ('DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD') if not os.getenv(v)]
    if missing:
        sys.exit(f'error: missing required environment: {", ".join(missing)}')
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
    )


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   text PRIMARY KEY,
            applied_at timestamp NOT NULL DEFAULT now()
        )
    """)


def applied_set(cursor):
    cursor.execute('SELECT filename FROM schema_migrations')
    return {row[0] for row in cursor.fetchall()}


def cmd_status(cursor):
    applied = applied_set(cursor)
    files = migration_files()
    pending = [f for f in files if f not in applied]

    print(f'{len(applied)} applied, {len(pending)} pending, {len(files)} total\n')
    for name in files:
        print(f'  {"applied" if name in applied else "PENDING":>7}  {name}')

    # A file recorded as applied but no longer on disk means the repository and
    # the database disagree about history — worth shouting about rather than
    # silently ignoring, since it usually means a renamed or deleted migration.
    orphans = applied - set(files)
    if orphans:
        print('\nWARNING: recorded as applied but not present in sql/:')
        for name in sorted(orphans):
            print(f'  {name}')
    return 0


def cmd_baseline(connection, cursor):
    """Record every existing migration as applied WITHOUT running it.

    For an existing database that predates this tracking table. Refuses to run
    if anything is already recorded, so it cannot silently mask a real pending
    migration on a database that has already been baselined.
    """
    already = applied_set(cursor)
    if already:
        print(f'error: schema_migrations already has {len(already)} row(s).')
        print('Baseline is a one-time operation on a database that predates tracking.')
        return 1

    files = migration_files()
    cursor.executemany(
        'INSERT INTO schema_migrations (filename) VALUES (%s)',
        [(name,) for name in files],
    )
    connection.commit()
    print(f'Baselined {len(files)} migration(s) as already applied. None were executed.')
    print('Future runs will apply only files added after this point.')
    return 0


def cmd_apply(connection, cursor, dry_run):
    applied = applied_set(cursor)
    pending = [f for f in migration_files() if f not in applied]

    if not applied:
        # Applying the whole history to an untracked database would fail (the
        # files are not replayable) or, worse, half-succeed. Make the operator
        # choose explicitly.
        print('error: schema_migrations is empty.')
        print('On an existing database run --baseline first.')
        print('On a genuinely fresh database, load sql/schema.sql, then --baseline.')
        return 1

    if not pending:
        print('Nothing to apply — up to date.')
        return 0

    if dry_run:
        print(f'Would apply {len(pending)} migration(s):')
        for name in pending:
            print(f'  {name}')
        return 0

    for name in pending:
        sql = (SQL_DIR / name).read_text()
        print(f'applying {name} ... ', end='', flush=True)
        try:
            # One transaction per migration: a failure leaves that migration
            # fully rolled back rather than half-applied, and earlier ones in
            # this run stay committed so a retry resumes rather than restarts.
            cursor.execute(sql)
            cursor.execute(
                'INSERT INTO schema_migrations (filename) VALUES (%s)', (name,)
            )
            connection.commit()
            print('ok')
        except psycopg2.Error as exc:
            connection.rollback()
            print('FAILED')
            print(f'\nerror in {name}: {exc}')
            print('This migration was rolled back. Nothing further was applied.')
            return 1

    print(f'\nApplied {len(pending)} migration(s).')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--status', action='store_true', help='show applied and pending')
    group.add_argument('--baseline', action='store_true',
                       help='one-time: record existing migrations as applied, running none')
    group.add_argument('--dry-run', action='store_true', help='show what would be applied')
    args = parser.parse_args()

    connection = connect()
    try:
        cursor = connection.cursor()
        ensure_table(cursor)
        connection.commit()

        if args.status:
            return cmd_status(cursor)
        if args.baseline:
            return cmd_baseline(connection, cursor)
        return cmd_apply(connection, cursor, args.dry_run)
    finally:
        connection.close()


if __name__ == '__main__':
    sys.exit(main())
