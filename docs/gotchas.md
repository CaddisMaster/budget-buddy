# Key Gotchas

> Split out of `CLAUDE.md` on 2026-08-17. **Read this before changing anything non-trivial** —
> every entry is a defect that already happened once. `CLAUDE.md` keeps only the rules that apply
> to almost every change; the rest live here.

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
- ⚠️ **The session cookie carries `"<id>:<session_token>"`, and `load_user` MUST FAIL CLOSED**
  (#272). `models.User.get_id()` overrides `UserMixin`'s default, which returned the bare
  primary key — a cookie saying only "user 42", with nothing in it the server could
  invalidate. Since `login_user(..., remember=True)` is unconditional (v10.13, so an
  installed PWA does not re-prompt on launch) and no `REMEMBER_COOKIE_DURATION` is set,
  Flask-Login's default applies and a cookie authenticates for **365 days** — so before
  this, a password change revoked nothing for up to a year.
  **`app/__init__.py::load_user` and `models.User.get_id` are ONE unit** — change the
  format in either and every session in existence stops resolving.
  ⚠️ **`load_user` returns `None` for anything it cannot fully verify, and must never
  raise.** That is not defensive politeness: it runs before any route's own error
  handling, so an exception there is a **500 on every page for every logged-in user**
  until they clear their cookies. And the pre-#272 format is a bare `"42"`, so on the
  deploy that ships a format change, *every outstanding cookie* takes the failure path.
  ⚠️ A test asserting `load_user("<some id>") is None` **passes vacuously** when that id
  does not exist — a missing user returns `None` regardless, before any token is compared.
  Assert it against a REAL user's id (`test_the_pre_272_cookie_format_is_rejected` and
  `test_a_bad_token_on_a_REAL_user_is_anonymous_not_an_error` both do).
  ⚠️ **That vacuity hid a live defect for one commit**: `secrets.compare_digest` raises
  `TypeError: comparing strings with non-ASCII characters is not supported` on `str`, so
  `load_user("<real id>:ü")` RAISED — a 500 on every authenticated page, from the one
  function whose contract is never to raise. It is compared as **bytes** now
  (`.encode()` both sides). Every probe with a made-up id had returned `None` happily,
  because the user lookup failed first and the comparison never ran.
  ⚠️ Rotating `users.session_token` is what signs a device out, so any write that rotates
  it must **re-issue the acting session** (`auth.change_password` calls `login_user()`
  again) — otherwise the user is logged out by their own password change, which reads as
  a bug rather than as security
