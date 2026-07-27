#!/usr/bin/env bash
#
# Run the pytest suite inside a throwaway Docker container (same Python 3.11 as
# production — nothing gets installed on your Mac). Uses the local dev db for the
# route/isolation tests, which create and clean up their own __pytest__ users.
#
# Usage:
#   ./test.sh                       # run the whole suite
#   ./test.sh tests/test_routes.py  # run one file
#   ./test.sh -k semimonthly        # run tests matching a keyword
#   ./test.sh -v                    # verbose output
#
# Any extra arguments are passed straight through to pytest.

set -euo pipefail
cd "$(dirname "$0")"

# `python -m pytest`, not bare `pytest`: the image runs as the non-root `appuser`,
# so pip installs console scripts into /home/appuser/.local/bin, which is not on
# PATH. Invoking through the module sidesteps PATH entirely and guarantees the
# same interpreter that did the install is the one that runs.
docker compose run --rm --build web \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest $*"
