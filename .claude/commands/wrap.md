---
description: Run the end-of-session wrap-up — PR, verify at localhost:5001, notes in one pass, merge.
---

Run the session wrap-up for this repo.

`CLAUDE.md` §"Git & Development Workflow" is the source of truth for *policy*. This command
carries the **sequence** and the **traps**. If the two disagree, `CLAUDE.md` wins and this file is
what needs fixing.

⚠️ **This repo is public.** Nothing personal or infrastructure-shaped belongs in this file — vault
paths, host names, the deploy user and the backup layout all live in the gitignored
`CLAUDE.local.md`. Read that for the details of step 3; do not restate them here.

Work through the steps in order, reporting after each. Stop and ask rather than skipping — the
ordering is the point.

## 1. Open a PR for the locally-tested unit(s)

Do not work directly on `main`.

- Confirm `./test.sh` has actually been run in full this session. If not, run it now. CI does not
  click through the app, and Jinja's silent-empty-string failure mode means a template typo
  renders as blank rather than raising — content-asserting tests are the only net.
- If anything under `app/` changed, add a `CHANGELOG.md` entry under `## [Unreleased]`, written
  for someone reading it in a year: what changed and why it matters, not what the diff says.
  (The `Stop` hook in `.claude/hooks/` will catch you if you forget, but it fires at the end —
  writing it here is cheaper.)
- Open the PR with `Closes #<issue>`, one line per issue closed.
- **Batch by coherence, never by calendar.** A shared file surface, test surface, or one
  user-facing story. A schema migration **always stands alone** — bundling it obscures the deploy
  ordering it depends on (additive before the pull, drops after).

## 2. Rebuild and verify locally

```bash
docker compose up -d --build web
```

Confirm it is actually serving at <http://localhost:5001> — not merely that the container
started. If the change touched a flow rather than a template, drive the real HTTP surface with
the `verify` skill in `.claude/skills/verify/`, which already solves the CSRF cookie-jar problem
that otherwise costs an hour.

## 3. Update the notes — in ONE pass, at the very end

This is a standing rule and it is deliberate: note-writing happens once, at the end, not
incrementally through the session.

Read `CLAUDE.local.md` for where the notes actually live — the reference notes, the per-release
tracker and the daily diary are all described there, and their locations are not in this repo.
Update the Claude project memory in the same pass.

If `CLAUDE.local.md` is not present, you are in a fresh clone and this step does not apply to
you. Say so and move on; nothing in the app depends on it.

## 4. Get it merged

Push, wait for CI green, squash-merge — then **check the run on `main`**.

⚠️ **A green PR does not predict a green `main` (#281).** The `changes` classifier
fails open on a push to `main`, so an inert PR — docs, or a workflow-only change —
SKIPS the expensive steps and meets them for the first time *after* merge. Worse,
a skipped step reports **pass**, not skipped: `gh pr checks` prints "Tests pass"
for a run that executed no tests, and it is indistinguishable from a real one at a
glance. Two merges have ridden past a red `main` this way.

So after every squash-merge: `gh run list --branch main --workflow ci.yml --limit 1`,
and read it before starting the next thing. ⚠️ **Do not re-run a red one before
reading it** — a race goes green on re-run and hides.

⚠️ **Do not push to a branch whose PR may already have been merged.** PR #113 was merged while a
follow-up commit was being pushed to its branch; the commit landed on the branch and never
reached `main`. The symptom is the PR head frozen at an older SHA while the branch ref moves on.
If a stacked branch is involved, **rebase** onto `main` after a squash-merge — merging instead
leaves the squashed commits showing as unmerged and drags unrelated files into the diff.

## What is NOT part of the wrap-up

**Releasing.** Cutting a GitHub Release, the `production` approval gate, the deploy and the
"What's new" strip on the dashboard are a separate event with its own sequence — see
`CLAUDE.md` and the `release-prep` agent in `.claude/agents/`. A session that merged three PRs has
not shipped anything, and `/wrap` should not imply otherwise.

Finish by telling me in three or four lines: what merged, what is still open, and what the next
session should pick up.