- All data tables have `user_id` FK — every SELECT/INSERT/UPDATE/DELETE must be scoped to `current_user.id`
- ⚠️ **The History pending-pin is sorted in PYTHON, never in SQL** (#86). `_load_history`
  seeds its balance walk from a SUM of every filtered row *older* than the page — and the
  seed query **defines "older" by repeating the page query's `ORDER BY`** with
  `OFFSET offset+per_page`. So those two `ORDER BY` clauses are ONE coupled unit: an
  `is_pending DESC` prefix on either does not merely reorder the display, it redefines
  which rows count as older, and the balance breaks for the pinned rows AND every row
  beneath them — invisible until pagination or a filter is active. Both queries and the
  walk are byte-identical to pre-#86; only the finished list is rearranged.
  ⚠️ **Pending rows are also never EXCLUDED from the paged query** for the same reason —
  they must stay in the walk, because a pending row counts normally in every figure. Every
  rearrangement happens strictly *after* the walk, which is what makes it safe.
  ⚠️ **The pin was page-scoped until #210 and is now page-1-scoped** (2026-08-17): page 1
  runs a second query for *every* pending row matching the filters and prepends them, and
  later pages render none, so each appears exactly once. The old behaviour rested on a
  premise that did not survive a real account — "a pending row is entered when the charge
  happens, so it is on page 1 in practice" — and a 40-day-old hold sat on page 4 unseen.
  `test_pending_transactions.py::test_posted_balances_are_unchanged_by_a_pending_row` and
  `::test_pinning_to_page_one_does_not_change_posted_balances` are the net
- **`is_pending` is a DISPLAY flag and the exact OPPOSITE of `is_adjustment`** despite the
  identical type/default: it excludes a row from **nothing** (dashboard, budgets, insights,
  forecasts, running balance all count it — the money did leave the account). Do **not** add
  it to the ~23 `is_adjustment = false AND is_transfer = false` filter lists. Set by a
  checkbox on the **create form only**; cleared by `POST /transactions/<id>/mark-posted`
  (clears only, never sets). `edit_transaction`'s UPDATE deliberately never mentions the
  column, which is what makes "editing the amount doesn't clear the flag" free and keeps
  `TxnEditRow` unchanged. Pending rows render an em dash in the balance cell
- ⚠️ **The charts are ApexCharts 4.7.0 — the LAST MIT RELEASE** (#234). 5.x moved to a dual
  licence and 6.x ships a `LicenseEnforcer` that watermarks charts using premium features, with
  terms that bind on annual revenue. `tests/test_design_system.py` asserts the vendored licence
  still says MIT and that no enforcer is in the bundle, because "upgrade the chart library" is
  otherwise an ordinary-looking dependency bump that changes what this public repo is allowed to
  ship. Chart.js was retired in the same change; the swap was Sean's call, made knowing the
  restyle-in-place option was cheaper
- ⚠️ **A chart must be constructed on FIRST OPEN of the `<details>`, never at parse time** — a
  chart library measures its container, and inside a closed `<details>` that is 0. This survived
  the library swap because it is a property of the drawer, not of Chart.js. `initCharts()` is
  called both immediately (desktop, where the drawer starts open) and on `toggle`, guarded by
  `chartsInitialized`
- ⚠️ **No chart may name a colour** — every one comes through `cssVar()`. The pre-#234 script
  hardcoded `#378ADD`/`#1D9E75`/`#E24B4A`, which *equalled* `--accent`/`--success`/`--danger` when
  written, so it looked correct and was the one surface in the app that could not follow a token
  change. `test_the_chart_script_holds_no_hardcoded_colour` scans the script with `//` comments
  stripped — the rule is about code, and the comment there names the retired literals on purpose
- ⚠️ **Green/red is legal ONLY with secondary encoding.** Income-vs-expenses separates at
  ΔE 7.2 under deuteranopia — inside the 6–8 floor band — so it ships with the gap between paired
  bars and a text legend; the account chart uses position (left/right of zero) instead. Validated
  with the dataviz skill's `validate_palette.js`, both modes, rather than by eye. Budget-vs-actual
  deliberately does NOT use green/red: painting Actual red reads as a warning even for a category
  comfortably under budget
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
  reverse **because the fold caps the drawn set at `PALETTE_SIZE` real slices against 8 hues**,
  so probing for a free slot always succeeds. Do not restore `% PALETTE.length` colouring.
  ⚠️ **`fold_chart_tail`'s limit and `assign_series_slots`' palette are ONE number now**
  (`main.PALETTE_SIZE`, #257) and must not be re-split: a limit ABOVE the palette means more
  drawn rows than hues, pass 2 finds no free slot, and the collision this exists to prevent
  comes back
- ⚠️ **The fold cuts at `main.PALETTE_SIZE` (8), NOT at 6** — changed in #257, and the old
  number is the thing to watch for in older comments. #108 chose six because *"a doughnut is
  past its readable limit around six slices"*; **#223 deleted the doughnut**, and a
  server-rendered list of ranked bars is no less readable at eight rows than at six. The
  premise expired and the number stayed — measured on real data, an 8-category account had
  two categories folded into grey every month and one had never been drawn once in five.
  **Before defending a constant, check the thing it was chosen for still exists.**
  `/categories`' swatch (#257) depends on this: at six, most categories had no colour to show
- **The chart folds to top-`PALETTE_SIZE` + "Other"** (#108/#110/#257) via pure `main.fold_chart_tail()`, in the
  **payload builder, NOT the SQL rollup** — every other surface needs complete per-category
  figures, and the card total comes from `cash_flow` so the fold can't move it. Applied to BOTH
  pill views (one canvas, one palette, one slot map). ⚠️ **`/categories` must read a category's
  colour from `main.category_slot_map()`, never from `creation_index % PALETTE_SIZE`** (#257):
  the latter is the *preferred* slot, which a category receives only when nothing contests it,
  so the swatch would disagree with the chart exactly when a collision occurred — and silently.
  Verified: against that shortcut, `/categories` claims slot 2 for a category the chart draws in
  slot 1. ⚠️ **The folded slice is coloured off its
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
- ⚠️ **The version stamp is a BUILD ARG, and `/healthz` must never carry it** (#305, and it
  reverses what `CLAUDE.md` proposed for months). Two halves. **(1)** `Dockerfile` declares
  `ARG APP_VERSION`/`APP_COMMIT` with a `dev` default and matching `ENV`s, both in **`base`** so
  `prod` and `dev` inherit — an `ENV` below `FROM base AS dev` stamps only the image nobody
  ships, and no local run can catch that, because local dev *is* the dev stage. Reading the
  Droplet's `TAG` instead was rejected: that records what compose was *told* to pull, and a
  stale or hand-restored `.env` makes it lie, which is the exact failure #190 could not see.
  **(2)** It renders admin-only on `/settings`. `main.healthz`'s docstring and rule 1 above
  `admin.integration_status` both say that endpoint exists to leak nothing, and it is the one
  URL guaranteed reachable by anyone — naming the build there hands over a CVE-matching hint.
  `tests/test_version_stamp.py` states the boundary as a test rather than as prose, because
  "just put the version on /healthz" is an obvious-looking improvement that this file's own
  core once recommended. ⚠️ `--build-arg APP_VERSION=` sets the variable to the EMPTY STRING
  rather than leaving it unset, so the reader strips and falls back rather than testing
  `os.getenv(var, DEFAULT)`; an empty version renders as a blank cell that reads as a lost
  version rather than an unstamped build. ⚠️ **`rollback.yml` warns where `release.yml` fails** —
  every pre-#305 image reports nothing and those are exactly what a rollback reaches for, so
  strictness there would refuse a rollback mid-incident. A *wrong* stamp still fails both.
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
- ⚠️ **THAT TRAP RECURRED ON 2026-08-17 (#218), in the same file, past the warning above** —
  a new real-file test in `test_release_prep.py` was written without the guard, twenty lines
  above the `REAL_CHANGELOG` / `_NOT_IN_IMAGE` constants that exist for it, and `main` went red
  twice before anyone noticed. **Mask (2) is now closed:** `ci.yml`'s `image` filter matches
  `tests/` and `.github/workflows/ci.yml` as well, so a `tests/`-only PR runs the in-image suite
  before merge. Duration tell is now ~1m skipped vs ~3m ran. Two things follow:
  **(a)** use the existing constants rather than hand-rolling a path, and note the `skipif`
  decorator is evaluated at **import**, so such a test must be defined *below* the constant —
  which puts it in the real-file section at the bottom of the module, where it belongs;
  **(b)** a real-file test must not pin state the thing it tests will change. The first cut of
  that test rolled the literal `0.7.0` against the real changelog and went red **on release
  day**, when that version came to exist and `[Unreleased]` emptied. Seed the input and roll a
  version that can never exist. ⚠️ The general lesson, since prose evidently did not prevent
  this: **a documented trap is not a guard** — change the mechanism
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
- ⚠️ **The AI-card read-state collapse is GONE** (#262). It was `<details>` +
  `data-ai-key`/`data-generated` + a localStorage key, driven by `initAiCollapse` in
  base.html. #232 folded three of its four cards into Home's one panel; the Goal Coach was
  the last consumer, and the script, the `summary.ai-head` chevron CSS and
  `tests/test_ai_collapse.py` were deleted **with** it rather than left as dead JS shipped
  on every page — which is exactly what the previous version of this entry instructed.
  ⚠️ A generate route that caches narration no longer needs `RETURNING created_at` for
  read-state purposes; nothing reads a `data-generated` timestamp any more
- ⚠️ **Home's AI panel re-renders WHOLE, and its gate is shared** (#232, `partials/_ask_panel.html`).
  Two things are load-bearing and neither is obvious from the markup. **(1)** `POST /insights/read`
  returns the entire panel, Ask box included, and the panel's root keeps `id="ask-panel"` — the
  read and the input are one feature, so a fragment carrying only the paragraph would swap the
  input out of the page and strand every later Refresh. **(2)** `insights.month_worth_reading()` is
  pure and called from BOTH sides: `main.py` decides whether the panel asks for a read
  (`hx-trigger="load"`), the route decides whether to answer. Two separately-written gates
  eventually disagree, and the failure mode is a panel that asks on every load and is refused every
  time — a paid round-trip per page view, silent. The dashboard passes month-TO-DATE totals and the
  route passes the whole month's, which is a superset, so the route can never refuse a read the
  dashboard just asked for. ⚠️ And the dashboard GET must never generate: a page load that waits on
  a model call is both slow and billable, which is why the empty state defers instead.
