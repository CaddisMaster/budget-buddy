# Contributing to Budget Buddy

This guide has two audiences, and they need the same thing: someone new to the
codebase, and the maintainer returning after a year away with no memory of how
any of it works. Everything below assumes neither of you remembers anything.

---

## 1. Get it running

**Prerequisite:** Docker Desktop. Nothing is installed on your machine — the app
and the tests both run in containers.

```bash
git clone git@github.com:CaddisMaster/budget-buddy.git
cd budget-buddy
cp .env.example .env
```

Open `.env` and fill in the two values it marks as required:

- `SECRET_KEY` — generate one with
  `python3 -c "import secrets; print(secrets.token_hex(32))"`.
  The app **refuses to boot** without it, deliberately, rather than failing
  confusingly at the first request.
- `DB_PASSWORD` — anything, it is a local container.

Then:

```bash
docker compose up --build
```

The app is at **http://localhost:5001**. The database schema initialises itself
on first boot from `sql/schema.sql`; there is no manual SQL step.

`.env.example` documents every variable the code actually reads. Two of them —
`ENABLE_DIGEST_SCHEDULER` and `COOKIE_SECURE` — **must stay unset locally**. The
first will start mailing real weekly digests from your laptop; the second turns
on Secure-only cookies, after which you cannot log in over plain `http://`.

### The AI and email features are optional

`ANTHROPIC_API_KEY` gates all nine AI surfaces, `RESEND_API_KEY` gates outbound
email. Leave both blank. Each feature checks `ai_enabled()` / `mail_enabled()`
and disables itself cleanly — you get a fully working app without them, and the
test suite passes either way (tests stub the network seams, so they never make a
real call regardless).

### Create your first user

There is no public registration. After first boot:

```bash
# Generate a password hash
docker compose exec web python3 -c \
  "from flask_bcrypt import Bcrypt; print(Bcrypt().generate_password_hash('yourpassword').decode())"

# Insert the admin user
docker compose exec db psql -U admin -d budget -c \
  "INSERT INTO users (username, password_hash, is_admin) VALUES ('admin', '<hash>', true);"
```

Further users can then be created in the UI under Settings → Manage users.

### Seed a development dataset

An empty database renders empty charts, empty budget grids and empty goal cards,
which makes almost nothing worth looking at. `scripts/seed_dev.py` fills one:

```bash
docker compose exec web python scripts/seed_dev.py
```

That creates an admin user `dev` / `dev-password-123` owning four accounts (two
of them credit cards with limits and APRs), categories of both kinds, six months
of transactions, budgets, schedules, recurring transfers, and two goals — enough
for every surface in the app to show something real. It is **synthetic data**;
none of it comes from a production or personal database.

Useful flags: `--months 12` for a longer history, `--username` for a different
account, `--seed` to vary the data, `--dry-run` to see what it would write, and
`--force` to replace an existing seeded user. Without `--force` it refuses to
touch a database that already has that user, so it cannot silently double a
dataset.

The same seed and the same day always produce the same data, so a bug found
against seeded data can be reproduced on another machine.

### Set up your editor

The application's dependencies live inside the Docker image, so out of the box
your editor's language server cannot resolve a single one of them — every
`import flask`, `import psycopg2` and `from app.db import ...` shows as
unresolved, with no autocomplete and no go-to-definition.

Create a local virtual environment for the editor to read:

