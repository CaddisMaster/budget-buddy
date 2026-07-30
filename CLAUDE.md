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
  __init__.py        # Flask app + extensions (Login, Bcrypt, Limiter, CSRF); registers the 18 blueprints; cookie flags + @after_request security headers (Secure/HSTS gated on COOKIE_SECURE); starts the APScheduler on ENABLE_DIGEST_SCHEDULER=1 ALONE and registers each job with its OWN gate (weekly digest ← mail_enabled(); daily tasks ← always — see the scheduler gotcha; safe only under single-worker gunicorn); |money template filter (thousands-sep, emits the NUMBER only — display templates ONLY: AI fact-builders / chart |tojson payloads / form input values stay raw, a comma'd value fails parse_positive_amount); css_v + brand_svg Jinja globals (both computed once at startup: the style.css content hash, and icons/icon.svg inlined |safe in base.html so sidebar mark + favicon share one source)
  pusher.py          # outbound Web Push seam (#33, the mailer.py twin). push_enabled() gate, public_key(), send_push(), PushError + PushGone (404/410 = the subscription is DEAD, caller deletes it; anything else is transient and retried tomorrow), single _call_webpush() network seam tests stub. NEVER touches the DB
  mailer.py          # outbound email seam (Resend). mail_enabled() gate (twin of ai_enabled()), send_email(), MailError, single _call_resend() network seam tests stub. NOT named email.py (would shadow stdlib)
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
    admin.py         # user mgmt, create user, backup, settings
    transfers.py     # account transfers (linked income/expense pair) + recurring transfers (transfer_schedules CRUD + run_due_transfers() materializing a paired transfer per due date)
    goals.py         # goals (save + payoff) + compute_goal_projection(..., apr=None) — payoff goals feed the linked card's apr (GOAL_SELECT carries a.apr AS account_apr; _goal_view reads rows by ATTRIBUTE): est_monthly_interest always in the dict (None unless apr + debt), pace date = bounded 600-month simulation (pace ≤ interest → None + Behind), required_per_month amortized; Goal Coach (compute_goal_coach_facts() + load_goal_coach() + POST /goals/coach/generate, cached in goal_coach); GET /goals gates the card on ai_enabled() + in-progress goals
    push.py          # POST /push/subscribe|unsubscribe (#33) — one row per DEVICE, endpoint is the identity (globally UNIQUE, so re-subscribing upserts); validates the posted JSON, scopes every write to current_user. Profile UI gated on push_enabled()
    reminders.py     # the DAILY job (#33). run_daily_tasks() = materialize_all_users() THEN send_due_reminders(); `flask run-daily` CLI. Materialization is UNGATED (see the scheduler gotcha); reminders gate on push_enabled(), enumerate tomorrow via main.upcoming_occurrences (so #32's end_date is honoured), and claim each occurrence in reminder_log with ON CONFLICT DO NOTHING BEFORE sending — a failed send is deliberately NOT retried (a duplicate notification is worse than a missed one)
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
scripts/             # ingest.py, clean.py, insert.py data pipeline (own requirements.txt — pandas lives THERE, not in the app image); migrate.py; seed_dev.py (#69 — synthetic dev dataset; PURE build_seed_plan() + thin write_plan(), standalone like migrate.py, never imports the app)
landing/             # Static landing page at seandesmet.com
.github/workflows/   # ci.yml (a `changes` job classifies the diff → app/image/sql flags; jobs ALWAYS RUN and gate their expensive STEPS on them — `paths-ignore` on a required check strands the PR forever; fails open at BOTH levels, incl. `if: ${{ !cancelled() }}` + `needs.changes.result != 'success'` so a classifier failure runs everything. lint + pytest on postgres:16 + image builds/boots as appuser + the suite re-run INSIDE the built image when Dockerfile/requirements change); release.yml (published Release → build+push ghcr → smoke the PUSHED image → approval gate → SSH deploy → verify /healthz); rollback.yml (workflow_dispatch a version → redeploy that exact tag); changelog.yml (app changes must touch CHANGELOG.md unless labelled `skip-changelog`); claude-triage.yml (automated first-pass comment on a new issue — see the Automated issue triage section below)
```

## Database Tables

- `transactions` — amount, description, transaction_date, category_id, account_id, transaction_type (income/expense), is_adjustment (exclude from analytics), is_transfer + transfer_group_id (transfer legs), **is_pending (#86 — a DISPLAY flag, pins the row to the top of History; excludes it from NOTHING, the opposite of is_adjustment)**, user_id, created_at. **`is_recurring`/`frequency`/`next_due`/`recur_second_day` are LEGACY** — recurrence moved to `schedules`; kept (always default) only so the History row shape is unchanged
- `schedules` — recurring income/expense templates: amount, description, category_id, account_id, transaction_type, frequency, anchor_day + second_day (semi-monthly), next_due, **`end_date` (NULL = runs indefinitely)**, is_active, user_id, created_at. **FINISHED ⇔ `end_date IS NOT NULL AND next_due > end_date`** — deliberately NOT `end_date < today` (a schedule ending the 15th whose next_due is the 1st still owes that occurrence on the 10th), and runner-independent, since next_due only moves forward and never past what was materialized. **Not a ledger row** — `run_due_schedules()` materializes a plain transaction on each due date (going forward, no back-fill) and advances `next_due`
- `transfer_schedules` — recurring **transfer** templates (the transfer twin of `schedules`): amount, description, **from_account_id + to_account_id** (no category), frequency, anchor_day + second_day, next_due, **`end_date` (same semantics as `schedules`)**, is_active, user_id, created_at. Separate table (a transfer needs two accounts). `run_due_transfers()` (transfers.py) materializes a **paired transfer** (linked expense+income legs sharing one `transfer_group_id`, both `is_transfer=true`) per due date, looping to catch up. Reuses `compute_next_due()`/`compute_initial_semimonthly_due()`, all six frequencies
- `insights` — cached monthly AI narration, one row per user per month: year, month, content (JSON `{summary, tips[]}`), model, user_id, created_at; **UNIQUE(user_id, year, month)** upsert. **Stores only the narrative** — figures are recomputed each load (`compute_month_facts()`), never persisted
- `forecasts` — cached month-ahead projection, **identical shape to `insights`** (separate table, not a `kind` column). Narrative only; figures recomputed by `compute_forecast()`
- `goal_coach` — cached goal-pace narration, identical shape to the twins, pointed at the Goals page. Monthly-keyed to reuse the load/upsert even though goals aren't month-scoped
- `agent_runs` — cached Money-agent weekly runs: user_id, **period_start (the week's Sunday, via `helpers.most_recent_sunday` — same boundary as `last_digest_sent_on`)**, content (JSON `{summary, findings:[{title, detail, evidence}], tools_used}`), model, created_at; **UNIQUE(user_id, period_start)** upsert. Stores only the narrative + cited evidence text — no figures are trusted from it
- `push_subscriptions` — one row per **DEVICE** (#33): user_id, `endpoint` (**globally UNIQUE** — it is the push service's URL for that browser install, so re-subscribing upserts and a different user subscribing on the same browser MOVES the row to them, which is correct), p256dh, auth, created_at. A user may have several
- `reminder_log` — the reminder idempotency marker (#33): user_id, `source` ('schedule' | 'transfer'), `source_id`, `occurrence_date`, sent_at; **UNIQUE(user_id, source, source_id, occurrence_date)**. Keyed per **OCCURRENCE**, not a per-day column on `users` — a date marker only holds while the lead time is exactly 1 day; widen the window and the same bill re-notifies daily. The row IS the lock (claimed with `ON CONFLICT DO NOTHING`), so it survives the container restart a deploy causes. `source_id` addresses two tables, so it is deliberately **not** an FK — orphaned markers are inert
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
  blue both fail). A category's slot is its **creation order** (`main.py`'s `category_slots`,
  built `ORDER BY id`, narrowed to the categories actually on the chart so the page carries no
  extra names). Never colour by position in the rendered array — that shifts with every filter
- **Amount validation:** every form amount goes through `helpers.parse_positive_amount()` — `float('nan')` passes a plain `<= 0` check and Postgres stores NaN in numeric, poisoning every SUM(). Never hand-roll `float(x); if x <= 0`
- **Param validation:** same rule for query/form params — `?month` → `parse_month_param()`, `?page` → `parse_page_param()`, posted FK ids → `parse_int_param()`. A raw string into a psycopg2 `%s` against an int column raises (= 500)
- **Write-side FK ownership:** when a form posts a `category_id`/`account_id`, validate it belongs to the user *before* the INSERT/UPDATE — `validate_category_account()` in transactions.py, folded into the route's validation-error path. Used by transaction new/edit, schedule create/edit, bulk edit, cleanup apply
- **Error messages:** unexpected write failures show `helpers.GENERIC_ERROR` and log the real exception via `current_app.logger.exception()` — never flash/toast `str(e)` (psycopg2 text leaks constraint names/SQL). The two FK-delete sites keep their friendly "Cannot delete — in use" branch
- **Cache-bust is automatic:** the stylesheet `?v=` is `css_v` (startup md5 of style.css) — nobody bumps a number. base.html AND login.html (which doesn't extend base) both read it. ⚠️ **Local dev consequence:** the source is now bind-mounted, and `css_v` is computed ONCE at import — so editing `style.css` changes nothing until `docker compose restart web` (~2s). Python and template edits ARE live. A CSS change that "does nothing" is this, not a broken mount
- **Local dev runs bind-mounted with live reload** (`docker-compose.override.yml`, local-only — the Droplet has no override, so production gets none of it): `.:/app` over the image's `COPY`, gunicorn `--reload`, and `TEMPLATES_AUTO_RELOAD=1`. **Both reload mechanisms are needed** — `--reload` watches Python modules only, so without the env var (read in `app/__init__.py`) an edited template reaches the container and is silently ignored, which looks exactly like the mount failing. Anonymous volumes mask `/app/.venv`, `/app/.ruff_cache` and `/app/.pytest_cache`: a bind mount ignores `.dockerignore`, and `.venv` holds macOS-native wheels that are wrong for Linux
- **`.venv/` is EDITOR-ONLY** — it exists so the language server can resolve `flask`/`psycopg2`/`pytest`; nothing is ever run from it and the app and tests stay in containers. VS Code auto-discovers it in the workspace root, so **no `.vscode/` config is committed** and none is needed. It must be **Python 3.11** (Homebrew): the Mac's system 3.9 cannot evaluate `app/ai.py`'s `str | None` annotations and reports working code as broken. Gitignored (both `venv/` and `.venv/`) and in `.dockerignore`
- ⚠️ **There is deliberately NO dev container** (#80, removed 2026-07-28). One was added in #76 and removed two PRs later: it shipped broken twice, needed `git`/`procps`/`curl` in the image's `dev` stage purely for the editor, and split the workflow because it has no Docker inside it. The `.venv` fixes the editor on its own with no maintenance. **Do not re-add one** without a reason that the venv does not already cover. If a remote/container editor setup is ever revisited, the trap that bit #78 is worth knowing: VS Code resolves **workspace** settings ABOVE **remote** ones, so a `python.defaultInterpreterPath` in `.vscode/settings.json` silently overrides a devcontainer's own value
- **Security headers + cookies** (`app/__init__.py`): one `@app.after_request` sets X-Frame-Options/X-Content-Type-Options/CSP `frame-ancestors 'none'`/Referrer-Policy (+ HSTS in prod). Cookies are `HttpOnly` + `SameSite=Lax`; **`Secure` + HSTS gated on `COOKIE_SECURE`** (Droplet-only) so local HTTP dev + tests still work. CSP is `frame-ancestors` only — a full policy would break the inline scripts
- Flask-Limiter: 60 req/min/IP, in-memory storage (single Gunicorn worker)
- Templates read rows by **attribute** (`t.amount`, `account.credit_limit`); row partials reuse the list query's row shape. The three remaining `[0]` indexes in templates are Python lists (flash messages, top_categories, remaining_items), not rows
- CSRF: Flask-WTF CSRFProtect — token on POST forms, plus a single `hx-headers='{"X-CSRFToken": ...}'` on `<body>` in base.html covering every HTMX post/put/delete
- **Flex overflow:** `.main-content` is a flex item and MUST keep `min-width: 0` — without it a wide table inflates the column past the viewport instead of scrolling inside its `.table-wrapper`. Same hotfix owns the iOS status-bar rules: `viewport-fit=cover` + `html` background + safe-area paddings (keep `theme-color` for Android; never `black-translucent`)
- **PWA/iOS testing:** responsive mode does NOT test iOS — installed-PWA bugs only show on the actual phone over HTTPS
- **AI-card collapse:** the four AI narration cards are `<details>` with `data-ai-key` + `data-generated` (the cache row's `created_at.isoformat()`; `initAiCollapse` in base.html + localStorage `bb-ai-seen:<key>` drive read-state). Generate routes must get the timestamp via `RETURNING created_at` — a route-local `datetime.today()` makes every regenerate read as new twice. Server renders CLOSED except empty-state (Generate must work without JS) and `just_generated` fragments. The What's-new strip says "weekly money check", NOT "Money agent" — a dashboard test asserts the agent CARD's absence by that exact string

## Current Status

### On `main`, NOT yet deployed — the triaged backlog, built (2026-07-29)

Four PRs closing three issues: **#104** (#83 doughnut colours), **#105** (#87 CSV
`Kind` column), **#106** (the `is_pending` migration, standing alone), **#107**
(#86 Pending transactions). Tests **668 → 706**. Two `### Fixed` changelog
entries and one `### Added`. Prod still runs `0.2.0`.

