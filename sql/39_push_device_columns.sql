-- 39: give each push subscription a device label and a last-seen time.
--
-- Filed as #438, split out of #437 so the schema change stands alone. The
-- behaviour that reads and writes these columns is #437 and ships separately —
-- this migration adds them and nothing reads them yet.
--
-- ── Why the columns exist ──────────────────────────────────────────────────
--
-- On 2026-10-05 a release announcement reported "3 device(s)" while Sean's
-- phone got nothing. One of his two rows was a dead phone subscription: Apple
-- still answered 201 for it, so nothing ever pruned it. Telling it apart from
-- his Mac took a tagged test push plus an Nginx access-log grep on the Droplet,
-- because no column says WHICH device a row is or WHETHER it is still around.
--
-- device_label  — a short name like "iPhone · Safari", derived SERVER-SIDE from
--                 the User-Agent at subscribe time. The client never posts free
--                 text into it. Bounded so a hostile User-Agent cannot bloat it.
-- last_seen_at  — touched by #437's re-send on every Home load, so a device
--                 that stops opening the app visibly goes stale on Profile.
--
-- ── ⚠️ EXISTING ROWS STAY NULL, DELIBERATELY ───────────────────────────────
--
-- `ADD COLUMN ... DEFAULT now()` would stamp every existing row with the
-- moment this migration ran, i.e. claim the dead phone above was "seen today".
-- Backfilling `created_at` would claim each was last seen when it was made.
-- Neither is known, so the column is added WITHOUT a default (existing rows get
-- NULL) and the default is attached afterwards (new rows get now()). #437
-- renders NULL as "not since tracking began". Do not merge these two statements
-- into one: that is the version that invents a history.
--
-- ── ⚠️ DEPLOY ORDER: THIS GOES *BEFORE* THE IMAGE PULL ─────────────────────
--
-- Additive only. The running image's INSERT names neither column, so it keeps
-- working against the new schema. pg_dump first, as always.
--
-- IF NOT EXISTS so a re-run is a no-op rather than an error.

BEGIN;

ALTER TABLE public.push_subscriptions
    ADD COLUMN IF NOT EXISTS device_label character varying(64);

ALTER TABLE public.push_subscriptions
    ADD COLUMN IF NOT EXISTS last_seen_at timestamp without time zone;

ALTER TABLE public.push_subscriptions
    ALTER COLUMN last_seen_at SET DEFAULT now();

COMMIT;
