#!/usr/bin/env bash
#
# Run the pytest suite against the dev Postgres, on the same Python 3.11 as
# production. Nothing is installed on your machine.
#
# Usage:
#   ./test.sh                       # run the whole suite
#   ./test.sh tests/test_routes.py  # run one file
#   ./test.sh -k semimonthly        # run tests matching a keyword
#   ./test.sh -v                    # verbose output
#
# Any extra arguments are passed straight through to pytest.
#
# Two paths, and the script says which one it took:
#
#   1. The dev stack is already up  → run inside the LIVE web container.
#      No container to create, no image to build, no dependencies to install.
#      This is the normal case, since `docker compose up` is how you work.
#   2. Nothing is running           → build and use a throwaway container.
#
# Both run the same code from the same image. The fixtures namespace every row
# they create behind a `__pytest__` prefix and tear down only their own, so
# sharing the running container with a serving app is safe by design.
#
# The test dependencies live in the image's `dev` stage (see the Dockerfile), so
# no `pip install` happens here. `python -m pytest`, not bare `pytest`: going
# through the module guarantees the interpreter that has the dependencies is the
# one that runs them. `-p no:cacheprovider` because /app is root-owned (WORKDIR
# created it before the COPY --chown) and pytest cannot write its cache there —
# passing it turns two warnings per run into none.

set -euo pipefail
cd "$(dirname "$0")"

PYTEST_ARGS=(python -m pytest -p no:cacheprovider "$@")

web_is_running() {
  docker compose ps --status running --services 2>/dev/null | grep -qx web
}

# A container started before the `dev` stage existed will not have pytest.
# Rather than failing with an import error, fall through to the throwaway path,
# which rebuilds and therefore fixes the situation for next time.
web_has_pytest() {
  docker compose exec -T web python -c 'import pytest' >/dev/null 2>&1
}

if web_is_running && web_has_pytest; then
  echo "→ Using the running web container."
  exec docker compose exec -T web "${PYTEST_ARGS[@]}"
fi

if web_is_running; then
  echo "→ The running web container predates the dev image; using a throwaway one."
  echo "  Run 'docker compose up -d --build web' once and later runs will reuse it."
else
  echo "→ No running stack; building a throwaway container."
fi
exec docker compose run --rm --build web "${PYTEST_ARGS[@]}"
