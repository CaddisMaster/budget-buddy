# `.claude/` — the harness half

`CLAUDE.md` says what the agreement is. This directory makes some of it *execute* rather than
rely on being noticed. Nothing here is authoritative over `CLAUDE.md`, `CONTRIBUTING.md` or the
workflows — if any of them disagree, this directory is what needs fixing.

```
.claude/
  agents/                   # gotcha-auditor, release-prep, sweeper
  skills/verify/            # drive the real HTTP surface at :5001 (CSRF cookie jar included)
  commands/wrap.md          # /wrap — the end-of-session sequence
  hooks/changelog-guard.sh  # Stop hook: the CHANGELOG rule, locally
  settings.json             # permission allowlist + hook wiring
```

## ⚠️ This repo is public — mind the line

`CLAUDE.md` already draws it: the Obsidian vault, the release tracker, the diary, the Droplet
access details and the backup layout live in the gitignored `CLAUDE.local.md`. **Everything in
this directory is committed**, so the same line applies here.

| Goes in the committed `settings.json` | Goes in `settings.local.json` (gitignored) |
|---|---|
| Repo-relative commands: `./test.sh`, compose, read-only `git`/`gh` | Absolute paths under `$HOME` |
| Anything true for *any* clone | `ssh`, host names, the deploy user, backup paths |

A permission grant in the committed file is a **reviewable line in a diff**. That is the whole
argument for it, and `settings.local.json` is the counter-example: it accreted a grant for one
specific `v10.6.0` commit-message string, which can never match again and which nothing ever
prompted anyone to remove.

`settings.local.json` is not going away and should not — it is the correct home for the machine
half. It is just not the correct home for *everything*.

## The Stop hook

Fires when a session ends. If the branch changed `app/` and not `CHANGELOG.md`, it says so once,
then stays quiet on the next stop.

It mirrors `.github/workflows/changelog.yml` — same `app/**` scope — and does **not** replace it.
The workflow is the enforcement point.

It **fails open on everything**: no git, no `main`, detached HEAD, empty or unparseable stdin,
missing `python3`. In particular, if `stop_hook_active` cannot be read it stays quiet rather than
blocking, because a hook that cannot tell whether it has already spoken would block on every stop
with no way out — the failure mode that turns a nudge into a trap.

The escape hatch is **not** the `skip-changelog` label; a local working tree cannot see PR labels.
It is `SKIP_CHANGELOG_GUARD=1`.

Test it without ending a session:

```bash
echo '{}' | .claude/hooks/changelog-guard.sh ; echo "exit=$?"   # 0 clean, 2 blocking
```

This script is a **twin** of the one in `material-list-import-tool`. A fix to the fail-open logic
in one is a fix in the other.
