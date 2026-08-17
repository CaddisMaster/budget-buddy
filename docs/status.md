# Current status, standing decisions, and release history

> Split out of `CLAUDE.md` on 2026-08-17. Read at the START of a session —
> ⚠️ this file lags `main`; reconcile against `git log` and `gh issue list` rather than trusting it.

## Current Status

### On `main`, NOT released — the `0.7.0` backlog, cleared (2026-08-13)

**Prod runs `0.6.0`; `main` is now genuinely ahead of it.** Three PRs merged, closing **#190** and
**#191**. Tests **875 → 906**. **One additive migration** (`sql/35_variable_bills.sql`). No new env
vars.

⚠️ **The `0.7.0` milestone is no longer empty — #203 was added to it on 2026-08-14** and now
**blocks closing it**. Recount with `gh issue list --milestone 0.7.0` rather than trusting any
figure here. #203 is a defect in `scripts/release_prep.py`: `roll_changelog()` appends one extra
trailing newline to `CHANGELOG.md` on **every** run, and it compounds (measured: 2 → 3 → 4 → 5 → 6
across four release cycles). Cosmetic, but it is in the tool used to cut the release it is blocking,
so fix it before running the prep rather than after.

⚠️ **There IS something to release.** `#191` is a user-facing feature and `## [Unreleased]` carries
an `### Added` and a `### Fixed`, so `0.7.0` is an honest MINOR. What a release still needs: the
**What's-new strip** (a human writes the prose; `scripts/release_prep.py` does the mechanical half)
and the usual gate. Not cut — Sean's call.

⚠️ **#190's fix is merged but NOT APPLIED TO PRODUCTION**, and merging cannot apply it: the
Droplet holds an `scp`-ed copy of `docker-compose.yml`. **Order is load-bearing** — the `.env`
line must land BEFORE the new compose file, or every compose command on the box fails in between:

```bash
cd /opt/budget-buddy && printf 'TAG=0.6.0\n' >> .env && grep '^TAG=' .env   # 1. pin first
scp docker-compose.yml <droplet>:/opt/budget-buddy/                          # 2. then the file
docker compose ps --format '{{.Service}} {{.Image}}'                         # 3a. still :0.6.0
mv .env .env.bak && docker compose config; mv .env.bak .env                  # 3b. MUST fail
```

Until 3b fails naming `TAG`, this fix is **unverified in production** — the deploy workflows pass
`TAG=` explicitly either way, so a green release proves nothing about it. ⚠️ This runs **from a Mac
terminal**, not from the dev VM — see `CLAUDE.local.md` for why the Droplet is unreachable there.

- **PR #194 — `sql/35_variable_bills.sql`**, standing alone. `schedules.is_variable_amount`,
  `transactions.schedule_id` (nullable, `ON DELETE SET NULL`, indexed), and `reminder_log.source`
  gaining `'posted'`; mirrored into `schema.sql`. Verified against a throwaway `postgres:16`: it
  applies to **main's** `schema.sql`, is re-runnable, and a migrated database is identical to a
  fresh one — 130 columns, 54 constraints, every index diffed. ⚠️ The first constraint diff was a
  **false green** (both sides errored on a `"char"` cast, so `diff` compared two empty files);
  print the line count next to the verdict.
- **PR #193 / #190 — the `${TAG}` trap is now enforced, not documented.** See the Key Gotchas.
  Both `release.yml` and `rollback.yml` pin the version into `.env`; `rollback.yml` needs it
  *more* (without it a rollback leaves the box naming the version it rolled away from, so the
  next bare `up -d` rolls production forward again). Also fixed: `RUNBOOK.md` §6's manual fallback
  carried a bare `docker compose pull`, the exact thing issue #22 exists to prevent.
- **PR #195 / #191 — a push alert when a variable-amount bill posts.** See the gotcha for why it
  reads the ledger rather than the schedules. `gotcha-auditor` on the branch: no violations.
