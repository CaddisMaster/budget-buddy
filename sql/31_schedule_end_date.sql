-- #32 Schedule end date — a recurring schedule can finish.
--
-- A schedule ran forever once created. Real obligations don't: a car loan has a
-- final payment, a gym membership is cancelled at the end of the year. The only
-- way to stop one was to remember to delete it on the right day, and forgetting
-- silently materialised transactions that never happened — which then poison
-- balances, budget-vs-actual and the forecast.
--
-- Both tables get the column together. transfer_schedules is the transfer twin
-- of schedules (run_due_transfers mirrors run_due_schedules line for line), and
-- the weekly digest enumerates both; ending one but not the other would show up
-- as the two Scheduled/Transfers pages disagreeing.
--
-- NULL = no end date = runs indefinitely, which is every existing row, so
-- nothing changes for anything already created. Purely additive.
--
-- A schedule is FINISHED when end_date IS NOT NULL AND next_due > end_date.
-- That predicate is deliberately not "end_date < today": a schedule ending
-- 2026-09-15 whose next_due is 2026-09-01 still owes that occurrence on the
-- 10th. It is also independent of whether the due-runner has fired, since
-- next_due only ever moves forward and never past what was materialised.
--
-- Additive, so this applies BEFORE the image pull (scripts/migrate.py runs it
-- in that order automatically).

BEGIN;

ALTER TABLE public.schedules
    ADD COLUMN IF NOT EXISTS end_date date;

ALTER TABLE public.transfer_schedules
    ADD COLUMN IF NOT EXISTS end_date date;

COMMIT;