```bash
brew install python@3.14          # the Mac's system 3.9 is too old — see below
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

VS Code auto-discovers a `.venv` in the workspace root, so there is nothing to
configure and no editor settings are committed.

**Nothing is ever run from this environment** — the app and the tests still run
in containers. It exists purely so the language server has real code to read,
and it is gitignored and excluded from the image.

It must be **3.14**, matching production. The Mac ships 3.9.6, which cannot
evaluate the `str | None` annotations in `app/ai.py` — a 3.9 environment reports
working code as broken.

There is deliberately **no dev container**. One was tried and removed: it needed
editor tooling baked into the image, had no Docker inside it (so `docker
compose` and the `verify` skill had to move to a separate terminal anyway), and
fixed nothing the virtual environment above does not already fix.

### What is live, and what needs a restart

The source is bind-mounted, so most changes need no rebuild:

| Change | What to do |
|---|---|
| Python | nothing — gunicorn `--reload` restarts the worker |
| Templates | nothing — `TEMPLATES_AUTO_RELOAD` re-reads them |
| `style.css` | `docker compose restart web` (~2s) |
| `requirements*.txt`, `Dockerfile` | `docker compose up -d --build web` |

CSS is the odd one out: the `?v=` cache-buster is a hash of the file computed
once at startup, so editing the stylesheet without a restart leaves the browser
on the old cached copy. See the note in `app/__init__.py`.

None of this reaches production — it all lives in
`docker-compose.override.yml`, which exists only on your machine.

### Install the pre-commit hooks

```bash
pip install pre-commit && pre-commit install
```

Runs `ruff` plus hygiene checks — trailing whitespace, missing final newlines,
merge-conflict markers, oversized files, and **private keys** — before each
commit.

These are **convenience, not a guarantee.** They run on your machine and any
commit can skip them with `--no-verify`. CI is what actually enforces. The
reason to install them anyway is speed (a lint error in two seconds rather than
two minutes) and the private-key check, which catches a credential *before* it
enters git history — after which removing it means rewriting history.

### Run the tests

```bash
./test.sh                        # the whole suite (~17 seconds)
./test.sh tests/test_routes.py   # one file
./test.sh -k semimonthly         # by keyword
./test.sh -n0                    # serial — for pdb, or readable failures
```

Runs on the same Python as production. It needs the `db` container up, because
the route and isolation tests hit a real database — they create and clean up
their own `__pytest__`-prefixed users.

If the dev stack is already running, the suite runs **inside the live `web`
container**, which is why there is nothing to build or install first. If nothing
is running it builds a throwaway container instead. The script prints which path
it took. Both use the same image, and the test dependencies are baked into its
`dev` stage rather than installed on every run.

Runs **in parallel** by default — one worker per CPU, which takes the suite from
~204s to ~17s. Because every worker is a separate process sharing one database,
each derives its own `TEST_PREFIX` from the xdist worker id and therefore owns
its own rows. **If you add a test that creates users, build the name from
`TEST_PREFIX`; never hardcode `__pytest__`.** With a shared prefix a parallel run
produces hundreds of errors as workers tear down each other's fixtures.

There is no reason to run a subset for speed any more. Run the whole thing.

---

## 2. The workflow

**Every change starts as an issue, and ends as a pull request.** No issueless
PRs, no direct pushes to `main`.

1. **Open an issue** (or claim an existing one) using one of the templates. A
   feature issue states its acceptance criteria in Given/When/Then form, so
   "done" is agreed before any code is written. **Assign it the open milestone**
   — see "Milestones" below.
2. **Branch** off `main`, named `<issue#>-short-slug` — e.g. `42-push-reminders`.
3. **Build it,** and test locally: `docker compose up --build` to see it work,
   then `./test.sh` for the full suite. Both, not one.
4. **Add tests.** New behaviour needs a test that fails without it. See §4 for
   why this matters more here than in most codebases.
5. **Update `CHANGELOG.md`** under `## [Unreleased]`. **This is enforced**: a pull
   request that changes anything under `app/` without touching `CHANGELOG.md` fails
   the `Changelog` check. If an entry genuinely does not apply — a pure refactor with
   no observable effect — add the `skip-changelog` label and the check re-runs and
   passes. Only `app/` is covered, so docs, workflow and test-only changes need
   nothing.
6. **Open a PR** with `Closes #<issue>` in the body — one line per issue the PR
   closes. CI must be green.
7. **Squash-merge.** `main` reads as one commit per pull request.

### Milestones

**A milestone is a released version, and it holds the issues resolved in that
release cycle.** Exactly one is open at a time — the next release — and it is
closed when that version ships. Assign one when you file the issue; it costs
nothing then, and it is the only record of *what shipped when* that does not
require reading tags and commit ranges.