- **PR #198 / #197 — `docker compose down -v` is no longer denied** in `.claude/settings.json`.
  Local development moved into an isolated Linux VM on 2026-08-13/14, so the dev database is
  reproducible (`seed_dev.py`, or a dump via `restore_check.py`) and the deny bought nothing.
  ⚠️ **The `git push --force` denies deliberately STAYED** — an isolated dev box bounds the
  filesystem and the credentials, not the remote, so a force-push still reaches GitHub. Do not
  "finish the job" by relaxing those too. `Read(./.env)` also stays; it is about keeping secrets
  out of transcripts, which isolation does not affect.

⚠️ **The operational backlog cleared on 2026-08-10** (five PRs, closing #160, #159, #153, #150;
tests 843 → 875; no migration) and every effective change was already in place, so **there was
nothing to release** at that point — the log limits were applied to the Droplet by hand,
`restore_check.py` runs from the maintainer's machine, `schema.sql` is inert on an existing
database, and the rest was CI and docs. That conclusion held only until #191 landed. The durable
lesson from that session: **every guard involved was already broken or vacuous, and all were
green** — the migrations job, the path filter that would have run it, a missing `ON_ERROR_STOP`,
a drift check that structurally could not see `sql/30`, and a documented restore procedure whose
"throwaway database" command targeted the *development* database. None of it was visible from a
dashboard; all of it was visible from running the thing and checking the exit code.

### Standing decisions — settled, do not re-open

- **Do not send a release notification for a release with no user-facing change** (Sean,
  2026-08-10). The only built-in lever is marking the GitHub Release as a pre-release —
  `release.yml` gates the announce step on `prerelease == false` — but that also suppresses the
  `:latest` update, so it is a misuse with side effects. Usually the right answer is that there
  is nothing to release.
- **#64's privacy design** (in-app feedback carries only what the user typed) — settled
  2026-07-30, do not re-open. Two deliberate deviations from the issue's own wording are worth
  keeping: the env var is **`FEEDBACK_GITHUB_TOKEN`**, not `GITHUB_TOKEN` (a magic name in
  Actions), and the HTTP call uses **stdlib `urllib`**, not `requests`.
- **#115's "the summary text comes from the Release notes body" was REVERSED by #131** (same day,
  before it ever deployed). Do not restore it from the issue's history — see the fixed-body
  gotcha for why the reversal *deletes* the injection surface rather than guarding it.
- **#133 is verified on a real device** (Sean, 2026-08-03). The "cannot be verified until the next
  release" caveat is DISCHARGED; do not re-raise it. It did **not** reopen #131.
- **#140 (Sonnet 5) stands on capability, not cost.** Intro pricing is not reliably cheaper once
  adaptive thinking (billed as output) and a ~30% tokenizer are in play. Do not re-file it as a
  cost win.
- **#111/#83 — the "colour is a pure function of the category" rule is deliberately reversed.**
  See the slot gotcha; it is safe only because the #108 fold caps the chart at 6 real slices.
- **#52 is closed as a one-off flake.** If it ever recurs, candidate fix **C** (pin buildx to the
  `docker` driver) still **does not work** — `type=gha` caching requires `docker-container`.
- **#37 (the unscheduled-backlog holding pen) was closed not-planned.** Its ideas and the standing
  rejection of *net worth over time* remain readable in the closed issue; **do not re-open it as a
  bucket.** Off the list for good: net worth over time (redundant with the net-balance-trend
  chart) and **CSV import**.
- **#33's design fork was decided YES** (Sean, 2026-07-28), against a recommendation to keep it
  read-only: the daily job also runs the due-runners server-side, for every user.
- **Three candidates checked and deliberately NOT filed** (2026-08-03): Flask-Login's 345
  deprecation warnings (**0.6.3 is the latest release**, so there is nothing to bump to); a second
  latent xdist race (schema checked — `push_subscriptions.endpoint` really is the only
  globally-unique non-user-scoped column besides `users.username`); and #36's trigger date
  (verified, not assumed).

