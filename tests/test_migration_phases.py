"""#277 — a destructive migration cannot run against code that still reads it.

`release.yml` applied every pending migration BEFORE pulling the new image. That
order is right for an additive migration and backwards for a DROP: the object
disappears while the OLD image is still serving, and keeps serving until the
swap completes. `sql/36` (dropping `forecasts` and `goal_coach`) shipped that way
at `0.8.0` against `v0.7.0`, which read both tables.

The fix is a mechanism, not another warning — see the standing lesson that a
documented trap is not a guard. A migration declares its phase in a header
pragma, `migrate.py --phase` filters on it, and `release.yml` runs the runner
twice, either side of the swap.

⚠️ **These tests are the load-bearing half.** The pragma only helps if forgetting
it is impossible, so the rule is mechanical and admits no judgement:

  1. Any migration containing `DROP TABLE` / `DROP COLUMN` MUST declare
     `-- deploy: after-pull`. `after-pull` is *always* safe for a drop — the new
     image does not reference the object, and the old one is gone — so there is
     no legitimate before-pull drop to carve an exception for.
  2. An `after-pull` migration must NOT also add schema the new code needs. A
     migration that both drops and adds cannot be phased, and must be split.

⚠️ Every test that reads a repo file SKIPS when the file is absent, naming
`.dockerignore`. `sql/` and `.github/` both ship in the image today, so these
guards are carried for symmetry with `test_deploy_pinning.py` rather than
because they currently fire — the point is that a future `.dockerignore` line
must not turn these red against an image that is correct.
"""
import re
from pathlib import Path

import pytest

from scripts import migrate

REPO_ROOT = Path(__file__).resolve().parent.parent

SQL_DIR = REPO_ROOT / "sql"
RELEASE_WF = REPO_ROOT / ".github/workflows/release.yml"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

# Same shape as scripts/migrate.py: numbered deltas only. schema.sql is the
# fresh-database artifact and is never applied as a migration.
MIGRATION_RE = re.compile(r"^(\d+)_.*\.sql$")

# The pragma, anchored to a whole line. A loose search would match the several
# header paragraphs that discuss deploy ordering in prose.
PHASE_RE = re.compile(r"^--\s*deploy:\s*(before-pull|after-pull)\s*$", re.M)

# Destructive DDL. `DROP CONSTRAINT` is deliberately absent: dropping a
# constraint never breaks a SELECT the old image is issuing.
DESTRUCTIVE_RE = re.compile(r"\bDROP\s+(TABLE|COLUMN)\b", re.I)

# Schema the NEW code may need in place the moment it starts serving. If a
# migration adds any of this it cannot be deferred past the swap.
ADDITIVE_RE = re.compile(r"\b(?:CREATE\s+TABLE|ADD\s+COLUMN|ADD\s+CONSTRAINT)\b", re.I)

# A string literal OR a `--` line comment, matched in one left-to-right pass so
# that a `--` inside quotes is consumed as part of the string and never read as
# the start of a comment (`''` is PostgreSQL's escaped quote). Only the comment
# alternative is removed.
#
# ⚠️ Deliberately NOT handled: `/* */` block comments and `$$` dollar quoting.
# Neither appears in sql/. Leaving block comments unstripped fails SAFE — a drop
# named inside one makes the guard fire, loudly — whereas a stripper that got
# either wrong could remove real SQL, and a hidden DROP is the silent failure.
_STRING_OR_LINE_COMMENT = re.compile(r"'(?:[^']|'')*'|--[^\n]*")


def sql_code(text):
    """The migration with its `--` comments removed, for the two scanners.

    ⚠️ The phase pragma is ITSELF a `--` comment, so `declared_phase` must keep
    reading the raw text. Only DESTRUCTIVE_RE and ADDITIVE_RE read this (#375).
    """
    return _STRING_OR_LINE_COMMENT.sub(
        lambda m: "" if m.group().startswith("--") else m.group(), text)


