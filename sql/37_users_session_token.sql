-- 37: give every user a rotatable session token.
--
-- Filed as #271, split out of #224 so the schema change stands alone. The
-- behaviour that consumes this column is #272 and ships separately — this
-- migration adds the column and nothing reads it yet.
--
-- ── Why the column exists ──────────────────────────────────────────────────
--
-- `models.py::User` extends UserMixin without overriding `get_id()`, so a
-- Flask-Login session cookie identifies a user by PRIMARY KEY ALONE — it says
-- "user 42" and carries nothing the server can invalidate. Combined with
-- `login_user(user, remember=True)` (auth.py, unconditional since v10.13 so an
-- installed PWA does not re-prompt on every launch) and no
-- REMEMBER_COOKIE_DURATION set, Flask-Login's default applies and a cookie
-- authenticates for 365 DAYS.
--
-- The consequence, and the reason this is worth a migration: changing your
-- password revokes nothing. A cookie on a device you no longer control keeps
-- working for up to a year, and the one action a worried user actually takes
-- does not shorten that by a day.
--
-- Folding a per-user token into `get_id()` gives the server something to
-- rotate. #272 rotates it in the same transaction as the password update.
--
-- ── ⚠️ DEPLOY ORDER: THIS GOES *BEFORE* THE IMAGE PULL ─────────────────────
--
-- Additive, and the mirror of sql/36's DROP. Existing rows take a token from
-- the DEFAULT, and code that has never heard of the column is unaffected, so
-- the currently-running image keeps working against the new schema. pg_dump
-- first, as always.
--
-- ── ⚠️ NO SESSION IS SIGNED OUT BY *THIS* FILE ─────────────────────────────
--
-- Nothing reads the column until #272 ships. The one-time sign-out happens when
-- the APP change lands and starts putting the token in `get_id()`: no cookie
-- outstanding at that moment carries a token, so every existing session — Sean's
-- included — is invalidated exactly once. That is expected, and belongs in the
-- release notes rather than arriving as a surprise. Do not try to avoid it by
-- backfilling something into the cookies; there is nothing to backfill into.
--
-- ── gen_random_uuid() needs no extension ───────────────────────────────────
--
-- It is built into PostgreSQL 13+, and prod runs `postgres:16`. Deliberately
-- NOT `pgcrypto`, which would be an extension to install on the Droplet for a
-- function core already provides.
--
-- IF NOT EXISTS so a re-run is a no-op rather than an error.

BEGIN;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS session_token uuid NOT NULL DEFAULT gen_random_uuid();

COMMIT;
