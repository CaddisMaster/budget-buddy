#!/usr/bin/env python3
"""Verify that a database dump actually restores (#153).

The nightly job already checks gzip integrity and a minimum file size, which
rules out truncation. It says nothing about whether the SQL inside will load
into a Postgres instance and produce the expected tables and rows — and until
this script existed, the documented restore procedure in ``RUNBOOK.md`` §7 had
never been executed at all. Two weeks of dumps on hand and no evidence that any
of them worked is the classic way to discover a backup problem on the one day
it matters.

The database is the only genuinely irreplaceable artifact in the system: source
is in git, images are in the registry, TLS certificates re-issue on demand.

Deliberately standalone, the same reasoning as ``scripts/migrate.py``,
``scripts/seed_dev.py`` and ``scripts/release_prep.py``: no Flask, no app
import, **standard library only**. It runs on whatever machine holds the dumps,
which is not the machine the application runs on, so it cannot assume the
project's virtualenv or its containers are present.

⚠️ **It never touches an existing database.** It creates its own throwaway
container and talks to it over ``docker exec`` — no published port, no network,
no connection string that could name another host. That is a structural
guarantee rather than an intention, which matters: the near-miss that prompted
writing it this way was an ``-e DB_HOST=...`` flag that silently failed to apply
and sent a migration at the development database instead.

⚠️ **No default dump location.** The directory is a required argument. This
repository is public and the backup location is maintainer-local.

The split below is for testing: ``newest_dump()``, ``parse_owner_role()`` and
``evaluate()`` are **pure**, so the suite exercises the judgement without Docker
anywhere near it. Everything that shells out is isolated in the ``_``-prefixed
seams.

Usage::

    python3 scripts/restore_check.py /path/to/dumps          # newest in the directory
    python3 scripts/restore_check.py /path/to/one.sql.gz     # a specific dump
    python3 scripts/restore_check.py /path/to/dumps --keep   # leave the container up
"""

import argparse
import gzip
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

# Tables that must exist AND hold rows for a restore to be considered real. A
# dump that parses cleanly but restores an empty ledger is the failure this
# script exists to catch, and these four are the ones that can never legitimately
# be empty on this system.
CORE_TABLES = ('users', 'transactions', 'categories', 'account')

# Must be PRESENT, but may legitimately hold any number of rows.
REQUIRED_TABLES = ('schema_migrations',)

# ⚠️ There is deliberately NO "every table must be non-empty" rule, and no
# expected-table list derived from sql/schema.sql. Both were tried against a real
# dump and both produce false failures:
#
#   * `goal_coach` and `insights` are legitimately empty — they cache AI
#     narration that may simply never have been generated.
#   * A dump taken before a migration legitimately lacks the newest table. The
#     2026-08-10 08:00 dump has no `job_runs` because sql/34 was applied hours
#     later that day. The dump is correct; a schema.sql-derived list would call
#     it broken every time a migration ships.
#
# A check that cries wolf gets ignored, which is worse than not having one.

DUMP_SUFFIX = '.sql.gz'
DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')
ROLE = r'"?([A-Za-z_][A-Za-z0-9_$]*)"?'
OWNER_RE = re.compile(r'OWNER TO ' + ROLE + r'\s*;')
GRANTEE_RE = re.compile(r'^(?:GRANT|REVOKE)\b.*?\b(?:TO|FROM) ' + ROLE + r'\s*;', re.MULTILINE)

# `GRANT ... TO PUBLIC` names a pseudo-role that always exists and must never be
# created. Compared case-insensitively — Postgres accepts `public` too.
PSEUDO_ROLES = {'public'}

# Exact counts for every table in one round trip. `n_live_tup` from pg_stat is
# an ESTIMATE maintained by autovacuum, which has not necessarily run against a
# database restored seconds ago — it can read 0 for a table full of rows, which
# would make this script report data loss that did not happen.
COUNT_SQL = """
SELECT c.relname,
       (xpath('/row/cnt/text()',
              query_to_xml(format('SELECT count(*) AS cnt FROM %I.%I',
                                  n.nspname, c.relname),
                           false, true, '')))[1]::text::bigint
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname
"""


class RestoreCheckError(Exception):
    """Anything that makes the check unable to reach a verdict."""


# ── pure ────────────────────────────────────────────────────────────────────


def newest_dump(names):
    """The dump with the latest date IN ITS FILENAME, or None.

    Pure. Ordering is by the embedded date rather than by modification time on
    purpose: mtime describes when the file last moved, not which night it
    covers, so a directory that has been copied, restored from another machine
    or touched by a rotation script orders wrongly — and this script's whole
    job is to check the RIGHT dump.

    Files without a `YYYY-MM-DD` or without the dump suffix are ignored. Ties
    break on the name so the result is deterministic.
    """
    dated = []
    for name in names:
        if not name.endswith(DUMP_SUFFIX):
            continue
        match = DATE_RE.search(name)
        if match:
            dated.append((match.group(1), name))
    if not dated:
        return None
    return max(dated)[1]