**One additive migration, `sql/33_pending_transactions.sql`** — applies BEFORE
the image pull, which `release.yml` already does automatically.

All three issues had a specified approach that was **wrong on contact**, and the
corrections are the durable part:

- **#87** — the issue said the export should exclude transfers/adjustments like
  the analytics do. Wrong peer: the export's filter list is byte-identical to
  `_load_history()`'s because it is a download of the **History view**.
  Excluding them would break "download what you see". A derived `Kind` column
  (`transfer`/`adjustment`/**blank**) was added instead; rows are unchanged.
- **#83** — the recommended probe-for-a-free-slot was rejected: it makes a colour
  depend on which *other* categories are on screen, and the `?month` filter
  changes exactly that, so switching months could repaint a survivor. Colours now
  come from **creation order** (`ORDER BY id`), which is collision-free *and*
  immovable. See the two gotchas below.
- **#86** — see the ⚠️ pin gotcha below; it is the one place a natural-looking
  one-line change ships a silent bug.

⚠️ **A doughnut cannot carry seven distinguishable slices, and #83 does not fix
that.** Validated with a real CVD/contrast checker: only ~4 hues clear all-pairs
separation, and at 8 the worst pair is red↔orange at ΔE 7.1 against a floor of
15 — which is *literally* #83's original "two slices read as oranges" complaint.
The collision fix removes identical hex (the acute bug) but #83's own acceptance
criterion is **not fully met and cannot be by any palette**. Tracked as **#108**
(top-6 + "Other", or ranked horizontal bars). Do not "fix" it by adding a ninth
hue.

⚠️ **Nobody has looked at the rendered doughnut.** No browser was available in the
session. The palette is validated and the stylesheet is asserted, but if
`cssVar('--series-N')` ever returned empty the slices would draw transparent, and
only looking rules that out. Worth one glance in light and dark.

### On `main`, NOT yet deployed — automated issue triage (2026-07-29)

Six PRs (#85, #89, #92, #95, #97, #99) closing #84, #88, #91, #94, #96, #98.
**No app code changed** — every one touched `.github/workflows/claude-triage.yml`
and nothing else, so all six carried `skip-changelog` and there is no
`CHANGELOG.md` entry. See "Automated issue triage" below for how it works.

⚠️ **When you file an issue from a session, add the `skip-triage` label.** It is
the whole convention: measured on 2026-07-29, auto-reviewing session-written
issues produced two comments nobody read (~$1 of subscription budget), while all
three genuinely useful runs were dispatched deliberately. A dispatch ignores the
label on purpose — hand-written issues are the ones most worth a second read.

### On `main`, NOT yet deployed — developer tooling pass (2026-07-28)

Five PRs closing seven issues, **all tooling, zero user-facing change**. Sitting under
`## [Unreleased]` in `CHANGELOG.md`; prod still runs `0.2.0`.

- **#69 / PR #73** — `scripts/seed_dev.py`, a synthetic 6-month dev dataset from one command.
- **#70 / PR #74** — `Dockerfile` gained a `dev` stage; `test.sh` execs into the running
  container instead of building a throwaway one and reinstalling pytest.
- **#71 / PR #75** — `pytest-xdist`, `-n auto` by default. **204s → 17.2s**, tests 635 → 668.
- **#76 / PR #77** — `.venv` for the editor + source bind mount with live reload.
- **#78 / PR #79**, then **#80 / PR #81** — a dev container was added, broke twice, and was
  removed. The venv, bind mount and reload all stayed.

Also closed without code: **#72** (Codespaces — premise didn't hold, see the `.venv` note above)
and **#60** (`test.sh`'s unquoted `$*`, fixed as a side effect of #70 and verified before
closing).

⚠️ **The local dev database was wiped and reseeded** from `seed_dev.py` — the old hand-built
demo data is gone deliberately. Reseed with
`docker compose exec web python scripts/seed_dev.py --username sean`.

### Shipped: `0.2.0` (2026-07-28)

**Prod runs `ghcr.io/caddismaster/budget-buddy:0.2.0`** — released, deployed and verified
2026-07-28. The first FEATURE release under the rebuilt envelope (`0.1.0` was a baseline
snapshot), and the first end-to-end exercise of issue → PR → Release → approval gate →
automated deploy carrying real behaviour.

Four PRs closing six issues: **#59** (#34 Ask dark mode + #35 logout confirm), **#61** (#32
schedule end dates), **#62** (#33 push reminders + daily server-side materialization), **#63**
(#58 release prep). Tests 579 → 635. Two additive migrations (`sql/31`, `sql/32`) applied
automatically before the image pull.

Deploy verified independently of the workflow: `schema_migrations` carries both files, the
pre-deploy dump is timestamped BEFORE them, the `db` container was NOT recreated (up 24h vs
web's 23s — `pull web` held), `/healthz` 200 over TLS, and `sw.js` serves `bb-static-v3` with
the push handlers. **Push delivery confirmed on the actual phone** — the one claim no test can
make.

`main` is currently level with the release; nothing is merged-but-undeployed.

Open and deliberately NOT being worked: **#52**, transient Docker Hub pull failures in CI —
record-and-watch. The one observed error was a *timeout*, not a `429`, so the obvious fix
(authenticate to Docker Hub) may not even apply; the trigger to act is a second occurrence WITH
its verbatim error. ⚠️ Do not "fix" it by switching buildx to the `docker` driver — `type=gha`
caching requires `docker-container`.

### ✅ Repository reboot — COMPLETE (2026-07-26 → 27)

⚠️ **Historical.** Kept because it explains why this repo starts at `0.1.0` with no earlier
tags, and why several conventions exist. The freeze it describes is OVER — feature work is
normal, and `0.2.0` shipped on 2026-07-28.

The app is mature and unchanged; the *envelope* around it was rebuilt
— issue→PR workflow, CI+CD in Actions, ghcr instead of Docker Hub, versioning reset to `0.x`.
**Golden rule during the move (now lifted): new envelope, same contents.** The only
sanctioned code changes were the non-root Dockerfile, a `/healthz` endpoint, and the `ruff`
formatting backlog. Everything else becomes an issue for `0.2.0`.

Where it stands:

- ✅ **Phase 1** — fresh repo, clean initial commit, verified runnable from a bare clone
  (`cp .env.example .env` → `docker compose up --build` → `./test.sh` green, 565 passing).
  Secret scanning + push protection ON. Old repo renamed `budget-buddy-archive` with a banner.
- ✅ **Phase 2** — README/CHANGELOG/CONTRIBUTING/VERSIONING/LICENSE/RUNBOOK, issue+PR templates,
  dependabot, CODEOWNERS.
- ✅ **Phase 3** — `lint` (ruff) + `docker-build` (boots the image, asserts `appuser`) added to
  CI; branch protection ON with three required checks.
- ✅ **Phase 4.5** — `/healthz`, `/admin/backup` hardened, least-privilege `budget_app` DB role.
- ✅ **Phase 4 (2026-07-27)** — Actions CD: `release.yml` + `rollback.yml`, a non-root `deploy`
  user, and the stack moved `/root/budget-buddy` → **`/opt/budget-buddy`** (a non-root user
  cannot own anything under `/root`; that move also closed a world-readable prod `.env`).
  Rehearsed end-to-end twice with throwaway pre-releases.
- ✅ **Phase 6 (2026-07-27)** — Droplet compose repointed at ghcr, **`v0.1.0` released,
  deployed and prod-verified**, legacy deploy scripts retired. The post-deploy `/healthz`
  check and a full rollback round-trip (`0.1.0` → `0.0.2-cd-test` → `0.1.0`) both pass, closing
  the two Phase 4 items that were blocked.
- ✅ **Phase 5/7 (2026-07-27)** — migration automation (`scripts/migrate.py`, tracked in
  `schema_migrations`, prod baselined) + issue migration and archiving. ⚠️ The numbered `sql/`
  files are **NOT replayable** — `schema.sql` is the only fresh-DB artifact, which is why
  `--baseline` exists.

Docker Hub stopped being the source of truth at the cutover, and its images were retired as a
fallback on 2026-07-28. **All eight phases are done and feature work is normal** — `0.1.0` was the baseline
snapshot, `0.2.0` (above) the first release to carry features through the same pipeline.

The pipeline is fully proven end-to-end: the post-deploy `/healthz` check passes, and a full
rollback round-trip (`0.1.0` → `0.0.2-cd-test` → `0.1.0`) succeeded, as did the manifest guard
rejecting a nonexistent version **before** any SSH. The two rehearsal images are retained in
ghcr as rollback targets.

Smoke aside carried over: POSTing `/insights/generate` without the form's year/month caches the
CURRENT month, not the last complete one — the UI always sends them; only bites hand-rolled
requests.

**Roadmap** — the issue tracker is authoritative. **The `0.2.0` milestone is CLOSED and
shipped** (see above). The `0.3.0` roadmap is not grouped yet and nothing is claimed.

New since the release: **#64** — let users report bugs/suggest features from inside the app,
auto-filing GitHub issues. ⚠️ It carries a design question that must be settled BEFORE building:
**this repo is public**, and a bug report about money tends to contain money. The issue lays out
four options (warn-and-post / never auto-attach context / a private feedback repo / email Sean
instead) and recommends the private repo. Do not start it by writing `app/github.py`.
**Deferred again on 2026-07-29** (Sean's call) — still unsettled, still not started.

**#108** (new, 2026-07-29) — the category doughnut is past its readable limit at seven
slices; show the top six and fold the tail into "Other", or switch the form to ranked
horizontal bars. Comes out of #83, which fixed the colour *collision* but could not fix
the form. Colour assignment is settled and should not be revisited — a fold must not
reintroduce set-membership-dependent colouring, which is what the tests guard.

Parked with triggers: **#36** budget-report-v2-reads-history (~Dec 2026, when the 6-mo window
sits fully inside logged history); **#8** Python 3.14 evaluation (unblocked by #7 now that CI
tests the shipped runtime, but its own change — Dockerfile surface, can break the image);
**#52** (see above). **#37** holds the unscheduled backlog: a tabbed AI panel, spending flags,
sinking funds, what-if simulator, tags. **Settled in `0.2.0`, do not re-open as a question:** the
#33 design fork — whether the daily job also runs the due-runners server-side — was decided YES
(Sean, 2026-07-28), against a recommendation to keep #33 read-only; the materialization now
happens daily for every user. Off the list: net worth over time (redundant with the
net-balance-trend chart). **CSV import remains dropped for good.**

### Release ledger

⚠️ **`CHANGELOG.md` (committed) is the authoritative record** — do not duplicate it here.
It carries `## [Unreleased]` plus a `## Prior history` summary of the `v1`–`v10.15.0` era,
whose full detail lives in the archived repo's tags and release notes.

## Testing

Run with **`./test.sh`** (args pass through to pytest, e.g. `./test.sh -k semimonthly`).
It runs in a throwaway `web` container on prod's Python 3.11 — no local venv;
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

**⚠️ Test-run economy is OBSOLETE — a full run costs ~17 SECONDS.** `./test.sh` runs `-n auto`
by default (#71, 668 tests, 15 workers on the Mac: 204s → 17.2s, measured across five
consecutive runs at ±0.15s). **Just run the full suite.** The old rationing advice — targeted
`-k` runs for iteration signal, one full run per commit as the gate, batching mechanical commits
coarser so each full run gates more work, capturing red output to a file because re-running was
expensive — was written when a run cost 2:40 and is now actively counterproductive. Delete it
from your habits; a `-k` run saves ~16 seconds and risks missing the thing that broke.

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

**706 tests in `tests/`.** Cross-cutting patterns: **no real API calls anywhere** — every
`ai.py::_call_*_model` seam (and `mailer.py::_call_resend`) is monkeypatched with canned
`SimpleNamespace` responses; every feature file asserts **user isolation**; route tests assert
anon → 302. What each file covers:

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
- `test_dashboard_merge.py` — /analytics redirect, Ask box on /, day-of-week chart, YoY gating, **the tojson regression** (a `</script>` category name arrives escaped); plus #83's colour slots (seven categories → seven distinct slots, a new category does not repaint, a month filter does not repaint, per-user) and a **stylesheet** assertion that `--series-1..8` are eight distinct hexes in BOTH mode blocks (the palette is CSS, so no request renders it)
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
   acceptance criteria (`.github/ISSUE_TEMPLATE/feature.yml`).
2. **Branch** off `main` as `<issue#>-short-slug`.
3. **Test locally — both:** `docker compose up --build` → verify at `http://localhost:5001`,
   AND `./test.sh` (full suite). CI does not click through the app.
4. **New behaviour gets a test that fails without it.** Jinja's silent-empty-string failure mode
   means content-asserting tests are the only net.
5. **Update `CHANGELOG.md`** under `## [Unreleased]`.
6. **Open a PR** with `Closes #<issue>` (one line per issue closed); squash-merge once CI is green.

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

A `sweeper` worker agent (Sonnet, tools: Read/Grep/Glob/Edit only) is defined in
`.claude/agents/sweeper.md`. Policy:

- **Delegate:** mechanical multi-file sweeps with an explicit written spec (attribute-access
  conversions, url_for conversions, find-replace-shaped work), and wide read-only recon. Write
  the spec to a scratchpad file (file list, old→new table, exclusions, expected per-file counts)
  and pass the worker the spec path — specs are re-runnable and diffable.
- **Do NOT delegate:** anything touching ownership guards, row shapes mid-refactor,
  SQL/migrations, AI seams (`_call_*_model`), or exception handling.
- **Every worker batch gets `./test.sh` (full suite) run by the orchestrator before commit**,
  plus a spot-grep that the swept pattern is gone. Workers never run tests, never commit, never
  edit outside their spec.
- **Batch sizing:** don't spin one worker per tiny area — target batches that gate meaningful
  work per full-suite run; per-file expected counts + the leftover grep localize failures.
- **Gotcha: agent definitions register at session START.** A `.claude/agents/*.md` created
  mid-session isn't callable until next session — fall back to a general-purpose agent with
  `model: sonnet` and the sweeper rules inlined.

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
  `COOKIE_SECURE=1` (Secure cookies + HSTS) — Droplet-only; must stay unset locally/tests. After
  editing `.env`, `docker compose up -d --force-recreate web`. `.env` is gitignored + never baked
  into the image.
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
  re-issue).
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
