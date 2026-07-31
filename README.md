# Budget Buddy

[![CI](https://github.com/CaddisMaster/budget-buddy/actions/workflows/ci.yml/badge.svg)](https://github.com/CaddisMaster/budget-buddy/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A self-hosted personal finance tracker built with Python, Flask, and PostgreSQL —
a full ledger, budgeting cockpit, and a set of AI features that narrate your
money without ever being trusted to do the arithmetic.

🔗 **[budget.seandesmet.com](https://budget.seandesmet.com)** *(login required — personal use only)*

## Why I built this

I wanted to expand my understanding of SQL, Python, and database systems, and I
learn far better by building something real than by working through courses. So
rather than follow a tutorial, I built the thing I actually wanted to use every
day — and then kept going until it was genuinely load-bearing for my own
finances. Everything here exists because I hit a real need for it.

## Features

### The ledger

- **Transactions** — income and expenses with categories, accounts, and dates, edited inline with HTMX and no page reloads
- **History** — search, month filters, pagination, and a running balance column that stays continuous across pages and filtered views
- **Bulk edit** — recategorise or delete many transactions at once, with transfer pairs deliberately protected from being half-edited
- **Account transfers** — move money between accounts as one linked pair of entries, kept out of the income and expense charts
- **Scheduled income and bills** — six frequencies including semi-monthly with two pay days; each posts a real transaction on its due date, going forward only, never back-dating on setup
- **Recurring transfers** — the same idea for money moving between your own accounts
- **CSV export** — filtered transactions out to a file, with formula-injection sanitising

### Making sense of it

- **Dashboard** — net position, savings rate, and Chart.js views of spending by category, cash flow, net balance over time, budget performance, and spending by day of week, plus a year-over-year comparison
- **Smart budgets** — one monthly amount per category, auto-suggested from six months of history; override it or clear it back to the suggestion
- **Budget report** — a hit/miss grid over the last six complete months, with streaks and a three-versus-three trend
- **Goals** — savings goals *and* debt-payoff goals, each with a projected completion date and an on-track/behind status; payoff goals fold in the card's APR
- **Accounts** — credit limits with utilisation warnings, APR with estimated monthly interest, and a balance check-in that reconciles against reality with a single adjusting entry

### AI assistance

Nine AI features, all built on the same principle: **the application computes
every number, and the model only ever narrates.** Figures are never persisted
from a model response, and the model has no database access — it reaches data
only through a fixed set of read-only, user-scoped, parameter-validated tools.

- **Quick add** — type "spent 42 on groceries at Safeway yesterday" and have it parsed into a pre-filled form you confirm
- **Monthly insight** — a plain-English recap of the month's figures with a coaching tip
- **Forecast** — a month-ahead projection from run-rate plus remaining scheduled items
- **Ask your finances** — a multi-turn question box backed by nine query tools
- **Auto-categorise** — batch-classify uncategorised expenses, presented as a review list you confirm
- **Budget review** — proposed budget amounts, snapped into a range derived from your actual history
- **Goal coach** — narration of whether your goal pace is realistic
- **Weekly email digest** — a Sunday summary of the week behind and the bills ahead
- **Money agent** — an autonomous investigation loop that decides which tools to call, and must cite evidence for every finding it reports

### Everything else

- **Multi-user** with session authentication, admin-only user creation, and strict per-user data isolation
- **Installable PWA** with an offline-capable service worker, tested on iOS
- **Dark mode**, automatic from system preference
- **Mobile layout** with card-based history and a collapsible sidebar
- **Admin tools** — user management and a database backup download

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.14, Flask, Gunicorn |
| Database | PostgreSQL 16 |
| Frontend | Jinja2, HTMX, Chart.js, hand-written CSS |
| AI | Anthropic Claude API |
| Email | Resend |
| Containers | Docker, Docker Compose |
| Hosting | DigitalOcean Droplet behind Nginx, TLS via Let's Encrypt |
| CI | GitHub Actions |

## Quick start

**Prerequisite:** Docker Desktop. Nothing gets installed on your machine.

```bash
git clone git@github.com:CaddisMaster/budget-buddy.git
cd budget-buddy
cp .env.example .env      # then fill in SECRET_KEY and DB_PASSWORD
docker compose up --build
```

The app is at **http://localhost:5001**. The schema initialises itself on first
boot — there is no manual SQL step.

The AI and email features are optional: leave `ANTHROPIC_API_KEY` and
`RESEND_API_KEY` blank and every one of them disables itself cleanly.

Creating your first user, running the tests, and the gotchas worth knowing
before you change anything are all in **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## Tests

```bash
./test.sh
```

565 tests, running in a throwaway container on the same Python as production.
They cover date arithmetic, routes and authentication, per-user isolation, the
HTMX endpoints, and every AI feature with its network seam stubbed — so the
suite never makes a real API call and needs no key. The same suite runs in
GitHub Actions on every push and pull request.

## Architecture

```
Developer machine
    │  git push → pull request → CI
    ▼
GitHub Actions ──build──▶ container registry
                              │
                              ▼  pull
                    DigitalOcean Droplet
                        Nginx  (TLS termination, reverse proxy)
                          └─▶ Gunicorn → Flask
                          └─▶ PostgreSQL (Docker volume)
```

Flask is never directly exposed; Nginx terminates TLS and proxies to a
loopback-bound container port. PostgreSQL is bound to `127.0.0.1` and is not
reachable from the internet. The application container runs as an unprivileged
user.

## Documentation

| Document | What it covers |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, the workflow, and the gotchas that make a first PR quietly wrong |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each release |
| [VERSIONING.md](VERSIONING.md) | The `0.x` scheme and why the numbering restarted |
| [RUNBOOK.md](RUNBOOK.md) | Production topology, TLS, backups, restore, and rebuilding from nothing |
| [CLAUDE.md](CLAUDE.md) | Detailed architecture notes, maintained for AI coding assistants and for future-me |

## History

This repository starts at `0.1.0`. The application is not new — it was developed
through `v10.15.0` in a previous repository, which is preserved read-only at
**[CaddisMaster/budget-buddy-archive](https://github.com/CaddisMaster/budget-buddy-archive)**.
Every earlier commit, tag, and release note lives there.

The reboot rebuilt the *envelope* around the application — workflow, CI/CD,
registry, versioning, and documentation — and deliberately changed nothing about
the application itself. [VERSIONING.md](VERSIONING.md) explains the reasoning.

## License

[MIT](LICENSE).
