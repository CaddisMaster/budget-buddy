# Current status, standing decisions, and release history

> Split out of `CLAUDE.md` on 2026-08-17. Read at the START of a session —
> ⚠️ this file lags `main`; reconcile against `git log` and `gh issue list` rather than trusting it.

## Current Status

### ✅ `0.8.0` SHIPPED AND LIVE (2026-08-20)

**Prod runs `0.8.0`, deployed and verified 2026-08-20.** The `0.8.0` milestone is **closed at
47 issues**. ⚠️ **No milestone is open — the next cycle needs one created.**

**Verified from OUTSIDE, not from the workflow's own report** — the first release where that was
possible:

```
local  md5(app/static/style.css) at v0.8.0  -> 7239e2ec
production serves  style.css?v=              -> 7239e2ec   ✅ match
v0.7.0 was                                   -> 44ef4f4a
```

Plus a second independent witness: `/login` carries the brand mark added in #256.
⚠️ **Do not assume this handle exists next time.** It works only because the overhaul happened
to rewrite the stylesheet; a release touching no static asset is back to trusting the pipeline.
A version on `/healthz` still does not exist. **Decide the handle before cutting.**

**Seven PRs on release day**, on top of the front-end overhaul already on `main`:

| PR | Closed |
|---|---|
| #268 | #222 — History filters by account; plus a latent Export CSV bug |
| #269 | #264 — `./test.sh` runs ruff before pytest, fail-fast, pins shared with CI |
| #270 | #267 — the `verify` skill's teardown delegates instead of duplicating |
| #273 | #271 — `sql/37`, `users.session_token` (migration, stood alone) |
| #274 | #272 — a password change signs out every other device |
| #275 | #257 — Categories shows the drawn colour; fold raised to `PALETTE_SIZE` |
| #276 | — release prep |

Suite **1142 passed, 4 skipped**. ⚠️ Recount rather than trusting that
(`./test.sh --collect-only -q -n0 | tail -1`) — the recorded count has been wrong five times.

⚠️ **The `0.8.0` deploy signed every session out once.** Cookies predating #272 carry no token
to verify. One-time, expected, and called out in the changelog and the What's-new strip.

⚠️ **`sql/36`'s DROP ran BEFORE the image swap**, against a `v0.7.0` container that reads both
tables. Accepted deliberately — one user, a watched deploy, `pg_dump` first — and **that
acceptance does not generalise.** Tracked as **#277**. The header on `sql/36` used to claim the
window was empty; it reasoned about `main`, and deploy ordering is only ever about the image
that is RUNNING. Corrected in #276.

⚠️ **`CLAUDE.md` said to apply migrations by hand and `RUNBOOK.md` says the deploy job does it.**
The runbook was right; the `CLAUDE.md` bullet was stale and cost a session's planning before
anyone read it. Corrected in #276.

### On `main`, not yet deployed (2026-08-20, evening)

**One PR, no app change.** #282 closed #281: CI's image job waited for Postgres with
`pg_isready` over the **unix socket**, and `postgres:16` runs `initdb` against a temporary server
started `-c listen_addresses=''` — socket only, no TCP — then stops it with `pg_ctl -m fast`
before starting the real one. The probe answered READY for the throwaway server, the wait broke
early, and the schema load landed in the shutdown window:

```
psql: FATAL:  the database system is shutting down
```

Now probes `-h 127.0.0.1`. The temp server never listens on TCP at all, so the race is
**removed, not narrowed** — a longer sleep or an N-consecutive-successes rule would have left the
same ~200ms window. An exhausted wait also fails loudly now instead of falling through to `psql`.

⚠️ **The lesson is about the CI shape, not Postgres.** #280 changed `landing/index.html` and
`CHANGELOG.md` only. Its PR was green in **41s**; the push to `main` for the same tree failed in
**3m50s**. Every job runs on both events, but the `changes` classifier **fails open on a push to
`main`**, so the expensive STEPS skip on an inert PR and run on the push. Consequence:

> A docs- or landing-only change meets the image job for the FIRST time after it is merged, and
> the defect it trips belongs to neither that PR nor its author.

When `main` goes red just after a merge that obviously could not have caused it, suspect the
path-gated steps before re-reading the innocent diff. ⚠️ And note a re-run would have gone
**green** here — the window is sub-second. Green-on-re-run is the failure hiding, not a
diagnosis. That is why the guard is `tests/test_ci_postgres_probe.py` rather than a comment: it
asserts no `pg_isready` in that step may omit `-h`, and that the loop has a failure path. Both
fail against the unfixed workflow.

⚠️ The test parses `ci.yml` as **text**, deliberately. PyYAML is in neither requirements file —
it resolves transitively, and the suite runs inside the shipped image whenever `tests/` changes.

