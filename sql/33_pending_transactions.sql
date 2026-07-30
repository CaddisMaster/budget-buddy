-- #86 Flag a transaction as Pending and pin it to the top of History.
--
-- Some charges enter the ledger before their real amount is known. A fuel pump
-- authorises $1.00, so $1.00 is what gets typed in, and the true amount only
-- lands a day or two later when the charge posts. Restaurant tips and hotel
-- holds behave the same way. Nothing marked those rows as provisional, so they
-- sank into History in date order and a placeholder amount could sit there
-- indefinitely, quietly wrong.
--
-- is_pending is a DISPLAY flag, and that is the whole distinction worth
-- recording here. It looks like is_adjustment — same type, same default — but it
-- means the opposite thing:
--
--   is_adjustment EXCLUDES a row from analytics. It exists so a balance
--     check-in can close a gap without that correction being read as spending.
--
--   is_pending excludes a row from NOTHING. The dashboard, budgets, insights,
--     forecasts and the running balance all count a pending row as a real
--     transaction, because the money genuinely did leave the account. A
--     placeholder amount being briefly wrong is preferable to spend that
--     silently does not count at all (settled, Sean, 2026-07-29).
--
-- So do not add is_pending to the `is_adjustment = false AND is_transfer =
-- false` filter lists that guard the analytics surfaces. There are 23 of them
-- and none of them should learn about this column.
--
-- Clearing the flag is a manual toggle on the History row; editing the amount
-- deliberately does not clear it. No back-fill: every existing row is posted,
-- which is what the default gives.
--
-- Purely additive — applies BEFORE the image pull (scripts/migrate.py already
-- runs migrations in that order), so the column exists before any code selects
-- it.

BEGIN;

ALTER TABLE public.transactions
    ADD COLUMN IF NOT EXISTS is_pending BOOLEAN NOT NULL DEFAULT false;

COMMIT;
