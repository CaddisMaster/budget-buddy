-- 30: a least-privileged role for the application to connect as.
--
-- Until this migration, the web process authenticated as the cluster
-- superuser, because compose creates the database with POSTGRES_USER=${DB_USER}
-- and the application used the same credentials. Anything achieving SQL or code
-- execution in the app inherited superuser: DROP anything, read every database
-- on the cluster, create roles, or COPY ... FROM PROGRAM to run shell commands.
--
-- The application performs no DDL at runtime. Schema changes are applied from
-- this directory by scripts/migrate.py, connecting as the OWNER — never as this
-- role. So DML is all the app needs.
-- ⚠️ "applied by hand" is what this said until #309 read it. The conclusion was
-- always right; the reason stopped being true at #277, when the deploy pipeline
-- took the job over. A stale reason is how a correct rule gets argued away.
--
-- The superuser is NOT removed — it remains the owner, and is what migrations
-- and pg_dump run as.
--
-- ── Applying it ────────────────────────────────────────────────────────────
--
--   psql -U <superuser> -d <database> -f sql/30_app_role.sql
--   psql -U <superuser> -d <database> \
--        -c "ALTER ROLE budget_app PASSWORD 'a-strong-password';"
--
-- then set DB_APP_USER=budget_app and DB_APP_PASSWORD=... in .env and restart
-- the web container. app/db.py falls back to DB_USER when those are unset, so
-- the role can exist unused until you are ready to switch.
--
-- NOTE: no password is set here on purpose — this file is in a public
-- repository. The role cannot log in until you set one.

BEGIN;

-- Idempotent: re-running the migration must not fail.
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
-- and of any CREATE on the schema — no DDL.
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

COMMIT;
