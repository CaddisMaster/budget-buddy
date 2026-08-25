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
| `seandesmet.com` | Static landing page | Nginx directly, from `/var/www/seandesmet.com` — **a separate repo since #299**, `CaddisMaster/seandesmet.com` |

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
> to be mode `0755` so Nginx could reach the landing page inside it, which left the
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

⚠️ **Two site files since #299.** `budget-buddy` holds the app;
`seandesmet.com` holds the landing page, which now serves from `/var/www/seandesmet.com`
and is deployed from its own repo. They were one file with two server blocks until then, so a
config mistake in either could take both down — splitting them was the point.
Certbot has rewritten both, adding the TLS directives and the HTTP→HTTPS redirect blocks.

```nginx
# /etc/nginx/sites-available/seandesmet.com   (its own file since #299)

server {
  server_name seandesmet.com www.seandesmet.com;
  root /var/www/seandesmet.com;
  index index.html;
  location / {
    try_files $uri $uri/ =404;
  }

  listen 443 ssl;                                                   # managed by Certbot
  # ⚠️ Read the LIVE lineage name off the box — do not copy one from this file.
  #    `certbot certificates` lists them; pick the one whose SAN covers BOTH
  #    names above. This block named `seandesmet.com-0001` until 2026-08-24,
  #    by which point that lineage had been DELETED. See §Duplicate certificate
  #    lineages.
  ssl_certificate     /etc/letsencrypt/live/<lineage covering apex + www>/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/<lineage covering apex + www>/privkey.pem;
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
  # Unversioned, and stable: this lineage covers exactly one name, so certbot
  # has never had cause to mint a `-000N` alongside it. Still worth confirming
  # with `certbot certificates` after any rebuild.
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

### ⚠️ Duplicate certificate lineages — the failure mode, and how to check for it

**Certbot creates a new `-000N` lineage rather than replacing an existing one**
when it is re-run with a different `-d` set. If Nginx then points at a lineage
whose SAN does not list every name its `server_name` claims, that name fails the
TLS handshake — while the certificate *that would work* sits unused on the same
box. Nothing warns you; the site simply stops serving on one hostname.

**Check which lineage actually answers, by name:**

```bash
echo | openssl s_client -servername www.seandesmet.com \
  -connect www.seandesmet.com:443 2>/dev/null | openssl x509 -noout -ext subjectAltName
certbot certificates    # every lineage on the box, and what each covers
```

The SAN must list every name the corresponding `server_name` claims. If it does
not, repoint that server block at the lineage that covers them all, reload, and
delete the orphan with `certbot delete --cert-name <lineage>`.

> ✅ **RESOLVED — this bit production once, and is fixed (recorded under `0.1.0`
> in `CHANGELOG.md`).** Four lineages had accumulated for two names, the landing
> page's server block presented `seandesmet.com-0001`, which covered the apex
> only, and **`https://www.seandesmet.com` failed the handshake**. It was
> repointed at the lineage covering both names and the orphan deleted.
>
> ⚠️ This section previously described that as a **current** failure. Re-verified
> against production **2026-08-24** — `www` presents a SAN of
> `DNS:seandesmet.com, DNS:www.seandesmet.com`, expiring `Nov 2 2026`, and
> returns `200`. **The runbook is read during an incident**, so a resolved
> failure written as current is worse here than anywhere else: it invites
> someone to "fix" a certificate that is already correct, under time pressure.
>
> **Keep checking after a rebuild**, or any time `certbot` is re-run with a
> different `-d` set — the mechanism above is real and has not gone away.

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
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
  web:
    image: ghcr.io/caddismaster/budget-buddy:${TAG:?TAG is not set — pass TAG=<version>, or see RUNBOOK §5}
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
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
volumes:
  postgres_data:
