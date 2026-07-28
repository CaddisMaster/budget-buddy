# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses the `0.x` versioning scheme described in
[`VERSIONING.md`](VERSIONING.md).

## [Unreleased]

### Fixed

- The "Ask your finances" answer panel is readable in dark mode. It was styled
  inline against `var(--bg-subtle)`, a custom property defined nowhere in the
  stylesheet. That never failed loudly because the declaration carried a
  hard-coded `#f6f7f9` fallback — a pale grey that looks right in light mode and
  leaves light text on a light panel in dark mode. Presentation moved to a real
  `.ask-answer` rule using the theme-aware `--surface-2` token. A test now
  asserts that every `var(--token)` in the stylesheet and templates resolves to
  a token that is actually defined, so a phantom one cannot return unnoticed.

### Changed

- Logging out asks for confirmation first. It is a nav item sitting beside
  ordinary navigation, and on the installed PWA a mis-tap costs a re-login on a
  touch keyboard. Logout remains a POST-only form with its own CSRF token; with
  JavaScript disabled it submits as before, without a prompt.

### Removed

- Mealie and Uptime Kuma were retired from the Droplet, and their cards removed
  from the landing page. The server now runs Budget Buddy alone. Their data was
  archived first — including Mealie's uploaded recipe images, which the nightly
  job had never covered — and the nightly backup no longer attempts to dump a
  database that is gone. Freed roughly 2 GB and dropped disk use from 64% to
  30%.

### Security

- Recorded that the Droplet's disk is **not** encrypted at rest. This had been
  documented as unverified; DigitalOcean states plainly that virtual disks on
  hypervisor local storage are not encrypted, and that encrypting them is the
  customer's responsibility. Affects the live Postgres volume, `.env`, and
  pre-deploy dumps. `RUNBOOK.md` now carries the finding, what it does and does
  not protect against, and the supported remedy (a LUKS-encrypted Block Storage
  Volume) should it ever be worth closing.

### Added

- The changelog is now enforced rather than merely requested: a pull request
  that changes `app/` without touching `CHANGELOG.md` fails, with a
  `skip-changelog` label as the escape hatch. This is what the repository
  reboot was originally started for — a convention nobody enforces decays, and
  the failure only becomes visible when someone tries to reconstruct a release
  and finds gaps.
- Migrations are applied automatically during deploy (`scripts/migrate.py`),
  tracked in a `schema_migrations` table. The deploy takes a verified `pg_dump`
  first and fails if that dump fails, then applies pending migrations **before**
  pulling the new image. `DROP`s remain deliberately manual — they must apply
  *after* the pull, which is the opposite order.
- A CI job asserting migrations apply cleanly to a database built from
  `schema.sql`, that the runner is idempotent, and that a PR adding a numbered
  migration also updates `schema.sql`. The last check closes a real drift risk:
  `schema.sql` builds every fresh database, so a migration missing from it means
  new environments silently lack the change.

### Removed

- `deploy.sh`, `promote.sh` and `docker-compose.staging.yml`. They built and
  promoted the Docker Hub image, which production no longer uses as of `0.1.0`;
  the staging step they fed is now the release workflow's `smoke` job, which
  tests the pushed artifact rather than a local rebuild. The Docker Hub image
  remains as an emergency fallback and the scripts stay in git history
  (`git show v0.1.0:deploy.sh`).

## [0.1.0] - 2026-07-27

The first release of the rebuilt repository, and a **baseline snapshot** rather
than a feature release: the application is carried over unchanged from
`v10.15.0`, while everything around it — workflow, CI, CD, registry, versioning
and documentation — was rebuilt. Production moves from Docker Hub to
`ghcr.io/caddismaster/budget-buddy` with this release.

### Added

- Repository reboot: fresh history, `.env.example`, tracked
  `docker-compose.override.yml`, MIT license, and contributor documentation
  (`README.md`, `CONTRIBUTING.md`, `VERSIONING.md`, `RUNBOOK.md`, this file).
- The Docker image runs as an unprivileged user (`appuser`, uid 10001) instead
  of root.
- `RUNBOOK.md` — production topology, Nginx configuration, TLS, backup and
  restore procedures, and a rebuild-from-nothing checklist. None of this
  previously existed outside the server itself.
- `GET /healthz` — an unauthenticated liveness probe that performs a real
  database round-trip, returning 200 when healthy and 503 when Postgres is
  unreachable. Wired up as a Docker healthcheck. Monitoring a normal page is
  not equivalent: with the database stopped, `/login` still returns 200.
- Ruff linting, configured in `pyproject.toml` and enforced in CI.
- Pre-commit hooks (`.pre-commit-config.yaml`) — ruff plus hygiene checks,
  including `detect-private-key`. Local convenience; CI remains the
  enforcement point.
- CI now builds the Docker image, asserts the container runs as `appuser`, and
  boots it to confirm gunicorn serves a request. Previously CI installed
  dependencies on the runner and never built the Dockerfile at all.
- Continuous deployment (`.github/workflows/release.yml`). Publishing a GitHub
  Release builds the image, pushes it to `ghcr.io/caddismaster/budget-buddy`
  tagged with the version, the commit SHA and (for full releases only)
  `latest`, boots that **pushed** image against a throwaway database to prove
  `/healthz` answers, then pauses on a required-reviewer approval gate before
  deploying to the Droplet and verifying the public `/healthz`. Replaces the
  hand-run `deploy.sh` → staging → `promote.sh` sequence; the smoke job takes
  over the "prod runs the bytes you tested" guarantee that the local staging
  step used to provide.
