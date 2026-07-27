# Operations Runbook

Everything needed to understand, operate, or completely rebuild the production
deployment. Written for the case where the server is gone and the person
rebuilding it remembers nothing.

Until this file existed, the Nginx configuration, TLS setup, production compose
file, and restore procedure lived **only on the server itself** — which meant
losing the server also meant losing the instructions for replacing it.

> **Secrets are not in this file and never should be.** Host addresses,
> credentials, and API keys live in the maintainer's private notes and in the
> server's `.env`, which is captured by the nightly backup. This file documents
> *shape and procedure* only.

---

## 1. What runs where

A single DigitalOcean Droplet (Ubuntu 24.04 LTS, 2 GB RAM, 24 GB disk) running
Nginx on the host, with everything else in Docker.

| Domain | Serves | Backed by |
|---|---|---|
| `budget.seandesmet.com` | Budget Buddy | Docker → `127.0.0.1:5001` |
| `seandesmet.com` | Static landing page | Nginx directly, from `/opt/budget-buddy/landing` |

**The Droplet runs Budget Buddy alone.** It previously also hosted Mealie and
Uptime Kuma; both were retired on 2026-07-27, along with their Nginx sites and
TLS certificates. Their data was archived to the maintainer's machine first.
Anything that restarts Docker or rewrites Nginx now affects only this app —
which is a meaningful simplification, since the old warning about collateral
damage to unrelated stacks no longer applies.

Their DNS records were removed from Squarespace the same day, so those hostnames
no longer resolve at all (verified NXDOMAIN via both `8.8.8.8` and `1.1.1.1`).
**Three names point at this Droplet and no others:** `seandesmet.com`,
`www.seandesmet.com`, and `budget.seandesmet.com`.

Every container binds to `127.0.0.1` only. This is deliberate and load-bearing:
**Docker publishes ports by writing iptables rules that bypass ufw entirely**, so
a bare `5001:5000` would expose the app to the public internet over plain HTTP,
firewall notwithstanding. Keep the loopback prefix on every port mapping.

### The deploy directory

`/opt/budget-buddy` on the server is a **pure deploy directory — no git, no source**:

```
/opt/budget-buddy/
├── .env                  # secrets; never in git; captured by the nightly backup
├── docker-compose.yml    # identical to the repo's tracked compose
├── sql/                  # migrations, copied up by hand when one is needed
├── landing/              # static landing page, served directly by Nginx
└── backups/              # ad-hoc pre-migration dumps
```

To change compose or add a migration you `scp` the file up. `git pull` does not
work there and never has.

The directory is owned by **`deploy`**, an unprivileged user that exists so CI
never needs a root key. It is in the `docker` group and owns nothing else on the
box. GitHub Actions authenticates as it with a dedicated ed25519 key whose
`authorized_keys` entry disables agent, port and X11 forwarding; the private key
lives only in the `production` environment's secrets, so no un-gated workflow in
the repository can read it.

> The stack lived in `/root/budget-buddy` until 2026-07-27. That required `/root`
> to be mode `0755` so Nginx could reach `landing/` inside it, which left the
> production `.env` world-readable. Moving to `/opt` let `/root` go back to
> `0700`. If you are following an older note that says `~/budget-buddy`, it means
> this directory.

> ⚠️ **Never copy `docker-compose.override.yml` to the server.** It is tracked in
> the repo (so a fresh clone can build locally) and it replaces the `image:` with
> a `build:` context. On the server, where there is no source, it would break the
> deploy. Only `docker-compose.yml` goes up.

---

## 2. Firewall

```
Status: active
Default: deny (incoming), allow (outgoing)

22                    ALLOW IN    Anywhere
80,443/tcp            ALLOW IN    Anywhere   (Nginx Full)
```

Nothing else is open. Postgres is not reachable from outside the host — not
because of ufw (see the Docker caveat above) but because it is bound to
loopback.