```

The deployed file **is** the repository's tracked `docker-compose.yml`, `scp`-ed up — that is
what makes copying it safe. The block above is that file with its (long) comments stripped
for reading, so check it with `diff`, not by eye:

```bash
# from a clone on the Mac
ssh <droplet> 'cat /opt/budget-buddy/docker-compose.yml' | diff - docker-compose.yml
```

### ⚠️ `TAG` has no default, deliberately (#190)

`${TAG:?…}` — there is **no `:-latest` fallback**, because that fallback was a live
foot-gun. The Droplet's local `latest` is stale *by construction*: deploys pull the exact
version tag and never `latest`, so nothing on the box ever refreshes it. On 2026-08-10 a
hand-run `docker compose up -d` while applying the log limits moved production from `:0.6.0`
back to the **`0.3.1`** image — three releases and two migrations — with **no signal**:
container `healthy`, `/healthz` 200, uptime check green, and `docker compose ps` showing a
tag that looks perfectly normal.

Two things replace it:

- **The deploy pins the version into `.env`.** `release.yml` and `rollback.yml` both rewrite
  a single `TAG=<version>` line into `/opt/budget-buddy/.env` before anything else runs, so a
  bare `docker compose up -d` now reproduces the running deployment instead of choosing a
  different one — and the running version is greppable on the host:

  ```bash
  cd /opt/budget-buddy && grep '^TAG=' .env      # → TAG=0.6.0
  ```

- **A missing pin fails loudly.** If that line is ever absent — a hand-restored `.env`
  (§8 restores one), a rebuilt server — then *every* compose command on the box, `ps` and
  `logs` included, exits non-zero naming `TAG` rather than starting the wrong image. Supply
  it for the one command (`TAG=0.6.0 docker compose ps`) and put the line back.

An explicit `TAG=` on the command line always beats the file, which is why the automated
deploys are authoritative and why the first deploy after this change works against an `.env`
that has no line yet.

⚠️ **`.env.example` carries a `TAG=` line for the same reason** — local dev never pulls this
image (the override builds from source) but compose still interpolates it, so a local `.env`
without the line breaks `./test.sh`. If you rebuild `.env` from a secret store, keep it: it
is not a secret and nothing will restore it for you.

> ⚠️ **Never `scp` `docker-compose.override.yml`.** It is tracked (so a fresh clone can
> build locally) and replaces `image:` with a `build:` context — on the server, where there
> is no source, that breaks the deploy.

> **ghcr is the only registry.** The Docker Hub fallback was retired 2026-07-28. Those images
> are `v10.15.0` code, so using one would have silently reverted the app by two releases —
> and past the `31`/`32` migrations, which the old code cannot read. **If ghcr is
> unreachable, roll FORWARD:** re-run the release workflow, or build and push from a clone.
> To roll back a bad deploy, use `rollback.yml` with a previous ghcr version — that is a
> different thing and still works.

`schema.sql` is mounted into the Postgres init directory, which **only runs on a
completely empty data volume**. On an existing database it is inert. This is why
migrations are applied by hand rather than by editing `schema.sql` alone.

### Container log limits, and changing the `db` service at all

Both services declare a `json-file` driver capped at `max-size: 10m` × `max-file: 3`
(30 MB each). Without it Docker's default has **no** limit, and `db` is the one
container that is never recycled — `release.yml` runs `docker compose pull web`
precisely so shipping app code cannot restart the database (issue #22), so its log
had been accumulating since the container was created.

Measured before the change (2026-08-10): `db` **46,982 bytes** over 14 days
(~3.3 KB/day), `web` 722 bytes, `/var/lib/docker/containers` 140 KB total, disk 35%.
At that rate the disk is never the problem — the cap exists for a **fault loop**, a
crash-looping container or a repeating Postgres error writing gigabytes in days, which
gives no warning because `/healthz` stays green until Postgres cannot write.

⚠️ **Any edit to the `db` service definition makes the next `docker compose up -d`
recreate the database container.** That is the one sanctioned exception to the
`pull web` rule and it is a scheduled operation, never a side effect of a release:

```bash
# 1. Verify a dump you could actually fall back to (§7), then take a fresh one.
python3 scripts/restore_check.py <backup-dir>            # must be green
cd /opt/budget-buddy
docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip \
  > "backups/pre-change-$(date +%Y%m%d-%H%M).sql.gz"
#    pull that file down and restore_check.py IT — the dump that matters is the
#    one taken immediately before, not last night's.

