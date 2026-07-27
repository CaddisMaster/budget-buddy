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
  __init__.py        # Flask app + extensions (Login, Bcrypt, Limiter, CSRF); registers the 16 blueprints; cookie flags + @after_request security headers (Secure/HSTS gated on COOKIE_SECURE); starts the weekly-digest APScheduler (gated on ENABLE_DIGEST_SCHEDULER=1 + mail_enabled(); safe only under single-worker gunicorn); |money template filter (thousands-sep, emits the NUMBER only — display templates ONLY: AI fact-builders / chart |tojson payloads / form input values stay raw, a comma'd value fails parse_positive_amount); css_v + brand_svg Jinja globals (both computed once at startup: the style.css content hash, and icons/icon.svg inlined |safe in base.html so sidebar mark + favicon share one source)
  mailer.py          # outbound email seam (Resend). mail_enabled() gate (twin of ai_enabled()), send_email(), MailError, single _call_resend() network seam tests stub. NOT named email.py (would shadow stdlib)
  db.py              # get_db_connection() + db_cursor() context manager (commit/rollback/close); db_cursor yields a NamedTupleCursor — rows read row.field (positional still works), SELECT columns must be uniquely named (alias with AS)
  helpers.py         # is_htmx(), hx_toast(), recent_months(), ai_enabled(); parse_positive_amount() (THE shared amount validator, rejects NaN/inf — every amount form routes through it) + parse_signed_amount() (same guard, allows negative/zero — bank balances); most_recent_sunday() (the weekly period key shared by the digest idempotency guard AND the agent's run key — lives here because digests.py imports agent.py); param parsers: parse_month_param() (every ?month read), parse_page_param() (every ?page read), parse_int_param() (every posted FK id); GENERIC_ERROR (the one user-facing message for unexpected write failures — raw exception text goes to current_app.logger.exception, never the browser)
  ai.py              # ALL model calls. parse_transaction_text() (NL quick-add), generate_insight(), generate_forecast(), answer_question() (Haiku multi-turn TOOL-USE loop), classify_transactions() (Sonnet batch), propose_budgets() (the model DOES propose amounts — pure _normalize_budget_proposals() re-resolves categories + SNAPS amounts into a facts-derived range), generate_digest(), coach, investigate_finances() (the Money agent's AUTONOMOUS loop — Sonnet, AGENT_MAX_TURNS=12, ends ONLY via the strict submit_findings tool intercepted locally; one nudge for a text-only turn then ParseError; grounding guard: submit with zero successful data-tool calls → ParseError; pure _normalize_findings() caps at 3 + drops unevidenced). Each feature has its OWN isolated _call_*_model() network seam so tests stub independently; pure _normalize()/_match_id() re-resolve ownership reading rows by ATTRIBUTE (.id/.name — the quick-add account feeder dual-names account_id AS id / account_name AS name). ai.py NEVER touches the DB or sees a user id — tool dispatch is a callback the blueprint supplies
  models.py          # User class with UserMixin, get_by_id, get_by_username
  blueprints/        # all routes, one module per area, registered with NO url_prefix
    auth.py          # login, logout (POST-only — GET was CSRF-able; base.html renders a form button styled as a nav link), change_password (72-BYTE bcrypt cap; reached via its Profile-page link, no nav entry), profile + digest opt-in; login rate-limit + constant-bcrypt (no enumeration) + session.clear() on login; login_user(remember=True) — the installed-PWA case
    main.py          # index() at '/' IS the dashboard (endpoint name main.index preserved for the redirect call sites; due-runners fire on '/'); dashboard() = 302 stub → / carrying ?month; service_worker() serves /sw.js from the ROOT (a SW's scope is capped at its URL dir — /static/sw.js could never control '/'). Pure _advance_past()/upcoming_occurrences() walkers over compute_next_due (the digest's upcoming-week enumerator imports them lazily). Dashboard owns the day-of-week + year-over-year queries (yoy only when a month is filtered AND last year has data); chart payloads are PLAIN LISTS rendered with |tojson (NOT json.dumps + |safe — script-tag breakout); an income-by-category rollup feeds the Spending card's Expense/Income pill toggle (hidden when empty)
    transactions.py  # transaction CRUD (inline), CSV export (_csv_safe() formula-injection sanitizer), render_history_tbody(), quick-add parse route; owns pure compute_next_due() + the shared validate_category_account() write-side ownership guard (both reused by schedules.py); Auto-Categorize (count_uncategorized/_load_cleanup_candidates — expense-scoped — + POST /transactions/cleanup/scan|apply, the History banner); Bulk edit (POST /transactions/bulk/category|delete — full guard stack + is_transfer=false so a transfer pair can't be half-edited; transfer rows get no checkbox in the UI either; selection is page-scoped, vanilla-JS bar in history.html); _load_history seeds the running-balance walk with the signed SUM of all filtered rows OLDER than the page slice (pages connect; filtered views carry the matching rows' net)
    categories.py    # category CRUD (inline); kind ('expense'|'income') on create/edit — flipping a BUDGETED category to income clears its budget in the same txn (logged via budgets.record_budget_change, lazy import)
    accounts.py      # account CRUD (inline); ACCOUNT_ROW_SQL (THE canonical balance formula, reused by ask.py — namedtuple read by NAME everywhere, column order doesn't matter); pure Jinja globals credit_utilization() (warn ≥30% / danger ≥80%, pct uncapped / bar capped 100, Decimal→float) + monthly_interest(balance, apr) (None when apr unusable OR debt ≤ 0 — THE gate every surface checks; ~monthly = debt × apr/100/12, "~" wording); _parse_credit_limit()/_parse_apr() (blank → NULL; apr REJECTS > 100 — the units-typo guard); credit_card_utilization_facts(user_id) (per-card facts for Insight/Digest — a card qualifies with usable utilization OR interest, per-key presence); Balance check-in (GET/POST /accounts/<id>/checkin — recomputes balance server-side in a FOR UPDATE-locked txn, inserts ONE is_adjustment transaction closing the gap, always stamps last_checked_in); edit error paths re-render via account._replace(...) echoing the RAW posted string
    budgets.py       # budget cockpit (set/clear) + compute_budget_suggestions/_vs_actual helpers; AI Budget Review (compute_budget_review_facts() + load_budget_rows() + POST /budgets/review/scan|apply); Budget report (pure build_budget_report() hit/miss grid + streaks + ±10% 3-vs-3 trend, load_budget_report() calendar-aligned last-6-COMPLETE-months — NOT the review facts' rolling window; only SAVED budgets grade, vs the CURRENT amount); record_budget_change() appends to budget_history at all THREE write points (set/clear/review-apply), before the upsert/delete in the same txn, no-ops skipped — writer only, nothing reads the log yet
    analytics.py     # redirect stub: GET /analytics → 302 / carrying ?month=. No @login_required on the hop — the target enforces auth
    admin.py         # user mgmt, create user, backup, settings
    transfers.py     # account transfers (linked income/expense pair) + recurring transfers (transfer_schedules CRUD + run_due_transfers() materializing a paired transfer per due date)
    goals.py         # goals (save + payoff) + compute_goal_projection(..., apr=None) — payoff goals feed the linked card's apr (GOAL_SELECT carries a.apr AS account_apr; _goal_view reads rows by ATTRIBUTE): est_monthly_interest always in the dict (None unless apr + debt), pace date = bounded 600-month simulation (pace ≤ interest → None + Behind), required_per_month amortized; Goal Coach (compute_goal_coach_facts() + load_goal_coach() + POST /goals/coach/generate, cached in goal_coach); GET /goals gates the card on ai_enabled() + in-progress goals
    schedules.py     # recurring income/expense schedules (Scheduled tab); run_due_schedules() + compute_initial_semimonthly_due()
    insights.py      # monthly AI digest card on the dashboard; compute_month_facts() (deterministic figures) + POST /insights/generate (narrate via ai.py, cache in insights table)
    forecasts.py     # month-ahead AI projection card (twin of insights.py); pure project_expenses() day-weighted run-rate + compute_forecast() (MTD actuals + remaining schedules) + POST /forecasts/generate
    ask.py           # "Ask your finances" tool-use box. The SECURITY BOUNDARY: 9 read-only, user-scoped, fixed-parameterized query tools (ASK_TOOLS, incl. recent_transactions — date-range listing, no text filter, carries is_transfer/is_adjustment flags); dispatch() validates every arg (strptime months/dates, clamped limit, category resolved only against the user's own rows) and FORCES user_id to the caller; POST /ask runs ai.answer_question() with a dispatch closure; the Money agent reuses TOOL_SPECS + dispatch verbatim. total_for_category sums by the category's kind (income-kind → total_received); list_categories returns {name, kind}. No cache (live query)
    digests.py       # Weekly Email Digest. compute_digest_facts() (reuses compute_month_facts + a next-7-days enumeration via main.upcoming_occurrences — EVERY occurrence in the window counts, a weekly bill due twice counts twice) → ai.generate_digest() narrates once → mailer.send_email(); send_weekly_digests() runner (idempotent by users.last_digest_sent_on, per-user try/except) + `flask send-digests` CLI; also runs the Money agent per recipient (reuses the week's cached run, own try/except — an agent failure never blocks the email)
    agent.py         # the Money agent (first AUTONOMOUS tool-use). run_money_agent(user_id) binds ask.dispatch into ai.investigate_finances (scheduler-thread safe, no current_user) and upserts agent_runs on (user_id, week-Sunday); load_agent_run() (latest, or a specific week for the digest so it never mails stale findings); POST /agent/run (3/min) drives the dashboard card, ParseError → previous card + error toast
  templates/         # Jinja2 HTML templates, all extend base.html
    partials/        # HTMX fragments (_X_row.html, _X_edit_row.html, _transactions_tbody.html, cards)
    emails/          # weekly_digest.html (rendered server-side, sent via Resend)
  static/            # style.css, htmx.min.js, chart.umd.min.js (Chart.js 4.5.1 pinned/vendored), manifest.json + sw.js + icons/ (PWA — SW is stale-while-revalidate on /static/ GETs ONLY, everything else passes through; bump the 'bb-static-vN' cache name in sw.js to purge — currently v2). icons/icon.svg = the coin-with-$ brand mark (favicon + sidebar via brand_svg); icons/icon-maskable.svg = full-bleed BUILD SOURCE only (not served) — maskable-512 AND apple-touch-icon rasterize from it (apple-touch must be full-bleed: iOS composites WHITE behind transparent corners); rasters regenerated via macOS qlmanage
