-- #151 Record when each scheduled job last actually finished.
--
-- /settings already reports the background scheduler, but only by reading its
-- env var — which answers "was the switch set when this process started", not
-- "is the job running". Those were the same question while the scheduler's only
-- job was the digest email: a missed digest is self-evident, because no email
-- arrives.
--
-- They stopped being the same question in 0.2.0. Since #33 the daily job also
-- calls materialize_all_users(), which is what turns a recurring schedule into
-- an actual transaction row for every user whether or not anyone logs in. The
-- ledger's correctness now depends on a background thread that is deliberately
-- gated on nothing at all (see the scheduler comment in app/__init__.py for why
-- hanging it off a credential would be wrong).
--
-- So the failure mode this table exists to make visible: the thread dies, or
-- ENABLE_DIGEST_SCHEDULER is dropped from the server .env by some future change,
-- and /settings still reports whatever the env var says, /healthz stays green
-- because the database is reachable, and recurring transactions silently stop
-- appearing — indistinguishable from "nothing was due". It would most likely be
-- noticed weeks later via wrong balances.
--
-- Three things worth recording about the shape:
--
--   ONE ROW PER JOB, UPSERTED — not an append-only log. The panel asks "when did
--     this last finish", which is one fact per job. agent_runs is the precedent
--     for upsert-on-a-unique-key; reminder_log is append-only because there every
--     occurrence is a distinct claim that must not be lost. A full run history
--     would need a pruning job to stay bounded, for no reader that wants it.
--
--   NO user_id, DELIBERATELY. Nearly every table here is user-scoped and the
--     rule is load-bearing, so the exception needs stating: these jobs run for
--     everyone at once, and "which user did the daily pass belong to" has no
--     answer. push_subscriptions is the other non-user-keyed shape (per DEVICE);
--     this one is per JOB.
--
--   WRITTEN ON COMPLETION, never on dispatch. A job that starts and throws must
--     not leave a row that looks like a job that worked.
--
-- `summary` is free text for a human reading the panel ("materialized 3 user(s),
-- sent 1 reminder(s)"). Nothing parses it, and nothing should start to — it costs
-- one column and lets the panel answer "did it do anything" as well as "did it
-- run".
--
-- Purely additive — applies BEFORE the image pull (scripts/migrate.py already
-- runs migrations in that order), so the table exists before any code writes it.
-- Nothing reads or writes it in the release that adds it.

BEGIN;

CREATE TABLE IF NOT EXISTS public.job_runs (
    id SERIAL PRIMARY KEY,
    job_name text NOT NULL UNIQUE,
    last_run_at timestamp without time zone NOT NULL DEFAULT now(),
    summary text
);

COMMIT;
