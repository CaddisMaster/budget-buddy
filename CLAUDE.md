# Budget Buddy

A personal finance tracking web app built with Flask, PostgreSQL, and Docker. Deployed at
budget.seandesmet.com on a DigitalOcean Droplet behind Nginx + Gunicorn with Let's Encrypt SSL.

> ⚠️ **This file is the always-loaded core and is deliberately kept small.** The detail lives in
> `docs/`, and those files are *not* loaded automatically — **read the relevant one before working
> in that area.** Each entry below is a defect that already happened at least once, so a pointer
> being unread is the failure mode to guard against.
>
> | Read this | Before you |
> |---|---|
> | [`docs/gotchas.md`](docs/gotchas.md) | change anything non-trivial — the full load-bearing invariant list |
> | [`docs/architecture.md`](docs/architecture.md) | need the full module map or exact table columns |
> | [`docs/testing.md`](docs/testing.md) | add or change tests, or sweep a pattern across files |
> | [`docs/status.md`](docs/status.md) | start a session — what's on `main`, settled decisions, release lessons |
> | [`docs/delegation.md`](docs/delegation.md) | spawn a worker agent, or touch issue triage |
> | [`docs/deployment.md`](docs/deployment.md) | cut a release, add an env var, or apply a migration |
> | [`RUNBOOK.md`](RUNBOOK.md) | touch the server itself |
> | [`CONTRIBUTING.md`](CONTRIBUTING.md) | need the workflow rationale rather than the rule |

## Tech Stack

- **Backend:** Python / Flask, psycopg2, Flask-Login, Flask-Bcrypt, Flask-Limiter, Gunicorn
- **Database:** PostgreSQL (Docker container)
- **Frontend:** Jinja2 templates + HTMX (vendored `htmx.min.js`, inline CRUD), Chart.js (vendored), vanilla CSS
- **Infrastructure:** Docker Compose, DigitalOcean, Nginx, Certbot

## Project map

Full detail — every module's responsibilities and its traps — is in
[`docs/architecture.md`](docs/architecture.md).

```
app/
  __init__.py      # app + extensions; registers the 18 blueprints; security headers; scheduler; |money filter; css_v/brand_svg globals
  db.py            # get_db_connection() + db_cursor() context manager (NamedTupleCursor)
  helpers.py       # is_htmx/hx_toast/ai_enabled; THE param + amount validators; GENERIC_ERROR
  models.py        # User (UserMixin)
  ai.py            # ALL model calls, one isolated _call_*_model() seam each. NEVER touches the DB
  mailer.py        # outbound email seam (Resend)          — single _call_resend() seam
  pusher.py        # outbound Web Push seam                — single _call_webpush() seam
  github.py        # outbound GitHub issue seam (stdlib urllib, NOT requests)
  jobs.py          # scheduled-job bookkeeping (job_runs); the only seam module that DOES touch the DB
  blueprints/      # all routes, one module per area, registered with NO url_prefix
  templates/       # Jinja2, all extend base.html; partials/ = HTMX fragments; emails/ = digest
  static/          # style.css, vendored JS, PWA manifest + sw.js + icons
sql/               # numbered migrations + schema.sql (clean single-file schema)
scripts/           # ingest/clean/insert pipeline, migrate.py, seed_dev.py, release_prep.py, restore_check.py
landing/           # static landing page at seandesmet.com
.github/workflows/ # ci.yml, release.yml, rollback.yml, changelog.yml, claude-triage.yml
docs/              # the reference detail this file points at
```

**Blueprints** (`app/blueprints/`, all registered with no `url_prefix`): `auth`, `main` (`/` IS the
dashboard), `transactions`, `categories`, `accounts`, `budgets`, `analytics` (redirect stub),
`admin`, `transfers`, `goals`, `push`, `feedback`, `announce`, `reminders` (the daily job),
`schedules`, `insights` (the month read behind Home's one AI panel), `ask` (the tool-use
security boundary), `digests`.