---

## 3. Nginx

Config lives at `/etc/nginx/sites-available/`, symlinked into `sites-enabled/`.
One site is enabled: `budget-buddy` (plus the packaged `default`). The
`mealie.seandesmet.com` and `status.seandesmet.com` files were removed with
those services on 2026-07-27.

The `budget-buddy` file holds **two** server blocks — the app and the landing
page. Certbot has rewritten it, adding the TLS directives and the HTTP→HTTPS
redirect blocks.

```nginx
# /etc/nginx/sites-available/budget-buddy

server {
  server_name seandesmet.com www.seandesmet.com;
  root /opt/budget-buddy/landing;
  index index.html;
  location / {
    try_files $uri $uri/ =404;
  }

  listen 443 ssl;                                                   # managed by Certbot
  ssl_certificate     /etc/letsencrypt/live/seandesmet.com-0001/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/seandesmet.com-0001/privkey.pem;
  include             /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;
}

server {
  server_name budget.seandesmet.com;
  location / {
    proxy_pass http://127.0.0.1:5001;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  listen 443 ssl;                                                   # managed by Certbot
  ssl_certificate     /etc/letsencrypt/live/budget.seandesmet.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/budget.seandesmet.com/privkey.pem;
  include             /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;
}

# Certbot-generated HTTP redirects
server {
  if ($host = budget.seandesmet.com) { return 301 https://$host$request_uri; }
  listen 80;
  server_name budget.seandesmet.com;
  return 404;
}

server {
  if ($host = seandesmet.com) { return 301 https://$host$request_uri; }
  listen 80;
  server_name seandesmet.com www.seandesmet.com;
  return 404;
}
```

> **Retired 2026-07-27.** This section used to document the Uptime Kuma site
> block. Worth keeping the one transferable detail: **a proxied app with a live
> dashboard needs `Upgrade`/`Connection "upgrade"` headers and
> `proxy_http_version 1.1`**, or the page loads and then never updates. If a
> WebSocket-using service is ever added here, that is the trap.

After any change:

```bash
nginx -t && systemctl reload nginx
```

`nginx -t` first, always. A reload with a broken config takes all four sites down.

---

## 4. TLS

Let's Encrypt via Certbot's Nginx plugin. Renewal is automatic through
`certbot.timer` (a systemd timer, **not** cron — `crontab -l` is empty by
design, so do not go looking for it there).

```bash
certbot certificates          # what exists and when it expires
systemctl status certbot.timer
certbot renew --dry-run       # verify renewal works without burning rate limit
```

Issuing a certificate for a new subdomain:

```bash
certbot --nginx -d newsub.seandesmet.com
```

### ⚠️ Known issue: duplicate certificate lineages

There are **four** lineages for two names, because Certbot was re-run with
different domain sets and creates a new `-000N` lineage rather than replacing:

| Lineage | Covers | Used by Nginx? |
|---|---|---|
| `seandesmet.com` | `seandesmet.com`, `www.seandesmet.com` | **No** |
| `seandesmet.com-0001` | `seandesmet.com` only | **Yes** |
| `budget.seandesmet.com` | `budget.seandesmet.com` | Yes |

**Consequence: `https://www.seandesmet.com` fails the TLS handshake.** The
server block claims `www` but presents `seandesmet.com-0001`, which does not
list `www` in its SAN. The lineage that *would* work is the unused one.

The fix is to point that server block at `/etc/letsencrypt/live/seandesmet.com/`
and reload, then delete the orphaned lineage with
`certbot delete --cert-name seandesmet.com-0001`. Verify before and after with:

```bash
echo | openssl s_client -servername www.seandesmet.com \
  -connect www.seandesmet.com:443 2>/dev/null | openssl x509 -noout -ext subjectAltName
```

**Watch for this after any rebuild** — a fresh Certbot run produces a lineage
named `seandesmet.com` with no suffix, so a config copied verbatim from here
would point at a path that does not exist and Nginx would fail to start.