def role_statements(lines):
    """The ownership and grant lines of a dump, as a single string.

    Pure. Skips ``COPY ... FROM stdin`` blocks entirely, which is both faster
    and a useful guarantee: the role scanner never looks at a single row of
    financial data, only at DDL.
    """
    kept, in_copy = [], False
    for line in lines:
        if in_copy:
            if line.rstrip('\n') == r'\.':
                in_copy = False
            continue
        if line.startswith('COPY ') and line.rstrip('\n').endswith('FROM stdin;'):
            in_copy = True
            continue
        if ' OWNER TO ' in line or line.startswith(('GRANT ', 'REVOKE ')):
            kept.append(line if line.endswith('\n') else line + '\n')
    return ''.join(kept)


def parse_owner_role(text):
    """The role a dump expects to OWN its objects, or None.

    Pure. ``pg_dump`` writes ``ALTER TABLE ... OWNER TO <role>`` but does NOT
    create the role, so restoring into a container whose superuser has a
    different name fails with ``role "<role>" does not exist``. Deriving the
    name from the dump instead of hardcoding it means the check keeps working if
    ``DB_USER`` ever changes.

    Returns the most frequently named role, since a dump names it once per
    object and a stray mention should not win.
    """
    owners = OWNER_RE.findall(text)
    if not owners:
        return None
    return max(set(owners), key=owners.count)


def referenced_roles(text):
    """EVERY role a dump needs to already exist, owner and grantees alike.

    Pure. ⚠️ The owner is not the whole story, and assuming it was is what made
    the first rehearsal look like it had succeeded. Since `sql/30_app_role.sql`
    shipped, production grants DML to the least-privilege `budget_app` role, so
    `pg_dump` emits 33 `GRANT ... TO budget_app` lines — and none of them can be
    replayed unless that role exists too. The failure lands at the very END of
    the restore, after every row is already in, so the data looks complete and
    the restore has still not succeeded.

    `PUBLIC` is excluded: it is a pseudo-role that always exists, and issuing
    `CREATE ROLE public` is an error.
    """
    roles = set(OWNER_RE.findall(text)) | set(GRANTEE_RE.findall(text))
    return {role for role in roles if role.lower() not in PSEUDO_ROLES}


def evaluate(counts):
    """``(ok, problems)`` for a mapping of table name to row count.

    Pure. See the CORE_TABLES comment above for why this is not "every table
    must be non-empty".
    """
    problems = []

    if not counts:
        return False, ['the restored database contains no tables at all']

    for table in CORE_TABLES:
        if table not in counts:
            problems.append(f'core table {table!r} is missing from the restored database')
        elif counts[table] == 0:
            problems.append(f'core table {table!r} restored with 0 rows')

    for table in REQUIRED_TABLES:
        if table not in counts:
            problems.append(f'table {table!r} is missing from the restored database')

    return not problems, problems


def format_counts(counts):
    """A human-readable count table. Row COUNTS only — never row contents.

    The dumps are plaintext financial history; this script is run at a terminal
    and its output ends up pasted into issues and notes.
    """
    if not counts:
        return '  (no tables)'
    width = max(len(name) for name in counts)
    lines = []
    for name in sorted(counts):
        flag = ''
        if name in CORE_TABLES:
            flag = '  <- core' if counts[name] else '  <- core, EMPTY'
        lines.append(f'  {name:<{width}}  {counts[name]:>8}{flag}')
    return '\n'.join(lines)


# ── seams ───────────────────────────────────────────────────────────────────


def _verify_gzip(path):
    """SEAM-ish: read the whole gzip stream. Equivalent to `gzip -t`.

    Uses the stdlib rather than the `gzip` binary so the script has no
    dependency beyond Python and Docker.
    """
    try:
        with gzip.open(path, 'rb') as handle:
            while handle.read(1 << 20):
                pass
    except (OSError, EOFError) as exc:
        return False, str(exc)
    return True, ''


