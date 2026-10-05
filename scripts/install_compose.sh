#!/bin/sh
# #433 — put a release's docker-compose.yml in place on the Droplet.
#
# Runs ON the Droplet, from the deploy directory, piped in over ssh by
# release.yml and rollback.yml:
#
#   ssh ... "cd /opt/budget-buddy && sh -s -- <sha256> <version> <release|rollback>" \
#     < scripts/install_compose.sh
#
# The workflow has already streamed the file to `.docker-compose.yml.new` in the
# same directory. This script refuses it unless it hashes to what the runner
# checked out, then renames it over the live file. The rename is the atomic step:
# a dropped connection leaves at worst a stray `.new`, never a half-written
# compose file that every later command would read.
#
# Before #433 the file only ever arrived by a hand scp from the Mac, so nothing
# tied it to the image, and at 0.12.0 the two drifted apart (#432).
#
# ⚠️ THE db GUARD. Copying the file automatically removes the human who used to
# stand between "the repo edited the db service" and "the deploy's `up -d`
# recreated the database container" (RUNBOOK §5, "changing the db service at
# all"). So before installing, compare the config hash compose WOULD compute for
# `db` under the new file with the hash label on the running db container. That
# label is exactly what `up -d` compares to decide on a recreate, so a mismatch
# here is a recreate there, whatever the cause, including a compose upgrade that
# changed the hashing.
#   release  — refuse, leaving the old file in place: a db change is a scheduled
#              operation with a verified dump in hand, never a release side effect.
#   rollback — warn and install anyway: an incident is no time to be strict, and
#              rollback.yml never touches db (`up -d --no-deps`, db excluded).
set -eu

EXPECTED="$1"
VERSION="$2"
MODE="$3"
STAGED=.docker-compose.yml.new
LIVE=docker-compose.yml

case "$MODE" in
  release|rollback) ;;
  *) echo "::error::install_compose.sh: mode must be release or rollback, not '$MODE'"; exit 2 ;;
esac

if [ ! -f "$STAGED" ]; then
  echo "::error::$STAGED is missing: the copy to the Droplet did not arrive."
  exit 1
fi

ACTUAL="$(sha256sum "$STAGED" | cut -d' ' -f1)"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  rm -f "$STAGED"
  echo "::error::docker-compose.yml hash mismatch: the Droplet received $ACTUAL, but the ${VERSION} checkout is $EXPECTED. Nothing was installed or swapped."
  exit 1
fi
echo "received docker-compose.yml $ACTUAL (matches ${VERSION})"

# A fresh box has no db container, so there is nothing to recreate.
DB_ID="$(docker compose ps -q db 2>/dev/null || true)"
if [ -n "$DB_ID" ]; then
  WANT="$(TAG="$VERSION" docker compose -f "$STAGED" config --hash db | cut -d' ' -f2)"
  # The pipe hides a failed `config` behind cut's status, so check the result:
  # an empty hash would otherwise read as "db changed" and blame the wrong thing.
  if [ -z "$WANT" ]; then
    rm -f "$STAGED"
    echo "::error::docker compose could not read ${VERSION}'s docker-compose.yml (config --hash db printed nothing). Nothing was installed or swapped."
    exit 1
  fi
  HAVE="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$DB_ID")"
  if [ "$WANT" != "$HAVE" ]; then
    if [ "$MODE" = release ]; then
      rm -f "$STAGED"
      echo "::error::${VERSION}'s docker-compose.yml changes the db service, so this deploy's up -d would recreate the database container. Apply it as a scheduled operation first (RUNBOOK §5, changing the db service), then re-run. Nothing was installed or swapped."
      exit 1
    fi
    echo "::warning::${VERSION}'s docker-compose.yml defines db differently from the running container. The rollback leaves db alone, but the next bare 'docker compose up -d' will recreate it (RUNBOOK §5)."
  fi
fi

chmod 644 "$STAGED"
mv -f "$STAGED" "$LIVE"
echo "installed docker-compose.yml $(sha256sum "$LIVE" | cut -d' ' -f1)"
