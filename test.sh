#!/usr/bin/env bash
#
# Run the pytest suite against the dev Postgres, on the same Python 3.14 as
# production. Nothing is installed on your machine.
#
# Usage:
#   ./test.sh                       # run the whole suite (parallel)
#   ./test.sh tests/test_routes.py  # run one file
#   ./test.sh -k semimonthly        # run tests matching a keyword
#   ./test.sh -v                    # verbose output
#   ./test.sh -n0                   # SERIAL — for pdb, or readable failure output
#   SKIP_LINT=1 ./test.sh           # skip ruff and go straight to the tests
#
# Any extra arguments are passed straight through to pytest.
#
# Runs `ruff check` FIRST and stops if it fails (#264) — lint is the cheapest
# class of defect to fix and used to be caught only by CI, four minutes away.
# See the block above the invocation for why it fails fast rather than warning.
#
# Runs in PARALLEL by default, which takes the full suite from ~204s to well
# under a minute. That is safe only because tests/conftest.py derives its
# TEST_PREFIX from the xdist worker id so every worker owns its own database
# rows — read the note there before changing how test users are named.
#
# The default is a BOUNDED 10 workers rather than `-n auto`. Why it is bounded
# at all is a claim about the machine, so it is measured, dated, and says which
# machine — because that machine has already changed once underneath it.
#
# ⚠️ RE-MEASURED 2026-08-31 (#309, tranche 10), on the `jupiter` VM, 8 cores,
# the 1,239-test suite:
#
#     -n auto (8)   61.1s
#     -n 10         61.8s
#     -n 6          76.2s
#     -n 4         106.6s
#
# So on the current machine the bound does nothing: `auto` is EIGHT workers,
# which is fewer than the default of 10, and marginally faster. The two are
# within noise of each other and both are ~1.7x faster than -n 4.
#
# ⚠️ The original rationale — reproduced here because it explains the 10, and
# because it is what a future move back would make true again — was measured on
# a 15-core Mac against an 806-test suite:
#
#     -n auto (15)   ~21s   every core pinned, fans audible
#     -n 10          ~29s   five cores left for the rest of the machine
#     -n 4           ~67s
#
# There, `auto` meant 15 workers inside the Docker VM, the host reported ~1000%
# CPU, and the machine was unpleasant to use during the five consecutive runs a
# verification protocol asks for (#128/#157). None of that describes an 8-core
# Linux VM: there are no cores left over to protect, and `-n auto` is now the
# gentler setting rather than the aggressive one.
#
# ⚠️ Do not trust either table without checking which machine you are on
# (`nproc`). Development moved from the Mac to the VM on 2026-08-14 and the
# front end has flipped five times in twelve days; a figure here is a
# measurement of a moment, not a property of the suite. The default is left at
# 10 because it measures the same as `auto` here and still protects a 15-core
# host if development moves back — but if you are tuning, re-measure first.
#
# ⚠️ CI is deliberately NOT affected: ci.yml invokes `pytest -q -n auto`
# directly and never goes through this script. Ephemeral runners have no
# interactive user to disturb, so full parallelism is right there and bounded
# parallelism is right here.
#
# Pass your own `-n` (including `-n0` for serial, or `-n auto` if you want the
# machine to yourself) and it is respected instead. Reach for `-n0` when you
# need pdb, or when interleaved parallel output is making a failure hard to read.
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

# ─── One run at a time (#206) ────────────────────────────────────────────────
#
# ⚠️ TWO CONCURRENT RUNS CORRUPT EACH OTHER. `conftest.py` builds TEST_PREFIX
# from PYTEST_XDIST_WORKER, which separates workers WITHIN one run and not two
# runs from each other: both spawn gw0..gw9, get identical prefixes, and create
# and tear down the same users. Measured at 424 errors + 1 failure when the
# prefix was briefly hardcoded — and it reads as flakiness, not as contention,
# which is what makes it expensive to diagnose.
#
# The guard lives HERE because this is the one place every path already goes
# through — an agent's tool call and a terminal both invoke this script. Any
# wrapper that inspects the terminal instead (a pane's foreground process, a
# window title) is blind to a bare `./test.sh` from an agent's shell, which is
# why the guard is not in one.
#
# ⚠️ An ADVISORY lock on a descriptor, deliberately not a marker file. A
# "does the lock file exist" check survives `kill -9` and wedges every later
# run, which is the failure that gets a guard deleted rather than fixed. The
# kernel drops this one when the holder dies, whatever killed it — so there is
# nothing to clean up and no trap to forget.
#
# ⚠️ It is taken on fd 9 and NEVER released. This script ends in
# `exec docker compose …`, which replaces the shell; an open descriptor survives
# `exec`, so the lock is held for the exec'd process's whole life. Releasing it
# here would guard the setup and nothing else.
#
# The path is in /tmp rather than the repo so it is never committed. Note this
# means a run started INSIDE a container has its own /tmp and cannot see a
# host-side run — in practice every real run is host-side, and the dev stack is
# what they contend for.
LOCKFILE="${TEST_SH_LOCKFILE:-/tmp/budget-buddy-test-sh.lock}"