sql/                 # Numbered migration files + schema.sql (clean single-file schema)
scripts/             # ingest.py, clean.py, insert.py data pipeline (own requirements.txt — pandas lives THERE, not in the app image)
landing/             # Static landing page at seandesmet.com
.github/workflows/   # ci.yml (lint + pytest on postgres:16 + image builds/boots as appuser, on every push/PR); release.yml (published Release → build+push ghcr → smoke the PUSHED image → approval gate → SSH deploy → verify /healthz); rollback.yml (workflow_dispatch a version → redeploy that exact tag)
```

## Database Tables

- `transactions` — amount, description, transaction_date, category_id, account_id, transaction_type (income/expense), is_adjustment (exclude from analytics), is_transfer + transfer_group_id (transfer legs), user_id, created_at. **`is_recurring`/`frequency`/`next_due`/`recur_second_day` are LEGACY** — recurrence moved to `schedules`; kept (always default) only so the History row shape is unchanged
- `schedules` — recurring income/expense templates: amount, description, category_id, account_id, transaction_type, frequency, anchor_day + second_day (semi-monthly), next_due, is_active, user_id, created_at. **Not a ledger row** — `run_due_schedules()` materializes a plain transaction on each due date (going forward, no back-fill) and advances `next_due`
- `transfer_schedules` — recurring **transfer** templates (the transfer twin of `schedules`): amount, description, **from_account_id + to_account_id** (no category), frequency, anchor_day + second_day, next_due, is_active, user_id, created_at. Separate table (a transfer needs two accounts). `run_due_transfers()` (transfers.py) materializes a **paired transfer** (linked expense+income legs sharing one `transfer_group_id`, both `is_transfer=true`) per due date, looping to catch up. Reuses `compute_next_due()`/`compute_initial_semimonthly_due()`, all six frequencies
- `insights` — cached monthly AI narration, one row per user per month: year, month, content (JSON `{summary, tips[]}`), model, user_id, created_at; **UNIQUE(user_id, year, month)** upsert. **Stores only the narrative** — figures are recomputed each load (`compute_month_facts()`), never persisted
- `forecasts` — cached month-ahead projection, **identical shape to `insights`** (separate table, not a `kind` column). Narrative only; figures recomputed by `compute_forecast()`
- `goal_coach` — cached goal-pace narration, identical shape to the twins, pointed at the Goals page. Monthly-keyed to reuse the load/upsert even though goals aren't month-scoped
- `agent_runs` — cached Money-agent weekly runs: user_id, **period_start (the week's Sunday, via `helpers.most_recent_sunday` — same boundary as `last_digest_sent_on`)**, content (JSON `{summary, findings:[{title, detail, evidence}], tools_used}`), model, created_at; **UNIQUE(user_id, period_start)** upsert. Stores only the narrative + cited evidence text — no figures are trusted from it
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
- **Due-runners are LAZY, login-triggered:** `run_due_schedules(user_id)` + `run_due_transfers(user_id)` fire on GET `/` (dashboard), `/transactions`, `/scheduled` (schedules only), `/transfers` (transfers only) — lazy-imported to avoid the schedules↔transactions import cycle. They materialize due rows for the CURRENT user only, **looping to catch up**, then advance `next_due` past today; new schedules seed `next_due` forward so setup never back-fills. A user who never logs in gets nothing materialized (and an understated digest/agent view) — the walkers handle a stale `next_due` (`_advance_past`), so enumeration-based surfaces stay correct. Recurring is configured ONLY on the Scheduled tab — Add Transaction is one-off entries only
- **Due-runner locking:** the due-row SELECTs are `FOR UPDATE` — gunicorn serves on 4 threads, and without the lock two simultaneous page loads could both materialize the same occurrence. Keep the lock if those queries are ever touched
- All data tables have `user_id` FK — every SELECT/INSERT/UPDATE/DELETE must be scoped to `current_user.id`
- **Amount validation:** every form amount goes through `helpers.parse_positive_amount()` — `float('nan')` passes a plain `<= 0` check and Postgres stores NaN in numeric, poisoning every SUM(). Never hand-roll `float(x); if x <= 0`
- **Param validation:** same rule for query/form params — `?month` → `parse_month_param()`, `?page` → `parse_page_param()`, posted FK ids → `parse_int_param()`. A raw string into a psycopg2 `%s` against an int column raises (= 500)
- **Write-side FK ownership:** when a form posts a `category_id`/`account_id`, validate it belongs to the user *before* the INSERT/UPDATE — `validate_category_account()` in transactions.py, folded into the route's validation-error path. Used by transaction new/edit, schedule create/edit, bulk edit, cleanup apply
- **Error messages:** unexpected write failures show `helpers.GENERIC_ERROR` and log the real exception via `current_app.logger.exception()` — never flash/toast `str(e)` (psycopg2 text leaks constraint names/SQL). The two FK-delete sites keep their friendly "Cannot delete — in use" branch
- **Cache-bust is automatic:** the stylesheet `?v=` is `css_v` (startup md5 of style.css) — nobody bumps a number. base.html AND login.html (which doesn't extend base) both read it
- **Security headers + cookies** (`app/__init__.py`): one `@app.after_request` sets X-Frame-Options/X-Content-Type-Options/CSP `frame-ancestors 'none'`/Referrer-Policy (+ HSTS in prod). Cookies are `HttpOnly` + `SameSite=Lax`; **`Secure` + HSTS gated on `COOKIE_SECURE`** (Droplet-only) so local HTTP dev + tests still work. CSP is `frame-ancestors` only — a full policy would break the inline scripts
- Flask-Limiter: 60 req/min/IP, in-memory storage (single Gunicorn worker)
- Templates read rows by **attribute** (`t.amount`, `account.credit_limit`); row partials reuse the list query's row shape. The three remaining `[0]` indexes in templates are Python lists (flash messages, top_categories, remaining_items), not rows
- CSRF: Flask-WTF CSRFProtect — token on POST forms, plus a single `hx-headers='{"X-CSRFToken": ...}'` on `<body>` in base.html covering every HTMX post/put/delete
- **Flex overflow:** `.main-content` is a flex item and MUST keep `min-width: 0` — without it a wide table inflates the column past the viewport instead of scrolling inside its `.table-wrapper`. Same hotfix owns the iOS status-bar rules: `viewport-fit=cover` + `html` background + safe-area paddings (keep `theme-color` for Android; never `black-translucent`)
- **PWA/iOS testing:** responsive mode does NOT test iOS — installed-PWA bugs only show on the actual phone over HTTPS
- **AI-card collapse:** the four AI narration cards are `<details>` with `data-ai-key` + `data-generated` (the cache row's `created_at.isoformat()`; `initAiCollapse` in base.html + localStorage `bb-ai-seen:<key>` drive read-state). Generate routes must get the timestamp via `RETURNING created_at` — a route-local `datetime.today()` makes every regenerate read as new twice. Server renders CLOSED except empty-state (Generate must work without JS) and `just_generated` fragments. The What's-new strip says "weekly money check", NOT "Money agent" — a dashboard test asserts the agent CARD's absence by that exact string

## Current Status

### ⚠️ Repository reboot in progress (started 2026-07-26)

This repo is **new**. The app is mature and unchanged; the *envelope* around it is being rebuilt
— issue→PR workflow, CI+CD in Actions, ghcr instead of Docker Hub, versioning reset to `0.x`.
**Golden rule: new envelope, same contents — do NOT refactor the app during the move.** The only
sanctioned code changes are the non-root Dockerfile (done), a `/healthz` endpoint, and the `ruff`
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
- ⏳ **Phase 5/7** — migration automation (deliberately last), then issue migration + archiving.

**Prod now runs `ghcr.io/caddismaster/budget-buddy:0.1.0`** — released, deployed and verified
2026-07-27. The Docker Hub image is no longer the source of truth; it survives only as an
emergency fallback. **Feature work is UNFROZEN** — the reboot's remaining phases (5 and 7) do
not touch the app, so `0.2.0` work can start.

The pipeline is fully proven end-to-end: the post-deploy `/healthz` check passes, and a full
rollback round-trip (`0.1.0` → `0.0.2-cd-test` → `0.1.0`) succeeded, as did the manifest guard
rejecting a nonexistent version **before** any SSH. The two rehearsal images are retained in
ghcr as rollback targets.

Smoke aside carried over: POSTing `/insights/generate` without the form's year/month caches the
CURRENT month, not the last complete one — the UI always sends them; only bites hand-rolled
requests.

**Roadmap:** next up is **bill-due PUSH reminders** (Web Push to the installed PWA — VAPID +
pywebpush + a `push_subscriptions` table + a daily APScheduler job walking
`upcoming_occurrences()`; open design fork: whether that job also runs the due-runners
server-side, ending lazy-login-only materialization), plus two small items: the **Ask dark-mode
fix** (phantom `--bg-subtle` token in `_ask_answer.html:5` — move to a `.ask-answer` rule on
`var(--surface-2)`) and a **logout confirmation** (base.html POST form). Parked with triggers:
budget-report-v2-reads-history (~Dec 2026, when the 6-mo window sits fully inside logged
history); a tabbed AI panel. Shortlist: spending flags, sinking funds, what-if simulator, tags.
Off the list: net worth over time (redundant with the net-balance-trend chart). **CSV import
remains dropped for good.**

### Release ledger

⚠️ **`CHANGELOG.md` (committed) is the authoritative record** — do not duplicate it here.
It carries `## [Unreleased]` plus a `## Prior history` summary of the `v1`–`v10.15.0` era,
whose full detail lives in the archived repo's tags and release notes.