**Parked with a trigger:** **#36** budget-report-v2-reads-history (~Dec 2026, when the 6-month
window sits fully inside logged history). The **only** date-parked item, and it correctly carries
**no milestone** — calendar-gated scope is not part of any release.

**Roadmap:** the issue tracker is authoritative — **recount from `gh issue list` rather than
trusting any figure written here.**

### Release history — the reusable lessons

⚠️ `CHANGELOG.md` is the durable per-change record and the GitHub Releases carry the ship notes.
What is kept here is only what a future session would otherwise re-derive.

- **`0.6.0` (2026-08-10) — prod runs `ghcr.io/caddismaster/budget-buddy:0.6.0`.** Background jobs
  you can check on (`app/jobs.py`, the `/settings` panel) plus `scripts/release_prep.py`. One
  additive migration (`sql/34_job_runs.sql`). Verification lessons, all reusable: **the app logs
  NOTHING when the scheduler starts**, so an empty `logs | grep -i scheduler` is not evidence —
  what discriminates is the **thread**, `docker compose exec -T web sh -c 'for p in /proc/[0-9]*;
  do for t in $p/task/*; do cat $t/comm 2>/dev/null; done; done | sort | uniq -c | sort -rn'`
  showing an `APScheduler` line. **`job_runs` is EMPTY immediately after a deploy**, so the panel
  reads NEVER for every job until the daily pass fires — correct, but the first sight of a new
  panel reading NEVER looks exactly like a fault; do not report it as one. And **`pywebpush`
  2.4.0's real delivery is confirmed only as far as the SEND** — the suite stubs `_call_webpush`
  by design, so whether a notification *appeared* still needs a human looking at a phone.
- **`0.5.0` (2026-08-03)** — the Sonnet 5 move and the integration-status panel. ⚠️ **The `css_v`
  deploy check only works when the release CHANGED the CSS** — it proved nothing for `0.4.1`.
  Check `git diff <lastTag>..HEAD -- app/static/style.css` before relying on it. The strongest
  check is **importing a module that did not exist in the previous image**, which is a fact about
  the running code rather than about metadata.
- **`0.4.1` (2026-07-31)** — feedback, release notifications, Python 3.14. ⚠️ **`v0.4.0` was
  tagged and WITHDRAWN at the approval gate, never deployed** — do not treat it as a shipped
  version, and there is deliberately **no `0.4.0` milestone**. Three things that forced or
  followed from it: **`github.event.release.body` is FROZEN at trigger time** (editing the notes
  does not change an in-flight run, and re-running replays the original payload), so combined
  with the announce logic living inside the already-built image **there is no way to change a
  release's notification after publishing** — it takes a new version; **cancelling a release run
  is safe ONLY at the approval gate** (confirm with container uptime + the absence of a new
  `backups/pre-deploy-*` dump); and **two PRs that both add a `## [Unreleased]` entry WILL
  conflict in `CHANGELOG.md`**, which is not a sign either branch is wrong. Three findings from
  the 3.14 work: `--only-binary=:all:` fails on `http-ece` **identically on 3.11**, so always run
  the same probe against the CURRENT runtime before calling a result a regression; **a
  warning-count explosion can be one warning** (345 vs 7, every one `flask_login`'s deprecated
  `datetime.utcnow()`) so count **distinct messages** before reacting; and **Dependabot's ignore
  state is not in `.github/dependabot.yml`** — it lives inside Dependabot and is cleared by
  commenting `@dependabot unignore python` on the original PR.
- **`0.3.1` (2026-07-31)** — the colour-collision fix, cut hours after `0.3.0`. ⚠️ **This was a
  shipped defect that had been identified, filed, judged low-risk and released anyway** — and the
  judgement was wrong on evidence already in hand. **If a known defect's trigger condition is
  "the user has more than N of something", check what N is on the real account before calling it
  unlikely.**