# 2. Record what must survive.
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM transactions)"
docker compose ps -q db          # note the container ID
docker compose ps --format '{{.Service}} {{.Image}}'   # note the web VERSION

# 3. Apply. ⚠️ ALWAYS pass TAG — see the warning below; a bare `up -d` silently
#    reverts the app. NEVER `docker compose pull` here either: the image is
#    pinned and already local, and a bare pull is what issue #22 exists to
#    prevent. (scp docker-compose.yml up first)
TAG=<the version currently running> docker compose up -d

# 4. Verify.
docker compose ps -q db          # ID MUST have changed
docker compose ps --format '{{.Service}} {{.Image}}'   # web still on :<version>
docker inspect -f '{{range .Mounts}}{{.Name}}{{end}}' $(docker compose ps -q db)
docker inspect -f '{{.HostConfig.LogConfig.Config}}' $(docker compose ps -q db)
#    row counts identical to step 2, and /healthz 200

# 5. Run the same command again and confirm the db container ID is UNCHANGED.
#    That is what proves every future release is safe again.
```

> ⚠️ **Pass `TAG=<version>` on any hand-run compose command.** Since #190 this is
> **enforced, not merely advised**: the image reads `${TAG:?…}` with no default, and
> the deploy writes the running version into `.env`, so a bare `docker compose up -d`
> either reproduces what is already running or fails naming `TAG`. See §5.
>
> The history, kept because it is why the default is gone. `docker-compose.yml` used
> to resolve the image as `${TAG:-latest}`, and **the Droplet's local `latest` tag is
> stale by construction** — deploys pull the exact version tag
> (`TAG=<v> docker compose pull web`) and never `latest`, so nothing on the box ever
> refreshes it.
>
> This is not hypothetical. Applying the log limits on **2026-08-10**, a bare
> `docker compose up -d` silently moved production from `:0.6.0` to `:latest` —
> which on that box still pointed at the **`0.3.1`** image, three releases and two
> migrations behind. The container reported `healthy` and `/healthz` returned 200
> throughout, because old code against an additively-migrated schema runs fine.
> Caught by comparing image IDs (`latest` = `632466328591`, `0.6.0` =
> `e6bc2309e87a`), then corrected with `TAG=0.6.0 docker compose up -d`.
>
> **Verify the version after any hand-run compose command**, and prefer a check
> on the running CODE rather than the tag — e.g. importing a module that only
> exists in the release you expect:
>
> ```bash
> docker compose ps --format '{{.Service}} {{.Image}}'
> docker compose exec -T web python -c "import app.jobs" </dev/null   # 0.6.0+
> ```

> ⚠️ **`docker compose exec -T` reads stdin.** Inside a script piped to
> `ssh ... bash -s`, it will swallow the remaining commands and the rest of the
> script silently does not run. Redirect it: `docker compose exec -T … < /dev/null`.
> Every `exec -T` in this document that appears inside a scripted sequence needs it.

⚠️ **Compose's output does not tell you whether the database was recreated.**
Rehearsed locally on 2026-08-10: it printed only `Container budget-buddy-db-1
Starting / Started` for a container it had in fact replaced. **Compare the container
ID**, which is unambiguous. The named volume re-attaches and the data survives — that
was verified in the same rehearsal, and step 1 exists for the case where it does not.

### Environment variables

The server's `.env` sets everything in `.env.example` plus the production-only
values below. ⚠️ **`.env.example` is not deployed to the Droplet** — it lives only
in the repository, so this list is the operative one for the server.

- `COOKIE_SECURE=1` — Secure cookies and HSTS
- `ENABLE_DIGEST_SCHEDULER=1` — starts the weekly digest scheduler
- `FEEDBACK_GITHUB_TOKEN` — enables in-app bug reports and feature suggestions
  (#64). A **fine-grained PAT scoped to this one repository, with `issues: write`
  and nothing else** — so a leak means issue spam, not code access. Deliberately
  *not* named `GITHUB_TOKEN`, which is a magic name in GitHub Actions.

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

The `budget_app` role holds `SELECT`, `INSERT`, `UPDATE`, `DELETE` and nothing
else. The application connects as it via `DB_APP_USER` / `DB_APP_PASSWORD`;
**`DB_USER` stays exactly as it is**, because migrations and `pg_dump` still
need the owner.

**Two paths create it, and which one you are on depends on the database:**

| Situation | Where the role comes from |
|---|---|
| An **existing** database (production today) | `sql/30_app_role.sql`, applied by hand — the procedure below |
| A **fresh** database (a rebuild, CI, a wiped dev volume) | `sql/schema.sql`, which carries the same block at the end |

⚠️ **Neither path sets a password** — this repository is public, so both files
deliberately omit one and the role cannot log in until you set one yourself. On
a rebuild that step is not optional: the `.env` you restore from the configs
backup already carries `DB_APP_USER=budget_app`, so without it the web container
starts and cannot connect. See §8 step 9.

Before #160 `schema.sql` carried no role block at all, which meant a fresh
database plus `scripts/migrate.py --baseline` recorded `30_app_role.sql` as
applied while the role did not exist — and `--status` reported everything
applied and nothing pending, so nothing surfaced the drift.

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

### Before cutting the Release — check for new environment variables

⚠️ **A missing environment variable is the one deploy failure that produces no
signal at all.** Nothing in `release.yml` writes or validates `.env` — its only
reference to the file is `source ./.env`, to read database credentials for the
pre-deploy dump. A feature gated on an unset variable does not error, does not
fail `/healthz`, and logs nothing; it is simply absent. **That is identical to the
feature working as designed**, which is what makes it easy to miss for weeks.

So, before publishing the Release:

```bash
# Releases are cut on GitHub, so their tags are NOT in your clone until you ask.
# Without this the next command dies with "fatal: bad revision".
git fetch --tags

