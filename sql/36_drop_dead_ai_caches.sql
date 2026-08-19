-- 36: drop the two dead AI cache tables.
--
-- `forecasts` and `goal_coach` are the last remains of the v10.x "one card per
-- AI feature" era. Both cached narrative only — every figure they described was
-- recomputed on read — so there is nothing here to migrate, only to remove.
--
--   forecasts   — dead since #232, which folded Home's four AI surfaces into one
--                 panel. The month-ahead Forecast card, its /forecasts/generate
--                 route and ai.generate_forecast() went with it. The ARITHMETIC
--                 survived (compute_forecast() still feeds the month read and
--                 the month_projection Ask tool); only the cache is dead.
--   goal_coach  — dead since #262, which removed the Goal Coach from /goals.
--
-- Filed as #236 for forecasts alone; goal_coach joined it rather than taking a
-- second migration, because a migration stands alone in its own PR and these
-- are one operation on two tables with one reason.
--
-- ── ⚠️ DEPLOY ORDER: THIS GOES *AFTER* THE IMAGE PULL ──────────────────────
--
-- A DROP is the opposite of an additive migration. The running container must
-- ALREADY be the version that no longer references these tables, or the old
-- code SELECTs something that has gone. Nothing has referenced `forecasts`
-- since #232 or `goal_coach` since #263 merged, so in practice the window is
-- empty either way — but the rule is the rule, and the habit is what protects
-- the migration where it is NOT already true.
--
-- ── ⚠️ ONE-WAY DOOR ────────────────────────────────────────────────────────
--
-- pg_dump first. These tables hold every forecast and coaching narrative ever
-- generated for every user. Nothing reads them and nothing can regenerate them
-- — the features are gone. Taking the dump is the only thing that makes this
-- reversible.
--
-- ── Older backups still contain them ───────────────────────────────────────
--
-- The retained dumps predate this migration and will carry both tables for as
-- long as they are kept. That is fine and needs no handling:
-- scripts/restore_check.py deliberately has NO expected-table list (see the
-- comment there), so a dump containing tables the current schema lacks still
-- validates. Restoring one into a fresh database recreates them, empty and
-- unreferenced; re-applying this migration removes them again.
--
-- IF EXISTS on both, so a re-run is a no-op rather than an error.

BEGIN;

DROP TABLE IF EXISTS public.forecasts;
DROP TABLE IF EXISTS public.goal_coach;

COMMIT;
