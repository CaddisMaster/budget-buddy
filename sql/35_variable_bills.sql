-- #191 Variable-amount bills: the schema half.
--
-- A bill that is due the same day every month but never for the same amount —
-- electricity is the filed example — posts with LAST month's figure and nothing
-- says so. The existing reminder (#33) is the evening BEFORE, and quotes that
-- same stale amount, so it cannot serve here: this is a post-posting nudge.
--
-- Three additive changes:
--
-- schedules.is_variable_amount — per-schedule, not global. A blanket alert on
--   every posting would fire for fixed subscriptions and paychecks, which is
--   noise nobody asked for. Defaults false, so nothing changes for any existing
--   schedule and no alert can fire until a user opts a bill in.
--
-- transactions.schedule_id — the link that did not exist. Nothing in the ledger
--   recorded which schedule materialized a row, so there was no way to ask "did a
--   variable bill post today" after the fact. That question has to be answerable
--   AFTER the fact, because run_due_schedules() also runs on three page-load
--   paths: open the app at 09:00 and the bill is already posted with next_due
--   advanced, leaving the evening job nothing to notice. Captured at the INSERT
--   instead.
--
--   NULLABLE and ON DELETE SET NULL, both deliberate. A deleted schedule must
--   never take its posted ledger rows with it — those are real money that really
--   moved. And every row predating this column stays NULL, which is what stops
--   the first deploy alerting about months of history.
--
-- reminder_log.source gains 'posted' — the idempotency marker for the new alert.
--   It must NOT reuse 'schedule': that key is already claimed by the due-tomorrow
--   reminder for the same occurrence, so sharing it would mean whichever fired
--   first silently suppressed the other. Here source_id is a TRANSACTION id
--   rather than a schedule id, which the existing comment already allows for —
--   source_id addresses several tables and is deliberately not a foreign key.
--
-- Purely additive — applies BEFORE the image pull (scripts/migrate.py already
-- runs migrations in that order, and release.yml runs it before `pull web`).

BEGIN;

ALTER TABLE public.schedules
    ADD COLUMN IF NOT EXISTS is_variable_amount boolean NOT NULL DEFAULT false;

ALTER TABLE public.transactions
    ADD COLUMN IF NOT EXISTS schedule_id integer
        REFERENCES schedules(id) ON DELETE SET NULL;

-- The daily pass looks up recently posted rows BY schedule, and the FK's own
-- ON DELETE SET NULL scans this column too.
CREATE INDEX IF NOT EXISTS transactions_schedule_idx
    ON public.transactions (schedule_id);

-- Unnamed CHECKs get the `<table>_<column>_check` name, which is what sql/32
-- created. Dropped IF EXISTS so this is re-runnable, then recreated with the
-- third value.
ALTER TABLE public.reminder_log
    DROP CONSTRAINT IF EXISTS reminder_log_source_check;
ALTER TABLE public.reminder_log
    ADD CONSTRAINT reminder_log_source_check
        CHECK (source IN ('schedule', 'transfer', 'posted'));

COMMIT;