⚠️ `blueprints/forecasts.py` and `blueprints/agent.py` are in that directory but define **no
blueprint** and are registered nowhere (#232): both lost their routes when Home's four AI
surfaces became one. The forecast arithmetic feeds the month read and an Ask tool; the money
agent runs inside the weekly digest.

## Database tables

Full column lists and the reasoning behind each shape are in
[`docs/architecture.md`](docs/architecture.md).

- `transactions` — the ledger. Flags: `is_adjustment` (excluded from analytics), `is_transfer` +
  `transfer_group_id` (paired legs), `is_pending` (**display only — excludes from nothing**),
  `schedule_id` (which schedule posted it)
- `schedules` / `transfer_schedules` — recurring templates, **not ledger rows**; a runner
  materializes a real transaction per due date and advances `next_due`
- `categories` (`kind` expense|income) · `budgets` (one monthly amount each) · `budget_history`
  (append-only, nothing reads it yet)
- `account` — ⚠️ **singular, and its PK is `account_id`, not `id`**. Carries `credit_limit`/`apr`
- `goals` · `users` · `transfer_group_seq`
- AI caches, narrative only — figures are always recomputed: `insights` (the month read),
  `goal_coach`, `agent_runs`. ⚠️ `forecasts` is **written by nothing since #232** — dropping it
  is a migration, so it stands alone in its own PR
- Job/notification bookkeeping: `job_runs` (one row per job, upserted, **no `user_id`**),
  `push_subscriptions` (one row per **device**, `endpoint` globally unique), `reminder_log`
  (append-only idempotency claims)

## Non-negotiables

These apply to nearly every change, which is why they are here rather than in `docs/gotchas.md`.
**That file holds ~40 more**, each one load-bearing — read it before any non-trivial work.

**Data access**
- Every SELECT/INSERT/UPDATE/DELETE is scoped to `current_user.id`. All data tables have `user_id`
- **All app DB access goes through `db_cursor()`** (`db.py`) — commits on clean exit, rolls back and
  re-raises on error. Write pattern: ownership guard in its own read `with`, then
  `try: with db_cursor(commit=True):` around only the writes
- **Rows are namedtuples** — read `row.amount`. Every SELECT column needs a unique, valid-identifier
  name (alias expressions). ⚠️ A typo'd attribute in Jinja renders as an **empty string**, not an
  error — content-asserting tests are the only net
- The `account` table is singular; its PK is `account_id`

**Validation and errors**
- Amounts go through `helpers.parse_positive_amount()` / `parse_signed_amount()` — never hand-roll
  `float(x); if x <= 0`, because `float('nan')` passes it and Postgres stores NaN, poisoning SUM()
- Params likewise: `?month` → `parse_month_param()`, `?page` → `parse_page_param()`, posted FK ids →
  `parse_int_param()`. A raw string into a `%s` against an int column is a 500
- Posted `category_id`/`account_id` are checked for ownership **before** the write, via
  `validate_category_account()`
- Unexpected write failures flash `helpers.GENERIC_ERROR` and log via
  `current_app.logger.exception()` — **never** surface `str(e)`; psycopg2 text leaks SQL
- Write handlers catch `psycopg2.Error`, **not** `Exception`, so an `abort()` can never be
  swallowed into a 200

**Routes and templates**
- All routes `@login_required`; admin routes also check `current_user.is_admin`. Ownership guards
  `SELECT 1 ... AND user_id` → `abort(404)`
- Templates build links with `url_for('blueprint.function')` — endpoint names are `blueprint.function`
  (no `url_prefix`). The one exception is `emails/weekly_digest.html`, which needs absolute URLs
- **HTMX inline CRUD:** flash does not render on a partial swap — use `hx_toast()`. The transactions
  history re-renders the **whole `<tbody>`** on save/delete, because the running balance shifts
- CSRF: one `hx-headers` on `<body>` in base.html covers every HTMX write

**Process**
- Run **`./test.sh`** (full suite, ~35–55s). Do not ration test runs, and do not delegate running them
- New behaviour gets a test that **fails without it**
- Update `CHANGELOG.md` under `## [Unreleased]` in every PR
- A **schema migration always stands alone in its own PR**. Deploy ordering: additive migrations go
  **before** the image pull, DROPs **after**
- `ai.py` never touches the DB and never sees a user id — tool dispatch is a callback the blueprint
  supplies

## Testing

Details, including the xdist isolation rules that make `-n auto` safe, are in
[`docs/testing.md`](docs/testing.md) — **read it before adding tests or sweeping a pattern.**

- **`./test.sh`** is the only path (args pass through to pytest). It runs in a container on prod's
  Python 3.14; the dev `db` container must be up. `runtests` was retired 2026-08-17
- It **refuses to run while another run is in flight** (an advisory `flock`) — two concurrent runs
  corrupt each other through identical xdist prefixes
- Defaults to a bounded `-n 10`; `-n0` is the serial escape for `pdb` or unreadable output
- **921 tests** (measured 2026-08-17; `910 passed, 11 skipped` inside the shipped image). ⚠️ Recount rather than trusting that number — it has been
  wrong four times running
- Also runs in CI, and **inside the shipped image** when `Dockerfile`, `requirements*.txt`,
  `tests/` or `ci.yml` change (widened in #218 — a `tests/`-only change used to skip it, which is
  how the same defect reached `main` twice). ⚠️ The dev bind mount hides files `.dockerignore`
  strips, so a green local run says nothing about the real artifact — a test reading a repo file
  needs a `skipif` guard, not a passing assertion

## Versioning

**`0.x` SemVer shape, no stability contract** — full rationale in `VERSIONING.md`.
`0.MINOR` = features, `0.MINOR.PATCH` = fixes; `1.0.0` only when deliberately declared stable.
While the leading digit is `0`, a MINOR may carry a break, called out under `### Breaking`.

**The release is the unit, not the feature:** a release bundles everything merged to `main` since
the last one into a single version bump, whenever Sean decides. Versions climb monotonically and
cut tags are never rewritten.

⚠️ **The numbering RESET at `0.1.0`** — `v1`–`v10.15.0` live in the archived repo
(`CaddisMaster/budget-buddy-archive`). Do not resurrect the old scheme.

## Git & development workflow

Rationale lives in `CONTRIBUTING.md`; this section is the rule.

**Issue → branch → PR → squash-merge.** Do not work directly on `main`.

1. **Every change starts from an issue** — no issueless PRs. Feature issues carry Gherkin
   acceptance criteria. Assign the milestone (normally the one open one)
2. **Branch** off `main` as `<issue#>-short-slug`
3. **Test locally, both:** `docker compose up --build` → verify at `http://localhost:5001`, and
   `./test.sh`. CI does not click through the app
4. **New behaviour gets a test that fails without it**
5. **Update `CHANGELOG.md`** under `## [Unreleased]`
6. **Open a PR** with `Closes #<issue>`; squash-merge once CI is green

**Milestones = what shipped in a release.** Exactly one is open at a time, closed when that version
ships. An issue closed `NOT_PLANNED` gets none, and neither does a date-parked item.

**Batch issues into PRs by COHERENCE, never by calendar** (#53). A PR may close several issues only
when they share a file surface, a test surface, or one user-facing story. Being open the same week
is not a reason. Two named extremes: a **docs sweep is one PR** however many issues it closes; a
**schema migration always stands alone**.

**Prefer a feature flag / env-gate** (like `ai_enabled()`) over a long-lived branch.

**The "What's new" strip is a RELEASE step, not a per-commit step** — at release prep, replace the
contents of the `.whatsnew` strip in `app/templates/dashboard.html`. Security/patch fixes and no-UI
infrastructure are not feature blocks.

▶️ `/wrap` runs this sequence (`.claude/commands/wrap.md`), and a `Stop` hook catches the
`app/`-without-`CHANGELOG.md` rule locally. None of it is required; this section stays authoritative.

## Delegation

Four Sonnet agents in `.claude/agents/` — two executors that edit (`sweeper`, `test-first`) and two
read-only reporters (`gotcha-auditor`, `release-prep`). Full policy, and the reasoning behind each
constraint, in [`docs/delegation.md`](docs/delegation.md) — **read it before spawning one.**

- **Why delegate at all:** the scarce resource is the orchestrator's context window, not time.
  Delegate when **reading** the files is the expensive part, not when writing is
- **Delegate:** mechanical multi-file sweeps with a written spec, and wide read-only recon
- **Delegate feature work ONLY test-first** — the orchestrator writes the failing test; the worker
  never edits it
- **Do NOT delegate:** ownership guards, row shapes mid-refactor, SQL/migrations, AI seams, or
  exception handling. And never delegate running the suite
- ⚠️ **Plan-then-execute is deliberately NOT the pattern here** — a filed plan is the fragile part,
  and a faithful worker turns a wrong plan into convincingly wrong code

## Deployment

Pipeline detail, env vars and their gates, and the backup/restore story are in
[`docs/deployment.md`](docs/deployment.md). `RUNBOOK.md` is the operational source of truth for the
server itself — **read it before touching anything on the Droplet.**

- Repo: https://github.com/CaddisMaster/budget-buddy · App: https://budget.seandesmet.com
- ✅ **Deploy is automated.** Publishing a GitHub Release builds → pushes to ghcr → smokes the
  pushed image → **approval gate** → SSH deploy → verifies `/healthz`. Rollback is the
  `rollback.yml` workflow dispatched with a version
- ⚠️ **`${TAG}` has no default** — a compose command naming no version fails naming the variable,
  and deploys pin `TAG=` into the Droplet's `.env`. A bare `docker compose up -d` once reverted
  production three releases with no signal
- ⚠️ **`docker compose pull web`, never a bare `pull`** — a bare pull also fetches `postgres:16` and
  recreates the database container
- ⚠️ **A missing env var is the one deploy failure with no signal.** Check
  `git diff v<last>..HEAD -- .env.example` **before** cutting. Verify a secret landed by **length,
  not presence**; `/settings` carries an admin-only Integrations panel that answers this
- **Schema changes:** apply the numbered `sql/` migration by hand, pg_dump first. Additive
  **before** the pull, DROPs **after**
- **Droplet access** is maintainer-only and lives in the gitignored `CLAUDE.local.md`.
  `/opt/budget-buddy` is a **pure deploy dir — no git, no source**; `scp` changes up

## Current status

⚠️ **[`docs/status.md`](docs/status.md) is the detail, and it lags `main` by construction** — it
describes the last session rather than the current tree, and it asserts rather than going quiet.
**Reconcile against `git log` and `gh issue list` at the start of every session.**

- **Prod runs `0.7.0`, shipped and verified 2026-08-17.** `main` is level with it apart from
  the docs commit that records this. The `0.7.0` milestone is closed; **no milestone is open**,
  so the next cycle needs one created
- **#36 is the only open issue** — date-parked to ~Dec 2026, correctly carries no milestone
- ⚠️ **Nothing on the public surface distinguishes one deployed version from the next** —
  `app/static/` did not change between `0.6.0` and `0.7.0`, so `css_v` could not verify the
  deploy. Decide the verification handle **before** cutting; a version on `/healthz` would end
  this permanently and does not exist yet
- **Standing decisions that must not be re-opened** are listed in `docs/status.md`. Check there
  before re-filing anything that looks like an obvious improvement

## Maintainer notes (local only)

The maintainer keeps a private Obsidian vault, Droplet access details and backup infrastructure.
None of it is in this repo; that context lives in the gitignored `CLAUDE.local.md`. The standing
rule it carries is **do all note-writing in ONE pass at the very end of a session**.

Nothing in the app or the test suite depends on any of it. A fresh clone is fully functional
without it.

The same line runs through `.claude/`. The harness there is **committed** — agents, skills,
`/wrap`, the changelog hook, the permission allowlist — because a permission grant belongs in a
reviewable diff. `.claude/settings.local.json` holds the machine half and is gitignored alongside
this file, as is `.claude/worktrees/`.