Not every issue gets one. An issue closed as **not planned** gets none, because
abandoned scope is not part of any release, and neither does an item parked on a
future date rather than a version.

Two things about the historical milestones are deliberate and should not be
"corrected":

- **There is no `0.4.0`.** That tag was cut and then withdrawn at the approval
  gate — it never deployed, and the same contents shipped as `0.4.1`. A
  milestone means what users actually got.
- **`0.3.1` has its own**, because it was a genuine shipped patch rather than a
  re-cut of `0.3.0`.

The convention lapsed once, after `0.2.0`, and four releases had to be
reconstructed afterwards from commit ranges. It is much cheaper to keep up.

### How much goes in one PR

A PR may close several issues. Group them by **coherence, never by calendar**:
issues belong together when they share a **file surface**, a **test surface**, or
a **single user-facing story**. Being open in the same week is not a reason.

Work happens in **weekly sessions**; a session typically produces two or three
PRs, not fifteen. If a session finds five things wrong with the same subsystem,
that is one PR closing five issues — not five PRs. Conversely, three unrelated
fixes that merely happened on the same afternoon are three PRs.

Two cases sit at the extremes and are worth naming:

- **A documentation sweep is one PR,** however many issues it closes. Five doc
  issues are genuinely one change; `skip-changelog` already covers it.
- **A schema migration always stands alone.** Deploy ordering is load-bearing —
  additive migrations go out *before* the image, drops *after* (§5) — and a PR
  that mixes a migration with unrelated work obscures the ordering it needs.

**Why not simply batch a week's work into one big PR?** It was considered, and
it costs more than it saves:

- **Revert granularity dies.** Squash-merge makes the whole week one commit. If
  one change breaks production and the rest are fine, none of them can be backed
  out independently, and `rollback.yml` only moves whole versions.
- **`git bisect` stops working.** A squashed commit spanning unrelated areas
  identifies nothing.
- **Review has no story to follow** — including your own review, a year later.
- **A week-long branch is the thing this project avoids.** The standing
  preference is a feature flag or env-gate over a long-lived branch.
- **It would not save CI anyway.** CI runs **per push, not per PR** — ten pushes
  to one weekly branch is ten runs. Expensive CI is fixed by filtering what each
  job does, not by merging fewer times.

### Commit messages

Imperative mood, capitalised subject, no trailing period, wrapped at ~72
characters. Explain **why** in the body — the diff already shows what. If it
took you twenty minutes to work out why something had to be done a particular
way, that reasoning belongs in the commit body, not in your memory.

```
Add rate limit to the backup endpoint

One authenticated GET returns the whole database as plaintext SQL, which
makes it a single-request exfiltration path if an admin session is ever
hijacked. Ten per hour is far above real use and far below scripted abuse.
```

---

## 3. How the code is laid out

```
app/
  __init__.py      Flask app, extensions, security headers, template filters
  db.py            get_db_connection() + the db_cursor() context manager
  helpers.py       Shared validators and parsers — read this file early
  ai.py            Every model call. Never touches the database
  mailer.py        Outbound email seam
  models.py        The User model for Flask-Login
  blueprints/      All routes, one module per area, registered with no url_prefix
  templates/       Jinja2, all extending base.html; partials/ holds HTMX fragments
  static/          CSS, vendored JS, PWA manifest and service worker
sql/               Numbered migrations + schema.sql (a clean single-file schema)
tests/             pytest suite
scripts/           Standalone data-pipeline scripts, with their own requirements
landing/           The static landing page, unrelated to the app itself
```

Routes are registered with **no URL prefix**, so endpoint names are
`blueprint.function` — `url_for('auth.login')`, `url_for('transactions.transactions')`.
Templates build every internal link with `url_for`; the one deliberate exception
is `templates/emails/weekly_digest.html`, which needs absolute production URLs
because it is read in a mail client.

---

## 4. Gotchas that will make your first PR quietly wrong

These are not style preferences. Each one is a real bug that has been written in
this codebase before, and most of them fail **silently**.

