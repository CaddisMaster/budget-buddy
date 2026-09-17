-- 38: the database refuses a NaN in every money column.
--
-- Filed as #314 by the tranche-2 read of `app/blueprints/` (#309). The per-site
-- half shipped in that tranche's PR; this is the part whose real fix is a
-- migration, so it stands alone.
--
-- ── Why a constraint rather than more guards ───────────────────────────────
--
-- PostgreSQL's `numeric` accepts NaN, and the constrained forms in this schema
-- accept it too — `SELECT 'NaN'::numeric(10,2)` returns NaN. (`Infinity` is a
-- different story: the precision rejects it outright with "numeric field
-- overflow", which is why this file is about NaN only.)
--
-- Nothing writes one today. `helpers.parse_signed_amount()` rejects non-finite
-- input and its docstring says why — "one NaN row poisons every SUM() the
-- dashboards aggregate". But that validator is younger than these columns, and
-- `accounts.monthly_interest` already carries the caveat in its own docstring:
-- "Guards independently of `_parse_apr` — stored values could predate it."
--
-- If one ever existed, `goals.compute_goal_projection()` raises
-- `ValueError: cannot convert float NaN to integer` — a 500 on a page the user
-- can reach — because `remaining` goes NaN, `complete = remaining <= 0` is False
-- (NaN compares False to everything), and the pace estimate reaches
-- `math.ceil()`. Elsewhere it propagates silently: a NaN `credit_limit` rendered
-- `nan%` on /accounts and turned the whole "Total available credit" summary into
-- `nan`, because that line sums `available` across cards.
--
-- Guarding at each point of use is defence in the wrong place. It is per-column
-- and per-call-site, so it has to be remembered at every new one — and three
-- sites had already written the rule three different ways, which is exactly how
-- the missing fourth stayed invisible. A guard also cannot repair a row: if one
-- exists, every surface disagrees about it until somebody finds it by hand.
--
-- ── ⚠️ THE OBVIOUS CONSTRAINT DOES NOT WORK, AND FAILS OPEN ────────────────
--
-- #314 proposed, and it is the form everyone reaches for first:
--
--     CHECK (credit_limit IS NULL OR credit_limit = credit_limit)  -- NaN <> NaN
--
-- That comment is IEEE 754 float semantics. PostgreSQL `numeric` is NOT IEEE
-- here: it defines NaN as EQUAL to itself, so that the type can be sorted and
-- indexed. Measured against postgres:16 rather than assumed:
--
--     SELECT 'NaN'::numeric = 'NaN'::numeric;    ->  t
--
-- So `v = v` holds for a NaN, the CHECK passes, and the row is stored. Written
-- that way this migration would have applied cleanly, gone green, and protected
-- nothing at all. `<> 'NaN'::numeric` is the form that actually rejects it, and
-- `tests/test_money_is_finite.py` proves it per column rather than trusting this
-- paragraph.
--
-- ── Why NULL needs no clause ───────────────────────────────────────────────
--
-- A CHECK constraint passes when its expression is NULL, and `NULL <> 'NaN'` is
-- NULL. So the three nullable columns keep their meaning untouched:
-- `account.credit_limit` and `account.apr` mean "not set", and
-- `budget_history.amount` means "cleared". An `IS NULL OR` prefix would be
-- redundant. The tests assert this rather than leaving it to be re-derived.
--
-- ── Which columns: all nine ────────────────────────────────────────────────
--
-- Every `numeric` column in the schema, not only the ones that feed arithmetic
-- today. Applying it selectively would recreate the very thing this file fixes —
-- a rule that has to be remembered per column. `budget_history.amount` is
-- append-only and read by nothing yet, which is a premise with a short shelf
-- life; it is an amount, so whatever eventually reads it will do arithmetic on
-- it. The constraint costs nothing to carry.
--
-- ── ⚠️ DEPLOY ORDER: THIS GOES *BEFORE* THE IMAGE PULL ─────────────────────
--
-- Additive, so no `-- deploy: after-pull` pragma — silence means additive, which
-- `tests/test_migration_phases.py` enforces. Adding a constraint cannot break
-- the currently-running image: it already refuses to write what this rejects.
--
-- ⚠️ `ALTER TABLE ... ADD CONSTRAINT` VALIDATES EVERY EXISTING ROW and fails the
-- deploy if one violates it. That is the right outcome — a NaN in production is
-- something to stop for, not to wave through — but it means the set must be
-- confirmed empty first. Checked against the 2026-09-16 production dump, one day
-- old, with a positive control proving the scan reached real data:
--
--     $ zcat budget_2026-09-16.sql.gz | grep -ciw nan          ->  0
--     $ zcat budget_2026-09-16.sql.gz | grep -ci infinity      ->  0
--
-- Zero across every numeric column, not just the ones that feed arithmetic.
-- ⚠️ A dump is a point in time. Re-run that check before cutting the release
-- that carries this file.
--
-- ── Idempotent ────────────────────────────────────────────────────────────
--
-- `ADD CONSTRAINT` has no `IF NOT EXISTS` in PostgreSQL, so each is preceded by
-- `DROP CONSTRAINT IF EXISTS` and a re-run is a no-op rather than an error.
-- Dropping a constraint is not destructive in the deploy-phase sense — it can
-- never break a SELECT the old image is issuing — which is why
-- `test_migration_phases.py::DESTRUCTIVE_RE` deliberately matches only a dropped
-- TABLE or COLUMN, and not a dropped CONSTRAINT.
--
-- ⚠️ That sentence is worded around the scanner on purpose. `DESTRUCTIVE_RE` is
-- a plain regex over the whole file, comments included, so spelling the two
-- destructive statements out literally here would make this additive migration
-- fail `test_an_undeclared_migration_is_additive_by_default` — a file described
-- as safe, failing for describing itself. Filed as #375 rather than fixed here,
-- because a migration stands alone in its own PR.

BEGIN;

ALTER TABLE public.account
    DROP CONSTRAINT IF EXISTS account_credit_limit_is_finite,
    ADD  CONSTRAINT account_credit_limit_is_finite
         CHECK (credit_limit <> 'NaN'::numeric);

ALTER TABLE public.account
    DROP CONSTRAINT IF EXISTS account_apr_is_finite,
    ADD  CONSTRAINT account_apr_is_finite
         CHECK (apr <> 'NaN'::numeric);

ALTER TABLE public.transactions
    DROP CONSTRAINT IF EXISTS transactions_amount_is_finite,
    ADD  CONSTRAINT transactions_amount_is_finite
         CHECK (amount <> 'NaN'::numeric);

ALTER TABLE public.budgets
    DROP CONSTRAINT IF EXISTS budgets_amount_is_finite,
    ADD  CONSTRAINT budgets_amount_is_finite
         CHECK (amount <> 'NaN'::numeric);

ALTER TABLE public.goals
    DROP CONSTRAINT IF EXISTS goals_target_amount_is_finite,
    ADD  CONSTRAINT goals_target_amount_is_finite
         CHECK (target_amount <> 'NaN'::numeric);

ALTER TABLE public.goals
    DROP CONSTRAINT IF EXISTS goals_baseline_amount_is_finite,
    ADD  CONSTRAINT goals_baseline_amount_is_finite
         CHECK (baseline_amount <> 'NaN'::numeric);

ALTER TABLE public.schedules
    DROP CONSTRAINT IF EXISTS schedules_amount_is_finite,
    ADD  CONSTRAINT schedules_amount_is_finite
         CHECK (amount <> 'NaN'::numeric);

ALTER TABLE public.transfer_schedules
    DROP CONSTRAINT IF EXISTS transfer_schedules_amount_is_finite,
    ADD  CONSTRAINT transfer_schedules_amount_is_finite
         CHECK (amount <> 'NaN'::numeric);

ALTER TABLE public.budget_history
    DROP CONSTRAINT IF EXISTS budget_history_amount_is_finite,
    ADD  CONSTRAINT budget_history_amount_is_finite
         CHECK (amount <> 'NaN'::numeric);

COMMIT;
