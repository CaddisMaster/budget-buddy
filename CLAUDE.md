# Budget Buddy

A personal finance tracking web app built with Flask, PostgreSQL, and Docker. Deployed at budget.seandesmet.com on a DigitalOcean Droplet behind Nginx + Gunicorn with Let's Encrypt SSL.

## Tech Stack

- **Backend:** Python / Flask, psycopg2, Flask-Login, Flask-Bcrypt, Flask-Limiter, Gunicorn
- **Database:** PostgreSQL (Docker container)
- **Frontend:** Jinja2 templates + HTMX (vendored `htmx.min.js`, inline CRUD), Chart.js (vendored), vanilla CSS
- **Infrastructure:** Docker Compose, DigitalOcean, Nginx, Certbot

## Project Structure

```
app/
  __init__.py        # Flask app + extensions (Login, Bcrypt, Limiter, CSRF); registers the 20 blueprints; cookie flags + @after_request security headers (Secure/HSTS gated on COOKIE_SECURE); starts the APScheduler on ENABLE_DIGEST_SCHEDULER=1 ALONE and registers each job with its OWN gate (weekly digest ← mail_enabled(); daily tasks ← always — see the scheduler gotcha; safe only under single-worker gunicorn); |money template filter (thousands-sep, emits the NUMBER only — display templates ONLY: AI fact-builders / chart |tojson payloads / form input values stay raw, a comma'd value fails parse_positive_amount); css_v + brand_svg Jinja globals (both computed once at startup: the style.css content hash, and icons/icon.svg inlined |safe in base.html so sidebar mark + favicon share one source)
  pusher.py          # outbound Web Push seam (#33, the mailer.py twin). push_enabled() gate, public_key(), send_push(), PushError + PushGone (404/410 = the subscription is DEAD, caller deletes it; anything else is transient and retried tomorrow), single _call_webpush() network seam tests stub. NEVER touches the DB
  mailer.py          # outbound email seam (Resend). mail_enabled() gate (twin of ai_enabled()), send_email(), MailError, single _call_resend() network seam tests stub. NOT named email.py (would shadow stdlib)
  github.py          # outbound GitHub issue seam (#64, the mailer.py/pusher.py triplet's fourth). feedback_enabled() gate (env FEEDBACK_GITHUB_TOKEN — deliberately NOT named GITHUB_TOKEN, which is a magic name in Actions), create_issue(), GitHubError, single _call_github() network seam tests stub. Uses stdlib urllib, NOT requests (requests is undeclared — only transitively present via pywebpush). NEVER touches the DB
  jobs.py            # scheduled-job bookkeeping (#151). record_job_run(job_name, summary) upserts ONE row per job on completion (never on dispatch — a job that throws must not leave a row that looks like it worked); load_job_runs(cursor); pure summarize_job_runs(rows, *, scheduler_on, digest_registered, checked_at) → one display row per known job with an OK/STALE/NEVER/NOT_SCHEDULED state from a per-job threshold. Job-name constants DAILY/WEEKLY_DIGEST are the join key to app/__init__.py's registrations. Its own module (not admin.py) because the WRITERS are reminders.py + digests.py — the mailer.py/pusher.py/github.py single-purpose shape, though unlike those three it DOES touch the DB. ⚠️ record_job_run() swallows its own psycopg2 errors deliberately: a bookkeeping write must never break a job that already did its work
  db.py              # get_db_connection() + db_cursor() context manager (commit/rollback/close); db_cursor yields a NamedTupleCursor — rows read row.field (positional still works), SELECT columns must be uniquely named (alias with AS)
  helpers.py         # is_htmx(), hx_toast(), recent_months(), ai_enabled(); parse_positive_amount() (THE shared amount validator, rejects NaN/inf — every amount form routes through it) + parse_signed_amount() (same guard, allows negative/zero — bank balances); most_recent_sunday() (the weekly period key shared by the digest idempotency guard AND the agent's run key — lives here because digests.py imports agent.py); param parsers: parse_month_param() (every ?month read), parse_page_param() (every ?page read), parse_int_param() (every posted FK id); GENERIC_ERROR (the one user-facing message for unexpected write failures — raw exception text goes to current_app.logger.exception, never the browser)
  ai.py              # ALL model calls. parse_transaction_text() (NL quick-add), generate_insight(), generate_forecast(), answer_question() (Haiku multi-turn TOOL-USE loop), classify_transactions() (Sonnet batch), propose_budgets() (the model DOES propose amounts — pure _normalize_budget_proposals() re-resolves categories + SNAPS amounts into a facts-derived range), generate_digest(), coach, investigate_finances() (the Money agent's AUTONOMOUS loop — Sonnet, AGENT_MAX_TURNS=12, ends ONLY via the strict submit_findings tool intercepted locally; one nudge for a text-only turn then ParseError; grounding guard: submit with zero successful data-tool calls → ParseError; pure _normalize_findings() caps at 3 + drops unevidenced). Each feature has its OWN isolated _call_*_model() network seam so tests stub independently; pure _normalize()/_match_id() re-resolve ownership reading rows by ATTRIBUTE (.id/.name — the quick-add account feeder dual-names account_id AS id / account_name AS name). ai.py NEVER touches the DB or sees a user id — tool dispatch is a callback the blueprint supplies
  models.py          # User class with UserMixin, get_by_id, get_by_username
  blueprints/        # all routes, one module per area, registered with NO url_prefix
    auth.py          # login, logout (POST-only — GET was CSRF-able; base.html renders a form button styled as a nav link), change_password (72-BYTE bcrypt cap; reached via its Profile-page link, no nav entry), profile + digest opt-in; login rate-limit + constant-bcrypt (no enumeration) + session.clear() on login; login_user(remember=True) — the installed-PWA case
    main.py          # index() at '/' IS the dashboard (endpoint name main.index preserved for the redirect call sites; due-runners fire on '/'); dashboard() = 302 stub → / carrying ?month; service_worker() serves /sw.js from the ROOT (a SW's scope is capped at its URL dir — /static/sw.js could never control '/'). Pure _advance_past()/upcoming_occurrences() walkers over compute_next_due (the digest's upcoming-week enumerator imports them lazily). Dashboard owns the day-of-week + year-over-year queries (yoy only when a month is filtered AND last year has data); chart payloads are PLAIN LISTS rendered with |tojson (NOT json.dumps + |safe — script-tag breakout); an income-by-category rollup feeds the Spending card's Expense/Income pill toggle (hidden when empty)
    transactions.py  # transaction CRUD (inline), CSV export (_csv_safe() formula-injection sanitizer + pure _export_kind() → the Kind column, transfer/adjustment/blank, #87 — rows are NOT filtered, the export mirrors History), render_history_tbody(), quick-add parse route; owns pure compute_next_due() + the shared validate_category_account() write-side ownership guard (both reused by schedules.py); Auto-Categorize (count_uncategorized/_load_cleanup_candidates — expense-scoped — + POST /transactions/cleanup/scan|apply, the History banner); Bulk edit (POST /transactions/bulk/category|delete — full guard stack + is_transfer=false so a transfer pair can't be half-edited; transfer rows get no checkbox in the UI either; selection is page-scoped, vanilla-JS bar in history.html); _load_history seeds the running-balance walk with the signed SUM of all filtered rows OLDER than the page slice (pages connect; filtered views carry the matching rows' net); Pending (#86 — is_pending on create, POST /transactions/<id>/mark-posted to clear, pin sorted in PYTHON after the fetch — see the gotcha)
    categories.py    # category CRUD (inline); kind ('expense'|'income') on create/edit — flipping a BUDGETED category to income clears its budget in the same txn (logged via budgets.record_budget_change, lazy import)
    accounts.py      # account CRUD (inline); ACCOUNT_ROW_SQL (THE canonical balance formula, reused by ask.py — namedtuple read by NAME everywhere, column order doesn't matter); pure Jinja globals credit_utilization() (warn ≥30% / danger ≥80%, pct uncapped / bar capped 100, Decimal→float) + monthly_interest(balance, apr) (None when apr unusable OR debt ≤ 0 — THE gate every surface checks; ~monthly = debt × apr/100/12, "~" wording); _parse_credit_limit()/_parse_apr() (blank → NULL; apr REJECTS > 100 — the units-typo guard); credit_card_utilization_facts(user_id) (per-card facts for Insight/Digest — a card qualifies with usable utilization OR interest, per-key presence); Balance check-in (GET/POST /accounts/<id>/checkin — recomputes balance server-side in a FOR UPDATE-locked txn, inserts ONE is_adjustment transaction closing the gap, always stamps last_checked_in); edit error paths re-render via account._replace(...) echoing the RAW posted string
    budgets.py       # budget cockpit (set/clear) + compute_budget_suggestions/_vs_actual helpers; AI Budget Review (compute_budget_review_facts() + load_budget_rows() + POST /budgets/review/scan|apply); Budget report (pure build_budget_report() hit/miss grid + streaks + ±10% 3-vs-3 trend, load_budget_report() calendar-aligned last-6-COMPLETE-months — NOT the review facts' rolling window; only SAVED budgets grade, vs the CURRENT amount); record_budget_change() appends to budget_history at all THREE write points (set/clear/review-apply), before the upsert/delete in the same txn, no-ops skipped — writer only, nothing reads the log yet
    analytics.py     # redirect stub: GET /analytics → 302 / carrying ?month=. No @login_required on the hop — the target enforces auth
    admin.py         # user mgmt, create user, backup, settings; pure integration_status() + scheduler_enabled() (#139) drive the admin-only Integrations table on /settings — three states from a per-variable length floor in INTEGRATIONS, never the value
    transfers.py     # account transfers (linked income/expense pair) + recurring transfers (transfer_schedules CRUD + run_due_transfers() materializing a paired transfer per due date)
    goals.py         # goals (save + payoff) + compute_goal_projection(..., apr=None) — payoff goals feed the linked card's apr (GOAL_SELECT carries a.apr AS account_apr; _goal_view reads rows by ATTRIBUTE): est_monthly_interest always in the dict (None unless apr + debt), pace date = bounded 600-month simulation (pace ≤ interest → None + Behind), required_per_month amortized; Goal Coach (compute_goal_coach_facts() + load_goal_coach() + POST /goals/coach/generate, cached in goal_coach); GET /goals gates the card on ai_enabled() + in-progress goals
    push.py          # POST /push/subscribe|unsubscribe (#33) — one row per DEVICE, endpoint is the identity (globally UNIQUE, so re-subscribing upserts); validates the posted JSON, scopes every write to current_user. Profile UI gated on push_enabled()
    feedback.py      # POST /feedback (#64) — in-app bug/feature reports filed as GitHub issues via github.py. ⚠️ The repo is PUBLIC and the body carries ONLY what the user typed: no username, no account names, no balances, no request context (settled 2026-07-30). Do NOT 'improve triage' by attaching context — that is the declined feature, not an oversight. Kind resolved against a fixed allowlist (a form value must never become a label); 5/hour rate limit; form on Profile, gated on feedback_enabled()
    announce.py      # the release broadcast (#115). broadcast_release(version) pushes to EVERY row of push_subscriptions — the ONE push path deliberately NOT scoped to a user, which is why it can't live in pusher.py (no DB there), same reason reminders.py exists. Gated on push_enabled() ALONE; per-device try/except; PushGone deletes the row, a transient PushError KEEPS it. `flask announce-release --version X` CLI, run by release.yml AFTER the /healthz verify and skipped for a pre-release. ⚠️ build_release_notification(version) takes NO notes argument (#131) — the body is a FIXED line, see the gotcha. No idempotency marker: `release: published` fires once by construction (a workflow re-run is the only double-send risk, accepted)
    reminders.py     # the DAILY job (#33). run_daily_tasks() = materialize_all_users() THEN send_due_reminders() THEN send_posted_bill_alerts() (#191), returning a 3-tuple; `flask run-daily` CLI. Both push passes share the _push_pass() spine (claim-before-send, per-user isolation, _deliver() pruning dead devices); _posted_variable_bills() reads the LEDGER via transactions.schedule_id — see the gotcha. Materialization is UNGATED (see the scheduler gotcha); reminders gate on push_enabled(), enumerate tomorrow via main.upcoming_occurrences (so #32's end_date is honoured), and claim each occurrence in reminder_log with ON CONFLICT DO NOTHING BEFORE sending — a failed send is deliberately NOT retried (a duplicate notification is worse than a missed one)
    schedules.py     # recurring income/expense schedules (Scheduled tab); run_due_schedules() + compute_initial_semimonthly_due()
    insights.py      # monthly AI digest card on the dashboard; compute_month_facts() (deterministic figures) + POST /insights/generate (narrate via ai.py, cache in insights table)
    forecasts.py     # month-ahead AI projection card (twin of insights.py); pure project_expenses() day-weighted run-rate + compute_forecast() (MTD actuals + remaining schedules) + POST /forecasts/generate
    ask.py           # "Ask your finances" tool-use box. The SECURITY BOUNDARY: 9 read-only, user-scoped, fixed-parameterized query tools (ASK_TOOLS, incl. recent_transactions — date-range listing, no text filter, carries is_transfer/is_adjustment flags); dispatch() validates every arg (strptime months/dates, clamped limit, category resolved only against the user's own rows) and FORCES user_id to the caller; POST /ask runs ai.answer_question() with a dispatch closure; the Money agent reuses TOOL_SPECS + dispatch verbatim. total_for_category sums by the category's kind (income-kind → total_received); list_categories returns {name, kind}. No cache (live query)
    digests.py       # Weekly Email Digest. compute_digest_facts() (reuses compute_month_facts + a next-7-days enumeration via main.upcoming_occurrences — EVERY occurrence in the window counts, a weekly bill due twice counts twice) → ai.generate_digest() narrates once → mailer.send_email(); send_weekly_digests() runner (idempotent by users.last_digest_sent_on, per-user try/except) + `flask send-digests` CLI; also runs the Money agent per recipient (reuses the week's cached run, own try/except — an agent failure never blocks the email)
    agent.py         # the Money agent (first AUTONOMOUS tool-use). run_money_agent(user_id) binds ask.dispatch into ai.investigate_finances (scheduler-thread safe, no current_user) and upserts agent_runs on (user_id, week-Sunday); load_agent_run() (latest, or a specific week for the digest so it never mails stale findings); POST /agent/run (3/min) drives the dashboard card, ParseError → previous card + error toast
  templates/         # Jinja2 HTML templates, all extend base.html
    partials/        # HTMX fragments (_X_row.html, _X_edit_row.html, _transactions_tbody.html, cards)
    emails/          # weekly_digest.html (rendered server-side, sent via Resend)
  static/            # style.css, htmx.min.js, chart.umd.min.js (Chart.js 4.5.1 pinned/vendored), manifest.json + sw.js + icons/ (PWA — SW is stale-while-revalidate on /static/ GETs ONLY, everything else passes through; bump the 'bb-static-vN' cache name in sw.js to purge — currently v3). sw.js also carries the #33 'push' + 'notificationclick' handlers. icons/icon.svg = the coin-with-$ brand mark (favicon + sidebar via brand_svg); icons/icon-maskable.svg = full-bleed BUILD SOURCE only (not served) — maskable-512 AND apple-touch-icon rasterize from it (apple-touch must be full-bleed: iOS composites WHITE behind transparent corners); rasters regenerated via macOS qlmanage
sql/                 # Numbered migration files + schema.sql (clean single-file schema)
scripts/             # ingest.py, clean.py, insert.py data pipeline (own requirements.txt — pandas lives THERE, not in the app image); migrate.py; seed_dev.py (#69 — synthetic dev dataset; PURE build_seed_plan() + thin write_plan(), standalone like migrate.py, never imports the app); release_prep.py (#154 — the MECHANICAL half of a release: rolls CHANGELOG's [Unreleased] under a dated heading, repairs the whole link-ref block, moves the What's-new strip's version/date, and reports env vars added to .env.example since the last tag. Pure roll_changelog()/rewrite_strip()/new_env_vars() + a thin main(); ONE subprocess seam _read_env_example_at() so tests need no git. It does NOT choose the version or write prose, and never touches a .whatsnew-block. Three-state env report — None means "could not tell" and must never render as "nothing new"); restore_check.py (#153 — restores a dump into its OWN throwaway postgres container, counts every table, exits non-zero naming the file it rejected. Pure newest_dump()/parse_owner_role()/referenced_roles()/role_statements()/evaluate() + `_`-seams. ⚠️ Runs on the machine holding the dumps, NOT in the app container — stdlib only, no Flask, no psycopg2. ⚠️ Reaches its container over `docker exec` ALONE — no published port, no --network — which is what makes "cannot touch production or dev" structural rather than intended. The dump path is a REQUIRED argument with no default: this repo is public and the backup location is maintainer-local)
landing/             # Static landing page at seandesmet.com
.github/workflows/   # ci.yml (a `changes` job classifies the diff → app/image/sql flags; jobs ALWAYS RUN and gate their expensive STEPS on them — `paths-ignore` on a required check strands the PR forever; fails open at BOTH levels, incl. `if: ${{ !cancelled() }}` + `needs.changes.result != 'success'` so a classifier failure runs everything. lint + pytest on postgres:16 + image builds/boots as appuser + the suite re-run INSIDE the built image when Dockerfile/requirements change); release.yml (published Release → build+push ghcr → smoke the PUSHED image → approval gate → SSH deploy → verify /healthz); rollback.yml (workflow_dispatch a version → redeploy that exact tag); changelog.yml (app changes must touch CHANGELOG.md unless labelled `skip-changelog`); claude-triage.yml (automated first-pass comment on a new issue — see the Automated issue triage section below)
```

