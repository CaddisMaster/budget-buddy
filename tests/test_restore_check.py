"""Tests for scripts/restore_check.py — does a dump actually restore? (#153)

`RUNBOOK.md` §7 documented a restore procedure that had never been executed. The
nightly job verified gzip integrity and a minimum file size, which rules out
truncation and says nothing about whether the SQL inside loads.

Everything asserted here is **pure**: `newest_dump()`, `parse_owner_role()` and
`evaluate()` take data and return data. Nothing in this file starts a container,
and nothing shells out — the suite runs inside the application image, which has
neither Docker nor `git`, so a test that needed either could never pass there
(the trap #176 documented, pointed at a new file).

That does leave a real ceiling, stated rather than papered over: green here
proves the JUDGEMENT is right — which dump gets picked, which role the throwaway
needs, which counts constitute a failure. It proves nothing about whether a real
dump restores. Only running the script against a real dump does that, which is
why the PR records having done it.

⚠️ The counts in `REAL_COUNTS` are the genuine figures from
`budget_2026-08-10.sql.gz`, restored on 2026-08-10. They are the fixture on
purpose: two of them are ZERO, and the issue's own acceptance criterion said
every table's count must be greater than zero.
"""

import pytest

from scripts import restore_check

# Verbatim from the first real restore rehearsal (2026-08-10).
REAL_COUNTS = {
    'account': 12,
    'agent_runs': 10,
    'budget_history': 39,
    'budgets': 22,
    'categories': 57,
    'forecasts': 3,
    'goal_coach': 0,
    'goals': 2,
    'insights': 0,
    'push_subscriptions': 3,
    'reminder_log': 9,
    'schedules': 24,
    'schema_migrations': 33,
    'transactions': 338,
    'transfer_schedules': 2,
    'users': 6,
}


# ── newest_dump ─────────────────────────────────────────────────────────────


def test_picks_the_latest_date_in_the_filename():
    names = [
        'budget_2026-08-08.sql.gz',
        'budget_2026-08-10.sql.gz',
        'budget_2026-08-09.sql.gz',
    ]
    assert restore_check.newest_dump(names) == 'budget_2026-08-10.sql.gz'


def test_ordering_is_by_filename_date_not_directory_order():
    """The input order must not decide the answer.

    `Path.iterdir()` yields in whatever order the filesystem gives, which is not
    sorted — so a scan that trusted iteration order would pick a different dump
    depending on the machine.
    """
    ordered = ['budget_2026-08-01.sql.gz', 'budget_2026-08-03.sql.gz']
    assert restore_check.newest_dump(ordered) == restore_check.newest_dump(ordered[::-1])


def test_ignores_files_that_are_not_dumps():
    names = [
        'configs_2026-08-10.tar.gz',   # the config snapshot, not a database dump
        '.DS_Store',
        'notes-2026-08-11.txt',
        'budget_2026-08-09.sql.gz',
    ]
    assert restore_check.newest_dump(names) == 'budget_2026-08-09.sql.gz'


def test_a_dump_with_no_date_is_ignored():
    assert restore_check.newest_dump(['budget_latest.sql.gz']) is None


def test_no_candidates_returns_none():
    assert restore_check.newest_dump([]) is None
    assert restore_check.newest_dump(['README.md']) is None


# ── parse_owner_role ────────────────────────────────────────────────────────


def test_finds_the_role_the_dump_expects_to_exist():
    """The finding that a real rehearsal produced, stated as a test.

    pg_dump writes OWNER TO but never creates the role, so a throwaway whose
    superuser is named something else fails with `role "admin" does not exist`
    after a single table. Verified against the real dump on 2026-08-10.
    """
    dump = (
        'SET statement_timeout = 0;\n'
        'CREATE TABLE public.transactions (id integer);\n'
        'ALTER TABLE public.transactions OWNER TO admin;\n'
    )
    assert restore_check.parse_owner_role(dump) == 'admin'


