-- #33 Bill-due push reminders to the installed PWA.
--
-- The app already knows when every bill is due (schedules.next_due +
-- transfer_schedules.next_due, enumerated by main.upcoming_occurrences), but
-- that knowledge only reaches the user if they open the app or read the Sunday
-- digest. A bill due Tuesday is most useful to know about on Monday.
--
-- Two tables:
--
-- push_subscriptions — one row per DEVICE, not per user: a phone and a laptop
--   are separate Web Push endpoints. `endpoint` is globally unique (it is the
--   push service's URL for that browser install), so re-subscribing the same
--   browser upserts rather than duplicating. If a different user signs in on
--   the same browser and subscribes, the endpoint moves to them, which is the
--   correct outcome — the notification must follow whoever is logged in.
--
-- reminder_log — the idempotency marker, one row per (user, schedule,
--   occurrence). Deliberately keyed per OCCURRENCE rather than a
--   users.last_reminder_sent_on date column: a date marker only holds while the
--   reminder lead time is exactly one day, and the moment the window widens the
--   same occurrence would re-notify every day it stayed in range. Claimed with
--   INSERT ... ON CONFLICT DO NOTHING, so the row itself is the lock and it
--   survives the container restart a deploy causes.
--
--   `source` is 'schedule' | 'transfer' and `source_id` points at the matching
--   table — deliberately NOT a foreign key, since it addresses two tables. A
--   deleted schedule leaves its markers behind; they are inert (nothing will
--   ever match them again) and the volume is a handful of rows per bill per
--   year, so there is no cleanup job.
--
-- Purely additive — applies BEFORE the image pull (scripts/migrate.py already
-- runs migrations in that order).

BEGIN;

CREATE TABLE IF NOT EXISTS public.push_subscriptions (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint text NOT NULL UNIQUE,
    p256dh text NOT NULL,
    auth text NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS push_subscriptions_user_idx
    ON public.push_subscriptions (user_id);

CREATE TABLE IF NOT EXISTS public.reminder_log (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source character varying(10) NOT NULL
        CHECK (source IN ('schedule', 'transfer')),
    source_id integer NOT NULL,
    occurrence_date date NOT NULL,
    sent_at timestamp without time zone DEFAULT now(),
    UNIQUE (user_id, source, source_id, occurrence_date)
);

COMMIT;