## Database Tables

- `transactions` — amount, description, transaction_date, category_id, account_id, transaction_type (income/expense), is_adjustment (exclude from analytics), is_transfer + transfer_group_id (transfer legs), **is_pending (#86 — a DISPLAY flag, pins the row to the top of History; excludes it from NOTHING, the opposite of is_adjustment)**, **schedule_id (#191 — which schedule materialized this row; NULL for hand-entered rows and everything predating sql/35, `ON DELETE SET NULL` so deleting a schedule never deletes the money it posted)**, user_id, created_at. **`is_recurring`/`frequency`/`next_due`/`recur_second_day` are LEGACY** — recurrence moved to `schedules`; kept (always default) only so the History row shape is unchanged
- `schedules` — recurring income/expense templates: amount, description, category_id, account_id, transaction_type, frequency, anchor_day + second_day (semi-monthly), next_due, **`end_date` (NULL = runs indefinitely)**, **`is_variable_amount` (#191 — opt-in per bill; changes NOTHING about how it posts, only whether the daily job alerts once it HAS posted)**, is_active, user_id, created_at. **FINISHED ⇔ `end_date IS NOT NULL AND next_due > end_date`** — deliberately NOT `end_date < today` (a schedule ending the 15th whose next_due is the 1st still owes that occurrence on the 10th), and runner-independent, since next_due only moves forward and never past what was materialized. **Not a ledger row** — `run_due_schedules()` materializes a plain transaction on each due date (going forward, no back-fill) and advances `next_due`
- `transfer_schedules` — recurring **transfer** templates (the transfer twin of `schedules`): amount, description, **from_account_id + to_account_id** (no category), frequency, anchor_day + second_day, next_due, **`end_date` (same semantics as `schedules`)**, is_active, user_id, created_at. Separate table (a transfer needs two accounts). `run_due_transfers()` (transfers.py) materializes a **paired transfer** (linked expense+income legs sharing one `transfer_group_id`, both `is_transfer=true`) per due date, looping to catch up. Reuses `compute_next_due()`/`compute_initial_semimonthly_due()`, all six frequencies
- `insights` — cached monthly AI narration, one row per user per month: year, month, content (JSON `{summary, tips[]}`), model, user_id, created_at; **UNIQUE(user_id, year, month)** upsert. **Stores only the narrative** — figures are recomputed each load (`compute_month_facts()`), never persisted
- `forecasts` — cached month-ahead projection, **identical shape to `insights`** (separate table, not a `kind` column). Narrative only; figures recomputed by `compute_forecast()`
- `goal_coach` — cached goal-pace narration, identical shape to the twins, pointed at the Goals page. Monthly-keyed to reuse the load/upsert even though goals aren't month-scoped
- `agent_runs` — cached Money-agent weekly runs: user_id, **period_start (the week's Sunday, via `helpers.most_recent_sunday` — same boundary as `last_digest_sent_on`)**, content (JSON `{summary, findings:[{title, detail, evidence}], tools_used}`), model, created_at; **UNIQUE(user_id, period_start)** upsert. Stores only the narrative + cited evidence text — no figures are trusted from it
- `job_runs` — when each scheduled job last **finished** (#151): job_name (**UNIQUE**), last_run_at, summary (free text for a human — *"materialized 3 user(s), sent 1 reminder(s)"*; nothing parses it and nothing should start to). **ONE ROW PER JOB, UPSERTED** — not append-only: the panel asks "when did this last finish", which is one fact per job (`agent_runs` is the upsert precedent; `reminder_log` is append-only because there every occurrence is a distinct claim that must not be lost). ⚠️ **NO `user_id`, deliberately** — these jobs run for everyone at once, so "which user did the daily pass belong to" has no answer. With `push_subscriptions` (per DEVICE) it is one of only two non-user-keyed shapes; this one is per JOB. Written on completion, **never on dispatch**
- `push_subscriptions` — one row per **DEVICE** (#33): user_id, `endpoint` (**globally UNIQUE** — it is the push service's URL for that browser install, so re-subscribing upserts and a different user subscribing on the same browser MOVES the row to them, which is correct), p256dh, auth, created_at. A user may have several
- `reminder_log` — the reminder idempotency marker (#33): user_id, `source` ('schedule' | 'transfer' | **'posted'** — the last is #191's claim, where `source_id` is a TRANSACTION id), `source_id`, `occurrence_date`, sent_at; **UNIQUE(user_id, source, source_id, occurrence_date)**. Keyed per **OCCURRENCE**, not a per-day column on `users` — a date marker only holds while the lead time is exactly 1 day; widen the window and the same bill re-notifies daily. The row IS the lock (claimed with `ON CONFLICT DO NOTHING`), so it survives the container restart a deploy causes. `source_id` addresses two tables, so it is deliberately **not** an FK — orphaned markers are inert
- `categories` — id, name, description, **kind ('expense' | 'income', default 'expense')**, user_id. Kind drives which category LISTS a surface offers (cockpit/review/Auto-Categorize = expense-kind only; forms group both kinds in optgroups; quick-add parse sees all) and Ask's `total_for_category` sums by it — transaction rollups still filter on the transaction's own `transaction_type`
- `budgets` — id, category_id, amount (one **monthly** amount per category — overrides only; no row = fall back to the suggested average), user_id, created_at; UNIQUE(user_id, category_id)
- `budget_history` — **append-only** log of budget changes: category_id (FK **CASCADE**, not RESTRICT), amount (**NULL = cleared**), changed_at, user_id. Written by `record_budget_change()` at set/clear/review-apply. **Nothing reads it yet** — it exists because history can't be backfilled; a future budget-report upgrade grades past months against the amount in effect then
- `account` — account_id (not `id`), account_name, type, user_id, `last_checked_in` date (NULL = never reconciled), `credit_limit` numeric NULL, `apr` numeric(5,2) NULL (both: NULL = not set, stored on any type so they survive a type flip, but only READ when type = 'Credit Card'; utilization/available/~monthly interest all derived at read time, never stored; app caps apr input at 100) ← named singular, PK is account_id
- `goals` — id, name, target_amount, target_date, account_id (linked), baseline_amount, **goal_type ('save' | 'payoff')**, user_id, created_at. A **payoff** goal snapshots at creation: baseline = the (negative) balance, target = the starting debt — so `compute_goal_projection()` is shared (saved = paid off, remaining = current actual debt, self-correcting); the type only drives wording/forms
- `users` — id, username, password_hash, is_admin, created_at, `email`, `weekly_digest` (opt-in, default false), `last_digest_sent_on` (digest idempotency marker)
- `transfer_group_seq` — sequence; a transfer is a linked expense+income pair sharing one `transfer_group_id`

## Key Gotchas

- The account table is named `account` (singular), PK is `account_id` not `id`
- Routes live in `app/blueprints/`, registered with **no `url_prefix`** — endpoint names are `blueprint.function` (e.g. `url_for('auth.login')`, `url_for('transactions.transactions')`)
- Templates build every app link with **`url_for('blueprint.function', ...)`**. The ONE exception: `emails/weekly_digest.html` keeps absolute prod URLs (email needs them; no SERVER_NAME configured). base.html's active-nav state string-matches `request.path` — that's fine
- All routes use `@login_required`; admin-only routes additionally check `current_user.is_admin` (inline `DELETE`/toggle admin routes `abort(403)` for non-admins)
- Edit/delete use REST-ish routes: `GET/POST /<entity>/<id>/edit`, `GET /<entity>/<id>/row`, `DELETE /<entity>/<id>`. Handlers guard ownership (`SELECT 1 ... AND user_id` → `abort(404)`). Guards run before the write `try`, and **write-path handlers catch `except psycopg2.Error`, not `Exception`** — an `abort()`'s HTTPException can structurally never be swallowed into a 200. FK-delete sites branch on `psycopg2.errors.ForeignKeyViolation`. Deliberately still-broad catches (commented in place): digests' per-user batch isolation, ask's tool-error isolation, ai.py's model seams, `db_cursor`'s rollback+reraise
- **HTMX inline CRUD:** lists render row/card partials from `templates/partials/`; Edit swaps one row in place, Save/Delete return the updated fragment + an `HX-Trigger` toast (flash doesn't render on a partial swap — use `hx_toast()`). The transactions **history re-renders the whole `<tbody>`** on Save/Delete (the running balance shifts every row) via `render_history_tbody()`, preserving month/search/page through a `filter_qs` query string
- **ALL app DB access goes through `db_cursor(commit=False)`** (`db.py`) — commits on clean exit, rolls back + re-raises on error, always closes. `get_db_connection()` remains only inside db.py and `tests/conftest.py` (deliberately). Write pattern: ownership guard in its own read `with`, then `try: with db_cursor(commit=True):` around only the writes
- **Rows are namedtuples:** `db_cursor()` sets `cursor_factory=NamedTupleCursor` — read `row.amount`; positional access still works. Consequences: every SELECT column needs a **unique, valid-identifier name** (alias expressions — two unaliased `COALESCE(...)` columns raise `duplicate field name` at fetch); hand-built error-path rows use `row._replace(...)` or the module-level namedtuples (`CategoryRow`, `TxnEditRow`, `HistoryRow`, `GoalEditRow`, `BudgetRow`); a typo'd attribute in Jinja renders as EMPTY STRING, not an error — content-asserting tests are the net. `tests/conftest.py` stays on plain tuples — do NOT put the factory there
- Budgets are the cockpit (`/budgets`): one monthly amount per category, `POST /budgets/set` (upsert) / `POST /budgets/clear`; no edit/delete-by-id routes. `compute_budget_suggestions()` (6-mo avg, whole-dollar) seeds the default; `compute_budget_vs_actual(user_id, year, month)` is month-based; both in budgets.py
- **Due-runners fire TWO ways (changed in `0.2.0`, #33):** the login-triggered path — GET `/` (dashboard), `/transactions`, `/scheduled` (schedules only), `/transfers` (transfers only), lazy-imported to avoid the schedules↔transactions import cycle, materializing for the CURRENT user only — **plus a daily server-side pass** (`reminders.run_daily_tasks` → `materialize_all_users()`) that runs both runners for EVERY user with an active schedule. Both **loop to catch up**, then advance `next_due` past today; new schedules seed `next_due` forward so setup never back-fills, and a schedule past its `end_date` materializes nothing (#32). The old "a user who never logs in gets nothing materialized" caveat is GONE — that was the gap #33 closed. Recurring is configured ONLY on the Scheduled tab — Add Transaction is one-off entries only
- **Due-runner locking is now LOAD-BEARING, not merely prudent:** the due-row SELECTs are `FOR UPDATE`. They already guarded two simultaneous page loads (gunicorn serves on 4 threads); since `0.2.0` the **scheduler thread races those page loads too**. Keep the lock if those queries are ever touched — `test_push_reminders.py::test_daily_job_racing_a_page_load_materializes_once` is the net
- **⚠️ The scheduler is NOT gated on `mail_enabled()`** (`app/__init__.py`). It was until `0.2.0`, which was fine while its only job was email — but the daily job now also materializes, and hanging that off a Resend key would mean a missing third-party credential silently stops the ledger updating. `ENABLE_DIGEST_SCHEDULER=1` starts the scheduler; **each JOB carries its own gate** (digest ← `mail_enabled()`, reminder half of the daily job ← `push_enabled()`, materialization ← nothing). Do not "tidy" this back into one condition
- ⚠️ **"The scheduler is enabled" and "the job ran" are DIFFERENT QUESTIONS** (#151, PR #156). `admin.scheduler_enabled()` reads an env var at request time — it answers *was the switch set when this process started*, not *is the job running*. Those were the same question while the scheduler's only job was the digest (a missed digest is self-evident: no email arrives). They stopped being the same in `0.2.0`, because the daily job now also runs `materialize_all_users()`, which is **gated on nothing** and is what turns a schedule into a real transaction row. The failure it makes visible: the thread dies, `/settings` still reports whatever the env var says, `/healthz` stays green (the database is reachable), and recurring transactions silently stop appearing — indistinguishable from "nothing was due", noticed weeks later via wrong balances. `app/jobs.py` answers the second question from `job_runs`; **do not collapse the two back into one indicator**. ⚠️ A job that is switched off on this server reports `NOT_SCHEDULED`, deliberately **not** a fault — a panel that cried wolf about a legitimate state would be worth ignoring
- All data tables have `user_id` FK — every SELECT/INSERT/UPDATE/DELETE must be scoped to `current_user.id`
- ⚠️ **The History pending-pin is sorted in PYTHON, never in SQL** (#86). `_load_history`
  seeds its balance walk from a SUM of every filtered row *older* than the page — and the
  seed query **defines "older" by repeating the page query's `ORDER BY`** with
  `OFFSET offset+per_page`. So those two `ORDER BY` clauses are ONE coupled unit: an
  `is_pending DESC` prefix on either does not merely reorder the display, it redefines
  which rows count as older, and the balance breaks for the pinned rows AND every row
  beneath them — invisible until pagination or a filter is active. Both queries and the
  walk are byte-identical to pre-#86; only the finished list is sorted, relying on
  `list.sort` being **stable** to keep both groups date-descending. Consequence accepted
  deliberately: **the pin is page-scoped** (a pending row 100 rows deep pins to the top of
  page 4). `test_pending_transactions.py::test_posted_balances_are_unchanged_by_a_pending_row`
  is the net
- **`is_pending` is a DISPLAY flag and the exact OPPOSITE of `is_adjustment`** despite the
  identical type/default: it excludes a row from **nothing** (dashboard, budgets, insights,
  forecasts, running balance all count it — the money did leave the account). Do **not** add
  it to the ~23 `is_adjustment = false AND is_transfer = false` filter lists. Set by a
  checkbox on the **create form only**; cleared by `POST /transactions/<id>/mark-posted`
  (clears only, never sets). `edit_transaction`'s UPDATE deliberately never mentions the
  column, which is what makes "editing the amount doesn't clear the flag" free and keeps
  `TxnEditRow` unchanged. Pending rows render an em dash in the balance cell
- **Chart series colours live in `style.css` as `--series-1..8`**, read via `cssVar()` so dark
  mode swaps with no JS; **dark has its own steps** (three light values fail 3:1 on the dark
  card). ⚠️ **The slot ORDER is load-bearing** — adjacent slots are the pairs a reader
  compares, and reordering breaks CVD validation (verified: red beside magenta, violet beside
  blue both fail)
- **A drawn slice's slot comes from `main.assign_series_slots()`** (#111), computed
  server-side **per view** and carried on each payload row as `slot`. Creation order is the
  PREFERENCE (earliest-created wins a contested slot); anything displaced takes the lowest
  free one, so **no two slices on screen ever share a hue**. ⚠️ There is deliberately NO
  shared name→slot map any more — the expense and income views are assigned independently
  because their union can exceed the palette while neither view alone does
- ⚠️ **This reverses #83's "colour is a pure function of the category, never of the set
  drawn".** That rule is incompatible with "no duplicates" above 8 categories; `% 8` wrapping
  shipped the exact collision #83 existed to prevent (prod, `0.3.0`). It is only safe to
  reverse **because the #108 fold caps the chart at 6 real slices against 8 hues**, so
  probing for a free slot always succeeds. Do not restore `% PALETTE.length` colouring
- **The doughnut folds to top-6 + "Other"** (#108/#110) via pure `main.fold_chart_tail()`, in the
  **payload builder, NOT the SQL rollup** — every other surface needs complete per-category
  figures, and the card total comes from `cash_flow` so the fold can't move it. Applied to BOTH
  pill views (one canvas, one palette, one slot map). ⚠️ **The folded slice is coloured off its
  `is_other` FLAG, never its label** — a user may own a real category *named* "Other", and
  label-matching would grey out the real one and steal its slot. Its `--series-other` token is
  achromatic and deliberately **not** `--series-9`: the stylesheet test counts `--series-<digit>`
  to catch duplicate hex, so don't loosen that regex to `[\w-]+`. ⚠️ The fold does not fix the past-8 wrap
  on its own (#108 wrongly claimed it did) — but it is the precondition that made the real fix
  possible, see the slot gotcha above
- ⚠️ **Sonnet 5 thinks BY DEFAULT, and `max_tokens` bounds thinking + output TOGETHER**
  (#140, PR #142). `ai.py` never passes `thinking`, and on `claude-sonnet-4-6` that meant
  *no* thinking; on `claude-sonnet-5` the same omission runs adaptive thinking. So the
  `max_tokens=4096` and the explicit `output_config={"effort": ...}` at
  `_call_categorize_model` and `_call_agent_model` are **load-bearing, not slack** —
  dropping either reintroduces silent truncation into the `ParseError` fallback rather
  than a loud failure. Its tokenizer also counts ~30% more per unit of text, shrinking
  the same headroom again. Effort is explicit because Sonnet 5 **defaults to `high`**.
  ⚠️ **Thinking is deliberately left ON** — with it disabled Sonnet 5 reaches for tools
  *less*, and the agent's grounding guard turns a run with no successful data-tool call
  into a `ParseError`. `tests/test_model_constants.py` pins all of this.
  ⚠️ **`messages.parse()` merges `output_format` INTO `output_config`** as its `"format"`
  key (verified in the SDK source), so passing both is supported, not a clash.
  ⚠️ The issue's claim that intro pricing makes Sonnet 5 "also the cheaper one" is **not
  reliably true**: thinking tokens bill as *output* and the tokenizer counts ~30% more,
  so $2/$10 can land above 4.6's $3/$15. Justify on capability; measure cost.
- ⚠️ **The integration-status panel judges by LENGTH, never by prefix** (#139, PR #143).
  `admin.integration_status()` reports three states per env-gated feature — `unset`,
  `configured`, `implausible` — from a per-variable minimum length in `INTEGRATIONS`.
  A prefix check would have **passed** the real incident (`github_pat_YOURTOKEN` starts
  with `github_pat_`); length is what discriminates it, 22 chars against a real PAT's
  ~93. The **third state is the point** — `unset` is a forgotten step, `implausible` is a
  bad paste that renders a feature which accepts input and fails every submission. Do not
  collapse it to a boolean. **Admin-only, never `/healthz`, and never the value** — not a
  prefix, not a mask; a test asserts the value, three prefix lengths of it, *and the
  variable names* are all absent from the response. `ENABLE_DIGEST_SCHEDULER` is
  deliberately its own line, not a fifth row: it is not a credential and its jobs each
  carry their own gate.
- ⚠️ **`${TAG}` has NO DEFAULT, and the deploy pins it into the Droplet's `.env`** (#190, PR #193).
  Two halves, both load-bearing: `docker-compose.yml` reads `${TAG:?…}` so a command naming no
  version **fails naming the variable** instead of choosing an image, and `release.yml` +
  `rollback.yml` rewrite one `TAG=<version>` line into `/opt/budget-buddy/.env` so a bare
  `up -d` reproduces the running deployment (`grep '^TAG=' .env` reads it off the host). Pinning
  alone was rejected: a hand-restored `.env` re-arms the trap silently. ⚠️ **Compose interpolates
  every file BEFORE merging**, so the mandatory variable fires **locally too**, even though
  `docker-compose.override.yml` replaces the image with a source build — hence a `TAG=` line in
  `.env.example` and in the local `.env`, where the value is meaningless but the line must exist.
  Check states with `docker compose config`, a **pure parse that needs no daemon**. In the deploy
  the pin goes **first** (the pre-deploy `pg_dump` is itself a bare `docker compose exec`) and is
  `chmod 600`'d **before** the redirect. `tests/test_deploy_pinning.py` states the property — any
  `:-` default at all fails it.
- ⚠️ **Historical (the reason the default is gone): a bare `docker compose up -d` reverted prod**
  (#190, observed 2026-08-10). Compose resolves the app image as `${TAG:-latest}`, and the
  Droplet's local `latest` is **stale by construction**: deploys pull the exact version tag and
  never `latest`, so nothing refreshes it. A bare `up -d` while applying the log limits moved
  production from `:0.6.0` to the **`0.3.1`** image — three releases and two migrations back —
  and gave **no signal**: container `healthy`, `/healthz` 200, app working (old code against an
  additively-migrated schema runs fine), uptime check green. Caught only by comparing image IDs.
  Automated deploys are unaffected (`release.yml`/`rollback.yml` always pass `TAG`); every
  *hand-run* command is exposed, which is exactly what a maintenance procedure is. **Verify the
  running CODE, not the tag** — `docker compose exec -T web python -c "import app.jobs"` proves
  0.6.0+. The local `latest` was deliberately NOT retagged: that hides the symptom and leaves the
  mechanism. See `RUNBOOK.md` §5.
- ⚠️ **`docker compose exec -T` reads STDIN and will eat the rest of your script.** Inside a
  heredoc piped to `ssh … bash -s`, it consumes the remaining commands and they silently do not
  run — output just stops, with no error. Every scripted `exec -T` needs `< /dev/null`.
- ⚠️ **Editing the `db` service in `docker-compose.yml` recreates the database container** on the
  next `docker compose up -d` — the one sanctioned exception to `pull web`, and never a side
  effect of a release. It needs a window and a dump verified by `restore_check.py`. **Compose's
  output does not tell you whether it happened**: it printed only `Starting`/`Started` for a
  container it had replaced. Compare `docker compose ps -q db` before and after, and run `up -d`
  a *second* time to confirm the ID is then unchanged (that is what proves future releases are
  safe). Verified both locally and in prod on 2026-08-10; the named volume re-attaches and data
  survives. Both services carry `json-file` at `max-size 10m` × `max-file 3`.
- ⚠️ **A `pg_dump` from this database needs TWO roles to already exist** (#153): `OWNER TO admin`
  *and* ~33 `GRANT ... TO budget_app` lines, and it creates neither. Restoring with only the owner
  fails at the grants — which are the **LAST** thing in a dump, so every row is already loaded
  when it happens: the tables look complete and the restore has **not** succeeded. `PUBLIC` is a
  pseudo-role and must never be created. ⚠️ Do not read a piped `psql`'s success from `$?` — that
  is the pipe's last command; this is exactly how the first rehearsal was misreported as passing.
- ⚠️ **The variable-bill alert reads the LEDGER, never the schedules** (#191, PR #195).
  `reminders.send_posted_bill_alerts()` asks *"which transactions did a variable-amount schedule
  post recently"* via `transactions.schedule_id`, not *"which schedules were due today"*. The
  latter is the obvious build and it is **wrong**: `run_due_schedules()` fires on three page-load
  paths as well as the 18:00 job, so opening the app in the morning posts the bill and advances
  `next_due`, leaving the evening job nothing to see — the alert would go silent on exactly the
  days the user was engaged. `test_a_bill_posted_by_a_page_load_is_still_alerted` is the net and
  asserts the intermediate state (`next_due > today`). Three coupled decisions: **`schedule_id` is
  stamped on EVERY materialized row**, not just variable ones (so ticking the flag later can still
  see what posted); the claim is `reminder_log.source='posted'` with a **transaction** id, never
  `'schedule'` (already held by the due-tomorrow reminder for the same occurrence); and the
  3-day lookback is safe only because the claim is per row. `is_pending` was considered as the
  in-app half and **rejected** — do not widen it. The payload links `/transactions`, deliberately
  not `/transactions/<id>/edit` (an HTMX `<tr>` fragment is not a page a notification can open).
  Both push passes share the `_push_pass()` spine: claim-before-send, per-user isolation and dead
  device pruning must not drift between them.
- **Amount validation:** every form amount goes through `helpers.parse_positive_amount()` — `float('nan')` passes a plain `<= 0` check and Postgres stores NaN in numeric, poisoning every SUM(). Never hand-roll `float(x); if x <= 0`
- **Param validation:** same rule for query/form params — `?month` → `parse_month_param()`, `?page` → `parse_page_param()`, posted FK ids → `parse_int_param()`. A raw string into a psycopg2 `%s` against an int column raises (= 500)
- **Write-side FK ownership:** when a form posts a `category_id`/`account_id`, validate it belongs to the user *before* the INSERT/UPDATE — `validate_category_account()` in transactions.py, folded into the route's validation-error path. Used by transaction new/edit, schedule create/edit, bulk edit, cleanup apply
- **Error messages:** unexpected write failures show `helpers.GENERIC_ERROR` and log the real exception via `current_app.logger.exception()` — never flash/toast `str(e)` (psycopg2 text leaks constraint names/SQL). The two FK-delete sites keep their friendly "Cannot delete — in use" branch
- ⚠️ **The release notification title deliberately does NOT name the app** —
  `Version {version} is live`, not `Budget Buddy {version} is live` (#133, PR #135).
  Chrome renders its **own** attribution line under the title — "from Budget Buddy" on
  an installed PWA, sourced from `manifest.json`'s `name` — which nothing in
  `announce.py`/`sw.js` emits and `showNotification()` has no option to suppress. Naming
  the app in our title too is what printed the phrase twice on the lock screen. ⚠️ Read
  the title and that attribution line **together**: the title alone looks incomplete, and
  on **desktop** the attribution is the origin rather than the app name, so it genuinely
  is thinner there — an accepted trade (Sean, 2026-08-03), not an oversight.
  `test_title_does_not_name_the_app` states the constraint rather than the string, since
  every other assertion would read a regression as a mere wording change. Renaming
  `manifest.json` is NOT the lever — that string is the home-screen icon label.
- ⚠️ **The release notification body is a FIXED line** — `announce.BODY`, "Check out
  what's new in the app!" — and `build_release_notification()` takes **only a version**
  (#131, reversing #115's settled "the text comes from the Release notes body", before
  it ever deployed). This is not merely wording: carrying human-authored text is what
  required a markdown flattener, a word-boundary truncator, an empty-notes fallback, a
  4KB payload ceiling to reason about, **and a base64 hop in `release.yml`** — the
  release body is free text and the deploy step builds its remote command by string
  interpolation, so a backtick or `$(...)` in a release note would execute on the
  Droplet. With nothing to carry, that surface is **deleted rather than guarded**; the
  only thing interpolated now is the version, a tag the workflow derived itself.
  `test_the_builder_takes_no_release_text` asserts the signature so reintroducing the
  argument is loud. **If you ever add human-authored text back, the base64 guard must
  come back with it.**
- ⚠️ **One push switch covers TWO things, and `profile.html` is the consent record.**
  The same per-device subscription sends bill reminders *and* release notifications, so
  the Profile copy names both. A third kind of notification must be added to that
  paragraph **in the same change that starts sending it** — shipping the send first
  widens an existing consent silently.
  `test_profile_copy_names_every_kind_of_notification` is the net (Jinja fails silent), and it asserts each kind SEPARATELY — one "does it mention notifications" check would pass while a whole category went undisclosed. Three kinds today: due-tomorrow reminder (#33), posted variable bill (#191), release note (#115).
- **Cache-bust is automatic:** the stylesheet `?v=` is `css_v` (startup md5 of style.css) — nobody bumps a number. base.html AND login.html (which doesn't extend base) both read it. ⚠️ **Local dev consequence:** the source is now bind-mounted, and `css_v` is computed ONCE at import — so editing `style.css` changes nothing until `docker compose restart web` (~2s). Python and template edits ARE live. A CSS change that "does nothing" is this, not a broken mount
- **Local dev runs bind-mounted with live reload** (`docker-compose.override.yml`, local-only — the Droplet has no override, so production gets none of it): `.:/app` over the image's `COPY`, gunicorn `--reload`, and `TEMPLATES_AUTO_RELOAD=1`. **Both reload mechanisms are needed** — `--reload` watches Python modules only, so without the env var (read in `app/__init__.py`) an edited template reaches the container and is silently ignored, which looks exactly like the mount failing. Anonymous volumes mask `/app/.venv`, `/app/.ruff_cache` and `/app/.pytest_cache`: a bind mount ignores `.dockerignore`, and `.venv` holds macOS-native wheels that are wrong for Linux
- ⚠️ **THE DEV CONTAINER AND THE SHIPPED IMAGE HOLD DIFFERENT FILES, and the bind mount hides
  it** (#176, PR #177, 2026-08-10). `.dockerignore` excludes `*.md`, so `CHANGELOG.md`,
  `README.md` and `RUNBOOK.md` are **genuinely absent from the built image** — while the
  `.:/app` mount above puts them back in the dev container. So a test that reads a repo file by
  path passes locally forever and fails only when CI runs the suite **inside the image**. Two
  masks, stacked: **(1)** `./test.sh` structurally cannot catch it, and **(2)** the in-image
  suite only runs when `Dockerfile`/`requirements*.txt` change, so the PR that introduces the
  break usually is not the PR that goes red — the next dependency bump is. That is the
  documented skipped-vs-passed trap **pointed the other way**, and the tell is the job's
  DURATION (~33s = boot check only, ~2m10s = it genuinely ran pytest). To check the real
  artifact, build and run with **no compose and no mount**:
  `docker build -q --target dev -t probe . && docker run --rm -e SECRET_KEY=x -e DB_HOST=db -e DB_NAME=budget -e DB_USER=admin -e DB_PASSWORD=x probe sh -c 'python -m pytest tests/ -q -n0'`
  (`SECRET_KEY` is required or `app/__init__.py` raises at import). A test that must read such
  a file **skips** when it is absent, naming `.dockerignore` — failing would assert the image is
  wrong when it is right
- **`.venv/` is EDITOR-ONLY — it exists for the language server, and nothing is ever run from it** (2026-08-17: an editor is back in the loop, so it is wanted again rather than optional). It lets the editor resolve `flask`/`psycopg2`/`pytest`; the app and tests stay in containers. Create it **on the machine the language server runs on** — with Remote-SSH that is the **dev VM, not the Mac**: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt`. It needs a Python new enough to parse `app/ai.py`'s `str | None` annotations, or the language server reports working code as broken — on Ubuntu 24.04 the system 3.12 is already fine (on macOS it would mean installing 3.14 via Homebrew, since the system Python is 3.9). Matching prod's 3.14 exactly is optional polish. Gitignored (both `venv/` and `.venv/`) and in `.dockerignore`; **no `.vscode/` config is committed** and none ever was. ⚠️ **A root-owned EMPTY `.venv/` in the repo root is not a virtualenv** — `docker-compose.override.yml` masks `/app/.venv` with an anonymous volume, and a bind mount makes Docker create the host directory to mount over. Deleting it does nothing; it reappears on the next `up`. That is the state on the dev VM as of 2026-08-17, where no venv has ever actually been created
- ⚠️ **On Linux, bind-mounted file ownership is LITERAL — `.env` at mode 600 crash-loops the app.** Docker Desktop translates UIDs across its file sharing on macOS, so a secrets file owned by your host user "just works" there. A Linux bind mount does no such thing: the host user is typically uid `1000` and the image's `appuser` is uid **`10001`**, so the container cannot read its own config and gunicorn dies at import with `PermissionError: [Errno 13] Permission denied: '/app/.env'`. The obvious fix — `chmod 644` — makes a file holding `ANTHROPIC_API_KEY` world-readable. Grant exactly the one uid instead: `setfacl -m u:10001:r .env` (needs the `acl` package; check with `getfacl -p .env`). ⚠️ This applies to **any** new secret file mounted into a container, not just `.env`
- ⚠️ **There is deliberately NO dev container** (#80, removed 2026-07-28). One was added in #76 and removed two PRs later: it shipped broken twice, needed `git`/`procps`/`curl` in the image's `dev` stage purely for the editor, and split the workflow because it has no Docker inside it. The `.venv` fixes the editor on its own with no maintenance. **Do not re-add one** without a reason that the venv does not already cover. ⚠️ **The trap this bullet carries is LIVE again as of 2026-08-17** (it was briefly moot while there was no editor at all): VS Code resolves **workspace** settings above **remote** ones, which is what bit #78 — a setting you believe you set on the remote can be silently overridden by one committed or cached in the workspace. Check which scope a setting actually resolved in before concluding the remote is misconfigured
- ⚠️ **The dev front end is VS Code on the Mac, driving the isolated Linux VM over Remote-SSH**
  (2026-08-17, replacing the Ghostty + tmux arrangement that ran 2026-08-14 → 17). Development
  still happens **in the VM** — the Mac is a thin client and the repo, the containers and the
  agents all live on the far side of the SSH link. What changed is only the front end. ⚠️ **This
  re-arms one isolation risk**: VS Code's `remote.SSH.enableAgentForwarding` **defaults to
  `true`**, which would forward the Mac's SSH agent into the VM — and that agent holds the key
  that opens production, which the VM is deliberately not allowed to reach. Set it to `false`.
  See `CLAUDE.local.md` for the isolation rule it protects.
- ⚠️ **NOTHING SURVIVES A DROPPED CONNECTION any more** (2026-08-17). tmux was retired along with
  the terminal front end, and it was the only thing making the VM's running state outlive the
  link to it. A dropped SSH link, a closed lid, a sleeping Mac or a quit VS Code now **kills
  every process started from that session** — a running agent, a long `docker compose` command,
  an open `psql`. This is a deliberate trade (the workflow is simpler and there is one less
  machine-local dotfile to keep current), not an oversight, but it changes how long-running work
  should be started: **anything that must outlive the connection needs `nohup`/`setsid` or a
  systemd unit, chosen explicitly.** A VM reboot was always fatal to running state; the change is
  that an ordinary disconnect now is too.
- **Security headers + cookies** (`app/__init__.py`): one `@app.after_request` sets X-Frame-Options/X-Content-Type-Options/CSP `frame-ancestors 'none'`/Referrer-Policy (+ HSTS in prod). Cookies are `HttpOnly` + `SameSite=Lax`; **`Secure` + HSTS gated on `COOKIE_SECURE`** (Droplet-only) so local HTTP dev + tests still work. CSP is `frame-ancestors` only — a full policy would break the inline scripts
- Flask-Limiter: 60 req/min/IP, in-memory storage (single Gunicorn worker)
- Templates read rows by **attribute** (`t.amount`, `account.credit_limit`); row partials reuse the list query's row shape. The three remaining `[0]` indexes in templates are Python lists (flash messages, top_categories, remaining_items), not rows
- CSRF: Flask-WTF CSRFProtect — token on POST forms, plus a single `hx-headers='{"X-CSRFToken": ...}'` on `<body>` in base.html covering every HTMX post/put/delete
- **Flex overflow:** `.main-content` is a flex item and MUST keep `min-width: 0` — without it a wide table inflates the column past the viewport instead of scrolling inside its `.table-wrapper`. Same hotfix owns the iOS status-bar rules: `viewport-fit=cover` + `html` background + safe-area paddings (keep `theme-color` for Android; never `black-translucent`)
- **PWA/iOS testing:** responsive mode does NOT test iOS — installed-PWA bugs only show on the actual phone over HTTPS
- **AI-card collapse:** the four AI narration cards are `<details>` with `data-ai-key` + `data-generated` (the cache row's `created_at.isoformat()`; `initAiCollapse` in base.html + localStorage `bb-ai-seen:<key>` drive read-state). Generate routes must get the timestamp via `RETURNING created_at` — a route-local `datetime.today()` makes every regenerate read as new twice. Server renders CLOSED except empty-state (Generate must work without JS) and `just_generated` fragments. The What's-new strip says "weekly money check", NOT "Money agent" — a dashboard test asserts the agent CARD's absence by that exact string

## Current Status

### On `main`, NOT released — the `0.7.0` backlog, cleared (2026-08-13)

**Prod runs `0.6.0`; `main` is now genuinely ahead of it.** Three PRs merged, closing **#190** and
**#191**. Tests **875 → 906**. **One additive migration** (`sql/35_variable_bills.sql`). No new env
vars.

⚠️ **The `0.7.0` milestone is no longer empty — #203 was added to it on 2026-08-14** and now
**blocks closing it**. Recount with `gh issue list --milestone 0.7.0` rather than trusting any
figure here. #203 is a defect in `scripts/release_prep.py`: `roll_changelog()` appends one extra
trailing newline to `CHANGELOG.md` on **every** run, and it compounds (measured: 2 → 3 → 4 → 5 → 6
across four release cycles). Cosmetic, but it is in the tool used to cut the release it is blocking,
so fix it before running the prep rather than after.

⚠️ **There IS something to release.** `#191` is a user-facing feature and `## [Unreleased]` carries
an `### Added` and a `### Fixed`, so `0.7.0` is an honest MINOR. What a release still needs: the
**What's-new strip** (a human writes the prose; `scripts/release_prep.py` does the mechanical half)
and the usual gate. Not cut — Sean's call.

⚠️ **#190's fix is merged but NOT APPLIED TO PRODUCTION**, and merging cannot apply it: the
Droplet holds an `scp`-ed copy of `docker-compose.yml`. **Order is load-bearing** — the `.env`
line must land BEFORE the new compose file, or every compose command on the box fails in between:

```bash
cd /opt/budget-buddy && printf 'TAG=0.6.0\n' >> .env && grep '^TAG=' .env   # 1. pin first
scp docker-compose.yml <droplet>:/opt/budget-buddy/                          # 2. then the file
docker compose ps --format '{{.Service}} {{.Image}}'                         # 3a. still :0.6.0
mv .env .env.bak && docker compose config; mv .env.bak .env                  # 3b. MUST fail
```

Until 3b fails naming `TAG`, this fix is **unverified in production** — the deploy workflows pass
`TAG=` explicitly either way, so a green release proves nothing about it. ⚠️ This runs **from a Mac
terminal**, not from the dev VM — see `CLAUDE.local.md` for why the Droplet is unreachable there.

- **PR #194 — `sql/35_variable_bills.sql`**, standing alone. `schedules.is_variable_amount`,
  `transactions.schedule_id` (nullable, `ON DELETE SET NULL`, indexed), and `reminder_log.source`
  gaining `'posted'`; mirrored into `schema.sql`. Verified against a throwaway `postgres:16`: it
  applies to **main's** `schema.sql`, is re-runnable, and a migrated database is identical to a
  fresh one — 130 columns, 54 constraints, every index diffed. ⚠️ The first constraint diff was a
  **false green** (both sides errored on a `"char"` cast, so `diff` compared two empty files);
  print the line count next to the verdict.
- **PR #193 / #190 — the `${TAG}` trap is now enforced, not documented.** See the Key Gotchas.
  Both `release.yml` and `rollback.yml` pin the version into `.env`; `rollback.yml` needs it
  *more* (without it a rollback leaves the box naming the version it rolled away from, so the
  next bare `up -d` rolls production forward again). Also fixed: `RUNBOOK.md` §6's manual fallback
  carried a bare `docker compose pull`, the exact thing issue #22 exists to prevent.
- **PR #195 / #191 — a push alert when a variable-amount bill posts.** See the gotcha for why it
  reads the ledger rather than the schedules. `gotcha-auditor` on the branch: no violations.
- **PR #198 / #197 — `docker compose down -v` is no longer denied** in `.claude/settings.json`.
  Local development moved into an isolated Linux VM on 2026-08-13/14, so the dev database is
  reproducible (`seed_dev.py`, or a dump via `restore_check.py`) and the deny bought nothing.
  ⚠️ **The `git push --force` denies deliberately STAYED** — an isolated dev box bounds the
  filesystem and the credentials, not the remote, so a force-push still reaches GitHub. Do not
  "finish the job" by relaxing those too. `Read(./.env)` also stays; it is about keeping secrets
  out of transcripts, which isolation does not affect.

⚠️ **The operational backlog cleared on 2026-08-10** (five PRs, closing #160, #159, #153, #150;
tests 843 → 875; no migration) and every effective change was already in place, so **there was
nothing to release** at that point — the log limits were applied to the Droplet by hand,
`restore_check.py` runs from the maintainer's machine, `schema.sql` is inert on an existing
database, and the rest was CI and docs. That conclusion held only until #191 landed. The durable
lesson from that session: **every guard involved was already broken or vacuous, and all were
green** — the migrations job, the path filter that would have run it, a missing `ON_ERROR_STOP`,
a drift check that structurally could not see `sql/30`, and a documented restore procedure whose
"throwaway database" command targeted the *development* database. None of it was visible from a
dashboard; all of it was visible from running the thing and checking the exit code.

### Standing decisions — settled, do not re-open

- **Do not send a release notification for a release with no user-facing change** (Sean,
  2026-08-10). The only built-in lever is marking the GitHub Release as a pre-release —
  `release.yml` gates the announce step on `prerelease == false` — but that also suppresses the
  `:latest` update, so it is a misuse with side effects. Usually the right answer is that there
  is nothing to release.
- **#64's privacy design** (in-app feedback carries only what the user typed) — settled
  2026-07-30, do not re-open. Two deliberate deviations from the issue's own wording are worth
  keeping: the env var is **`FEEDBACK_GITHUB_TOKEN`**, not `GITHUB_TOKEN` (a magic name in
  Actions), and the HTTP call uses **stdlib `urllib`**, not `requests`.
- **#115's "the summary text comes from the Release notes body" was REVERSED by #131** (same day,
  before it ever deployed). Do not restore it from the issue's history — see the fixed-body
  gotcha for why the reversal *deletes* the injection surface rather than guarding it.
- **#133 is verified on a real device** (Sean, 2026-08-03). The "cannot be verified until the next
  release" caveat is DISCHARGED; do not re-raise it. It did **not** reopen #131.
- **#140 (Sonnet 5) stands on capability, not cost.** Intro pricing is not reliably cheaper once
  adaptive thinking (billed as output) and a ~30% tokenizer are in play. Do not re-file it as a
  cost win.
- **#111/#83 — the "colour is a pure function of the category" rule is deliberately reversed.**
  See the slot gotcha; it is safe only because the #108 fold caps the chart at 6 real slices.
- **#52 is closed as a one-off flake.** If it ever recurs, candidate fix **C** (pin buildx to the
  `docker` driver) still **does not work** — `type=gha` caching requires `docker-container`.
- **#37 (the unscheduled-backlog holding pen) was closed not-planned.** Its ideas and the standing
  rejection of *net worth over time* remain readable in the closed issue; **do not re-open it as a
  bucket.** Off the list for good: net worth over time (redundant with the net-balance-trend
  chart) and **CSV import**.
- **#33's design fork was decided YES** (Sean, 2026-07-28), against a recommendation to keep it
  read-only: the daily job also runs the due-runners server-side, for every user.
- **Three candidates checked and deliberately NOT filed** (2026-08-03): Flask-Login's 345
  deprecation warnings (**0.6.3 is the latest release**, so there is nothing to bump to); a second
  latent xdist race (schema checked — `push_subscriptions.endpoint` really is the only
  globally-unique non-user-scoped column besides `users.username`); and #36's trigger date
  (verified, not assumed).

**Parked with a trigger:** **#36** budget-report-v2-reads-history (~Dec 2026, when the 6-month
window sits fully inside logged history). The **only** date-parked item, and it correctly carries
**no milestone** — calendar-gated scope is not part of any release.

**Roadmap:** the issue tracker is authoritative — **recount from `gh issue list` rather than
trusting any figure written here.**

### Release history — the reusable lessons

⚠️ `CHANGELOG.md` is the durable per-change record and the GitHub Releases carry the ship notes.
What is kept here is only what a future session would otherwise re-derive.

- **`0.6.0` (2026-08-10) — prod runs `ghcr.io/caddismaster/budget-buddy:0.6.0`.** Background jobs
  you can check on (`app/jobs.py`, the `/settings` panel) plus `scripts/release_prep.py`. One
  additive migration (`sql/34_job_runs.sql`). Verification lessons, all reusable: **the app logs
  NOTHING when the scheduler starts**, so an empty `logs | grep -i scheduler` is not evidence —
  what discriminates is the **thread**, `docker compose exec -T web sh -c 'for p in /proc/[0-9]*;
  do for t in $p/task/*; do cat $t/comm 2>/dev/null; done; done | sort | uniq -c | sort -rn'`
  showing an `APScheduler` line. **`job_runs` is EMPTY immediately after a deploy**, so the panel
  reads NEVER for every job until the daily pass fires — correct, but the first sight of a new
  panel reading NEVER looks exactly like a fault; do not report it as one. And **`pywebpush`
  2.4.0's real delivery is confirmed only as far as the SEND** — the suite stubs `_call_webpush`
  by design, so whether a notification *appeared* still needs a human looking at a phone.
- **`0.5.0` (2026-08-03)** — the Sonnet 5 move and the integration-status panel. ⚠️ **The `css_v`
  deploy check only works when the release CHANGED the CSS** — it proved nothing for `0.4.1`.
  Check `git diff <lastTag>..HEAD -- app/static/style.css` before relying on it. The strongest
  check is **importing a module that did not exist in the previous image**, which is a fact about
  the running code rather than about metadata.
- **`0.4.1` (2026-07-31)** — feedback, release notifications, Python 3.14. ⚠️ **`v0.4.0` was
  tagged and WITHDRAWN at the approval gate, never deployed** — do not treat it as a shipped
  version, and there is deliberately **no `0.4.0` milestone**. Three things that forced or
  followed from it: **`github.event.release.body` is FROZEN at trigger time** (editing the notes
  does not change an in-flight run, and re-running replays the original payload), so combined
  with the announce logic living inside the already-built image **there is no way to change a
  release's notification after publishing** — it takes a new version; **cancelling a release run
  is safe ONLY at the approval gate** (confirm with container uptime + the absence of a new
  `backups/pre-deploy-*` dump); and **two PRs that both add a `## [Unreleased]` entry WILL
  conflict in `CHANGELOG.md`**, which is not a sign either branch is wrong. Three findings from
  the 3.14 work: `--only-binary=:all:` fails on `http-ece` **identically on 3.11**, so always run
  the same probe against the CURRENT runtime before calling a result a regression; **a
  warning-count explosion can be one warning** (345 vs 7, every one `flask_login`'s deprecated
  `datetime.utcnow()`) so count **distinct messages** before reacting; and **Dependabot's ignore
  state is not in `.github/dependabot.yml`** — it lives inside Dependabot and is cleared by
  commenting `@dependabot unignore python` on the original PR.
- **`0.3.1` (2026-07-31)** — the colour-collision fix, cut hours after `0.3.0`. ⚠️ **This was a
  shipped defect that had been identified, filed, judged low-risk and released anyway** — and the
  judgement was wrong on evidence already in hand. **If a known defect's trigger condition is
  "the user has more than N of something", check what N is on the real account before calling it
  unlikely.**
- **`0.3.0` (2026-07-31)** — the triaged backlog, the doughnut fold, automated issue triage and
  the dev-tooling pass. Process notes worth not repeating: **a PR merged out from under an
  in-flight session**, whose symptom (PR head frozen while the branch ref moves on) reads exactly
  like GitHub lag — **`gh pr view --json state` distinguishes them in one call**; a squash-merge
  means a stacked branch must be **rebased** (`git rebase --onto origin/main <old-base>`), not
  merely retargeted; and **CI only triggers on PRs targeting `main`**, so a stacked PR shows "no
  checks reported" and looks broken when it is merely unrunnable. ⚠️ **Four issues running (#87,
  #83, #86, #108) had a specified approach that was wrong on contact with the code** — treat a
  filed approach as a hypothesis and check it before building it. Also: the local dev database was
  wiped and reseeded from `seed_dev.py`, so the old hand-built demo data is gone deliberately
  (`docker compose exec web python scripts/seed_dev.py --username sean`).
- **`0.2.0` (2026-07-28)** — the first FEATURE release under the rebuilt envelope, and the first
  end-to-end exercise of issue → PR → Release → approval gate → automated deploy carrying real
  behaviour. **Push delivery was confirmed on the actual phone** — the one claim no test can make.
- **`0.1.0` (2026-07-27) — the repository reboot.** Kept because it explains why this repo starts
  at `0.1.0` with no earlier tags and why several conventions exist: the app was mature and
  unchanged, and the *envelope* around it was rebuilt — issue→PR workflow, CI+CD in Actions, ghcr
  instead of Docker Hub, a non-root image, `/healthz`, a least-privilege `budget_app` DB role, a
  non-root `deploy` user, and the stack moved `/root/budget-buddy` → **`/opt/budget-buddy`**.
  ⚠️ **The numbered `sql/` files are NOT replayable** — `schema.sql` is the only fresh-DB
  artifact, which is why `--baseline` exists. Docker Hub stopped being the source of truth at the
  cutover and its images were retired 2026-07-28; if ghcr is ever unreachable, **roll forward
  rather than back**. The retired `deploy.sh`/`promote.sh` remain in git history
  (`git show v0.1.0:deploy.sh`). Smoke aside carried over: POSTing `/insights/generate` without
  the form's year/month caches the CURRENT month, not the last complete one — the UI always sends
  them, so it only bites hand-rolled requests.

### Release ledger

⚠️ **`CHANGELOG.md` (committed) is the authoritative record** — do not duplicate it here.
It carries `## [Unreleased]` plus a `## Prior history` summary of the `v1`–`v10.15.0` era,
whose full detail lives in the archived repo's tags and release notes.

## Testing

Run with **`./test.sh`** (args pass through to pytest, e.g. `./test.sh -k semimonthly`).

⚠️ **`test.sh` REFUSES TO RUN while another run is in flight** (#206) — an advisory
`flock` on fd 9, taken before anything expensive and never released, so it survives the
final `exec docker compose …` and the kernel drops it when the run ends (including a
`kill -9`, which a marker file would not). This is the guard that actually holds, because
`test.sh` is the one place **every** path goes through. Set `TEST_SH_LOCKFILE` to override
the path; a machine with no `flock(1)` warns and continues rather than refusing.

⚠️ **`runtests` is GONE** (2026-08-17). It was a machine-local wrapper that sent the suite to a
visible tmux pane; tmux was retired with the terminal front end, so **`./test.sh` is the only
path** and a stale `runtests` on `PATH` should be deleted rather than repaired. The `flock` above
is unaffected — it always was the guard that actually held, and it lives in `test.sh` itself.

It runs in a throwaway `web` container on prod's Python 3.14 — no local venv;
`requirements-dev.txt` adds just `pytest`. Needs the dev `db` container up (route/isolation
tests hit it). Also runs in **GitHub Actions CI** on every push/PR (`.github/workflows/ci.yml`,
`postgres:16` service + `schema.sql`) — but **only when the diff can affect behaviour** (the
`changes` job's `app` flag; a docs-only PR skips the suite and the job still reports success).
**When `Dockerfile`/`requirements*.txt` change, the suite ALSO runs inside the built image**
(`docker-build`'s "Test suite runs inside the shipped image") — the runner's Python is not
necessarily the shipped one, so a base-image bump used to go green having tested the runtime it
was replacing.

**`test.sh` takes one of two paths and says which:** if the dev stack is up it runs the suite
inside the **live `web` container** (`docker compose exec`) — no container created, no image
built, no dependencies installed; otherwise it falls back to a throwaway `run --rm --build`. A
container started before the `dev` stage existed has no pytest, so the script probes for it and
falls back rather than dying on an import error. Both paths run the same image.

**⚠️ `tests/conftest.py`'s `TEST_PREFIX` is PER-WORKER** — `"__pytest__" + PYTEST_XDIST_WORKER`
— and that is the single thing making `-n auto` safe. Workers are separate processes sharing ONE
database; with a shared prefix they all create and tear down the same three users, and a run
produces hundreds of errors (verified: 424 errors + 1 failure with the prefix hardcoded). The
env var is absent serially, so the prefix is then exactly `__pytest__` and nothing changes.
**Never hardcode `__pytest__` in a test** — build names from `TEST_PREFIX`. Two files had done
so and were fixed in #71 (`test_admin_backup.py`'s log assertion, `test_seed_dev.py`'s
`SEED_USER`); a third literal in `test_hardening.py` is a deliberately-nonexistent username and
is fine.

**⚠️ The prefix rule is WIDER than usernames: it covers any GLOBALLY UNIQUE column** (#128,
2026-07-31). `TEST_PREFIX` protects rows scoped by `user_id`; it does nothing for a column whose
uniqueness spans every worker by definition. `push_subscriptions.endpoint` is the one such column
today, and `test_push_reminders.py` hardcoded endpoints for months — two workers then addressed
ONE row and the helper's `ON CONFLICT (endpoint) DO UPDATE SET user_id` silently reassigned a
device mid-test. Every endpoint that reaches the DB now goes through `EP()`; `test_release_announce.py`
builds its own from `TEST_PREFIX` for the same reason. ⚠️ A **broadcast** test compounds this — it
SELECTs every row globally, so assert on **your own endpoints, never a total count**, and make a
failing seam fail **selectively by endpoint** (an unconditional `PushGone` stub deletes a parallel
worker's subscriptions).

**⚠️ Adding tests can expose another file's latent race** by changing xdist's work distribution —
#126's 22 new tests turned #128 red, in a file that PR never touched. Before assuming you broke it
(or that it is "just flaky"): `git diff --stat main~1 main -- <file>` and `grep -c TEST_PREFIX <file>`.
A file that writes shared state and references the prefix **zero** times is latently broken whether
or not it fails today. Verify a fix with **five consecutive full runs** — a one-in-three race passes
a single re-run often enough to look fixed.

**⚠️ The `Dockerfile` is multi-stage and `prod` MUST STAY LAST.** `base` → `dev` (adds
`requirements-dev.txt`) → `prod` (empty, `base` under a name). A build with no `--target` gets
the FINAL stage, and three things build with no target: CI's `docker-build` job, the release
workflow, and a bare `docker build .`. Make `dev` last and all three silently ship pytest to
production. `docker-compose.override.yml` (local only, never on the Droplet) selects
`target: dev`. CI's "Shipped image carries no test dependencies" step is the enforcement —
it asserts `import pytest` FAILS in the built image.

**Gotcha — `python -m pytest`, never bare `pytest`:** going through the module guarantees the
interpreter holding the dependencies is the one that runs them. (In the `dev` stage they are
installed as root into system site-packages, so bare `pytest` would in fact resolve there — but
CI installs `requirements-dev.txt` into the *prod* image as `appuser`, where console scripts land
in `/home/appuser/.local/bin`, which is NOT on `PATH`.) `test.sh` also passes
`-p no:cacheprovider`: `/app` is root-owned (`WORKDIR` created it before the `COPY --chown`) so
pytest can never write its cache there, and passing it turns two warnings per run into none.

**⚠️ Test-run economy is OBSOLETE — a full run costs ~35 SECONDS.** **Just run the full
suite.** The old rationing advice — targeted `-k` runs for iteration signal, one full run per
commit as the gate, batching mechanical commits coarser so each full run gates more work,
capturing red output to a file because re-running was expensive — was written when a run cost
2:40 and is now actively counterproductive. Delete it from your habits; a `-k` run saves
~30 seconds and risks missing the thing that broke.

⚠️ **`./test.sh` defaults to a BOUNDED `-n 10`, deliberately NOT `-n auto`** (`test.sh:20`
carries the measurements: `-n auto` pins every core at ~21s, `-n 10` leaves five cores for the
rest of the machine at ~29s, `-n 4` is ~67s). Pass `-n auto` explicitly to buy the difference
back for one run. **CI is deliberately unaffected** — `ci.yml` invokes `pytest -q -n auto`
directly, where pinning every core is the point.

⚠️ **`pytest.ini` sets `--dist loadgroup`, not the default `load`** (#157/PR #158), and it
lives there rather than in `test.sh` because **CI invokes `pytest` directly** (twice), so
`test.sh` is not on every path this must hold for. Scheduling is identical for ungrouped
tests; a test marked `@pytest.mark.xdist_group("scheduler_sweep")` is guaranteed the SAME
worker as everything else in that group. That is the only way to stop two workers running a
**global sweep** simultaneously: `reminders.send_due_reminders()` reads
`SELECT DISTINCT user_id FROM push_subscriptions` with no user filter — correct, it is the
daily job for everybody — so one worker's call also processes every other worker's users, and
its `ON CONFLICT DO NOTHING` claim in `reminder_log` means the neighbour then delivers through
the **wrong process's mocked seam**. ⚠️ **`TEST_PREFIX` cannot help here** — it isolates *rows*,
and this is contention over a global *operation*. FIVE files drive such a sweep
(`test_push_reminders.py`, `test_job_runs.py`, `test_digest.py`, `test_money_agent.py`,
`test_variable_bills.py`) plus the broadcast in `test_release_announce.py`; all carry the group.

What still holds: the suite's content assertions are the only net for Jinja's
silent-empty-string failure mode, so a global change (a cursor-factory flip, a template-wide
sweep) must be gated on the full suite. And when planning a sweep, grep for the failure SHAPE
(e.g. `\$[0-9]{4}`), not just assertions near the feature — the `|money` sweep broke two
credit-limit assertions the feature-local grep missed.

**`-n0` is the serial escape** — reach for it when you need `pdb`, or when interleaved parallel
output makes a failure hard to read. Any explicit `-n` you pass is respected.

⚠️ **Two superseded claims, recorded so they are not re-derived:** the old
"`test.sh` re-`pip install`s each run (~15s recoverable)" note was simply WRONG — measured, the
install cost ~1.5s and total per-invocation overhead ~2.9s, cut to ~0.8s by #70. And xdist was
predicted to "roughly halve" the run; it actually cut it by ~12×, because the suite is
IO/DB-bound rather than CPU-bound and parallelises far better than a CPU-bound suite would.

**912 tests in `tests/`** (measured 2026-08-17; 5 of them skip on the last day of a month — `test_forecast.py`'s date guards, and 1 more skips *inside the shipped image* — see the `.dockerignore` gotcha). ⚠️ **Recount rather than trusting this number** — it has now been wrong four times (757 against a true 762 on 2026-08-03, 783 against a true 806 on 2026-08-04, 806 against a true 843 on 2026-08-10, 906 against a true 912 on 2026-08-17), so the drift is real and the month-end skips do not explain it. Cross-cutting patterns: **no real API calls anywhere** — every
`ai.py::_call_*_model` seam (and `mailer.py::_call_resend`) is monkeypatched with canned
`SimpleNamespace` responses; every feature file asserts **user isolation**; route tests assert
anon → 302. What each file covers:

- `test_variable_bills.py` — #191 end to end via the mocked `pusher._call_webpush` seam: the link column (stamped on materialization, NULL for hand-entered rows, surviving a schedule delete), which rows the pass selects (variable only, the 3-day window, per-user scoping), the payload, sending (idempotent per transaction, the `push_enabled()` gate claiming nothing, isolation, `PushGone` deletes vs `PushError` keeps), the daily job, and the form round-trip. ⚠️ **`test_a_bill_posted_by_a_page_load_is_still_alerted` is the load-bearing one** — it materializes the way a page load does, asserts `next_due` has already moved past today, and only then runs the job; it is the test that fails under the rejected schedule-side design. Carries `@pytest.mark.xdist_group("scheduler_sweep")` and builds every endpoint from `TEST_PREFIX`. ⚠️ Known to FAIL without the feature, not merely to pass with it: removing the one-line `schedule_id` stamp turns 11 of its 23 red
- `test_deploy_pinning.py` — #190: the compose image ref has **no `:-` default of any kind** (the property, not the string) and errors naming `TAG`; `.env.example` carries a `TAG=` line; both workflows rewrite the pin, `chmod 600` **before** the write, and the release pins **before** its first compose command. ⚠️ Every test SKIPS when the file it reads is absent, naming `.dockerignore` — `docker-compose*.yml` and `.env.*` are genuinely excluded from the shipped image (#176). Verified red against the pre-fix compose file
- `test_model_constants.py` — #140: which model each beat runs on, **and the request
  parameters the Sonnet 5 move made load-bearing**. Beyond the two constant scenarios it
  pins `max_tokens >= 4096` and the explicit `effort` at both Sonnet seams, so a later
  "tidy" back to 2048 fails loudly instead of silently reintroducing truncation. It does
  this by stubbing the **client factory** (`anthropic.Anthropic` / `ai._get_ask_client`)
  with a recorder rather than the `_call_*_model` seam itself — one level deeper than the
  usual convention, because the kwargs live *inside* the seam. Also asserts no sampling
  parameter is ever passed (a 400 on Sonnet 5). ⚠️ Its docstring states the ceiling: green
  proves the constants are spelled right and the kwargs are what we intended, and nothing
  about whether real output fits — the live run is the gate
- `test_restore_check.py` — #153: `scripts/restore_check.py`, entirely pure — the tool needs Docker, the suite runs inside an image that has none (the `.dockerignore` trap pointed at a new file), so every judgement is a pure function and everything that shells out is a `_`-seam. Covers dump selection (by the date **in the filename**, never mtime), role extraction, `role_statements()` skipping `COPY` blocks, and `evaluate()`. ⚠️ **Two tests exist to make a REVERSAL loud, the `test_release_announce.py` device:** `test_an_empty_non_core_table_is_not_a_failure` asserts `goal_coach`/`insights` are legitimately 0 (the issue's own criterion said every table must be non-empty — false against a real dump), and `test_the_owner_is_not_the_only_role_a_dump_needs` asserts a dump names **both** `admin` and `budget_app`. The fixture `REAL_COUNTS` is the verbatim table from a real restore *because* two of its values are zero. ⚠️ Its docstring states the ceiling: green proves the judgement is right and nothing about whether a real dump restores — only running it against one does that
- `test_release_prep.py` — #154: `scripts/release_prep.py`, all pure except one stubbed seam. The changelog roll (content moved not copied, the fresh empty `[Unreleased]`, link refs derived from the version HEADINGS rather than the stale `[Unreleased]` ref), the strip rewrite (**the prose comes through byte-identical** — the load-bearing one, since the tool rewriting copy would be a defect), the refusals (empty `[Unreleased]`, double-roll), and the env report's **three** states. ⚠️ Three separate traps are stated as tests here, each having actually bitten: `test_the_new_entry_is_spaced_like_the_ones_already_in_the_file` compares the seam against a release ALREADY in the file rather than a literal `\n\n` (#174 — the previous assertion checked contents and never shape); `test_the_tool_is_never_told_which_variables_are_new` asserts the ABSENCE of a `--env-var` flag, the `test_release_announce.py` device; and the two real-file tests **skip when the file is absent** (#176 — see the `.dockerignore` gotcha) and assert the parser copes with the real FORMAT, never with today's contents, because the tool itself replaces those contents
- `test_job_runs.py` — #151: the pure `summarize_job_runs()` state machine (OK/STALE/NEVER/NOT_SCHEDULED boundaries, per-job thresholds, unknown job names filtered out), the writer (**upsert not append**, a swallowed `psycopg2.Error`, the naive-datetime cast), and the route (admin sees the panel, non-admin/anon don't). ⚠️ **`test_every_badge_actually_renders` is the load-bearing one** — every state must render real text, since a Jinja typo in a badge is an empty string rather than an error. Carries `@pytest.mark.xdist_group("scheduler_sweep")` — it drives a global sweep, see the loadgroup gotcha
- `test_integration_status.py` — #139: the pure three-state rule, the placeholder property
  (`github_pat_YOURTOKEN` must not read as configured — stated as the incident so swapping
  the length floor for a format check fails loudly), push needing *both* VAPID keys, the
  scheduler line, and non-admin/anon redirects. ⚠️ **The load-bearing one asserts the value,
  three prefix lengths of it, and the variable names are all absent from the response.**
  ⚠️ Every test sets the environment **explicitly** — the dev container legitimately
  carries a real `ANTHROPIC_API_KEY`, so a test assuming "unset by default" would pass or
  fail depending on whose machine it runs on
- `test_release_announce.py` — #115/#131/#133 via the mocked `pusher._call_webpush` seam: the fixed body, that **only the version varies** between two releases, that the title **does not contain "Budget Buddy"** (#133 — stated as a property, not a string, because every other assertion would read a regression as a mere wording change and the duplicate is only visible on a real device), and — the load-bearing one — that `build_release_notification` **takes exactly one parameter** (`inspect.signature`), so reintroducing release text is a visible regression rather than a quiet reopening of the injection surface; the CLI rejecting `--notes`; the `push_enabled()` gate; **reaching every user's devices** (the not-user-scoped property); `PushGone` deleting vs a transient `PushError` keeping; one bad device not aborting the batch; and the Profile consent copy. ⚠️ Assertions are written against **this worker's own endpoints**, never a global count — see the xdist gotcha above
- `test_feedback.py` — #64 end to end via the mocked `github._call_github` seam: the gate (no token → no UI on /profile and the route creates nothing), happy path + label set (`bug`/`enhancement` **plus `from-app`**), an arbitrary posted kind never becoming a label, validation, the input caps, `GitHubError` → GENERIC_ERROR with **the API text, status code and repo name all absent from the response**, the seam unit tests, anon → 302, and the rate-limit registration (`limiter._marked_for_limiting`, the test_admin_backup.py convention — the limiter is disabled under test). ⚠️ **`test_body_carries_only_what_the_user_typed` is the load-bearing one**: it asserts the username and the seeded account/category/transaction names and amount are all absent. The privacy decision is invisible in the code's shape, so a later change attaching context would look like an improvement and break nothing else
- `test_pending_transactions.py` — #86 end to end: create/badge (content-asserting — `HistoryRow` is positional), the page-scoped pin, several pending rows staying newest-first (the stable-sort property), pinning NOT overriding `?month`, the em-dash balance cell **anchored to the actual `<td>`** (a bare `"—"` also matches the edit selects' "— none —" and passes without the feature), **the walk-guard property** (posted rows' balance cells are character-identical whether a sibling is pending or posted — this fails if anyone moves the pin into SQL), page-1 top balance still equalling the full net, mark-posted (clears only, whole-tbody fragment, returns the row to date order), edit-preserves-the-flag, pending counting in month spending, a pending row still being an Auto-Categorize candidate (i.e. NOT behaving like `is_adjustment`), export + cleanup keeping pure date order, and the isolation/anon set
- `test_seed_dev.py` — `scripts/seed_dev.py` (#69): determinism (same seed+day → identical rows), dates derived from `today` not hardcoded, **the fixture scaling property** (parametrized 2–36 months — a fixed opening debt pays the card off entirely on a long window and a fixed goal target gets overshot), analytics-exclusion coverage, schedule `next_due` always FUTURE, plus a DB round-trip: persistence counts, transfer-pair shape, login + populated dashboard, **loading `/` materializes nothing**, refuse/force/dry-run paths
- `test_push_reminders.py` — #33 end to end via the mocked `pusher._call_webpush` seam: the `push_enabled()` gate, subscribe/unsubscribe routes (upsert on endpoint, payload validation, 503 when unconfigured, cross-user IDOR), the reminder window (honours #32's `end_date`), per-occurrence dedup, dead-subscription (404/410) cleanup vs a KEPT transient failure, per-user isolation, and the two materialization properties — a user who never logs in gets rows, and **materialization still runs with push unconfigured** (the gating trap). Plus the threaded daily-job-vs-page-load `FOR UPDATE` twin
- `test_apr.py` — APR: pure `_parse_apr`/`monthly_interest`, /accounts interest line (independent of the limit bar), ask enrichment, either/or facts, payoff "interest adds" render, edit error-path echo
- `test_goal_projection.py` — pure projection incl. the APR block (apr=None backward-compat, interest pushes pace date, pace ≤ interest → no date + Behind, amortized required/mo, 600-month cap terminates)
- `test_ai_collapse.py` — AI cards render `<details>` CLOSED with `data-ai-key`/`data-generated`; empty-state renders OPEN with no key; generate fragment's `data-generated` EQUALS the next load's (the RETURNING round-trip)
- `test_pwa.py` — /sw.js 200 + JS mimetype + anon-accessible, manifest scope "/", five icons, PWA head tags on BOTH shells, remember cookie
- `test_money_filter.py` — `|money` units + a rendered comma on /
- `test_history_balance.py` — running balance: pages connect (page 1 top = true full net), month-filtered continuity, tbody swap carries the seed
- `test_bulk_edit.py` — bulk category/delete: IDOR pair, transfer legs untouchable, garbage ids skipped, filter_qs preserved, page markup (transfer row has no checkbox)
- `test_param_hardening.py` — pure parsers + every pre-fix 500 as a route test, invalid-date errors, bcrypt 72-byte cap, GENERIC_ERROR (never exception text), base/login cache-bust lockstep
- `test_money_agent.py` — the agent loop via the mocked seam (grounding guard, nudge recovery, turn cap), `_normalize_findings`, `recent_transactions` dispatch, `run_money_agent` week-key upsert + isolation, `/agent/run` route, digest integration (cached run reused; agent failure → email still sends)
- `test_credit_limits.py` — pure `credit_utilization`/`_parse_credit_limit` (tier boundaries, over-limit, Decimal), edit error-path echo regression, /accounts bar rendering, ask enrichment, facts + isolation
- `test_budget_history.py` — `record_budget_change` at set/clear/review-apply, no-ops skipped, NULL = cleared, isolation
- `test_dashboard_merge.py` — /analytics redirect, Ask box on /, day-of-week chart, YoY gating, **the tojson regression** (a `</script>` category name arrives escaped); plus #83's colour slots (a new category does not repaint, a month filter does not repaint, per-user) and a **stylesheet** assertion that `--series-1..8` are eight distinct hexes in BOTH mode blocks (the palette is CSS, so no request renders it). Also #108's fold: pure `fold_chart_tail` (short list untouched, tail arithmetic, ranks by total not input order, **only the synthetic row flagged** — a real category named "Other" keeps its hue), route-level (never >7 segments, **total conserved against `SUM(amount)`**, no premature fold, income view folds too, a survivor keeps its colour across months), and `--series-other` being present, unused by any category, and actually neutral in both modes. ⚠️ The slot test seeds **distinct descending amounts** — tied totals leave "which six survive" to SQL tie-breaking, which flakes. Plus #111's distinct-colour set: pure `assign_series_slots` (preferred slot when free, collision broken, never repeats across the drawn set, folded row untouched) and the **production regression** — nine categories where the 1st and 9th created are BOTH in the top six, which is the shape that shipped broken in `0.3.0`
- `test_budget_report.py` — pure grid derivation (grades, streaks, ±10% trend, ordering) + DB loader window (seed dates derived from `_report_months()`, never `timedelta` — no month-boundary flake) + route smoke
- `test_occurrences.py` — pure walkers (`_advance_past` stale catch-up, `upcoming_occurrences` start-exclusive/end-INCLUSIVE, weekly multi-occurrence)
- `test_checkin.py` — check-in: one adjustment closes the gap, match = stamp only, **the trust property** (balance moves, month facts unchanged), cross-user 404s
- `test_amounts.py` — `parse_positive_amount`/`parse_signed_amount` + a `"nan"` POST against EVERY amount form asserting nothing written
- `test_goal_coach.py` — coach facts/route/cache-hit (GET /goals doesn't call the model)/fallback + glow-up smoke
- `test_digest.py` — digest facts (incl. double-occurrence week), recipient selection, idempotency, per-user try/except, opt-in route; two seams mocked
- `test_budget_review.py` — proposal normalization (snap bounds, unknown dropped), facts, scan/apply write-side guards, banner gating
- `test_autocategorize.py` — suggestion normalization, expense-only candidates, scan filter, apply write-side guards, banner count
- `test_transfer_schedules.py` — paired materialization + catch-up + gates + CRUD validation/IDOR + the FOR UPDATE concurrency twin
- `test_ask.py` — tool dispatch arg validation, per-user scoping, the multi-turn loop (turn cap, tools_used), /ask route
- `test_hardening.py` — `_csv_safe` + CSV end-to-end, security headers, cookie flags, constant-bcrypt path; plus #87's `Kind` column — pure `_export_kind()`, the header contract, per-row labelling, that transfer legs/adjustments are still PRESENT (a regression test against "fixing" it by filtering), and **the reconciliation arithmetic** (all rows minus blank-Kind rows == the transfer legs + adjustment exactly)
- `test_ai_parse.py` — quick-add `_normalize`/`_match_id`, parse route, every graceful-fallback path
- `test_insight.py` / `test_forecast.py` — facts, generate route, **cache-hit (page load never calls the model)**, fallback, not-enough-data skip, isolation; forecast adds pure `project_expenses()`
- `test_schedules.py` — semimonthly init math, materialize/catch-up/gates, the **4-thread FOR UPDATE concurrency test** (verified red without the lock), CRUD
- `test_recurring.py` — `compute_next_due()`, all 6 frequencies (pure)
- `test_routes.py` — pages 200 logged-in / 302 anon; login/logout (POST 302, GET 405)
- `test_isolation.py` — A can't touch B's data (all tables); missing/other-user → 404; write-side IDOR
- `test_crud.py` — create/edit/delete happy paths (incl. kind CRUD, flip-clears-budget, credit-limit persist)
- `test_htmx.py` — fragment shape (no `<html>`), isolation 404s, admin 403 gaps
- `test_budget_vs_actual.py` / `test_budget_suggestions.py` — the two budget helpers
- `test_transfers.py` / `test_goals.py` — transfers + goals routes (payoff snapshot, balance-≥-0 rejection, payoff-edit lock; the analytics-exclusion test asserts the dashboard hero figures)

Fixtures (`conftest.py`) use the dev Postgres with `__pytest__`-prefixed users, torn down in
FK-safe order (child rows first — transactions→categories/account are `ON DELETE RESTRICT`).
CSRF + rate limiter disabled under test.

## Versioning

**`0.x` SemVer shape, no stability contract** — full rationale in `VERSIONING.md` (committed).
`0.MINOR` = features, `0.MINOR.PATCH` = fixes; `1.0.0` only when deliberately declared stable.
While the leading digit is `0` a MINOR may carry a break — called out under a `### Breaking`
heading in the changelog entry.

**The release is the unit, not the feature:** a release bundles **everything merged to `main`
since the last release** into ONE version bump, and happens whenever Sean decides — no fixed ship
day. Versions climb monotonically; already-cut tags are never rewritten.

⚠️ **The numbering RESET at `0.1.0`.** This repo's history begins at the reboot; `v1`–`v10.15.0`
live in the archived repo (`CaddisMaster/budget-buddy-archive`). Do not resurrect the old
`v10.x`/`v11.0.0` scheme — it was a compatibility contract for consumers that don't exist.

## Git & Development Workflow

**Issue → branch → PR → squash-merge.** This REPLACED the old trunk-based/no-PR model at the
2026-07 repo reboot; do not work directly on `main`.

1. **Every change starts from an issue.** No issueless PRs. Feature issues carry Gherkin
   acceptance criteria (`.github/ISSUE_TEMPLATE/feature.yml`). **Assign it a milestone** — the
   next release, normally the one open milestone (see below).
2. **Branch** off `main` as `<issue#>-short-slug`.
3. **Test locally — both:** `docker compose up --build` → verify at `http://localhost:5001`,
   AND `./test.sh` (full suite). CI does not click through the app.
4. **New behaviour gets a test that fails without it.** Jinja's silent-empty-string failure mode
   means content-asserting tests are the only net.
5. **Update `CHANGELOG.md`** under `## [Unreleased]`.
6. **Open a PR** with `Closes #<issue>` (one line per issue closed); squash-merge once CI is green.

**Milestones = what shipped in a release** (#183, backfilled 2026-08-10). One per released
version, holding the issues **resolved in that release cycle** — not strictly "which PR closed
it", since an issue can be resolved in one cycle and closed by a PR that lands in the next.
Exactly **one milestone is open at a time** (the next release); it is **closed when that version
ships**. An issue closed `NOT_PLANNED` gets none — abandoned scope is not part of any release —
and neither does a date-parked item like #36.

⚠️ **There is deliberately NO `0.4.0` milestone.** That tag was cut and **withdrawn at the
approval gate**; it never deployed, and its contents shipped as `0.4.1`. A milestone means *what
users actually got*, so creating one would assert a release that never happened. ⚠️ `0.3.1`
*does* have one — it was a real shipped patch, not a re-cut of `0.3.0`.

⚠️ **This convention lapsed once already** — used for `0.1.0` and `0.2.0`, then dropped for four
releases, which is why `0.3.0`–`0.6.0` had to be reconstructed from `git log <prev>..<tag>` and
each PR's `closingIssuesReferences`. That reconstruction is possible but tedious; assigning at
filing time costs nothing.

▶️ **If you use Claude Code, `/wrap` runs this sequence** (`.claude/commands/wrap.md`), and a
`Stop` hook catches the `app/`-without-`CHANGELOG.md` rule locally rather than on CI. Documented
in [`.claude/README.md`](.claude/README.md). None of it is required, and this section stays
authoritative over all of it.

**Batch issues into PRs by COHERENCE, never by calendar** (issue #53). A PR may close several
issues — but only when they share a **file surface**, a **test surface**, or one **user-facing
story**. Being open the same week is not a reason. Work runs in **weekly sessions**, each producing
two or three PRs, not fifteen; the 2026-07-27 session merged 15, several of them fragments of one
logical change (see #51). Five findings in one subsystem = ONE PR closing five issues. Three
unrelated fixes on the same afternoon = three PRs. Two named extremes: a **docs sweep is one PR**
however many issues it closes (`skip-changelog` covers it), and a **schema migration ALWAYS stands
alone** — bundling obscures the deploy ordering it depends on (additive before the pull, drops
after). Full rationale in `CONTRIBUTING.md` §2 "How much goes in one PR"; the short version of why
one big weekly PR was rejected: squash-merge would destroy revert granularity and `git bisect`, and
it saves nothing anyway because **CI runs per push, not per PR**.

**Prefer a feature flag / env-gate** (like `ai_enabled()`) over a long-lived branch — ship it dark
behind the gate, turn it on when ready.

**The "What's new" strip is now a RELEASE step, not a per-commit step** (reconciled at the reboot
— `CHANGELOG.md` carries the per-change record, so the strip is purely user-facing release
comms). At release prep, replace the contents of the single dismissible `.whatsnew` strip at the
top of `app/templates/dashboard.html`: one `.whatsnew-block` per shipped feature (bold
sub-heading + ~3 plain-English bullets), `data-version` + heading = the release version, badge =
the actual ship date. Security/patch fixes and no-UI infrastructure are NOT feature blocks.

Release: cut a GitHub Release for the version → Actions builds, pushes the image, and pauses on
an approval gate before deploying (see Deployment).

## Automated issue triage (`.github/workflows/claude-triage.yml`)

A newly opened issue gets an automated first-pass comment in ~2 minutes. Two prompts behind a
label branch, resolved by querying the issue's labels:

- **`enhancement` → SPECIFY** — restates the idea against real files, drafts Gherkin acceptance
  criteria, names the file surface, lists the applicable gotchas from this file, and raises the
  open design questions. Built for a rough idea filed from the GitHub phone app; it is instructed
  NOT to invent requirements and to put real choices in Open questions instead.
- **anything else (incl. unlabelled) → TRIAGE** — diagnoses: what the code actually does, whether
  a stated cause holds, what a fix would touch.

**READ-ONLY by construction**, and it is enforced in two independent places: `permissions:` gives
`contents: read` with no `pull-requests` at all, and `--allowedTools` grants exactly
`Bash(gh issue view:*),Bash(gh issue comment:*)`. It comments; it never edits the issue, pushes,
or opens a PR. **Do not widen the allowlist to `Bash(gh:*)`** — that reaches `gh issue edit`,
`gh pr create` and `gh api`, i.e. write access to everything, through the one control you thought
was constraining it. `contents: read` blocks the git half; the allowlist is the only thing
blocking the API half.

- **`skip-triage` suppresses the automatic run** — apply it to every issue you file from a
  session (see Current Status for the measurement behind this). A **dispatch deliberately ignores
  it**: hand-written issues are exactly the ones worth a second read, so honouring it there would
  make them the only issues that could never be reviewed.
- **Manual run:** `gh workflow run claude-triage.yml -f issue=<n>` — the only way to review an
  issue opened before the workflow existed, since `issues: opened` cannot reach it and reopening
  is not `opened`.
- **Cost ~$0.50 / ~9 turns per run**, billed against the Claude **subscription** (via
  `claude_code_oauth_token`), so it competes with local Claude Code usage — hence `opened`-only,
  the turn cap, and `skip-triage`.

⚠️ **A change to this workflow CANNOT be verified before merge, and the failure is silent.**
`claude-code-action` refuses to run when the workflow file differs from the copy on the default
branch (a deliberate control — otherwise a branch could edit the workflow and exfiltrate the
token). `workflow_dispatch --ref <branch>` does not help. **The run reports SUCCESS while posting
nothing** — only the log says why. Merge small, verify immediately by dispatching, revert if
wrong; a workflow-only change reverts cleanly.

Two gotchas worth not rediscovering: `permissions:` needs **`id-token: write`** (the action swaps
an OIDC token for an installation token, and fails before reaching the model without it), and
**`github.event.issue.labels` does not exist on a dispatch** — an expression like
`contains(github.event.issue.labels.*.name, …)` silently evaluates false on every dispatched run,
which is why the label is queried with `gh` instead. A `#` comment inside `claude_args` is passed
through as a literal CLI argument; comments go outside the block.

## Delegation (worker agents)

Four agents are defined in `.claude/agents/`, all Sonnet. They split into **two executors**
that edit files and **two reporters** that only read:

| Agent | Tools | Does |
|---|---|---|
| `sweeper` | Read/Grep/Glob/**Edit** | Mechanical multi-file sweeps from an explicit spec file |
| `test-first` | Read/Grep/Glob/**Edit** | Makes an orchestrator-written failing test pass, editing app code only |
| `gotcha-auditor` | Read/Grep/Glob/**Bash** | Audits a branch diff against this file's Key Gotchas |
| `release-prep` | Read/Grep/Glob/**Bash** | Runs the pre-release checklist against `main`, reports go/no-go |

**Why delegate at all:** the scarce resource on this project is the orchestrator's context
window, not time. Wide reads are what exhaust it, and wide reads are exactly what the
gotchas demand (*grep for the failure SHAPE*, the ~23 `is_adjustment = false` filter lists).
An agent burns its own context and returns a conclusion. That is the entire trade — against a
cold start, since **an agent cannot see the session that spawned it**.

Policy:

- **Delegate:** mechanical multi-file sweeps with an explicit written spec (attribute-access
  conversions, url_for conversions, find-replace-shaped work), and wide read-only recon. Write
  the spec to a scratchpad file (file list, old→new table, exclusions, expected per-file counts)
  and pass the worker the spec path — specs are re-runnable and diffable.
- **Delegate feature work ONLY test-first**, via `test-first`: the orchestrator writes the
  failing test, runs the suite, and hands the worker the test path, the verbatim failure and
  an explicit file surface. The worker edits app code and **never the test**. This is the one
  delegable shape of feature work, and it is delegable *because the spec is machine-checkable*
  — a wrong build stays red instead of going quietly wrong. See the ⚠️ below for why
  plan-then-execute delegation is NOT the general pattern here.
- **Do NOT delegate:** anything touching ownership guards, row shapes mid-refactor,
  SQL/migrations, AI seams (`_call_*_model`), or exception handling. `test-first` carries this
  same list as a decline list and hands back rather than doing it carefully.
- **Do NOT delegate running the suite.** A full run is ~35 seconds — a background test agent
  has no win to capture. **Every worker batch gets `./test.sh` (full suite) run by the
  orchestrator before commit**, plus a spot-grep that the swept pattern is gone. Workers never
  run tests, never commit, never edit outside their spec. With `test-first` this is also the
  iteration loop: suite → relay the failure → worker fixes, which keeps the orchestrator's
  per-cycle cost at a suite result rather than a diff review.
- **Batch sizing:** don't spin one worker per tiny area — target batches that gate meaningful
  work per full-suite run; per-file expected counts + the leftover grep localize failures.
- **If the prompt approaches the length of the work, don't delegate.** The cold start is then
  pure loss, which is why the delegate list names sweeps, recon and test-first work — and
  nothing else. For `test-first` the test itself is most of the prompt, so the rule is
  already satisfied whenever the test was worth writing.

⚠️ **Plan-then-execute is deliberately NOT the general pattern**, despite being the popular
one. The orchestrator does not hand a written plan to a cheap worker and take the diff.
Two reasons, both specific to this repo. First, **the plan is the fragile part**: four
issues running (#87, #83, #86, #108) had a specified approach that was *wrong on contact
with the code*, so a worker that faithfully implements a plan produces convincingly wrong
code rather than obviously wrong code. Second, **delegation pays only when verification is
cheaper than the work** — that holds for a reporter's conclusion (spot-check a few claims)
and for a sweep (grep + suite), but reviewing a feature diff against these gotchas costs
about what writing it costs, and Jinja's silent-empty-string failure mode means a wrong
diff can look right. A **failing test** is what restores the asymmetry, which is the whole
reason `test-first` is scoped the way it is. Settled 2026-08-04.

✅ **`test-first` was first exercised on 2026-08-10** (PR #173, `scripts/release_prep.py`) and
the pattern held: 25 tests green on the first pass, 10 more after one round trip, the test file
never edited, and it did not claim the tests passed. Two things worth keeping. It **flagged its
own scope creep** — an unrequested `--env-var` flag — rather than leaving it to be found; that
flag was in fact wrong, and volunteering it is what surfaced the design error. And **the round
trip caught a wrong SPEC, not wrong code**: the orchestrator's replacement tests shelled out to
`git`, which can never pass because the suite runs in a container with no git binary. A faithful
worker would have stayed red rather than producing something convincing — which is the entire
argument for this shape, demonstrated in about ninety seconds. **`sweeper` is now the only agent
never run.**

⚠️ **The second `test-first` outing (2026-08-14) was a MIS-SIZING, and the agents were not the
problem.** Two ran in parallel, one per worktree, on two small throwaway issues. Both produced
correct code first pass with zero round trips, neither claimed the tests passed, and both reported
scope creep they had **declined** (one wanted to factor a shared helper out of a function five
other tests pin — correctly refused). And it was still the wrong call: **~195k tokens of agent
context for 22 and 51 lines of code**, with prompts longer than what came back. The rule that
should have stopped it is already above — *if the prompt approaches the length of the work, don't
delegate* — and it was broken in the act of demonstrating it. **Sizing heuristic, use it alongside
the verification rule rather than instead of it: delegate when READING the files is the expensive
part, not when writing is.** Verification cost alone does not discriminate here — checking a
20-line diff is cheap, so that rule says yes while the economics say no; both tests must pass.
⚠️ Running the two in **parallel bought nothing** (both finished in ~90s) and cannot help anyway:
the two branches queue for the suite regardless, because `TEST_PREFIX` separates xdist workers
within one run and not two runs from each other.

⚠️ **An agent working in a `git worktree` cannot run the suite at all** — `.env` is gitignored so
`worktree add` never copies it, `test.sh` does `cd "$(dirname "$0")"` so compose runs in the
worktree under a *different project name*, and `${TAG:?}` (#190) then refuses outright. This costs
nothing, because `test-first` has no Bash either way — but it means the orchestrator runs every
test. To test a branch that is live in a worktree, `git` refuses a normal checkout and a **detached**
one is the way in: `git checkout --detach <branch>` in the main clone, run, `git checkout main`.
⚠️ Commit in the worktree first — the bench sees the commit, not the worktree's working files.

⚠️ **The two executors are tool-constrained; the two reporters are only PROMPT-constrained.**
`sweeper` and `test-first` have no Bash, so "you cannot run tests or commit" is structurally
true. `gotcha-auditor` and `release-prep` need `git diff`/`gh` reads to be useful standalone,
so they carry Bash and their read-only discipline lives in their prompt — a weaker guarantee.
Under manual invocation that is an accepted trade. **If either is ever wired to automatic
routing, add `deny` rules for `Bash(git commit:*)`, `Bash(git push:*)` and
`Bash(gh release:*)` to `.claude/settings.json` first.**

⚠️ **Agents are not skills, and neither is a slash command.** `/verify` is a skill — it loads
instructions into the orchestrator's own turn. An agent is a separate instance with its own
context whose report comes back to the orchestrator, never straight to the user. There is no
`/gotcha-auditor`. Nothing invokes an agent on a schedule either: automatic firing would be a
**hook** in `settings.json`, deliberately not configured (a Sonnet agent on every commit is
expensive and noisy, and both reporters are naturally once-per-PR / once-per-release).

⚠️ **Gotcha: agent definitions register at session START.** A `.claude/agents/*.md` created
mid-session isn't callable until next session — fall back to a general-purpose agent with
`model: sonnet` and the relevant rules inlined.

## Deployment

- GitHub repo: https://github.com/CaddisMaster/budget-buddy
- App URL: https://budget.seandesmet.com · Landing page: https://seandesmet.com
- ✅ **DEPLOY IS AUTOMATED (built 2026-07-27).** Publishing a GitHub Release triggers
  `release.yml`: build → push to **ghcr.io/caddismaster/budget-buddy** → **smoke the pushed
  image** (boots it against a throwaway Postgres, asserts `/healthz` 200) → **approval gate**
  (the `production` Environment, Sean as required reviewer) → SSH deploy as `deploy` →
  `/healthz` verification. To ship: cut the Release, click approve. **Rollback** = the
  `rollback.yml` workflow dispatched with a version (it confirms the manifest exists in ghcr
  before touching the box).
  - **A pre-release deliberately does NOT move `:latest`** — that is what makes throwaway test
    releases safe. Images are tagged `:<version>`, `:sha-<short>`, and `:latest`.
  - **`docker compose pull web`, never a bare `pull`.** A bare pull also fetches `postgres:16`,
    and `up -d` then recreates the DB container — shipping app code must never upgrade or
    restart the database engine (found by rehearsal, issue #22).
  - **Deploy secrets are Environment-scoped** to `production`, so no un-gated workflow can read
    the SSH key. `DROPLET_USER` is a repo **variable**, not a secret — as a secret, Actions
    redacted the string `deploy` inside ordinary words.
- **ghcr is the only registry.** The Docker Hub escape hatch was retired 2026-07-28 after two
  releases (`0.1.0`, `0.2.0`) shipped from ghcr without incident — it pointed at `v10.15.0`
  code, so "falling back" would have meant silently reverting the app by two releases and a
  schema. If ghcr is ever unreachable, roll forward (re-push) rather than back. The retired
  `deploy.sh`/`promote.sh`/`docker-compose.staging.yml` remain in git history if ever wanted:
  `git show v0.1.0:deploy.sh`.
- **Env vars:** `ANTHROPIC_API_KEY` gates every AI surface via `ai_enabled()` (optional — app runs
  fine without it). `RESEND_API_KEY` gates email (`mail_enabled()`), `ENABLE_DIGEST_SCHEDULER=1`
  starts the digest scheduler — both **Droplet-only** (unset locally/CI so nothing auto-sends).
  `COOKIE_SECURE=1` (Secure cookies + HSTS) — Droplet-only; must stay unset locally/tests.
  `FEEDBACK_GITHUB_TOKEN` gates in-app feedback (`feedback_enabled()`, #64) — Droplet-only, a
  **fine-grained PAT scoped to this repo with `issues: write` and nothing else**, so a leak means
  issue spam rather than code access. ⚠️ Deliberately NOT named `GITHUB_TOKEN` — that is a magic
  name in GitHub Actions. After editing `.env`, `docker compose up -d --force-recreate web`.
  `.env` is gitignored + never baked into the image. ⚠️ **A missing env var is the one deploy
  failure with NO signal** — nothing in `release.yml` writes or validates `.env`, and a gated
  feature whose variable is unset is indistinguishable from that feature working as designed.
  `RUNBOOK.md` §6 carries the pre-release check (`git diff v<last>..HEAD -- .env.example`).
  ✅ **That check earned its keep on its first outing** (2026-07-31, cutting `0.4.1`): it surfaced
  `FEEDBACK_GITHUB_TOKEN` as the one new variable since `v0.3.1`, which would otherwise have
  deployed #64 completely invisible. Run it **before** cutting the release.
  ✅ **Since #139 the app answers this itself** — `/settings` carries an admin-only
  Integrations table reporting each gate as configured / not configured / set-but-implausible.
  It is the first place to look after a deploy, and it makes the check below something you do
  to *confirm* a suspicion rather than to discover one.
  ⚠️ **Verify a secret landed by LENGTH, not presence.** `grep -c` reports a placeholder as
  happily as a real token — a literal `github_pat_YOURTOKEN` from copy-pasted instructions once
  reached the Droplet `.env` and would have rendered a form that accepts input and fails on every
  submission (worse than absent, since `feedback_enabled()` only tests non-empty). Confirm inside
  the container: `docker compose exec -T web sh -c 'printf "len=%s\n" ${#FEEDBACK_GITHUB_TOKEN}'`
  — a fine-grained PAT is ~93 chars. ⚠️ Note also that a **read** call against this PUBLIC repo
  returns 200 whatever the token's permissions are, so `GET /repos` proves only that the token is
  *valid*; `issues: write` is unprovable without writing, and was finally confirmed by the first
  real form submission (#133).
- **Schema changes:** `schema.sql` only runs on a *fresh* DB. For prod, apply the numbered `sql/`
  migration **by hand** — pg_dump first. **Order matters:** additive migrations (new
  columns/tables) go **BEFORE** `docker compose pull` (new code must never query a missing
  column); column/table **DROPs go AFTER** the pull (old code still SELECTs them until the swap).
- **Releases:** each gets a GitHub Release whose notes list every bundled item (and which, once
  the Release is what *triggers* the deploy). `CHANGELOG.md` is the durable record —
  update it under `## [Unreleased]` in every PR. **No tags exist in this repo yet**; the first
  will be `v0.1.0`. The `v10.15.0` tag and everything before it live in the archive repo.
- **Droplet access:** host, credentials, and the deploy-dir layout live in the gitignored
  `CLAUDE.local.md` (maintainer-only). The shape: **`/opt/budget-buddy`** on the Droplet is a
  **PURE DEPLOY DIR — NO git, NO source**, just `docker-compose.yml`, `.env`, `sql/`, and
  `landing/`. To change compose or add a migration, `scp` it up — `git pull` doesn't work there.
  It is owned by an unprivileged **`deploy`** user (docker group, no sudo) that CI authenticates
  as. It moved from `/root/budget-buddy` on 2026-07-27 — a non-root user cannot own or traverse
  `/root`, and serving `landing/` from in there had forced `/root` to `0755`, leaving the prod
  `.env` world-readable. **Compose derives its project name from the directory basename**, so the
  move kept the `budget-buddy_postgres_data` volume; renaming the directory would have silently
  created an empty one.
- **Backups:** in-app `/admin/backup` (manual pg_dump download), plus an automated nightly pull to
  the maintainer's machine (Mac-side launchd job, not in the repo — see `CLAUDE.local.md`).
  Rationale: the DB is the only irreplaceable thing (source in git, images in the registry, certs
  re-issue). ✅ **The restore was executed for the first time on 2026-08-10 (#153) and all 14
  retained dumps restore cleanly** — verify any dump with
  `python3 scripts/restore_check.py <dump-or-dir>`. ⚠️ Two holes in the retained window, both
  still worth an answer from healthchecks.io: **2026-08-02** is missing entirely (the run never
  fired), and **2026-08-09** is a *partial* failure — database dump fine, SSH dropped, configs
  tarball absent. On that day **no ping was sent at all**, because the direct ping is blocked on
  some networks and the fallback routes *via the Droplet*, so one fault silences start and
  failure together and absence is the only remaining signal.
- 📕 **`RUNBOOK.md` (committed) is the operational source of truth** — topology, the full Nginx
  config, TLS/certbot, prod compose, backup + **restore** procedure, and a rebuild-from-nothing
  checklist. Read it before touching anything on the server. **The Droplet now runs Budget Buddy
  ALONE** — Mealie and Uptime Kuma were retired 2026-07-27 (data archived first), so restarting
  Docker or rewriting Nginx no longer has collateral effects. Disk 30%, RAM ~0.5 GB of 2 GB.
  **External monitoring is a DigitalOcean Uptime check on `/healthz`** (one free check, 1-min
  interval, off-box) — it replaced the retired Uptime Kuma, which had been watching a page that
  returns 200 during a database outage. Two settings matter: watch `/healthz`, and accept only
  `200-299`, since the endpoint returns 503 when the database is unreachable. The two previously-recorded issues are both resolved: the `www`
  TLS failure was fixed, and the `status.seandesmet.com` backup gap was a false claim, retracted.

## Maintainer notes (local only)

The maintainer keeps a private Obsidian vault — reference notes, a per-release tracker, and a
daily diary — plus Droplet access details and backup infrastructure. None of it is in this repo.
If you are the maintainer, that context lives in the gitignored `CLAUDE.local.md`; the standing
rule it carries is **do all note-writing in ONE pass at the very end of a session**, not
incrementally.

Nothing in the app or the test suite depends on any of it. A fresh clone is fully functional
without it.

The same line runs through `.claude/`. The harness there is **committed** — agents, skills,
`/wrap`, the changelog hook, the permission allowlist — because a permission grant belongs in a
reviewable diff. `.claude/settings.local.json` holds the machine half (absolute `$HOME` paths,
`ssh`, the deploy user) and is gitignored alongside this file, as is `.claude/worktrees/`.
`/wrap` step 3 defers to `CLAUDE.local.md` for where notes go rather than restating any of it.