def test_the_most_frequently_named_owner_wins():
    dump = (
        'ALTER TABLE public.a OWNER TO admin;\n'
        'ALTER TABLE public.b OWNER TO admin;\n'
        'ALTER TABLE public.c OWNER TO someone_else;\n'
    )
    assert restore_check.parse_owner_role(dump) == 'admin'


def test_a_quoted_owner_is_unquoted():
    assert restore_check.parse_owner_role('ALTER TABLE public.a OWNER TO "admin";') == 'admin'


def test_a_dump_with_no_ownership_yields_none():
    """Callers fall back to `postgres`; None must be distinguishable."""
    assert restore_check.parse_owner_role('CREATE TABLE public.a (id integer);') is None


# ── referenced_roles ────────────────────────────────────────────────────────

# Shaped exactly like the real dump's trailing section.
GRANTS = """\
ALTER TABLE public.transactions OWNER TO admin;
GRANT USAGE ON SCHEMA public TO budget_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.account TO budget_app;
GRANT SELECT,USAGE ON SEQUENCE public.account_account_id_seq TO budget_app;
"""


def test_the_owner_is_not_the_only_role_a_dump_needs():
    """⚠️ THE OTHER LOAD-BEARING ONE, and the reason the first rehearsal looked
    like it had passed when it had not.

    Since sql/30_app_role.sql shipped, production grants DML to `budget_app`, so
    pg_dump emits 33 `GRANT ... TO budget_app` lines. None can be replayed
    unless the role exists. Creating only the OWNER leaves the restore failing
    at `role "budget_app" does not exist` — and because grants are the LAST
    thing in a dump, every row is already loaded when it happens, so the tables
    look complete and the restore has still not succeeded.
    """
    assert restore_check.referenced_roles(GRANTS) == {'admin', 'budget_app'}
    assert restore_check.parse_owner_role(GRANTS) == 'admin'


def test_public_is_never_treated_as_a_role_to_create():
    """`GRANT ... TO PUBLIC` names a pseudo-role that always exists; issuing
    CREATE ROLE public is an error, so it must be filtered out."""
    text = 'GRANT SELECT ON TABLE public.a TO PUBLIC;\nGRANT SELECT ON TABLE public.a TO budget_app;'
    assert restore_check.referenced_roles(text) == {'budget_app'}


def test_revoke_names_a_role_too():
    assert 'admin' in restore_check.referenced_roles('REVOKE ALL ON SCHEMA public FROM admin;')


def test_a_dump_with_no_grants_needs_only_its_owner():
    """Dumps from before the least-privilege role existed take this path."""
    assert restore_check.referenced_roles('ALTER TABLE public.a OWNER TO admin;') == {'admin'}


# ── role_statements ─────────────────────────────────────────────────────────


def test_row_data_is_never_scanned_for_roles():
    """The scanner skips COPY blocks entirely.

    Two reasons, and the second is the important one. It is faster; and it means
    a transaction description can never be mistaken for SQL. A real description
    beginning "GRANT ..." is a perfectly ordinary thing for someone to type.
    """
    dump = (
        'COPY public.transactions (id, description) FROM stdin;\n'
        '1\tGRANT SELECT ON TABLE public.x TO evil_role;\n'
        '\\.\n'
        'ALTER TABLE public.transactions OWNER TO admin;\n'
    )
    ddl = restore_check.role_statements(dump.splitlines(keepends=True))
    assert 'evil_role' not in ddl
    assert restore_check.referenced_roles(ddl) == {'admin'}


def test_statements_after_a_copy_block_are_still_seen():
    """Grants sit at the very END of a dump, after every COPY block — so a
    scanner that stopped at the first COPY, or read a fixed-size header, would
    miss exactly the roles that break the restore."""
    dump = (
        'COPY public.a (id) FROM stdin;\n'
        '1\n'
        '\\.\n'
        'GRANT SELECT ON TABLE public.a TO budget_app;\n'
    )
    ddl = restore_check.role_statements(dump.splitlines(keepends=True))
    assert restore_check.referenced_roles(ddl) == {'budget_app'}