---

## 5. Production compose

```yaml
# /opt/budget-buddy/docker-compose.yml
services:
  db:
    image: postgres:16
    restart: always
    environment:
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: ${DB_NAME}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./sql/schema.sql:/docker-entrypoint-initdb.d/schema.sql
    ports:
      - "127.0.0.1:5432:5432"
  web:
    image: ghcr.io/caddismaster/budget-buddy:${TAG:-latest}
    restart: always
    env_file:
      - .env
    ports:
      - "127.0.0.1:5001:5000"
    healthcheck:
      test: ["CMD", "python", "-c",
             "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=5).status == 200 else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
    depends_on:
      - db
volumes:
  postgres_data:
```

This is **byte-identical to the repository's tracked `docker-compose.yml`** as of the
2026-07-27 cutover, which is what makes `scp`-ing it up safe. `${TAG}` is supplied by the
deploy job so production runs an exact released version; the `latest` fallback keeps a
hand-run `docker compose up -d` working.

> ⚠️ **Never `scp` `docker-compose.override.yml`.** It is tracked (so a fresh clone can
> build locally) and replaces `image:` with a `build:` context — on the server, where there
> is no source, that breaks the deploy.

> **Emergency fallback to Docker Hub.** `caddismaster/budget-buddy:latest` still exists
> (`v10.15.0` code). If ghcr were unreachable, set `image:` back to it and
> `docker compose up -d`. Remove this note once `0.1.0` has been stable a while.

`schema.sql` is mounted into the Postgres init directory, which **only runs on a
completely empty data volume**. On an existing database it is inert. This is why
migrations are applied by hand rather than by editing `schema.sql` alone.

### Environment variables

The server's `.env` sets everything in `.env.example` plus the two
production-only switches:

- `COOKIE_SECURE=1` — Secure cookies and HSTS
- `ENABLE_DIGEST_SCHEDULER=1` — starts the weekly digest scheduler

> **`ENABLE_DIGEST_SCHEDULER=1` is only safe under single-worker Gunicorn.** The
> image runs `--workers 1 --threads 4`. With multiple *workers*, APScheduler
> would start once per worker and mail duplicate digests, and Flask-Limiter's
> in-memory store would fragment. **If you ever touch the Gunicorn command,
> `--workers 1` stays.** Use threads for concurrency, never workers.

After editing `.env`:

```bash
docker compose up -d --force-recreate web
```

There are also two dead variables, `APP_USERNAME` and `APP_PASSWORD`, read by no
code. Safe to remove.

### The least-privilege application role

`DB_USER` is the database **owner** — compose creates the cluster with it, so it
is a superuser. An application connected as that role means any SQL injection or
code execution inherits superuser: drop any table, read every database on the
cluster, create roles, or run shell commands via `COPY ... FROM PROGRAM`.

`sql/30_app_role.sql` creates a `budget_app` role holding `SELECT`, `INSERT`,
`UPDATE`, `DELETE` and nothing else. The application connects as it via
`DB_APP_USER` / `DB_APP_PASSWORD`; **`DB_USER` stays exactly as it is**, because
migrations and `pg_dump` still need the owner.

Rolling it out on an existing deployment:

```bash
cd /opt/budget-buddy

# 1. Back up first — always, before anything touching the database.
docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip \
  > "backups/pre-app-role-$(date +%Y%m%d-%H%M).sql.gz"

# 2. Create the role and its grants.
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  < sql/30_app_role.sql

# 3. Give it a password. The migration deliberately sets none — the repository
#    is public, and the role cannot log in until you do this.
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
  -c "ALTER ROLE budget_app PASSWORD 'generate-a-strong-one';"

# 4. Point the app at it, then restart only the web container.
#    Nothing has changed for the app until this step, so you can pause here.
echo 'DB_APP_USER=budget_app'            >> .env
echo 'DB_APP_PASSWORD=the-same-password' >> .env
docker compose up -d --force-recreate web

# 5. Verify.
curl -s localhost:5001/healthz     # {"database":"ok","status":"ok"}
```

