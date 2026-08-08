#!/usr/bin/env bash
#
# Stop hook: the CHANGELOG rule, evaluated locally instead of six minutes later.
#
# `.github/workflows/changelog.yml` fails a PR that changes `app/` without
# touching `CHANGELOG.md`. That check is required, so the cost of forgetting is
# a push, a CI round-trip and a second commit. The rule itself is two `git diff`
# calls, so there is no reason to learn it from a red check.
#
# ⚠️ Expect CHANGELOG.md conflicts when a session runs two PRs in parallel —
# both add under `## [Unreleased]`. That is normal and neither branch is wrong.
# This hook makes it marginally MORE likely by making the entry get written,
# which is the right trade.
#
# Twin of the one in material-list-import-tool. Keep them in step: a fix to the
# fail-open logic here is a fix there.
#
# ⚠️ This MIRRORS the workflow, it does not replace it. The workflow is the
# enforcement point; if the two ever disagree, the workflow is right. Scope is
# deliberately identical (`app/**` only) — widening it here would train everyone
# to reach for the escape hatch, which is the same reasoning the workflow's own
# comment gives for not covering test-only or workflow-only changes.
#
# THE ESCAPE HATCH IS NOT THE LABEL. `skip-changelog` lives on the PR and cannot
# be read from a local working tree, so this hook cannot honour it. Set
# SKIP_CHANGELOG_GUARD=1 in the environment instead. It also blocks at most
# once per stop (see stop_hook_active below), so it can never trap a session.
#
# FAILS OPEN, EVERYWHERE. Unparseable input, no git, no `main`, a detached HEAD,
# a missing tool — every one of them exits 0. A harness that blocks work because
# it cannot understand its own input is worse than no harness, and this one
# guards a rule that CI already enforces properly.

# Deliberately no `set -e`: a non-zero from any probe below must fall through to
# the fail-open exit at the bottom, not abort the script with an unknown status.
set -uo pipefail

exit_open() { exit 0; }

command -v git >/dev/null 2>&1 || exit_open

# ---------------------------------------------------------------------------
# Hook input. Claude Code passes a JSON object on stdin carrying, among other
# things, `stop_hook_active` — true when the session is stopping BECAUSE a Stop
# hook already blocked it once. Honouring it is what makes a block a nudge
# rather than a trap: say your piece once, then get out of the way.
# ---------------------------------------------------------------------------
# ⚠️ If `stop_hook_active` cannot be READ, this hook must not block — an empty
# stdin, a reshaped payload or a missing python3 would otherwise mean blocking
# on every stop with no way to notice it had already spoken, which is the one
# failure mode that turns a nudge into a trap. Unknown state = stay quiet.
input="$(cat 2>/dev/null || true)"
[ -n "$input" ] || exit_open
command -v python3 >/dev/null 2>&1 || exit_open

already_blocked="$(
  printf '%s' "$input" | python3 -c '
import json, sys
try:
    print("1" if json.load(sys.stdin).get("stop_hook_active") else "0")
except Exception:
    # Field renamed, payload reshaped, not JSON at all.
    print("unknown")
' 2>/dev/null || echo unknown
)"
[ "$already_blocked" = "0" ] || exit_open

[ "${SKIP_CHANGELOG_GUARD:-}" = "1" ] && exit_open

# ---------------------------------------------------------------------------
# Where are we?
# ---------------------------------------------------------------------------
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit_open
[ -n "$repo_root" ] || exit_open
cd "$repo_root" 2>/dev/null || exit_open

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" || exit_open
# On `main` there is no branch to compare and nothing to open a PR from. A
# detached HEAD is a bisect or a tag checkout — not session work.
case "$branch" in
  main | HEAD | "") exit_open ;;
esac

# Merge-base, not `main` itself: comparing against the tip would report every
# file that landed on main since this branch started as if this branch had
# changed it.
base="$(git merge-base main HEAD 2>/dev/null)" || exit_open
[ -n "$base" ] || exit_open

# ---------------------------------------------------------------------------
# Did app/ change, and did CHANGELOG.md keep up?
#
# `git diff <base>` (no second commit) compares base against the WORKING TREE,
# so committed, staged and unstaged changes are all covered by one call. Only
# untracked files fall outside it, hence the ls-files pass — a brand-new,
# never-added `app/routes/thing.py` is exactly the change most likely to need a
# changelog entry, and it is the one a plain diff cannot see.
# ---------------------------------------------------------------------------
changed_app="$(
  {
    git diff --name-only "$base" -- 'app/**' 2>/dev/null
    git ls-files --others --exclude-standard -- 'app/**' 2>/dev/null
  } | sort -u
)"

[ -n "$changed_app" ] || exit_open

changed_log="$(git diff --name-only "$base" -- CHANGELOG.md 2>/dev/null)"
[ -n "$changed_log" ] && exit_open

# ---------------------------------------------------------------------------
# Block — once. Exit 2 sends stderr back to Claude as a reason to keep working.
# ---------------------------------------------------------------------------
{
  echo "CHANGELOG guard: this branch changes app/ but not CHANGELOG.md."
  echo
  echo "Changed under app/:"
  printf '%s\n' "$changed_app" | sed 's/^/  /'
  echo
  echo "The 'Changelog' check is REQUIRED and will fail this PR. Add an entry"
  echo "under '## [Unreleased]' in CHANGELOG.md describing the change and why it"
  echo "matters to someone reading it later — not what the diff says."
  echo
  echo "If an entry genuinely does not apply (a pure refactor with no observable"
  echo "effect), the PR needs the 'skip-changelog' label — this hook cannot read"
  echo "labels, so add it on the PR, and re-run with SKIP_CHANGELOG_GUARD=1 to"
  echo "silence this locally."
} >&2

exit 2
