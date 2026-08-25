-- Phase: smart/monthly budgets (v7.0)
--
-- Budgets move from dated rows (one per period: category + amount + period_start
-- + period_end) to a single MONTHLY amount per category. The table now holds at
-- most one row per (user_id, category_id) and stores OVERRIDES ONLY — a category
-- with no row falls back to its 6-month-average suggested default in the app.
--
-- Wipe-and-re-seed: the old dated rows have no clean mapping to a single monthly
-- amount, so they are dropped. Every category re-populates the Budgets cockpit
-- from its live suggestion until the user sets an explicit amount.
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image, so
-- the new code never queries period_start/period_end after they're gone.
--
-- ⚠️ THIS FILE IS THE COUNTEREXAMPLE THAT #277's RULE NOW FORBIDS. It drops two
-- columns AND adds uq_budget_user_category, which the new code's ON CONFLICT
-- upsert in /budgets/set needs the moment it starts serving. Those two halves
-- want opposite phases, so no single declaration is right for it.
-- The pragma below records what actually HAPPENED — it ran before the pull — and
-- the file is grandfathered in tests/test_migration_phases.py because it is
-- already recorded in schema_migrations on every live database and cannot be
-- split now without orphaning that row. A new mixed migration gets two files.
-- deploy: before-pull

-- 1. Drop the old dated rows.
DELETE FROM budgets;

-- 2. Drop the period model.
ALTER TABLE budgets DROP CONSTRAINT IF EXISTS valid_period;
ALTER TABLE budgets DROP COLUMN IF EXISTS period_start;
ALTER TABLE budgets DROP COLUMN IF EXISTS period_end;

-- 3. One budget per category per user — enables ON CONFLICT upsert from /budgets/set.
ALTER TABLE budgets ADD CONSTRAINT uq_budget_user_category UNIQUE (user_id, category_id);
