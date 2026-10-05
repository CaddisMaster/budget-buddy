-- 40: give a transaction somewhere to keep the statement reference it was
-- imported from.
--
-- Filed as #444, split out of #445 (fill in missing transactions from a
-- statement export) so the schema change stands alone. Nothing reads or writes
-- the column yet.
--
-- ── Why ─────────────────────────────────────────────────────────────────────
--
-- An OFX/QFX export gives every transaction a bank-issued FITID, there so an
-- importer can tell "already imported" from "new" exactly. Without somewhere to
-- keep it, a re-import can only guess by amount and date.
--
-- import_ref — the FITID (or, for a CSV, whatever #445 derives). NULL for every
--              existing row and every row entered by hand. Bounded at 255: the
--              OFX spec's own limit, and it keeps an uploaded file from storing
--              an arbitrary blob.
--
-- ── ⚠️ THE UNIQUE INDEX IS PER ACCOUNT, AND PARTIAL ─────────────────────────
--
-- A FITID is only unique within one account at one bank, and two banks can
-- issue the same string. So the same reference may appear in two accounts, but
-- never twice in one. No user_id column: account_id is a global serial key and
-- every account belongs to one user, so the account already separates users
-- (a mutation pass found user_id in the index changed nothing). `WHERE
-- import_ref IS NOT NULL` keeps the index to imported rows: hand-entered rows
-- are most of the table and never need it.
--
-- ── ⚠️ DEPLOY ORDER: THIS GOES *BEFORE* THE IMAGE PULL ─────────────────────
--
-- Additive only. The running image's INSERTs don't name the column, so they
-- keep working. A plain (not CONCURRENTLY) index: the table is small, and
-- CONCURRENTLY cannot run inside this transaction. pg_dump first, as always.
--
-- IF NOT EXISTS so a re-run is a no-op rather than an error.

BEGIN;

ALTER TABLE public.transactions
    ADD COLUMN IF NOT EXISTS import_ref character varying(255);

CREATE UNIQUE INDEX IF NOT EXISTS transactions_import_ref_uniq
    ON public.transactions (account_id, import_ref)
    WHERE import_ref IS NOT NULL;

COMMIT;
