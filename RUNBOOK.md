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
| `seandesmet.com` | Static landing page | Nginx directly, from `~/budget-buddy/landing` |
| `mealie.seandesmet.com` | Mealie (unrelated project) | Docker → `127.0.0.1:9925` |
| `status.seandesmet.com` | Uptime Kuma monitoring | Docker → `127.0.0.1:3001` |

**Budget Buddy shares the box with two unrelated stacks.** Anything that
restarts Docker, rewrites Nginx, or rebuilds the Droplet affects Mealie and
Uptime Kuma as well. They are separate compose projects in separate directories
and have their own data volumes.

Every container binds to `127.0.0.1` only. This is deliberate and load-bearing:
**Docker publishes ports by writing iptables rules that bypass ufw entirely**, so
a bare `5001:5000` would expose the app to the public internet over plain HTTP,
firewall notwithstanding. Keep the loopback prefix on every port mapping.

### The deploy directory

`~/budget-buddy` on the server is a **pure deploy directory — no git, no source**:

```
~/budget-buddy/
├── .env                  # secrets; never in git; captured by the nightly backup
├── docker-compose.yml    # identical to the repo's tracked compose
├── sql/                  # migrations, copied up by hand when one is needed
├── landing/              # static landing page, served directly by Nginx
└── backups/              # ad-hoc pre-migration dumps
```

To change compose or add a migration you `scp` the file up. `git pull` does not
work there and never has.

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
Three sites are enabled: `budget-buddy`, `mealie.seandesmet.com`,
`status.seandesmet.com`.

The `budget-buddy` file holds **two** server blocks — the app and the landing
page. Certbot has rewritten it, adding the TLS directives and the HTTP→HTTPS
redirect blocks.

```nginx
# /etc/nginx/sites-available/budget-buddy

server {
  server_name seandesmet.com www.seandesmet.com;
  root /root/budget-buddy/landing;
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

The Uptime Kuma site needs WebSocket upgrade headers for its live dashboard —
without `Upgrade`/`Connection` the page loads but never updates:

```nginx
# /etc/nginx/sites-available/status.seandesmet.com  (TLS block omitted, Certbot adds it)
server {
  server_name status.seandesmet.com;
  location / {
    proxy_pass http://127.0.0.1:3001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

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
| `status.seandesmet.com` | `status.seandesmet.com` | Yes |

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
# ~/budget-buddy/docker-compose.yml
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
    image: caddismaster/budget-buddy:latest
    restart: always
    env_file:
      - .env
    ports:
      - "127.0.0.1:5001:5000"
    depends_on:
      - db
volumes:
  postgres_data:
```

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
cd ~/budget-buddy

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

```bash
cd ~/budget-buddy
docker compose pull && docker compose up -d
```

**Migration ordering matters, and the two directions are opposites:**

- **Additive** changes (new columns, new tables) apply **before** the pull — new
  code must never query a column that does not exist yet.
- **Drops** apply **after** the pull — the old code is still selecting those
  columns right up until the container is replaced.

Always `pg_dump` before applying anything (see §7).

---

## 7. Backups and restore

### What exists

1. **Nightly automated pull** — a launchd job on the maintainer's Mac (daily
   08:00; launchd rather than cron so a missed run fires on wake) SSHes in and
   streams down gzipped `pg_dump`s of Budget Buddy and Mealie, plus a tarball of
   configs. 14-day rotation.
2. **In-app** `/admin/backup` — an authenticated admin download of a live
   `pg_dump`.
3. **Ad-hoc pre-migration dumps** left in `~/budget-buddy/backups/`.

> ⚠️ **The dumps are plaintext SQL of every transaction.** Encrypted backups were
> considered and deliberately declined; treat the files as the most sensitive
> artifact in the system and keep them on encrypted disks.

### Gaps to be aware of

- The configs tarball captures `/etc/nginx/sites-available/` for
  `budget-buddy`, `mealie.seandesmet.com`, and `default` — but **not
  `status.seandesmet.com`**, which is enabled and live. Its content is
  reproduced in §3 above; the backup job's file list should be widened.
- Mealie's uploaded recipe images are **not** covered by the nightly job.
- Let's Encrypt material is not backed up, which is fine — certificates
  re-issue freely. Only DNS needs to be correct.

### Taking a manual dump

```bash
cd ~/budget-buddy
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
4. **DNS:** point `seandesmet.com`, `www`, `budget`, `mealie`, and `status` at
   the new IP. Wait for propagation — Certbot's HTTP-01 challenge needs the name
   already resolving to this host.
5. **Recreate `~/budget-buddy/`:** `docker-compose.yml` and `sql/` from this
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
11. **Re-point** the Mac backup job and Uptime Kuma at the new host.

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

**Point Uptime Kuma at `/healthz`**, not at the home page.

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

Reference values at the time of writing: 61% disk used, ~1 GB of 2 GB memory in
use with three stacks running. Memory is the tighter constraint of the two.