## Testing

Run with **`./test.sh`** (args pass through to pytest, e.g. `./test.sh -k semimonthly`).
It runs in a throwaway `web` container on prod's Python 3.11 — no local venv;
`requirements-dev.txt` adds just `pytest`. Needs the dev `db` container up (route/isolation
tests hit it). Also runs in **GitHub Actions CI** on every push/PR (`.github/workflows/ci.yml`,
`postgres:16` service + `schema.sql`).

**Gotcha — `python -m pytest`, never bare `pytest`:** the image runs as non-root `appuser`, so
pip puts console scripts in `/home/appuser/.local/bin`, which is NOT on `PATH`. Bare `pytest`
fails with `not found`. (Harmless side effect: pytest can't write its cache to `/app` — that
directory is root-owned because `WORKDIR` created it before the `COPY --chown`. Two warnings per
run, no impact.)

**Test-run economy (a full run costs ~2:40):** on multi-commit passes, use targeted `-k` runs
for fast signal while iterating and spend ONE full run as each commit's gate; batch mechanical
commits coarser so each full run gates more work; when debugging a red run, capture the output
to a file the first time. Do NOT skip the full run where the change is global (a cursor-factory
flip, a template-wide sweep) — the suite's content assertions are the only net for Jinja's
silent-empty-string failure mode. When planning a sweep, grep for the failure SHAPE (e.g.
`\$[0-9]{4}`), not just assertions near the feature — the `|money` sweep broke two credit-limit
assertions the feature-local grep missed. If suite time becomes the bottleneck: `test.sh`
re-`pip install`s each run (~15s recoverable); pytest-xdist would halve the serial run BUT
conftest's fixed `__pytest__` usernames collide across workers — per-worker prefixes first.

