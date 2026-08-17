# Deployment

> Split out of `CLAUDE.md` on 2026-08-17. `RUNBOOK.md` remains the operational source of truth
> for the server itself; this file covers the release/deploy pipeline and its env vars.

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