# What variables did this release introduce?
git diff v<last-version>..HEAD -- .env.example
```

If it names anything new, set it on the Droplet **before** approving the deploy:

```bash
# on the Droplet, as deploy
cd /opt/budget-buddy
vi .env                                   # add the variable
docker compose up -d --force-recreate web
```

⚠️ **Order matters.** The deploy job's `up -d` will *not* pick up an `.env` edit
made after it runs — the container is already up with the old environment. Either
set the variable before approving, or force-recreate afterwards.

Then confirm the gate actually flipped, rather than assuming:

```bash
docker compose exec web python -c \
  "from app.github import feedback_enabled; print(feedback_enabled())"
```

Applies to every optional-feature gate — `ANTHROPIC_API_KEY`, `RESEND_API_KEY`,
the VAPID keys, `FEEDBACK_GITHUB_TOKEN`. Those earlier ones were only set reliably
because the feature *was* the point of that release; the risk appears when a gated
feature ships bundled with unrelated work.

### The deploy itself

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
# `pull web`, never a bare `pull` — that also fetches postgres:16 and the
# following `up -d` then recreates the database container (issue #22).
TAG=0.1.0 docker compose pull web && TAG=0.1.0 docker compose up -d

# Then pin it, exactly as the workflows do, so the box records what it runs
# and a later bare `up -d` cannot change it (#190):
touch .env.tmp && chmod 600 .env.tmp        # mode FIRST: this file holds every secret
grep -v '^TAG=' .env > .env.tmp && printf 'TAG=%s\n' 0.1.0 >> .env.tmp && mv .env.tmp .env
```

`TAG` pins an exact released image. Omitting it no longer falls back to `latest` — the
command fails naming `TAG`, which is the whole of #190. See §5.

### Migrations

**Migrations are applied automatically by the deploy job — both directions since
#277**, in this order:

1. `pg_dump` to `backups/pre-deploy-<timestamp>.sql.gz` — **a failed dump fails the deploy**
2. `scripts/migrate.py --phase before-pull` — the **additive** migrations
3. the image is pulled and the container swapped
4. `scripts/migrate.py --phase after-pull` — the **DROPs**, now that the old
   container has stopped

An empty pass at step 2 or 4 is normal and exits 0; most releases carry a
migration for one phase and nothing for the other.

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