# ⚠️ GRANDFATHERED, and the reason is the rule itself.
#
# `sql/13_monthly_budgets.sql` drops `budgets.period_start`/`period_end` AND adds
# `uq_budget_user_category`, which the new code's ON CONFLICT upsert in
# /budgets/set requires. It genuinely needs both phases — it should have been two
# files — which is precisely what rule 2 now forbids.
#
# It is not fixed retroactively because it CANNOT be: it applied in the v7.0 era
# and is recorded in `schema_migrations` on every live database. Renaming or
# splitting it now orphans that row (`migrate.py --status` shouts about exactly
# that), and re-marking it `after-pull` would record a lie about what happened.
#
# ⚠️ Do not add to this set. A new mixed migration gets split into two files.
GRANDFATHERED_MIXED = {"13_monthly_budgets.sql"}


def migration_files():
    """Every numbered migration, ordered by numeric prefix — as migrate.py sorts."""
    found = []
    for path in SQL_DIR.iterdir():
        match = MIGRATION_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return [path for _, path in sorted(found, key=lambda pair: pair[0])]


def declared_phase(path):
    """The phase this file declares, or None. Two declarations is an error."""
    found = PHASE_RE.findall(path.read_text())
    assert len(found) <= 1, (
        f"{path.name} declares the deploy phase {len(found)} times ({found}). "
        "One file, one phase — a second line silently loses to the first."
    )
    return found[0] if found else None


def drops_without_after_pull(paths):
    """Rule 1's offenders: a DROP that does not declare `after-pull`."""
    offenders = []
    for path in paths:
        if not DESTRUCTIVE_RE.search(sql_code(path.read_text())):
            continue
        if path.name in GRANDFATHERED_MIXED:
            continue
        if declared_phase(path) != "after-pull":
            offenders.append(f"{path.name} (declares: {declared_phase(path)})")
    return offenders


def after_pull_that_adds_schema(paths):
    """Rule 2's offenders: deferred past the swap, yet adds schema."""
    offenders = []
    for path in paths:
        if path.name in GRANDFATHERED_MIXED:
            continue
        if declared_phase(path) != "after-pull":
            continue
        if ADDITIVE_RE.search(sql_code(path.read_text())):
            offenders.append(path.name)
    return offenders


def undeclared_but_destructive(paths):
    """Files that default to before-pull while containing a DROP."""
    return [
        path.name for path in paths
        if declared_phase(path) is None
        and DESTRUCTIVE_RE.search(sql_code(path.read_text()))
    ]


# ---------------------------------------------------------------------------
# The sql/ corpus obeys the rule
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not SQL_DIR.exists(), reason=_NOT_IN_IMAGE)
def test_every_destructive_migration_declares_after_pull():
    """Rule 1. This is the assertion the whole issue exists for.

    Stated over the corpus rather than over one file, so it covers the migration
    that does not exist yet — which is the only one that can still cause the
    outage.
    """
    offenders = drops_without_after_pull(migration_files())

    assert not offenders, (
        "These migrations drop a table or column but do not declare "
        "`-- deploy: after-pull`:\n  " + "\n  ".join(offenders) + "\n\n"
        "A DROP must land AFTER the image swap — the running image still "
        "SELECTs the object until the new container is up. Add the pragma line "
        "to the file's header comment."
    )


@pytest.mark.skipif(not SQL_DIR.exists(), reason=_NOT_IN_IMAGE)
def test_an_after_pull_migration_adds_no_schema_the_new_code_needs():
    """Rule 2 — a migration cannot be in two phases at once.

    The failure this prevents is subtle: the additive half arrives one image-pull
    LATE, so the new code serves against schema that is not there yet. That is
    the additive bug the original ordering existed to prevent, reintroduced by
    the fix for the destructive one.
    """
    offenders = after_pull_that_adds_schema(migration_files())

    assert not offenders, (
        "These migrations defer to after the swap but also add schema:\n  "
        + "\n  ".join(offenders) + "\n\n"
        "Split it into two files: the additive half (before-pull, the default) "
        "and the destructive half (`-- deploy: after-pull`)."
    )