The migration is idempotent and safe to re-run.

**Rollback** is removing the two `DB_APP_*` lines from `.env` and recreating the
web container — the application falls back to `DB_USER`. The role can be left in
place; it does nothing while unused.

Verified against a real database — `budget_app` is refused every one of these:

| Attempt | Result |
|---|---|
| `DROP TABLE transactions` | `must be owner of table transactions` |
| `CREATE TABLE evil (x int)` | `permission denied for schema public` |
| `TRUNCATE transactions` | `permission denied for table transactions` |
| `COPY (SELECT 1) TO PROGRAM 'id'` | `permission denied to COPY to or from an external program` |
| `CREATE ROLE eviluser SUPERUSER` | `permission denied to create role` |

while ordinary `SELECT`/`INSERT`/`UPDATE`/`DELETE` and sequence access all work,
and the full test suite passes connected as the role.

---

## 6. Deploying

**The normal path is not manual.** Publishing a GitHub Release runs
`.github/workflows/release.yml`, which builds the image, pushes it to ghcr,
boots that pushed image and checks `/healthz`, then **waits for an approval**
on the `production` environment before deploying over SSH as `deploy` and
verifying the public `/healthz`. To ship, cut the Release and click approve.

To go back to a known-good version, run the **Rollback** workflow with a
version (e.g. `0.1.0`). It refuses versions that are not in the registry, so a
typo fails before the running container is touched.

Manual fallback, if Actions is unavailable — run it as `deploy`, not `root`:

```bash
cd /opt/budget-buddy
TAG=0.1.0 docker compose pull && TAG=0.1.0 docker compose up -d
```

`TAG` pins an exact released image; omitting it falls back to `latest`.

### Migrations

**Additive migrations are applied automatically by the deploy job**, in this order:

1. `pg_dump` to `backups/pre-deploy-<timestamp>.sql.gz` — **a failed dump fails the deploy**
2. `scripts/migrate.py` applies anything pending
3. *then* the image is pulled and the container swapped

The runner tracks applied files in a **`schema_migrations`** table. Production was
baselined on 2026-07-27 (30 files recorded as applied, none re-executed).

```bash
cd /opt/budget-buddy
docker compose run --rm --no-deps -T -e DB_HOST=db web python scripts/migrate.py --status
```

> ⚠️ **`sql/NN_*.sql` are forward-only deltas, NOT a replayable history.**
> `users` is created only in `schema.sql`; replaying the numbered files from an empty
> database produces 4 tables of 13. **`schema.sql` is the only artifact that builds a
> database from nothing** — that is what a rebuild uses (§8). The runner refuses to
> apply against a database with no tracking table for exactly this reason; on an
> existing database you `--baseline` first.

**⚠️ `DROP`s are NOT automated and must stay manual.** The two directions are opposites:

- **Additive** (new columns/tables) → **before** the pull. New code must never query a
  column that does not exist yet. *This is the automated path.*
- **Drops** → **after** the pull. Old code is still selecting those columns until the
  container is replaced. Automating this would apply them in the wrong order.

So a release that drops a column: let the deploy run, then apply the drop by hand
afterwards (`pg_dump` first).

The runner connects as **`DB_USER`**, never `DB_APP_USER` — the least-privilege
`budget_app` role has no DDL rights by design.

---

## 7. Backups and restore

### What exists