**565 tests in `tests/`.** Cross-cutting patterns: **no real API calls anywhere** — every
`ai.py::_call_*_model` seam (and `mailer.py::_call_resend`) is monkeypatched with canned
`SimpleNamespace` responses; every feature file asserts **user isolation**; route tests assert
anon → 302. What each file covers:

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
- `test_dashboard_merge.py` — /analytics redirect, Ask box on /, day-of-week chart, YoY gating, **the tojson regression** (a `</script>` category name arrives escaped)
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
- `test_hardening.py` — `_csv_safe` + CSV end-to-end, security headers, cookie flags, constant-bcrypt path
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
6. **Open a PR** with `Closes #<issue>`; squash-merge once CI is green.

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
- **Emergency route back to Docker Hub.** `deploy.sh`/`promote.sh`/`docker-compose.staging.yml`
  were deleted at the cutover (they built and promoted the *Docker Hub* image, which prod no
  longer uses). The old image still exists at `caddismaster/budget-buddy:latest` (`v10.15.0`
  code) — if ghcr were unreachable, edit the Droplet's compose `image:` back to that and
  `docker compose up -d`. Retrieve the scripts from git history if ever needed:
  `git show v0.1.0:deploy.sh`. Delete this note once `0.1.0` has been stable a while.
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
