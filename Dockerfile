# ⚠️ STAGE ORDER IS LOAD-BEARING. `prod` must stay LAST.
#
# A Dockerfile with no explicit --target builds the FINAL stage, and three
# things build this file with no target: CI's docker-build job, the release
# workflow, and a bare `docker build .`. If `dev` ever becomes the last stage,
# every one of them silently starts shipping pytest and the dev dependencies to
# production. Add new stages ABOVE prod, never below it.
#
# The base image is pinned by DIGEST (#405): a tag is movable, and whoever
# controls it decides what the next build runs. The digest is the multi-arch
# INDEX, so one line serves the arm64 VM and the amd64 CI/Droplet alike.
#
# ⚠️ The tag names the Python PATCH (3.14.7), not 3.14, on purpose. Read in
# Dependabot's docker updater source (not assumed): it rewrites a pinned
# digest in place, but a GitHub-side experiment
# (`docker_digest_only_update_suppression`) can skip a digest-only refresh
# of a versioned tag whose NAME is unchanged, and nobody outside GitHub can
# see whether it is on. Pinned to `3.14-slim`, that would freeze the image,
# with no Debian security rebuilds, until 3.15 existed, and nothing would
# say so. A patch tag changes name at every Python patch, and a tag change
# is never suppressed.
FROM python:3.14.7-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2 AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
# Run as an unprivileged user — the app only READS /app (state is in Postgres,
# Flask-Limiter is in-memory, /admin/backup streams pg_dump to the response).
# gunicorn binds :5000 (>1024) so no privileged-port need.
RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser . .

# #305 — the version this image WAS BUILT AS, so a deploy can be verified.
#
# ⚠️ This is deliberately a build arg and not a runtime env var from the
# Droplet's .env. `TAG` there records what compose was TOLD to pull; this
# records what the image IS, which is the only one of the two that a stale or
# hand-restored .env cannot make lie. #190 removed the cause of a silent stale
# deploy — production sat three releases back with /healthz green — but left
# nobody able to SEE which version was serving.
#
# The defaults matter: CI's docker-build job, the local override and a bare
# `docker build .` all pass no build arg, and an undefaulted ARG interpolates to
# the empty string, which renders as a blank cell indistinguishable from a lost
# version. Keep both in `base` — `prod` and `dev` inherit ENV from it, and an
# ENV set below `FROM base AS dev` would stamp only the image nobody ships.
#
# Placed after COPY on purpose: a new version invalidates this layer and
# everything under it, and there is nothing under it but metadata.
ARG APP_VERSION=dev
ARG APP_COMMIT=dev
ENV APP_VERSION=${APP_VERSION}
ENV APP_COMMIT=${APP_COMMIT}

USER appuser
EXPOSE 5000
# --threads keeps one process (in-memory Flask-Limiter stays consistent) while
# letting a slow request run concurrently with others. --timeout 120 gives the
# v10.3 Ask tool-use loop room for several sequential model calls (the default
# 30s killed the worker mid-loop → the HTMX request hung).
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:5000", "app:app"]

# Local development only — selected by docker-compose.override.yml, which is
# never used on the Droplet. Carries the test dependencies so `./test.sh` does
# not reinstall pytest into a fresh container on every single run.
#
# Installed as root, so they land in the system site-packages alongside the
# application's own dependencies rather than in /home/appuser/.local — which
# means the interpreter finds them without any PATH involvement.
FROM base AS dev
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
USER appuser

# The shipped image. Deliberately empty: it is `base` under a name, existing
# only so that this — and not `dev` — is the final stage. See the note at the
# top of the file.
FROM base AS prod