@pytest.mark.skipif(not SQL_DIR.exists(), reason=_NOT_IN_IMAGE)
def test_the_known_destructive_migrations_are_marked():
    """The three drops that exist today, named.

    The corpus test above passes vacuously if the pragma is never used at all, so
    this pins the actual files — and would fail if a future edit quietly stripped
    a pragma rather than adding one.
    """
    by_name = {path.name: path for path in migration_files()}

    assert declared_phase(by_name["27_drop_account_spendable.sql"]) == "after-pull"
    assert declared_phase(by_name["36_drop_dead_ai_caches.sql"]) == "after-pull"
    # The mixed one records what actually happened, not what the rule now wants.
    assert declared_phase(by_name["13_monthly_budgets.sql"]) == "before-pull"


@pytest.mark.skipif(not SQL_DIR.exists(), reason=_NOT_IN_IMAGE)
def test_an_undeclared_migration_is_additive_by_default():
    """Silence means before-pull, and that default has to stay safe.

    It is safe only because rule 1 makes silence impossible for a DROP. If that
    guard were ever deleted this default becomes the original bug again, so the
    two tests are a pair.
    """
    undeclared = [p for p in migration_files() if declared_phase(p) is None]
    assert undeclared, "expected most migrations to declare nothing"

    offenders = undeclared_but_destructive(undeclared)
    assert not offenders, (
        f"{offenders} default to before-pull but contain a DROP"
    )


# ---------------------------------------------------------------------------
# The rules read SQL, not prose (#375)
# ---------------------------------------------------------------------------
#
# `sql/38` is additive, and failed rule 1's sibling because its header EXPLAINED
# that it drops no table or column — quoting the two statements to say so. Every
# migration here carries a long header, and a guard that fires on a file for
# describing itself gets weakened mid-incident (a false pragma, or a new
# GRANDFATHERED entry), which is worse than the confusion it causes.
#
# ⚠️ The second and third scenarios are the load-bearing ones. A fix that stops
# scanning comments can just as easily stop catching real drops, or stop finding
# the pragma — which is itself a comment — and both of those go silently green.


def _migration(tmp_path, sql, name="99_under_test.sql"):
    path = tmp_path / name
    path.write_text(sql)
    return [path]


def test_an_additive_migration_may_describe_the_destructive_statements(tmp_path):
    paths = _migration(tmp_path, (
        "-- 99: add a check\n"
        "--\n"
        "-- DESTRUCTIVE_RE matches only DROP TABLE and DROP COLUMN, so the\n"
        "-- DROP CONSTRAINT below is not a destructive statement.\n"
        "\n"
        "BEGIN;\n"
        "ALTER TABLE t DROP CONSTRAINT IF EXISTS c;  -- not a DROP COLUMN\n"
        "ALTER TABLE t ADD CONSTRAINT c CHECK (x > 0);\n"
        "COMMIT;\n"
    ))

    assert undeclared_but_destructive(paths) == []
    assert drops_without_after_pull(paths) == []


def test_an_after_pull_migration_may_describe_the_additive_statements(tmp_path):
    """The same defect in rule 2's scanner, which reads the same way."""
    paths = _migration(tmp_path, (
        "-- 99: drop a dead table\n"
        "-- deploy: after-pull\n"
        "-- Its replacement came from CREATE TABLE in an earlier file; this one\n"
        "-- must not ADD COLUMN or ADD CONSTRAINT, or it would need both phases.\n"
        "BEGIN;\n"
        "DROP TABLE IF EXISTS dead;\n"
        "COMMIT;\n"
    ))

    assert after_pull_that_adds_schema(paths) == []


@pytest.mark.parametrize("sql", [
    "BEGIN;\nDROP TABLE dead;\nCOMMIT;\n",
    "ALTER TABLE t DROP COLUMN c;\n",
    "-- a header\nALTER TABLE t\n    DROP COLUMN IF EXISTS c;\n",
    "DROP TABLE dead;  -- trailing comment\n",
    # A `--` inside a string literal is not a comment, so it must not swallow
    # the rest of the line — that is the one way stripping could hide a drop.
    "UPDATE t SET note = 'a -- b'; DROP TABLE dead;\n",
    "UPDATE t SET note = 'it''s -- quoted'; DROP TABLE dead;\n",
], ids=["bare", "column", "multiline", "trailing-comment",
        "dashes-in-string", "escaped-quote-in-string"])
