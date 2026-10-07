-- 41: give an account somewhere to keep the last four digits of its number.
--
-- Filed as #471, split out of #461 (work out which account a statement
-- belongs to) so the schema change stands alone. Nothing reads or writes the
-- column yet.
--
-- ── Why ─────────────────────────────────────────────────────────────────────
--
-- A statement names its account: an OFX carries ACCTID, and a PDF or a
-- screenshot prints "ending in 1234". With the digits stored, an upload can be
-- matched to the account it belongs to instead of asking every time.
--
-- number_last4 — the LAST FOUR DIGITS ONLY, never a full account number; the
--                CHECK refuses anything that isn't exactly four digits. NULL
--                for every existing account and every account made by hand.
--
-- ── ⚠️ THE UNIQUE INDEX IS PER USER, AND PARTIAL ────────────────────────────
--
-- #461's rule, settled with Sean: the last apply wins, so applying an import
-- MOVES its digits onto the account it was applied to. The index makes "one of
-- my accounts at most" a database guarantee rather than a convention. user_id
-- belongs in it (unlike #444's index): four digits are only unique within one
-- user's accounts, and two users may share any four. `WHERE number_last4 IS
-- NOT NULL` lets every account without digits coexist.
--
-- ── ⚠️ DEPLOY ORDER: THIS GOES *BEFORE* THE IMAGE PULL ─────────────────────
--
-- Additive only. The running image's INSERTs and UPDATEs don't name the
-- column, so they keep working. The CHECK is inline on ADD COLUMN, so a re-run
-- skips both (PostgreSQL 16 has no ADD CONSTRAINT IF NOT EXISTS).
-- pg_dump first, as always.

BEGIN;

ALTER TABLE public.account
    ADD COLUMN IF NOT EXISTS number_last4 character varying(4)
        CONSTRAINT account_number_last4_is_digits CHECK (number_last4 ~ '^[0-9]{4}$');

CREATE UNIQUE INDEX IF NOT EXISTS account_number_last4_uniq
    ON public.account (user_id, number_last4)
    WHERE number_last4 IS NOT NULL;

COMMIT;