**The two directions are opposites, and a migration now declares which one it is:**

- **Additive** (new columns/tables) → **before** the pull. New code must never query a
  column that does not exist yet. This is the default; the file says nothing.
- **Drops** → **after** the pull. Old code is still selecting those columns until the
  container is replaced. The file declares it with a pragma line in its header:

  ```sql
  -- deploy: after-pull
  ```

> ⚠️ **This section used to read "`DROP`s are NOT automated and must stay manual."**
> That was never true of the pipeline it described — step 2 ran `migrate.py` with no
> filter, which applies **every** pending file including the drops. `sql/36` shipped
> through it at `0.8.0` against a `v0.7.0` image that still read both dropped tables,
> which would have 500'd `/` and `/goals` for the length of the image pull. Accepted
> deliberately at the time (one user, a watched deploy, a `pg_dump` taken first) and
> fixed properly in #277.

**Forgetting the pragma is a red test suite, not an outage.**
`tests/test_migration_phases.py` fails any migration containing `DROP TABLE` or
`DROP COLUMN` that does not declare `after-pull`. It also fails an `after-pull`
migration that *adds* schema: a migration that both drops and adds cannot be phased
and must be split into two files.

To apply everything in one pass — a local run, or a rebuild — omit `--phase`:

```bash
docker compose run --rm --no-deps -T -e DB_HOST=db web python scripts/migrate.py
```

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

   ⚠️ **Some networks block healthchecks.io.** Sean's work network refuses
   `hc-ping.com` on 443 outright (DNS resolves correctly; the TCP connect is
   rejected in ~15 ms) while the Droplet reaches it fine. Since the laptop moves
   between networks and launchd fires the job wherever it wakes, the ping falls
   back to sending **from the Droplet** over the SSH connection this job already
   requires. Without that, a backup that genuinely succeeded would report as
   failed purely because of where the laptop happened to be — and a monitor that
   cries wolf gets ignored, which is worse than not having one. If both paths
   fail, nothing is sent and the check alerts on absence, which is the correct
   outcome.
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
- **The retained window has two holes** (observed 2026-08-10). `2026-08-02` is
  missing entirely — the job never fired, and the catch-up the following morning
  was recorded as *that* day's run, so the 08-02 snapshot does not exist and
  cannot be recovered. `2026-08-09` is a **partial** failure of a different kind:
  the database dump succeeded, then the SSH connection dropped
  (`Connection reset by peer`) and the configs tarball failed 86 minutes later.
  The database dump for 08-09 is present and verified good; only the configs
  snapshot is absent.
- ⚠️ **The two dead-man's-switch ping paths are not independent.** The direct
  `hc-ping.com` call is blocked on some networks, and the documented fallback
  sends the ping *from the Droplet* over the SSH connection the job already
  needs. On 08-09 that connection was the thing that broke, so **no ping was
  sent at all** — not start, not failure. That is the designed behaviour (the
  check then alerts on absence, which is correct), but it means a Droplet-side
  network fault silences the start ping and the failure ping together, leaving
  absence as the only signal. Worth confirming the check's period and grace are
  tight enough to catch a single missed run.

### Taking a manual dump

```bash
cd /opt/budget-buddy
docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip \
  > "backups/pre-change-$(date +%Y%m%d-%H%M).sql.gz"
```

### Checking that a dump actually restores

✅ **Executed for the first time on 2026-08-10, and it works.** All **14** retained
dumps (`2026-07-27` … `2026-08-10`) restored cleanly into a disposable container —
16 tables, every core table populated, row counts climbing monotonically day over
day. Until that date this procedure had never been run: two weeks of dumps and no
evidence any of them worked.

```bash
python3 scripts/restore_check.py ~/path/to/backups/droplet/budget-buddy
```

Point it at a directory (it takes the newest dump by the date **in the filename**)
or at one `.sql.gz`. It creates its own throwaway Postgres container, restores with
`ON_ERROR_STOP=1`, counts every table, and exits non-zero on any failure naming the
file it rejected. It talks to the container over `docker exec` only — no published
port — so it **cannot** reach production or your development database.