def test_a_real_drop_is_still_caught(tmp_path, sql):
    paths = _migration(tmp_path, sql)

    assert undeclared_but_destructive(paths) == ["99_under_test.sql"]
    assert drops_without_after_pull(paths) == ["99_under_test.sql (declares: None)"]


def test_a_real_addition_after_the_pull_is_still_caught(tmp_path):
    paths = _migration(tmp_path, (
        "-- deploy: after-pull\n"
        "DROP TABLE dead;\n"
        "ALTER TABLE t ADD COLUMN c int;  -- the half that needed before-pull\n"
    ))

    assert after_pull_that_adds_schema(paths) == ["99_under_test.sql"]


def test_the_pragma_is_still_read_although_it_is_a_comment(tmp_path):
    """The asymmetry the fix depends on, pinned.

    The scanners read the SQL with comments removed; the pragma lives in a
    comment. If `declared_phase` were ever pointed at the stripped text, every
    after-pull file would read as undeclared — and this correct drop would be
    reported as an offender.
    """
    paths = _migration(tmp_path, (
        "-- 99: drop a dead table\n"
        "-- deploy: after-pull\n"
        "BEGIN;\n"
        "DROP TABLE dead;\n"
        "COMMIT;\n"
    ))

    assert declared_phase(paths[0]) == "after-pull"
    assert drops_without_after_pull(paths) == []
    assert undeclared_but_destructive(paths) == []


# ---------------------------------------------------------------------------
# release.yml actually runs the two phases, in the right places
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not RELEASE_WF.exists(), reason=_NOT_IN_IMAGE)
def test_release_runs_both_phases_either_side_of_the_swap():
    """The ordering property, asserted as ordering rather than as presence.

    A substring assertion would pass on a workflow that ran both phases in the
    wrong order, or both before the pull — which is the bug. So this compares
    positions.
    """
    text = RELEASE_WF.read_text()

    before = text.find("--phase before-pull")
    pull = text.find("docker compose pull web")
    up = text.find("docker compose up -d")
    after = text.find("--phase after-pull")

    assert before != -1, "release.yml no longer runs the before-pull migration phase"
    assert after != -1, "release.yml no longer runs the after-pull migration phase"
    assert pull != -1 and up != -1, "release.yml no longer pulls and swaps the image"

    assert before < pull, "additive migrations must be applied BEFORE the image is pulled"
    assert up < after, "destructive migrations must be applied AFTER the container swaps"


@pytest.mark.skipif(not RELEASE_WF.exists(), reason=_NOT_IN_IMAGE)
def test_release_no_longer_claims_drops_are_a_manual_step():
    """The comment that said this was already handled.

    Before #277 `release.yml` stated that DROPs "are deliberately NOT automated
    and stay a manual step" — while step 2 ran `migrate.py` with no filter, which
    applies every pending file including the drops. Anyone reading the workflow
    to check the risk was told it was covered. A false comment about a guard is
    worse than no comment, so it is asserted gone.
    """
    text = RELEASE_WF.read_text()
    assert "stay a manual step" not in text
    assert "NOT automated" not in text


# ---------------------------------------------------------------------------
# The runner reads the pragma the way the corpus tests assume
# ---------------------------------------------------------------------------
#
# The tests above assert a property of the sql/ FILES using their own regex. That
# proves nothing about migrate.py, which has a second copy of that regex — the
# two could drift and every test above would stay green while deploys applied the
# wrong set. These exercise the runner's own parser.

def test_an_undeclared_migration_reads_as_before_pull():
    assert migrate.phase_of("ALTER TABLE account ADD COLUMN nickname text;\n") == "before-pull"


def test_the_pragma_is_read_from_the_header():
    sql = "-- 38: drop something\n-- deploy: after-pull\n\nDROP TABLE dead;\n"
    assert migrate.phase_of(sql) == "after-pull"