if command -v flock > /dev/null 2>&1; then
  exec 9> "$LOCKFILE"
  if ! flock -n 9; then
    echo "✗ A suite run is already in progress (lock: $LOCKFILE)." >&2
    echo "  Two runs share one database and tear down each other's rows." >&2
    echo "  Watch the running one, or wait for it to finish." >&2
    exit 1
  fi
else
  # BSD/macOS has no flock(1). Warn rather than refuse: an unguarded run is the
  # status quo everywhere this script has ever run, and failing here would make
  # the guard worse than its absence.
  echo "⚠ flock not found — cannot check for a concurrent run; continuing." >&2
fi

# Default to a BOUNDED worker count, unless the caller specified their own -n.
# See the header for why this is 10 and not `auto`. Matched as a prefix so `-n0`,
# `-n 4` and `-nauto` are all recognised.
#
# Built as a plain string rather than an array: macOS still ships bash 3.2,
# where expanding an EMPTY array under `set -u` is an "unbound variable" error.
PARALLEL="-n 10"
for arg in "$@"; do
  case "$arg" in
    -n*|--numprocesses*) PARALLEL="" ; break ;;
  esac
done

# shellcheck disable=SC2206  # word-splitting $PARALLEL is intended
PYTEST_ARGS=(python -m pytest -p no:cacheprovider $PARALLEL "$@")

web_is_running() {
  docker compose ps --status running --services 2>/dev/null | grep -qx web
}

# A container started before the `dev` stage existed will not have pytest, and
# one started before #264 will not have ruff. Rather than failing with an import
# error, fall through to the throwaway path, which rebuilds and therefore fixes
# the situation for next time.
#
# ⚠️ Both are probed, deliberately. Probing only pytest would leave a
# pre-#264 container passing the check and then failing on the ruff invocation
# below — turning "your container is slightly old", which this script already
# knows how to repair silently, into a hard error on every run.
web_has_dev_deps() {
  docker compose exec -T web python -c 'import pytest' >/dev/null 2>&1 &&
    docker compose exec -T web python -m ruff --version >/dev/null 2>&1
}

if web_is_running && web_has_dev_deps; then
  echo "→ Using the running web container."
  RUNNER="docker compose exec -T web"
else
  if web_is_running; then
    echo "→ The running web container predates the dev image; using a throwaway one."
    echo "  Run 'docker compose up -d --build web' once and later runs will reuse it."
  else
    echo "→ No running stack; building a throwaway container."
  fi
  RUNNER="docker compose run --rm --build web"
fi

# ─── Lint before tests (#264) ────────────────────────────────────────────────
#
# Ruff was only ever run by CI, so an unused import — the cheapest possible
# defect — was caught in the most expensive possible place. #263 orphaned four
# imports, `./test.sh` went green, CI went red on F401, and a ~4-minute pipeline
# re-ran for a one-line fix.
#
# ⚠️ It FAILS FAST rather than warning and continuing (option A of the three in
# #264). A warning you can ignore is a warning you will ignore, which is exactly
# how this reached CI in the first place. `SKIP_LINT=1 ./test.sh` is the escape
# hatch for when you want the test signal mid-iteration and already know a stray
# import is there; ruff itself takes well under a second, so the normal case
# costs nothing.
#
# ⚠️ This runs BEFORE the `exec` below, which is why the `exec` survives at all.
# The flock on fd 9 is unaffected: it is held by this shell for the ruff run and
# then inherited across the exec, exactly as before. Dropping the `exec` to run
# both and report both (option C) would have changed that documented reasoning
# and needed re-verifying under `kill -9`; fail-fast does not.
#
# ⚠️ `python -m ruff`, not bare `ruff`, for the reason the pytest invocation
# gives: going through the module guarantees the interpreter holding the
# dependency is the one that runs it.
#
# On the throwaway path this is a second `docker compose run`. The build is a
# cache hit by then, and that path is already the slow one — worth it to keep a
# single code path for both cases.
if [ -n "${SKIP_LINT:-}" ]; then
  echo "→ Skipping ruff (SKIP_LINT is set)."
else
  # shellcheck disable=SC2086  # word-splitting $RUNNER is intended
  if ! $RUNNER python -m ruff check; then
    echo "✗ ruff found problems — fix them, or re-run with SKIP_LINT=1 to get" >&2
    echo "  the test signal first. CI runs the same version and would fail here." >&2
    exit 1
  fi
fi

# shellcheck disable=SC2086  # word-splitting $RUNNER is intended
exec $RUNNER "${PYTEST_ARGS[@]}"