def _docker(args, check=True):
    """SEAM: run a docker command, returning its CompletedProcess."""
    result = subprocess.run(
        ['docker', *args], capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise RestoreCheckError(
            f'docker {" ".join(args[:2])} failed: {result.stderr.strip() or result.stdout.strip()}'
        )
    return result


def _psql(container, owner, database, sql):
    """SEAM: run one statement inside the throwaway and return stdout."""
    result = _docker([
        'exec', '-i', container,
        'psql', '-U', owner, '-d', database, '-tA', '-F', '|', '-c', sql,
    ])
    return result.stdout


def _stream_restore(container, owner, database, dump_path):
    """SEAM: stream the gzipped dump into psql inside the throwaway.

    Decompressed in Python and written to the process's stdin rather than
    assembled as a shell pipeline — there is no shell involved, so nothing here
    can be reinterpreted by one.
    """
    process = subprocess.Popen(
        ['docker', 'exec', '-i', container,
         'psql', '-U', owner, '-d', database, '-v', 'ON_ERROR_STOP=1', '-q'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        with gzip.open(dump_path, 'rb') as handle:
            while True:
                chunk = handle.read(1 << 20)
                if not chunk:
                    break
                process.stdin.write(chunk)
    finally:
        process.stdin.close()
    output = process.stdout.read().decode('utf-8', 'replace')
    return process.wait(), output


def _wait_for_postgres(container, owner, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        probe = _docker(['exec', container, 'pg_isready', '-U', owner, '-q'], check=False)
        if probe.returncode == 0:
            return
        time.sleep(1)
    raise RestoreCheckError(f'Postgres in {container} was not ready within {timeout}s')


def _parse_counts(stdout):
    counts = {}
    for line in stdout.splitlines():
        if '|' not in line:
            continue
        name, _, value = line.partition('|')
        name, value = name.strip(), value.strip()
        if name and value.isdigit():
            counts[name] = int(value)
    return counts


# ── driver ──────────────────────────────────────────────────────────────────


def resolve_dump(target):
    """A concrete dump path from a file or a directory argument."""
    path = Path(target).expanduser()
    if path.is_dir():
        chosen = newest_dump([p.name for p in path.iterdir() if p.is_file()])
        if chosen is None:
            raise RestoreCheckError(f'no {DUMP_SUFFIX} files with a date in their name under {path}')
        return path / chosen
    if path.is_file():
        return path
    raise RestoreCheckError(f'no such file or directory: {path}')


def run_check(dump_path, image, keep=False, database='restore_probe'):
    """Restore `dump_path` into a throwaway container and report. Returns 0/1."""
    print(f'dump:  {dump_path}')
    print(f'size:  {dump_path.stat().st_size:,} bytes')

    ok, error = _verify_gzip(dump_path)
    if not ok:
        print(f'\nFAILED: {dump_path.name} is not a readable gzip stream — {error}')
        return 1

    # Streamed line by line rather than read whole: grants sit at the very END
    # of a dump, so a fixed-size header read would miss exactly the roles that
    # break the restore.
    with gzip.open(dump_path, 'rt', encoding='utf-8', errors='replace') as handle:
        ddl = role_statements(handle)

    owner = parse_owner_role(ddl)
    if owner is None:
        owner = 'postgres'
        print('owner: none declared in the dump — using "postgres"')
    else:
        print(f'owner: {owner} (from the dump; the throwaway is created with this superuser)')

    others = sorted(referenced_roles(ddl) - {owner})
    if others:
        print(f'roles: {", ".join(others)} (granted to by the dump — created before restoring)')

    container = f'bb-restore-check-{secrets.token_hex(4)}'
    try:
        _docker([
            'run', '-d', '--rm', '--name', container,
            # No published port and no --network: the only way in is docker exec,
            # so this cannot reach production or the development database.
            '-e', f'POSTGRES_USER={owner}',
            '-e', f'POSTGRES_PASSWORD={secrets.token_hex(16)}',
            '-e', f'POSTGRES_DB={database}',
            image,
        ])
        _wait_for_postgres(container, owner)

        for role in others:
            # Names come from a file, so re-validate rather than trusting that
            # the extracting regex was the only path here. NOLOGIN and no
            # password: the role exists only so GRANT statements have a target.
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', role):
                raise RestoreCheckError(f'refusing to create implausible role name {role!r}')
            _psql(container, owner, database, f'CREATE ROLE "{role}" NOLOGIN')

        code, output = _stream_restore(container, owner, database, dump_path)
        if code != 0:
            print(f'\nFAILED: {dump_path.name} did not restore cleanly.')
            tail = [ln for ln in output.splitlines() if ln.strip()][-12:]
            for line in tail:
                print(f'  {line}')
            return 1

        counts = _parse_counts(_psql(container, owner, database, COUNT_SQL))
        print(f'\ntables restored: {len(counts)}')
        print(format_counts(counts))

        ok, problems = evaluate(counts)
        if not ok:
            print(f'\nFAILED: {dump_path.name}')
            for problem in problems:
                print(f'  - {problem}')
            return 1

        total = sum(counts.values())
        print(f'\nOK: {dump_path.name} restored cleanly — '
              f'{len(counts)} tables, {total:,} rows, every core table populated.')
        return 0
    finally:
        if keep:
            print(f'\n--keep: container {container} left running. '
                  f'Remove it with: docker rm -f {container}')
        else:
            _docker(['rm', '-f', container], check=False)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument(
        'target',
        help='a .sql.gz dump, or a directory to take the newest dump from. '
             'Required — there is deliberately no default location.',
    )
    parser.add_argument('--image', default='postgres:16',
                        help='throwaway Postgres image (default: postgres:16)')
    parser.add_argument('--keep', action='store_true',
                        help='leave the throwaway container running for inspection')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run_check(resolve_dump(args.target), args.image, keep=args.keep)
    except RestoreCheckError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