1. **Nightly automated pull** — a launchd job on the maintainer's Mac (daily
   08:00; launchd rather than cron so a missed run fires on wake) SSHes in and
   streams down a gzipped `pg_dump` of Budget Buddy, plus a tarball of configs.
   14-day rotation. (It also dumped Mealie until that service was retired on
   2026-07-27.)

   **Dead-man's switch.** The job pings a [Healthchecks.io](https://healthchecks.io)
   check on start, success and failure, so a backup that silently *stops* raises
   an alert on its own — the dangerous failure mode, because you would otherwise
   discover it on the day you need a restore. Failure pings POST that run's log,
   so the alert email carries the reason. The ping URL lives in
   `~/personal-projects/backups/.healthchecks-url`; **absent that file the pings
   are skipped and the job behaves exactly as before.** A monitoring outage can
   never turn a good backup into a failed one — the ping is explicitly
   non-fatal.
2. **In-app** `/admin/backup` — an authenticated admin download of a live
   `pg_dump`.
3. **Ad-hoc pre-migration dumps** left in `/opt/budget-buddy/backups/`.

### Encryption at rest

The question "shouldn't the database be encrypted?" was considered and answered
deliberately. The short version: encryption at rest defends against **offline**
theft — a stolen disk, a leaked dump, a stray snapshot. It does **not** defend
against someone who already holds database credentials or application access,
because a running web app must be able to decrypt in order to render a
dashboard. Column-level encryption was rejected specifically because it breaks
`SUM()`, `ORDER BY`, and indexes on exactly the columns every screen aggregates.

So the useful question is what the underlying disks do.