def test_prose_about_deploy_order_is_not_a_declaration():
    """The reason the regex is anchored.

    `sql/36` says "AFTER THE IMAGE PULL" in English three times before it says
    anything in a pragma. A loose search would read a paragraph as a declaration —
    and worse, would read a paragraph *warning* about the wrong order as an
    instruction to use it.
    """
    sql = (
        "-- ⚠️ DEPLOY ORDER: THIS GOES *AFTER* THE IMAGE PULL\n"
        "-- A DROP is the opposite of an additive migration; deploy: after-pull\n"
        "--   is the pragma you would write, but not on this line.\n"
        "ALTER TABLE t ADD COLUMN c int;\n"
    )
    assert migrate.phase_of(sql) == "before-pull"


def test_two_declarations_are_an_error_rather_than_first_wins():
    """Guessing is the one thing this mechanism exists to remove."""
    sql = "-- deploy: before-pull\n-- deploy: after-pull\nDROP TABLE t;\n"
    with pytest.raises(ValueError, match="2 times"):
        migrate.phase_of(sql)


def test_the_two_phase_regexes_agree_on_every_real_migration():
    """This file's regex and migrate.py's must not drift apart.

    They are deliberately separate — a test importing the implementation's regex
    to check the implementation asserts nothing — so this is the seam that would
    otherwise go unwatched.
    """
    for path in migration_files():
        mine = declared_phase(path) or "before-pull"
        assert migrate.phase_of(path.read_text()) == mine, path.name


# ---------------------------------------------------------------------------
# Splitting a batch across the swap reorders it — deliberately unguarded
# ---------------------------------------------------------------------------


def test_a_pending_batch_may_legitimately_interleave_the_phases():
    """⚠️ Read this before adding a numeric ordering rule to migrate.py.

    One was written for #277 and removed the same afternoon: it refused any
    pending batch whose phases were not monotonic, and it rejected both real
    batches it was ever shown — `27`/`28` and `36`/`37`, neither of which shares
    a table. It was a blunt proxy for "these two depend on each other", wrong
    every time it fired, and a guard that blocks a legitimate deploy is a guard
    that gets deleted mid-incident.

    This test pins the two pairs as ACCEPTABLE so that reinstating the rule turns
    it red immediately, rather than at the next release.
    """
    by_name = {path.name: path for path in migration_files()}
    for earlier, later in (
        ("27_drop_account_spendable.sql", "28_category_kind.sql"),
        ("36_drop_dead_ai_caches.sql", "37_users_session_token.sql"),
    ):
        assert declared_phase(by_name[earlier]) == "after-pull"
        assert declared_phase(by_name[later]) is None  # i.e. before-pull

    assert not hasattr(migrate, "check_phase_order"), (
        "migrate.py has regrown a phase-ordering check. The two pairs above are "
        "benign and any corpus-wide numeric rule refuses them — see the comment "
        "block in migrate.py for what to do instead."
    )


def test_an_untracked_database_is_refused_before_any_phase_runs():
    """The behaviour the test above leans on, asserted rather than assumed.

    If this ever stopped being true, replaying the corpus would become reachable
    and the interleave above would stop being harmless.
    """
    class _EmptyCursor:
        def execute(self, *args):
            pass

        def fetchall(self):
            return []

    assert migrate.cmd_apply(None, _EmptyCursor(), dry_run=False,
                             phase="before-pull") == 1


def test_a_phase_with_nothing_to_do_succeeds():
    """The common case, and the one that would break every deploy if it failed.

    Most releases carry a migration for one phase and nothing for the other, so
    `release.yml` runs an empty pass nearly every time. A non-zero exit there
    would fail the release AFTER the image had already been swapped.
    """
    class _AllAppliedCursor:
        def execute(self, *args):
            pass

        def fetchall(self):
            return [(path.name,) for path in migration_files()]

    for phase in ("before-pull", "after-pull"):
        assert migrate.cmd_apply(None, _AllAppliedCursor(), dry_run=False,
                                 phase=phase) == 0, phase