- A rollback workflow (`.github/workflows/rollback.yml`) — dispatch a version
  and the Droplet is brought back up on that exact immutable tag, after
  confirming the image actually exists in the registry.
- Deploys pull only the `web` service. A bare `docker compose pull` also
  fetched `postgres:16`, and `up -d` then recreated the database container —
  so shipping application code could upgrade and restart the database engine
  as a side effect. A database upgrade is a deliberate action taken after a
  dump, not a consequence of a release.

### Changed

- The deployed image is pinned to an exact version at deploy time
  (`TAG=<version> docker compose up -d`) rather than tracking `latest`, so the
  running container is always traceable to a release. `docker-compose.yml`
  resolves `${TAG:-latest}`, keeping a hand-run `docker compose up -d` working.

- CI runs on `main` and pull requests only, with a concurrency group, so a
  branch push and its pull request no longer trigger duplicate runs.
- Workflows declare least-privilege `permissions`.

### Security

- The application can now connect as a least-privilege `budget_app` role
  (`sql/30_app_role.sql`) holding only `SELECT`/`INSERT`/`UPDATE`/`DELETE`,
  configured via `DB_APP_USER`/`DB_APP_PASSWORD`. Previously it authenticated
  as the database owner — a superuser — so any SQL injection or code execution
  inherited the ability to drop tables, read every database on the cluster, or
  run shell commands via `COPY ... FROM PROGRAM`. Falls back to `DB_USER` when
  unset, so existing deployments are unaffected until they opt in.
- `/admin/backup` — one authenticated GET returns the whole database as
  plaintext SQL, so it is now rate-limited to 5 per hour, returns `403` for
  non-admins instead of flashing and redirecting, and logs every export with
  the username. Previously a full-database export left no trace at all.
- Deployments no longer run as `root`. The production stack moved from
  `/root/budget-buddy` to `/opt/budget-buddy`, owned by a dedicated
  unprivileged `deploy` user that CI authenticates as with a restricted
  ed25519 key; the deploy secrets are scoped to the approval-gated
  `production` environment. Moving out of `/root` also let `/root` return to
  mode `0700` — it had been widened to `0755` so Nginx could serve the landing
  page from inside it, which left the production `.env` (database password,
  `SECRET_KEY`, API keys) readable by **every user on the host**. Both that
  file and Mealie's are now unreadable to anyone but their owner.
  (Server-side configuration change, recorded here for the history.)

### Fixed

- `https://www.seandesmet.com` failed the TLS handshake. Certbot had
  accumulated four certificate lineages for two names; the landing page's
  server block claimed `www` while presenting a certificate that did not cover
  it. Repointed at the lineage covering both names and deleted the orphan.
  (Server-side configuration change, recorded here for the history.)

- `./test.sh` invoked bare `pytest`, which is not on `PATH` under the non-root
  image — pip installs console scripts to `~/.local/bin`. Now invoked as
  `python -m pytest`.
- `.gitignore` listed `.DS_store`, which never matched the real `.DS_Store`
  (git patterns are case-sensitive).

---

## Prior history

Versions before `0.1.0` — the `v1` through `v10.15.0` era — were developed in a
different repository, archived read-only at
[CaddisMaster/budget-buddy-archive](https://github.com/CaddisMaster/budget-buddy-archive).
Its git tags and GitHub releases carry the full detail. A summary of that
lineage, most recent first:

- **v10.15.0** — APR on credit cards with an interest-aware payoff projection; AI-card read-state collapse
- **v10.14.0** — brand icon redo: coin-with-$ mark, full-bleed maskable and apple-touch sources
- **v10.13.0** — design pass: vendored Chart.js, money formatting filter, navigation regroup, collapsible charts, home/dashboard merge, PWA shell, mobile history cards
- **v10.12.0** — income categories (`categories.kind`); history running-balance fix; safe-to-spend removed
- **v10.11.x** — bulk edit; parameter-validation hardening; named-row refactor; automatic CSS cache-busting
- **v10.10.0** — credit limits; the Money agent (first autonomous tool-use loop)
- **v10.9.0** — debt-payoff goals; balance check-in; budget report; dashboard consolidation
- **v10.8.0** — weekly email digest (APScheduler + Resend); goal coach
- **v10.7.0** — AI budget review
- **v10.6.0** — design refresh: design tokens, dashboard hero, chart grid
- **v10.5.0** — auto-categorize (batch classification)
- **v10.4.0** — recurring account transfers
- **v10.3.0** — ask-your-finances (multi-turn tool use)
- **v10.2.0** — month-ahead forecast
- **v10.1.x** — monthly insight; security-hardening patch (write-side IDOR fix, security headers, cookie flags, CSV formula-injection sanitizer)
- **v10.0** — scheduled income and bills; recurrence moved out of the ledger
- **v9.0** — conversational transaction entry (first AI feature)
- **v1–v8** — core CRUD and deployment, UI overhaul, multi-user authentication, blueprints and pytest, ownership guards, transfers and goals, smart budgets, HTMX inline CRUD and CI

[Unreleased]: https://github.com/CaddisMaster/budget-buddy/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CaddisMaster/budget-buddy/releases/tag/v0.1.0