- **`0.3.0` (2026-07-31)** — the triaged backlog, the doughnut fold, automated issue triage and
  the dev-tooling pass. Process notes worth not repeating: **a PR merged out from under an
  in-flight session**, whose symptom (PR head frozen while the branch ref moves on) reads exactly
  like GitHub lag — **`gh pr view --json state` distinguishes them in one call**; a squash-merge
  means a stacked branch must be **rebased** (`git rebase --onto origin/main <old-base>`), not
  merely retargeted; and **CI only triggers on PRs targeting `main`**, so a stacked PR shows "no
  checks reported" and looks broken when it is merely unrunnable. ⚠️ **Four issues running (#87,
  #83, #86, #108) had a specified approach that was wrong on contact with the code** — treat a
  filed approach as a hypothesis and check it before building it. Also: the local dev database was
  wiped and reseeded from `seed_dev.py`, so the old hand-built demo data is gone deliberately
  (`docker compose exec web python scripts/seed_dev.py --username sean`).
- **`0.2.0` (2026-07-28)** — the first FEATURE release under the rebuilt envelope, and the first
  end-to-end exercise of issue → PR → Release → approval gate → automated deploy carrying real
  behaviour. **Push delivery was confirmed on the actual phone** — the one claim no test can make.
- **`0.1.0` (2026-07-27) — the repository reboot.** Kept because it explains why this repo starts
  at `0.1.0` with no earlier tags and why several conventions exist: the app was mature and
  unchanged, and the *envelope* around it was rebuilt — issue→PR workflow, CI+CD in Actions, ghcr
  instead of Docker Hub, a non-root image, `/healthz`, a least-privilege `budget_app` DB role, a
  non-root `deploy` user, and the stack moved `/root/budget-buddy` → **`/opt/budget-buddy`**.
  ⚠️ **The numbered `sql/` files are NOT replayable** — `schema.sql` is the only fresh-DB
  artifact, which is why `--baseline` exists. Docker Hub stopped being the source of truth at the
  cutover and its images were retired 2026-07-28; if ghcr is ever unreachable, **roll forward
  rather than back**. The retired `deploy.sh`/`promote.sh` remain in git history
  (`git show v0.1.0:deploy.sh`). Smoke aside carried over: POSTing `/insights/generate` without
  the form's year/month caches the CURRENT month, not the last complete one — the UI always sends
  them, so it only bites hand-rolled requests.

### Release ledger

⚠️ **`CHANGELOG.md` (committed) is the authoritative record** — do not duplicate it here.
It carries `## [Unreleased]` plus a `## Prior history` summary of the `v1`–`v10.15.0` era,
whose full detail lives in the archived repo's tags and release notes.


## Milestone convention — the full rule

> Relocated from `CLAUDE.md` on 2026-08-17. The short form is in `CLAUDE.md`; this is the detail.

**Milestones = what shipped in a release** (#183, backfilled 2026-08-10). One per released
version, holding the issues **resolved in that release cycle** — not strictly "which PR closed
it", since an issue can be resolved in one cycle and closed by a PR that lands in the next.
Exactly **one milestone is open at a time** (the next release); it is **closed when that version
ships**. An issue closed `NOT_PLANNED` gets none — abandoned scope is not part of any release —
and neither does a date-parked item like #36.

⚠️ **There is deliberately NO `0.4.0` milestone.** That tag was cut and **withdrawn at the
approval gate**; it never deployed, and its contents shipped as `0.4.1`. A milestone means *what
users actually got*, so creating one would assert a release that never happened. ⚠️ `0.3.1`
*does* have one — it was a real shipped patch, not a re-cut of `0.3.0`.

⚠️ **This convention lapsed once already** — used for `0.1.0` and `0.2.0`, then dropped for four
releases, which is why `0.3.0`–`0.6.0` had to be reconstructed from `git log <prev>..<tag>` and
each PR's `closingIssuesReferences`. That reconstruction is possible but tedious; assigning at
filing time costs nothing.