### Jinja typos render as empty strings, not errors

A misspelled attribute in a template produces `""` and a page that looks almost
right. Nothing raises. This is why tests assert on **content** — that the number
actually appears — rather than just on a 200 status code. When you touch a
template, add or extend an assertion that would catch the value going missing.

### Rows are namedtuples, so every SELECT column needs a unique alias

`db_cursor()` uses psycopg2's `NamedTupleCursor`. Read fields by name
(`row.amount`), not by index. The consequence: **two unaliased expression
columns in one SELECT** — say two bare `COALESCE(...)` — raise
`duplicate field name` at fetch time. Alias every computed column with `AS`.

### The account table is singular, and its key is not `id`

The table is `account`, not `accounts`, and its primary key is `account_id`.
Every other table uses `id`. This trips up everyone exactly once.

### Every query must be scoped to the current user

All data tables carry a `user_id` foreign key. Every SELECT, INSERT, UPDATE and
DELETE is scoped to `current_user.id` — there are no exceptions. Ownership is
checked *before* a write, not after: guard with
`SELECT 1 ... AND user_id = %s` → `abort(404)` (404, not 403 — a 403 confirms the
record exists to someone who should not know that).

When a form posts a `category_id` or `account_id`, validate that the referenced
row belongs to the user before the write. `validate_category_account()` in
`blueprints/transactions.py` does this; use it.

### All amounts go through `parse_positive_amount()`

Never hand-roll `float(x); if x <= 0`. `float('nan')` passes that check,
PostgreSQL happily stores NaN in a `numeric` column, and from then on **every
`SUM()` that touches the row returns NaN** — silently poisoning balances,
budgets, charts and AI facts. `helpers.parse_positive_amount()` rejects NaN and
infinity; `parse_signed_amount()` is its sibling for values that may be negative
or zero.

The same rule applies to parameters: `?month` goes through
`parse_month_param()`, `?page` through `parse_page_param()`, and posted foreign
keys through `parse_int_param()`. A raw string passed into a `%s` placeholder
against an integer column raises, which is a 500.

### Never show a database exception to a user

Unexpected write failures flash `helpers.GENERIC_ERROR` and log the real
exception with `current_app.logger.exception()`. psycopg2 error text leaks
constraint names and SQL structure. Write-path handlers catch
`except psycopg2.Error`, deliberately **not** bare `Exception` — that way an
`abort()` can never be swallowed into a 200 response.

### Database access goes through `db_cursor()`

It commits on clean exit, rolls back and re-raises on error, and always closes.
The pattern is: ownership guard in its own read block, then
`try: with db_cursor(commit=True):` wrapped around only the writes.

---

## 5. Database changes

`sql/schema.sql` runs **only on a fresh database**. An existing deployment is
migrated by the numbered files in `sql/`.

A schema change therefore means **two edits**: a new numbered migration
(`sql/NN_short_name.sql`) *and* the corresponding update to `schema.sql`, so a
fresh clone and a live deployment converge on the same schema. Updating only one
is the classic mistake here.

Prefer **additive** migrations — new columns and tables — over drops. Additive
changes can be applied before the new code ships; drops cannot, because the old
code is still selecting those columns until the moment it is replaced.

---

## 6. Security expectations

The application stores spending history. It stores **no card numbers, no bank
account numbers, and no bank API credentials**, and that is a deliberate line —
it is why a worst-case data disclosure is a privacy harm rather than a financial
one. Do not add stored banking credentials without a serious conversation first.

- Passwords are bcrypt-hashed, with input truncated at bcrypt's 72-byte limit.
- Admin-only routes `abort(403)` for non-admins.
- CSRF protection is global via Flask-WTF; HTMX requests carry the token through
  a single `hx-headers` attribute on `<body>`.
- Secure cookies and HSTS are gated on `COOKIE_SECURE` so local HTTP still works.
- Never commit `.env`. Secret scanning with push protection is enabled on this
  repository and will block a push containing a recognised credential.

If you believe you have found a security issue, please open a private security
advisory rather than a public issue.