| Location | Holds | Status |
|---|---|---|
| This Mac | The nightly plaintext `pg_dump` files | **FileVault On** — verified with `fdesetup status` |
| The Droplet | The live Postgres volume, `.env`, and pre-deploy dumps | ❌ **NOT ENCRYPTED — verified 2026-07-27.** DigitalOcean states plainly: *"The virtual disks for Droplets stored on the hypervisor's local storage are not encrypted at rest."* ([Shared Responsibility Model for Droplets](https://www.digitalocean.com/security/shared-responsibility-model-droplets)). Encrypting it is **the customer's responsibility** under their model |

Calibration: the worst case here is disclosure of spending history — a real
privacy harm, but not account takeover or fraud. The schema stores **no card
numbers, no bank account numbers, and no bank API tokens**, so nobody can move
money with a stolen dump. Passwords are bcrypt-hashed. Keep it that way: adding
stored bank credentials would invalidate this entire analysis and require
revisiting it from scratch.

### The Droplet's disk is not encrypted at rest (verified 2026-07-27)

Long recorded here as unverified; now answered, and the answer is **no**. Droplet local
storage is explicitly excluded from DigitalOcean's at-rest encryption — the contrast is
deliberate on their side, since **Block Storage Volumes *are*** LUKS-encrypted with the
storage cluster fully encrypted at rest and snapshots inheriting it.

**What is exposed:** the `budget-buddy_postgres_data` volume (every transaction), `.env`
(database password, `SECRET_KEY`, API keys), and the pre-deploy dumps under `backups/`.

**What this does and does not protect against.** At-rest encryption defends against
**offline** theft — a stolen or improperly decommissioned drive in the datacenter. It does
nothing against a live compromise that already has credentials, which is the likelier path.
DigitalOcean's physical security is the mitigating control here, and the residual risk is
low-probability. It is now a *known* gap rather than an assumed-fine one.

**If it is ever worth closing**, the supported route is a **Block Storage Volume** (~$1/month
for 10 GB), moving Postgres's data directory onto it. DigitalOcean encrypts it; nothing in the
app changes. LUKS on the local disk is not a practical alternative — it needs a passphrase at
boot, and storing the key on the same disk defeats the purpose.

> ⚠️ **The dumps are plaintext SQL of every transaction.** Encrypted backups were
> considered and deliberately declined; treat the files as the most sensitive
> artifact in the system and keep them on encrypted disks.

### Gaps to be aware of

- Let's Encrypt material is not backed up, which is fine — certificates
  re-issue freely. Only DNS needs to be correct.

### Taking a manual dump

```bash
cd /opt/budget-buddy
docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip \
  > "backups/pre-change-$(date +%Y%m%d-%H%M).sql.gz"
```

### Restoring

Into a **throwaway local database first** — never straight at production.

```bash
gunzip -c backup.sql.gz | docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME"
```

To restore over a live database, drop and recreate it first so you are not
merging into existing rows:

```bash
docker compose stop web                 # stop writes
docker compose exec -T db psql -U "$DB_USER" -d postgres \
  -c "DROP DATABASE \"$DB_NAME\";" -c "CREATE DATABASE \"$DB_NAME\";"
gunzip -c backup.sql.gz | docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME"
docker compose start web
```

Then confirm: log in, check the dashboard totals, and check that the most recent
transaction is present.

---

## 8. Rebuilding the server from nothing

1. **Provision** an Ubuntu 24.04 Droplet. Add the SSH key. Do not enable password
   authentication.
2. **Firewall:** `ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable`
3. **Install** Docker Engine with the Compose plugin, plus `nginx` and
   `certbot` + `python3-certbot-nginx`.
4. **DNS:** point `seandesmet.com`, `www`, and `budget` at the new IP. Wait for propagation — Certbot's HTTP-01 challenge needs the name
   already resolving to this host.
5. **Recreate `/opt/budget-buddy/`:** `docker-compose.yml` and `sql/` from this
   repository, `landing/` from `landing/`, and `.env` from the configs backup.
6. **Nginx:** write the site files from §3, symlink into `sites-enabled`,
   `nginx -t`, reload.
7. **TLS:** `certbot --nginx -d ...` per domain. Re-read the §4 warning about
   lineage names before copying any `ssl_certificate` path verbatim.
8. **Start:** `docker compose up -d`. The empty volume triggers `schema.sql`, so
   you get the current schema directly — no migration replay needed.
9. **Restore** the most recent dump (§7).
10. **Verify:** load the site over HTTPS, log in, confirm the dashboard figures
    match the pre-loss state, and confirm the container runs as a non-root user
    (`docker compose exec web whoami` → `appuser`).
11. **Re-point** the Mac backup job at the new host.

---

## 9. Health checks

`GET /healthz` is unauthenticated and returns JSON:

```json
{"status": "ok", "database": "ok"}      // 200
{"status": "error", "database": "unreachable"}   // 503
```

It performs a real round-trip to Postgres, which is the entire point. **A page
request is not a substitute.** With the database stopped, `GET /login` still
returns 200 — the login page does not touch the database — so anything
monitoring a page reports green while the application is actually unusable.
Verified by stopping the `db` container: `/login` → 200, `/healthz` → 503.

⚠️ **There is currently NO external uptime monitoring.** Uptime Kuma was retired
on 2026-07-27, and it had been watching the home page rather than `/healthz`
anyway — so it would have reported green throughout a database outage. If
monitoring is reinstated, point it at **`/healthz`** and accept only `200-299`:
the endpoint returns **503** on database failure, so a status range that swallows
5xx defeats the entire purpose.

The compose file runs the same endpoint as a container healthcheck every 30s,
so `docker compose ps` shows `(healthy)` / `(unhealthy)` rather than a bare
`Up` that tells you nothing.

## 10. Routine checks

```bash
docker compose ps                          # look for (healthy), not just Up
curl -s localhost:5001/healthz             # app + database round-trip
docker compose logs --tail 50 web          # app logs
df -h /                                    # disk (Postgres and images grow)
free -h                                    # memory
certbot certificates                       # expiry dates
docker compose exec web whoami             # should be: appuser
```

Reference values after retiring the other two stacks (2026-07-27): **30% disk
used, ~0.5 GB of 2 GB memory in use** — down from 64% and ~1 GB when three stacks
shared the box. Both are now comfortable; memory was previously the tighter
constraint and prompted the 1 GB → 2 GB resize.