# ── evaluate ────────────────────────────────────────────────────────────────


def test_the_real_dump_passes():
    ok, problems = restore_check.evaluate(REAL_COUNTS)
    assert ok, problems
    assert problems == []


def test_an_empty_non_core_table_is_not_a_failure():
    """⚠️ THE LOAD-BEARING ONE. The issue's acceptance criterion said "each
    table's row count is greater than zero". A real dump disproves it:
    `goal_coach` and `insights` cache AI narration that may never have been
    generated, so they are legitimately empty.

    Tightening this to "all tables non-empty" would look like a strengthening
    and would in fact make the check fail on every good dump — so it is asserted
    directly rather than left implicit in the fixture above.
    """
    assert REAL_COUNTS['goal_coach'] == 0
    assert REAL_COUNTS['insights'] == 0
    ok, _ = restore_check.evaluate(REAL_COUNTS)
    assert ok


def test_a_table_newer_than_the_dump_is_not_a_failure():
    """A dump taken before a migration legitimately lacks the newest table.

    The 2026-08-10 08:00 dump has no `job_runs` because sql/34 was applied hours
    later the same day. Deriving the expected tables from sql/schema.sql would
    fail every dump taken between a migration shipping and the next night's run.
    """
    assert 'job_runs' not in REAL_COUNTS
    ok, _ = restore_check.evaluate(REAL_COUNTS)
    assert ok


@pytest.mark.parametrize('table', restore_check.CORE_TABLES)
def test_an_empty_core_table_is_a_failure(table):
    counts = dict(REAL_COUNTS, **{table: 0})
    ok, problems = restore_check.evaluate(counts)
    assert not ok
    assert any(table in problem for problem in problems)


@pytest.mark.parametrize('table', restore_check.CORE_TABLES)
def test_a_missing_core_table_is_a_failure(table):
    counts = {k: v for k, v in REAL_COUNTS.items() if k != table}
    ok, problems = restore_check.evaluate(counts)
    assert not ok
    assert any(table in problem for problem in problems)


def test_a_missing_schema_migrations_is_a_failure():
    counts = {k: v for k, v in REAL_COUNTS.items() if k != 'schema_migrations'}
    ok, problems = restore_check.evaluate(counts)
    assert not ok
    assert any('schema_migrations' in problem for problem in problems)


def test_schema_migrations_may_be_empty():
    """Present is the requirement; a freshly baselined database is not broken."""
    ok, _ = restore_check.evaluate(dict(REAL_COUNTS, schema_migrations=0))
    assert ok


def test_an_empty_restore_is_a_failure_and_says_so_plainly():
    """A dump that gzips fine and contains nothing must not read as success."""
    ok, problems = restore_check.evaluate({})
    assert not ok
    assert len(problems) == 1
    assert 'no tables' in problems[0]


def test_problems_name_the_table():
    """The operator is at a terminal at an awkward hour; the message has to say
    which table, not merely that something is wrong."""
    ok, problems = restore_check.evaluate(dict(REAL_COUNTS, transactions=0))
    assert not ok
    assert 'transactions' in problems[0]


# ── format_counts ───────────────────────────────────────────────────────────


def test_output_carries_counts_and_never_row_contents():
    """The dumps are plaintext financial history and this output gets pasted
    into issues and notes. Only table names and integers may appear."""
    rendered = restore_check.format_counts(REAL_COUNTS)
    for name, count in REAL_COUNTS.items():
        assert name in rendered
        assert str(count) in rendered


def test_an_empty_core_table_is_visible_in_the_output():
    """Jinja is not involved here, but the same silent-empty-string risk is: a
    count table that rendered a zero indistinguishably from a healthy row would
    make the failure hard to see next to the verdict."""
    rendered = restore_check.format_counts(dict(REAL_COUNTS, users=0))
    assert 'EMPTY' in rendered
