# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses the `0.x` versioning scheme described in
[`VERSIONING.md`](VERSIONING.md).

## [Unreleased]

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

### Changed

- CI runs on `main` and pull requests only, with a concurrency group, so a
  branch push and its pull request no longer trigger duplicate runs.
- Workflows declare least-privilege `permissions`.

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

[Unreleased]: https://github.com/CaddisMaster/budget-buddy/commits/main