⚠️ **A dump needs TWO roles to already exist, and the second one is easy to miss.**
`pg_dump` writes `OWNER TO admin` *and*, since `sql/30_app_role.sql` shipped, 33
`GRANT ... TO budget_app` lines — but it creates neither role. Restoring into a
container that has only `admin` fails at `role "budget_app" does not exist`. Because
grants are the **last** thing in a dump, every row is already loaded when it happens:
the tables look complete, the counts look right, and the restore has still not
succeeded. `restore_check.py` reads both roles out of the dump and creates them; a
hand-rolled restore must do the same.

⚠️ **Check the exit code of `psql`, not of a pipeline.** `... | psql ... | head` reports
`head`'s status, which is always 0. That is exactly how the failure above was missed on
first inspection.

Scheduling this monthly is a reasonable next step now that it is trusted; it is
deliberately manual for the moment.

### Restoring

Into a **genuinely throwaway database first** — never straight at production.
`restore_check.py --keep` does this and leaves the container up for inspection:

```bash
python3 scripts/restore_check.py backup.sql.gz --keep
# then: docker exec -it <printed name> psql -U admin -d restore_probe
# and when finished: docker rm -f <printed name>
```

⚠️ This section previously showed
`gunzip -c backup.sql.gz | docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME"`
under the heading "throwaway local database". That command targets the **local
development database** — the opposite of a throwaway. Restoring into `db` merges
production rows into whatever is already there.

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
   repository and `.env` from the configs backup. The landing page comes from its
   own repo — clone `CaddisMaster/seandesmet.com` and follow its `SETUP.md`.
   **Then check `.env` names a version:** `grep '^TAG=' .env` (#190). A configs
   backup taken before that pin existed has no such line, and step 8 will fail
   naming `TAG` rather than starting an arbitrary image — which is the designed
   behaviour, not a fault. Add `TAG=<the version you mean to run>`.
6. **Nginx:** write the site files from §3, symlink into `sites-enabled`,
   `nginx -t`, reload.
7. **TLS:** `certbot --nginx -d ...` per domain. Re-read the §4 warning about
   lineage names before copying any `ssl_certificate` path verbatim.
8. **Start:** `docker compose up -d`. The empty volume triggers `schema.sql`, so
   you get the current schema directly — no migration replay needed.
9. **Give the app role a password.** `schema.sql` creates `budget_app` but
   deliberately sets no password (this repository is public), while the `.env`
   you restored in step 5 already points the app at it. Until you do this, the
   web container starts and cannot connect:

    ```bash
    cd /opt/budget-buddy
    docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
      -c "ALTER ROLE budget_app PASSWORD 'the-value-of-DB_APP_PASSWORD-in-.env';"
    docker compose up -d --force-recreate web
    ```

    Use the password already in the restored `.env` rather than generating a new
    one — then `.env` needs no edit. If you would rather rotate it, change both
    together.

10. **Restore** the most recent dump (§7).
11. **Baseline the migration tracker:** `python scripts/migrate.py --baseline`.
    `schema.sql` is current, so every numbered migration is genuinely already
    applied and none should be executed.
12. **Verify:** load the site over HTTPS, log in, confirm the dashboard figures
    match the pre-loss state, and confirm the container runs as a non-root user
    (`docker compose exec web whoami` → `appuser`).
13. **Re-point** the Mac backup job at the new host.

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

**External monitoring: a DigitalOcean Uptime check watches `/healthz`** (set up
2026-07-27). One check is free per month; it runs at 1-minute intervals from
multiple regions and can alert on `down`, `latency` and `ssl_expiry`.

Two settings are load-bearing:

- **Watch `/healthz`, not a page.** This replaced Uptime Kuma, which had been
  watching the home page — it would have reported green throughout a database
  outage, which is the failure it existed to catch.
- **Accept only `200-299`.** `/healthz` returns **503** on database failure, so a
  status range that swallows 5xx defeats the entire purpose.

It also runs **off-box**, which the old self-hosted monitor did not: a monitor
living on the machine it watches dies with the thing it is meant to report on.

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