**`## [Unreleased]` holds the CI fix and nothing else**, which is not a reason to cut a
release. It briefly also held the ai-atlas landing card (#280); that card was removed again by
#287 on 2026-08-24 after the project was abandoned and its repo deleted, and because it never
shipped in a release its changelog entry was deleted rather than answered with a `### Removed`
line. Prod is still `0.8.0`. Still **no open milestone**.

### Open after `0.8.0`

- **#277** — `release.yml` applies DROP migrations before the image swap (filed 2026-08-20)
- **#36** — date-parked to ~Dec 2026, correctly carries no milestone

### Superseded — the morning of the same day (2026-08-19)

**Three PRs merged after `0.7.0`, all under the then-open `0.8.0` milestone.**

- **#226 — the design system** (#225 phase 1). See the 2026-08-18 block below.
- **#229 — automated triage inverted to opt-in.** See below.
- **#227 — Home, rebuilt.** One squash-merge closing **four** issues:
  - **#223** the page opens with the answer (hero net position, ranked category bars,
    year-over-year as one line);
  - **#232** the four AI surfaces become ONE "Ask your finances" panel — a cached month
    read plus the box. The read merges `compute_month_facts()` with `compute_forecast()`
    into a single model call cached in the **existing `insights` table** (no migration).
    Two new Ask tools, `month_summary` and `month_projection`, so the box reaches what the
    retired cards showed. The v9 NL quick-add is **removed entirely**;
  - **#233** two layout holes closed (a lone stat tile filled a third of its row; Goals in
    the narrow column left a 380px void). Page height 3107 → 2983 at 1440;
  - **#234** the charts redrawn — **Chart.js out, ApexCharts 4.7.0 in**.

Tests: **953** on `main` (was 921 at `0.7.0`). CI on the merge commit is green.

⚠️ **`blueprints/forecasts.py` and `blueprints/agent.py` no longer define a blueprint** —
both lost their routes; 18 registered, was 20. The forecast arithmetic feeds the month
read and an Ask tool; the money agent runs inside the weekly digest, which is deliberately
untouched.

⚠️ ~~**The `forecasts` TABLE is now dead** — dropping it is **#236**~~ — **DONE**, dropped
with `goal_coach` in `sql/36` (PR #265). Kept here because the reasoning still applies to the
next dead table: a migration stands alone in its own PR.

⚠️ **ApexCharts is pinned at 4.7.0 for a LICENCE reason, not inertia.** 5.x is dual-licensed
and 6.x ships a `LicenseEnforcer` that watermarks charts, with terms binding on annual
revenue. 4.7.0 is the last MIT release. `tests/test_design_system.py` asserts the vendored
licence still says MIT and that no enforcer is in the bundle — treat any bump of this
library as a licence decision.

⚠️ **A conflicting PR runs NO CI at all** (learned the hard way this session). #227 sat a
full day with a `CHANGELOG.md` conflict; `pull_request` workflows test the merge ref,
GitHub cannot compute one for a conflicting PR, so nothing queued — and its last real run
had been a **failure**. This file previously recorded that PR as "green, 954 passing".
Check `gh pr view <n> --json mergeable,mergeStateStatus` before believing an absence of red.

⚠️ **Put every `Closes #N` in the PR BODY.** Two of the four were only in commit messages,
which a squash-merge does not reliably carry; `gh pr view --json closingIssuesReferences`
showed only two linked until they were added to the body.

### The rest of #225 is now filed, one issue per page

**14 issues, #237–#250, all on `0.8.0`** — History, Budgets, Accounts, Goals, Scheduled,
Transfer, Categories, Add transaction, Profile, Settings, User management, Login, Change
password, Create user. Written from the templates themselves, so each names what that page
actually is today. Three things worth knowing before picking one up:

- **Five share one shape** — Accounts, Categories, Goals, Scheduled, Transfer are all "Add
  X form on top, Existing X table below", which is the #223 complaint. Decide it once.
- **Three may not deserve to exist** — #247, #249, #250 are 18, 13 and 16 lines and are
  arguably sections of Settings and Profile.
- **Four carry a "do not break this"**: Profile's notification copy is a consent record;
  Settings must never render a secret or its prefix, and `NOT_SCHEDULED` must not read as a
  fault; Login is the second shell and must not leak whether a username exists.

Also open: **#235** (two stacked topbars, ~49px and a hamburger the desktop does not need —
touches `base.html`, so every page) and **#236** (the `forecasts` drop). **Both shipped that evening — see the block at the top.**

⚠️ **#225 stays OPEN** — it is the umbrella, and its own plan is "design-system PR, then
per-page PRs". #226 and Home are done; the 14 are the rest.

### On `main`, not yet deployed (2026-08-18)

Two PRs merged after `0.7.0`, both under the open **`0.8.0`** milestone:

- **#226 — the design system (#225 phase 1).** Two vendored typefaces (`app/static/fonts/`,
  ~52 KB, latin subset, OFL texts beside them), tabular figures on every money surface, a dark
  nav rail in both themes, a gradient hero, one entrance + one hover, and a single
  `prefers-reduced-motion` block. `tests/test_design_system.py` (10 tests) asserts the
  stylesheet itself; 7 were red against `main` first. ⚠️ `--accent` deliberately still holds
  the exact brand blue — `icons/icon.svg` hardcodes it and the PNG rasters have no build step.
- **#229 — automated triage inverted to opt-in.** The `triage` label now runs
  `claude-triage.yml`; the two issue templates and `app/blueprints/feedback.py` apply it
  themselves. Verified after merge: a hand-filed unlabelled issue produced a successful run
  with **0 comments** and both model steps skipped. `skip-triage` is left in place and now
  does nothing.

~~**Open and deliberately held: PR #227**~~ — **merged 2026-08-19**, see the block above.
⚠️ Two claims made here were wrong and are worth keeping as a caution: it was recorded as
"green, 954 passing" when its last CI run had **failed** (two ruff errors), and the
`CHANGELOG.md` conflict noted here was not merely cosmetic — **it suppressed CI entirely**
for a day.

Tests: **932** on `main`; **954** on the #227 branch.

⚠️ **Three defects shipped through a green suite this session** and were caught only by
screenshotting the running app: every chart blank (deleting the doughnut took the shared
`Chart.defaults`/`gridScales`/`initCharts` scaffolding with it), a `hidden` list rendering
anyway (`[hidden]` is user-agent origin and loses to any author `display` rule), and bars
squeezed to a stub on a phone. A Flask test client returns markup — it applies no CSS and runs
no JS. Headless Chromium now lives in `~/.tools/bb-shots/` (outside the repo); `check.mjs`
reports page errors, ink per canvas and real visibility.

### Level with production before this session (2026-08-17)

**Prod runs `0.7.0`, shipped and verified.** `main` carries nothing unreleased beyond the docs
commit recording this session. The `0.7.0` milestone is **closed** (19 issues); **no milestone
is open**, so the next cycle needs one created. **#36 is the only open issue** — date-parked to
~Dec 2026, correctly carrying no milestone.

Tests **921** (measured 2026-08-17; `910 passed, 11 skipped` inside the shipped image — two
legitimately different numbers, see `docs/testing.md`).

⚠️ **Loose ends, none blocking:**

- **Nothing on the public surface distinguishes deployed versions.** `app/static/` did not change
  between `0.6.0` and `0.7.0`, so `css_v` could not verify the deploy and neither could anything
  else reachable without logging in. Decide the verification handle **before** cutting next time;
  a version string on `/healthz` would end this class of doubt permanently and does not exist.
- **The `workflow` token scope is now persistently granted** (needed to push `ci.yml` for #218).
  It was previously withheld on purpose. Either revoke it after workflow work or drop that
  rationale from the notes — do not leave a claimed protection that no longer holds.
- **`docs/status.md` is no longer auto-loaded.** `CLAUDE.md` was split on 2026-08-17 and keeps
  only a pointer, so this file is *more* likely to go unread and stale than before, not less.

**Applied to production this session:** #190's `${TAG}` fix, which had been merged-but-unapplied
since 2026-08-13. The ordering resolved itself — `release.yml` step 0 writes the `.env` pin
before any other compose command, so the `scp` of the new compose file became safe afterwards
rather than needing a hand-typed pin first. ⚠️ The sequence previously recorded here started
with `printf 'TAG=0.6.0' >> .env`; following that *after* a deploy would append a second `TAG=`
line. Check `grep -c '^TAG=' .env` is exactly 1.

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

- **`0.7.0` (2026-08-17) — prod runs `ghcr.io/caddismaster/budget-buddy:0.7.0`.** Variable-amount
  bill alerts (#191) and pending rows pinned to page 1 (#210). One additive migration
  (`sql/35_variable_bills.sql`), applied automatically by `release.yml` step 2. Reusable lessons:

  ⚠️ **`css_v` could not verify this deploy, and neither could anything else public** —
  `app/static/` was byte-identical to `0.6.0` and `sw.js` still read `bb-static-v3`. The deploy
  was confirmed from the box's own output instead, which is the only place the ordering is
  observable: `pinned TAG=0.7.0` → `backup ok` → `Applied 1 migration(s).` → pull → up, with
  `db … Up 6 days` proving the database container was **not** recreated. Ask what will be
  observable from outside *before* cutting.

  ⚠️ **Three of the four defects fixed this cycle were in the release tooling, not the app**,
  and each was found by *using* it rather than by testing it — the suite was green throughout.
  Two were the same defect recurring in the same file days apart, past a comment describing it
  exactly. That is the case for changing a mechanism rather than a document; see
  `docs/testing.md`.

- **`0.6.0` (2026-08-10) — shipped `ghcr.io/caddismaster/budget-buddy:0.6.0`.** Background jobs
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
