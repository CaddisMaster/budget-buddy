-- ============================================================
-- Budget Buddy — Complete Database Schema
-- Run this single file on a fresh database to set up everything
-- ============================================================

-- ------------------------------------------------------------
-- Users (must exist before tables that reference it)
-- ------------------------------------------------------------
CREATE TABLE public.users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW(),
    email TEXT,
    weekly_digest BOOLEAN NOT NULL DEFAULT false,
    last_digest_sent_on DATE
);

-- ------------------------------------------------------------
-- Categories
-- ------------------------------------------------------------
CREATE TABLE public.categories (
    id SERIAL PRIMARY KEY,
    name character varying(50) NOT NULL,
    description text,
    kind VARCHAR(10) NOT NULL DEFAULT 'expense'
        CHECK (kind IN ('expense', 'income')),
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Account
-- ------------------------------------------------------------
CREATE TABLE public.account (
    account_id SERIAL PRIMARY KEY,
    account_name character varying(50) NOT NULL,
    type character varying(50) NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_checked_in date,  -- v10.9 balance check-in; NULL = never reconciled
    credit_limit numeric(10,2),  -- v10.10 credit limit; NULL = not set; meaningful for Credit Card accounts
    apr numeric(5,2)  -- v10.15 APR percent; NULL = not set; meaningful for Credit Card accounts
);

-- ------------------------------------------------------------
-- Transactions
-- ------------------------------------------------------------
CREATE TABLE public.transactions (
    id SERIAL PRIMARY KEY,
    amount numeric(10,2) NOT NULL,
    description text,
    category_id integer,
    account_id integer,
    transaction_date date NOT NULL DEFAULT CURRENT_DATE,
    transaction_type character varying(10) NOT NULL DEFAULT 'expense',
    created_at timestamp without time zone DEFAULT now(),
    is_recurring BOOLEAN NOT NULL DEFAULT false,
    frequency VARCHAR(20) DEFAULT NULL,
    next_due DATE DEFAULT NULL,
    recur_second_day SMALLINT DEFAULT NULL,
    is_adjustment BOOLEAN NOT NULL DEFAULT false,
    is_transfer BOOLEAN NOT NULL DEFAULT false,
    -- A DISPLAY flag, unlike is_adjustment above: a pending row is pinned to the
    -- top of History but counts normally in every figure. See sql/33.
    is_pending BOOLEAN NOT NULL DEFAULT false,
    -- Which schedule materialized this row, if any (#191, sql/35). NULL for
    -- everything entered by hand and for every row predating the column. The FK
    -- is declared further down, since `schedules` is created after this table.
    schedule_id integer,
    transfer_group_id integer,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT valid_transaction_type CHECK (transaction_type IN ('expense', 'income'))
);

-- Ties the two legs of a transfer together (one nextval per transfer).
CREATE SEQUENCE public.transfer_group_seq;

-- Indexes (v10.11, sql/26): user_id backs every page's scoping filter; the FK
-- columns back the ON DELETE RESTRICT checks + transfer-pair lookups.
CREATE INDEX idx_transactions_user_id ON transactions (user_id);
CREATE INDEX idx_transactions_category_id ON transactions (category_id);
CREATE INDEX idx_transactions_account_id ON transactions (account_id);
CREATE INDEX idx_transactions_transfer_group_id ON transactions (transfer_group_id);
-- The daily variable-bill pass looks rows up BY schedule (#191, sql/35), and the
-- FK's ON DELETE SET NULL scans this column too.
CREATE INDEX transactions_schedule_idx ON transactions (schedule_id);

-- ------------------------------------------------------------
-- Budgets
-- ------------------------------------------------------------
-- One row per (user_id, category_id) holding a single monthly amount; stores
-- overrides only — a category with no row falls back to its suggested default.
CREATE TABLE public.budgets (
    id SERIAL PRIMARY KEY,
    category_id integer NOT NULL,
    amount numeric(10,2) NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT uq_budget_user_category UNIQUE (user_id, category_id)
);

-- ------------------------------------------------------------
-- Goals (account-linked; save UP toward a target or pay a balance DOWN to $0)
-- ------------------------------------------------------------
-- goal_type 'payoff' (v10.9, sql/20) snapshots at creation: baseline_amount =
-- the account's (negative) balance, target_amount = the starting debt — so the
-- projection math is shared with 'save' and the type only drives wording.
CREATE TABLE public.goals (
    id SERIAL PRIMARY KEY,
    name character varying(80) NOT NULL,
    target_amount numeric(10,2) NOT NULL,
    target_date date,
    account_id integer NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    baseline_amount numeric(10,2) NOT NULL DEFAULT 0,
    goal_type VARCHAR(10) NOT NULL DEFAULT 'save'
        CHECK (goal_type IN ('save', 'payoff')),
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Schedules (recurring income & expense templates, v10.0)
-- ------------------------------------------------------------
-- A schedule is NOT a ledger row — it generates a plain transaction on each
-- due date (run_due_schedules). frequency + anchor_day/second_day (semimonthly)
-- + next_due drive generation. The transactions.is_recurring/frequency/
-- next_due/recur_second_day columns above are legacy as of v10 (always default).
CREATE TABLE public.schedules (
    id SERIAL PRIMARY KEY,
    amount numeric(10,2) NOT NULL,
    description text,
    category_id integer REFERENCES categories(id) ON DELETE RESTRICT,
    account_id integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    transaction_type character varying(10) NOT NULL DEFAULT 'expense'
        CHECK (transaction_type IN ('expense', 'income')),
    frequency character varying(20) NOT NULL,
    anchor_day smallint,
    second_day smallint,
    next_due date NOT NULL,
    -- NULL = runs indefinitely. Finished when next_due > end_date (#32).
    end_date date,
    -- #191 (sql/35): this bill's amount changes every time, so the row posted on
    -- its due date carries the PREVIOUS amount and needs correcting. Opt-in per
    -- schedule — a blanket alert would fire for fixed subscriptions too.
    is_variable_amount boolean NOT NULL DEFAULT false,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Foreign Key Constraints
-- ------------------------------------------------------------
ALTER TABLE transactions
    ADD CONSTRAINT fk_transactions_category
    FOREIGN KEY (category_id)
    REFERENCES categories (id)
    ON DELETE RESTRICT;

ALTER TABLE transactions
    ADD CONSTRAINT fk_transactions_account
    FOREIGN KEY (account_id)
    REFERENCES account (account_id)
    ON DELETE RESTRICT;

-- #191 (sql/35). SET NULL, never CASCADE: deleting a schedule must not delete
-- the transactions it posted — those are real money that really moved.
ALTER TABLE transactions
    ADD CONSTRAINT fk_transactions_schedule
    FOREIGN KEY (schedule_id)
    REFERENCES schedules (id)
    ON DELETE SET NULL;

ALTER TABLE budgets
    ADD CONSTRAINT fk_budgets_category
    FOREIGN KEY (category_id)
    REFERENCES categories (id)
    ON DELETE RESTRICT;

-- ------------------------------------------------------------
-- Budget history (v10.9, sql/22 — append-only log of budget changes)
-- The budgets row upserts in place, so this log is the ONLY record of past
-- amounts. Written on every set/clear/review-apply (record_budget_change in
-- budgets.py); nothing reads it yet — a future budget report will grade past
-- months against the amount actually in effect. amount NULL = cleared.
-- category FK CASCADEs (RESTRICT would forever block deleting any category
-- that ever had a budget).
-- ------------------------------------------------------------
CREATE TABLE public.budget_history (
    id SERIAL PRIMARY KEY,
    category_id integer NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    amount numeric(10,2),
    changed_at timestamp without time zone NOT NULL DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Insights (v10.1 — cached monthly AI digest, one row per user per month)
-- content is narrative JSON {"summary": ..., "tips": [...]}; the figures it
-- describes are recomputed deterministically server-side, never stored here.
-- ------------------------------------------------------------
CREATE TABLE public.insights (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year smallint NOT NULL,
    month smallint NOT NULL,
    content text NOT NULL,
    model character varying(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_insight_user_period UNIQUE (user_id, year, month)
);

-- Money agent — cached weekly investigation runs, one row per (user, week)
-- (see sql/25). The insights pattern keyed by the week's Sunday; content is
-- the narrative JSON {summary, findings:[{title, detail, evidence}]}.
CREATE TABLE public.agent_runs (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_start date NOT NULL,
    content text NOT NULL,
    model character varying(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_agent_run_user_period UNIQUE (user_id, period_start)
);

-- ------------------------------------------------------------
-- Transfer schedules (v10.4 — recurring transfers; see sql/17)
-- The transfer-tab twin of `schedules`: run_due_transfers() materializes a
-- paired expense+income transfer (shared transfer_group_id, is_transfer=true) on
-- each due date going forward. Two accounts, no category → its own table.
-- ------------------------------------------------------------
CREATE TABLE public.transfer_schedules (
    id SERIAL PRIMARY KEY,
    amount numeric(10,2) NOT NULL,
    description text,
    from_account_id integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    to_account_id   integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    frequency character varying(20) NOT NULL,
    anchor_day smallint,
    second_day smallint,
    next_due date NOT NULL,
    -- NULL = runs indefinitely. Finished when next_due > end_date (#32).
    end_date date,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Push subscriptions + reminder log (#33 — bill-due push reminders; see sql/32)
-- One subscription row per DEVICE (endpoint is the push service's URL for that
-- browser install, hence globally unique). reminder_log is the idempotency
-- marker, keyed per OCCURRENCE so widening the reminder lead time later can't
-- make the same bill re-notify daily; `source`/`source_id` address two tables,
-- so they are deliberately not a foreign key.
-- ------------------------------------------------------------
CREATE TABLE public.push_subscriptions (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint text NOT NULL UNIQUE,
    p256dh text NOT NULL,
    auth text NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);

CREATE INDEX push_subscriptions_user_idx ON public.push_subscriptions (user_id);

CREATE TABLE public.reminder_log (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- 'posted' (#191, sql/35) claims a transaction that a variable-amount
    -- schedule just materialized; source_id is then a TRANSACTION id. It is a
    -- separate value on purpose — 'schedule' is already claimed by the
    -- due-tomorrow reminder for the very same occurrence.
    source character varying(10) NOT NULL
        CHECK (source IN ('schedule', 'transfer', 'posted')),
    source_id integer NOT NULL,
    occurrence_date date NOT NULL,
    sent_at timestamp without time zone DEFAULT now(),
    UNIQUE (user_id, source, source_id, occurrence_date)
);

-- ------------------------------------------------------------
-- Job runs (#151 — when each scheduled job last finished; see sql/34)
-- One row per JOB, upserted on completion — not an append-only log, and
-- deliberately NOT user-scoped: these jobs run for everyone at once, so there is
-- no user the daily pass belongs to. Exists because /settings could previously
-- only report that the scheduler was switched ON, which since #33 is a different
-- question from whether the ledger is still being materialized daily.
-- `summary` is free text for a human; nothing parses it.
-- ------------------------------------------------------------
CREATE TABLE public.job_runs (
    id SERIAL PRIMARY KEY,
    job_name text NOT NULL UNIQUE,
    last_run_at timestamp without time zone NOT NULL DEFAULT now(),
    summary text
);

-- ============================================================
-- The least-privilege application role (see sql/30_app_role.sql)
--
-- ⚠️ MUST STAY LAST. `GRANT ... ON ALL TABLES IN SCHEMA public` binds what
-- exists at execution time, so every table and sequence above has to be
-- created before this runs. Anything added below these statements would be
-- unreachable by the app until the next `ALTER DEFAULT PRIVILEGES` grant
-- happened to cover it.
--
-- Why it is here at all: sql/30_app_role.sql is a forward-only migration, and
-- schema.sql is the ONLY artifact that builds a database from nothing. Without
-- this block a fresh database plus `scripts/migrate.py --baseline` records
-- 30_app_role.sql as applied while the role does not exist — `--status` then
-- reports everything applied and nothing pending, and the drift is invisible
-- to every tool that would normally surface it (#160). The numbered migration
-- stays exactly as it is; it remains the path for an EXISTING deployment.
-- ============================================================

-- Idempotent: a role is a CLUSTER-level object, not a database one, so it may
-- already exist on a cluster that has hosted a previous database. Loading
-- schema.sql into a second database on that same cluster must not fail.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'budget_app') THEN
        CREATE ROLE budget_app LOGIN;
    END IF;
END
$$;

-- Explicitly deny the things it must never have. NOSUPERUSER/NOCREATEDB/
-- NOCREATEROLE are the defaults, but stating them makes the intent auditable
-- and survives a role that was created by hand with different options.
ALTER ROLE budget_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;

-- Reach the database and see the schema, but not modify the schema itself.
-- GRANT ... ON DATABASE needs a literal name, so the current one is
-- interpolated rather than hardcoded — this file has to work against whatever
-- DB_NAME an environment happens to use.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO budget_app', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO budget_app;

-- Read and write rows. Note the absence of TRUNCATE, REFERENCES and TRIGGER,
-- and of any CREATE on the schema — the application performs no DDL at runtime.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO budget_app;

-- Sequences back every id column, and transfer_group_seq is read directly when
-- pairing the two legs of a transfer.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO budget_app;

-- Tables added by a later migration are created by the superuser, so without
-- this the app would lose access to them the moment they appear.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO budget_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO budget_app;

-- ⚠️ NO PASSWORD IS SET HERE, deliberately — this repository is public, and the
-- same reasoning as sql/30_app_role.sql applies identically. The role cannot
-- log in until an operator runs:
--
--   ALTER ROLE budget_app PASSWORD 'generate-a-strong-one';
--
-- On a rebuild that is not optional: a restored .env carrying
-- DB_APP_USER=budget_app is the difference between the app connecting and not.
-- See RUNBOOK.md §8 step 9.
