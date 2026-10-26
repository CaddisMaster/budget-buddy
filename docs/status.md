# Current status, standing decisions, and release history

> Split out of `CLAUDE.md` on 2026-08-17. Read at the START of a session —
> ⚠️ this file lags `main`; reconcile against `git log` and `gh issue list` rather than trusting it.

## Current Status

▶️ **NEXT SESSION: #493** (the docs sweep), then the container-hardening issues **#497 → #498 →
#499**; **#500** (Postgres) is a scheduled operation and gets no milestone until Sean picks a date.
Dependabot **#487** and **#488** have waited since 2026-10-19 (rebuild the container before testing
#488). Prod runs **`0.13.0`** (shipped 2026-10-12). **On `main`, NOT deployed, all `0.13.1`
content:** #483 (the digest sent once, #494), #491 (rows read by name, #501), #490 (the app
factory, #502), #492 (CI compares `schema.sql` with what the migrations build, #503), **#489** (the
services layer, #506–#519) and **#513** (five more positional unpacks, #514). Production still
sends the digest twice until `0.13.1` ships; cutting it is Sean's call, with no deadline. ⚠️
**`0.13.1` changes `docker-compose.yml`** (#490: web runs `app.wsgi:app`); the deploy ships it
(#433) and `db` is untouched, so the guard passes. **After that deploy:** `docker compose logs
worker` shows `Scheduler running`, and that is the whole check. #489 adds no migration, env var or
compose change. ⏳ **Not yet checked by Sean:** his real Discover export through the import **in
production** (the first real bank file, and the first live call on `anthropic` 1.12.1), a real
phone screenshot, a multi-page PDF (only one page was ever timed: 5.9 s against a 12-page cap),
and #437's device list on his phone. ⚠️ **The Droplet's login banner says `System restart
required`**: a reboot takes Budget Buddy and Orbit Ops down together, so schedule it on its own.
**#36** stays date-parked. ⚠️ The `Acceptance criteria` check is **still not a required status
check** (re-checked 2026-10-23).

### The 2026-10-24 session: #489 finished in twelve parts, and #513 found on the way

| PR | Issue | |
|---|---|---|
| #506 | #489 (1) | `forecasts.py`, `agent.py` → `app/services/`; `tests/test_module_boundaries.py` |
| #507 | #489 (2) | `services/recurrence.py`: next-due maths and the occurrence walkers |
| #508 | #489 (3) | `services/budgets.py`: spend vs budget, the budget-change log |
| #509 | #489 (4) | `services/accounts.py`: the balance SQL, credit utilization and interest |
| #510 | #489 (5) | `services/ownership.py`: `validate_category_account` |
| #511 | #489 (6) | `services/devices.py`: a push device's label |
| #512 | #489 (7) | `services/chart_palette.py`: the fold and the slot assignment |
| #514 | **#513** | five positional unpacks one assignment from a fetch; the scan now sees them |
| #515 | #489 (8) | `services/due_runners.py`; `ALLOWED_LAZY` empty, its criterion claimed |
| #516 | #489 (9) | `services/month_read.py`: every month figure; `insights.py` is a route |
| #517 | #489 (10) | `services/goals.py`: goal progress and projection |
| #518 | #489 (11) | `services/ask_tools.py`: **the security boundary**; `ask.py` is the route |
| #519 | #489 (12) | `services/history.py` + `helpers.history_filters`; `ALLOWED_EDGES` empty, #489 closed |
| this PR | **#520** | this record |

- **The issue named about half the edges.** An AST scan found 34 blueprint→blueprint imports in 15
  modules, against the issue's list of about a dozen. The test was written from the scan, not the
  issue.
- **The allowlist is checked in BOTH directions**: a new edge fails, and so does a listed edge
  that has gone, so every PR had to delete the lines it made false and the list could only
  shrink (28 → 0; lazy markers 7 → 0). Each PR's test run named exactly the edges it predicted
  before the list was edited. ⚠️ **Its positive control had to move once**: it asserted `main →
  budgets` existed, and part 3 deleted that edge. It now anchors on an import the finished state
  keeps (`main.py` importing a service).
- **The order was leaves first, not the issue's "outward from the `# lazy` imports".** Moving
  what others import (`budgets`, `accounts`, `transactions`' pure helpers) before what imports
  them meant every PR removed edges and added none. Moving Ask's tools first would have traded
  one edge for three service→blueprint ones.
- **The services rule is stronger than the criterion**: no service imports a blueprint either,
  so the cycles could not simply move into `services/`.
- **Sean chose the History split** (#519) from three previewed options: the balance walk in
  `services/history.py` with explicit args, the request-filter parsing in `helpers.py`, and each
  blueprint rendering its own `<tbody>` (`transfers._history_tbody()` is the twin). No service
  reads the request or renders a template.
- **Every moved guard was mutated at its new home.** Both `FOR UPDATE` locks (the schedules lock
  fails both race scenarios; the transfers lock fails `test_concurrent_runs_post_exactly_one_pair`),
  `validate_category_account` (8 isolation tests across all three callers), Ask's
  `_resolve_category` (`test_dispatch_cannot_total_another_users_category`), the two Jinja globals
  `accounts.py` now registers with `add_app_template_global` (4 failures each), and the finished
  boundary rule itself.
- **#513, found reading the due-runners**: `due = cursor.fetchall(); for (sid, …) in due` got past
  #491's scan, which matched an unpack only directly on the call. Five sites, both due-runners
  among them. The scan now flags an unpack through a name the SAME function bound from a fetch;
  per-function scoping keeps `build_budget_report(spend_rows)` and `category_spending()`'s by-name
  tuples quiet. Fixed before part 8, so the runners moved already reading by name.
- **Two traps hit and caught by counting.** A blanket `_advance_past` rename turned two tests into
  `testadvance_past_*`, which `python_functions = test_*` silently stopped collecting (1786 vs
  1788 passed, nothing red); diffing collected IDs against `main` found it. And a `web` rebuild
  started while `./test.sh` was running made pytest exit with `service "web" is not running`
  after behave passed, which a background wrapper reported as success. **Never rebuild `web`
  while the suite runs in it.**
- **`transfers` had two unmarked lazy imports** ("to avoid a circular import", no `# lazy`), which
  the lazy-marker test could not see. The edge test did, so they went with the last edge.
- **Pointers moved with the code**, including the two agent prompts in `.claude/agents/` that
  named `_load_history`'s `ORDER BY` pair, the `push.js`, `style.css` and template comments, and
  `test_history_row_shape.py`'s source path (its precondition test would have failed loudly).
- **Not seen in a browser:** push is not configured on dev, so part 6 proved Profile's device rows
  are built (the route builds them unconditionally) but not rendered; `test_push_devices.py`
  covers the render with push on.
- The untracked `budget-buddy-container-hardening-handoff.md` was **deleted** on Sean's request;
  its content lives in #497–#500.
- **Milestones:** #489, #513 and #520 are on `0.13.1`.

### The 2026-10-23 session: three review issues merged, and a hardening review filed

| PR | Issue | |
|---|---|---|
| #501 | **#491** | Rows read by column name everywhere; an AST scan keeps positional reads out |
| #502 | **#490** | `create_app()` starts no scheduler; `app/wsgi.py` is gunicorn's entry and the only scheduler start |
| #503 | **#492** | CI compares the migrated database with a fresh `schema.sql` one, object by object |
| this PR | **#504** | this record |

- **An outside container-hardening review** (an untracked
  `budget-buddy-container-hardening-handoff.md`, read at `b14ba7d`, never committed, deleted
  2026-10-24) became four issues: **#497** root-owned code, **#498** runtime lockdown, **#499**
  scan/SBOM/signing, **#500** the Postgres window. Its diagnosis held. Corrected on filing:
  its "done when" wanted `/healthz` to report the version (a standing decision says never;
  `/settings` does), it named `test_hardening.py` for compose assertions (that file is HTTP
  hardening), the `$HOME` check belongs to #498 not the optional `nologin` item, tmpfs is
  charged to `mem_limit`, and #499's scan starts report-only.
- **#491's issue listed about half the sites.** An AST scan found ~30 in 12 files (Ask tools,
  every chart payload, the schedule loops). Scope was commented on the issue first.
- **#490 kept the old-compose matrix row** rather than retiring it: pre-#402 files run the
  image's new CMD, and 0.12/0.13 files' `app:app` resolves through a lazy module attribute that
  builds via the factory and starts nothing. #483's shutdown-in-`run-scheduler` was deleted as
  unreachable. ⚠️ **Never add a root `wsgi.py`/`app.py`**: Flask's CLI imports those first.
  Verified on the built image in five boots by counting `APScheduler` threads. A mutation
  showed `@with_appcontext` on `run-scheduler` changed nothing (Flask's CLI pushes the context
  itself), so it went.
- **#492 compares catalog facts as sets**, not a `pg_dump` diff: column order is free, names
  and grants are compared. Replayed against `sql/38`–`41`'s real commits first (all match); a
  `schema.sql` carrying #314's `amount = amount` fails, naming each constraint. The script is in
  the classifier's `sql` filter. It ran for real on its own PR in Actions.
- **Found for #498:** gunicorn writes a control socket to `/home/appuser/.gunicorn/` at
  startup, which `read_only: true` would break. Commented on the issue.
- **Milestones:** #490, #491 and #492 were filed without one (as were #489 and #493) and were
  put on `0.13.1` once merged, so the milestone records what that release carries.

### Older sessions

Every session from 2026-08-17 to 2026-10-21 is in [`status-archive.md`](status-archive.md),
unedited (#493). Read it for the reasoning behind a decision, not for current state.

### `CLAUDE.md`'s status block as it stood on 2026-10-26 (moved here by #493)

> Moved out of `CLAUDE.md`, unedited, when its `## Current status` was cut down to current state
> and standing rules. The per-release detail below is history; the rules it argued for are
> restated briefly in `CLAUDE.md`.

▶️ **On `main`, not yet deployed (`0.13.1`'s content): #483** (the digest sent twice, #494),
**#491** (rows read by name), **#490** (the app factory: gunicorn runs `app.wsgi:app`, and the
compose file changes with it), **#492** (CI compares `schema.sql` with what the migrations build),
**#489** (the `app/services/` layer, #506–#519: no blueprint imports another) and **#513** (five
more positional row reads). Cutting it is Sean's call, with no deadline. **NEXT UP:** **#493**,
then the container-hardening issues **#497–#500**, in the order `docs/status.md` gives. ✅ **Prod runs `0.13.0`, shipped 2026-10-12** (27 PRs; three additive migrations
`sql/39`–`41`; `pypdf`; statement import #445–#474; the device list #437). The deploy log is in
`docs/status.md`: **#433's compose copy passed on its first real run** and left `db` untouched,
and RUNBOOK §3's Nginx upload block was added first and proven from outside (a 2 MB POST went
from Nginx's 413 to the app's response). ⏳ **Not yet checked by Sean:** his real Discover
export **in production** (the first real bank file, and the first live call on `anthropic`
1.12.1), a real phone screenshot, a multi-page PDF, and #437 on his phone. ⚠️ The Droplet's
banner says `System restart required`; a reboot takes Orbit Ops down too. **#36** stays
date-parked.

🛑 **THE MAC HAS NO CLONE OF THIS REPO** (2026-10-05). Every Droplet step runs from the Mac, and
every line of code lives in the VM. So "scp it from your clone" copies whatever file is in the
Mac shell's directory. A `diff` against that file is then vacuous, and at 0.12.0 it printed
`identical` over the stale v0.11.0 compose file. #433 (shipped in `0.13.0`, proven on its first deploy) makes the
workflows copy it themselves; as break-glass, use RUNBOOK §5's recipe:
a download pinned to a tag, checked against a hash taken from the repo at both ends. **Before
handing Sean commands for the Mac, check what exists there**, and give one command per copy box.
Rolling back past #402 still needs the old compose file first (RUNBOOK §5).

⚠️ **"Issue criteria are claimed by tests" is still NOT a required check** (re-checked
2026-10-23 against branch protection), so a red result doesn't block a merge. It's Sean's
setting.

🛑 **AN ISSUE BEING OPEN IS NOT EVIDENCE THAT IT IS OPEN WORK.** Kept as a standing rule now that
its examples are closed. On 2026-09-04 "#326 and #314, the two migrations" was proposed as the next
session's work when **#326 was not work at all** — Sean had decided on 2026-09-02 to close it as
premise-wrong, and a decision recorded in a comment leaves no trace in `gh issue list`. **Read the
comments, not just the body, before proposing any issue as work.** A mechanical milestone roll
cannot read comments either, so it always overstates the work.

🛑 **AN ISSUE'S SPECIFIED FIX CAN BE WRONG AGAINST THE PLATFORM, not just against the code**
(#314, 2026-09-16). It specified `CHECK (col = col)` to reject a NaN, on the premise that
`NaN <> NaN`. True in IEEE 754; **false in PostgreSQL `numeric`, which defines NaN as equal to
itself** so the type can be sorted and indexed. Verified against `postgres:16`: that constraint
accepts a NaN and stores it, so the migration would have applied cleanly, passed CI and protected
nothing. The working form is `<> 'NaN'::numeric`, and it is the **inverse of Python** — a guard
ported from the app layer into SQL keeps its shape and loses its meaning. `docs/gotchas.md` carries
the rule; `docs/status.md` carries the session.


⚠️ **The open milestone is `0.13.1`** (#483). ✅ **`0.13.0` was closed on ship day** (2026-10-12, 42/42). ✅ **`0.12.0` was closed on ship day** (2026-10-05, 24/24). ✅ **`0.10.0` was closed ON SHIP DAY** (2026-09-11) at
`open=0, closed=25`, with its nine open items moved to `0.11.0` first — the corrective landing, and
the shape every milestone but `0.9.0`'s has closed at. `0.9.0` was closed 2026-09-03, late: it
shipped on 2026-09-02 but was left open to hold a backlog, so **seven commits landed after the
`v0.9.0` tag carrying a milestone for a version that had already shipped**. That contradicts the rule two
sections above — *exactly one is open at a time, closed when that version ships* — and it is the
only milestone in the project's history that has ever been open with work in it. Corrected in
#359; `docs/status.md` records how the boundary was drawn, including the two traps that make it
easy to get wrong.

⚠️ **[`docs/status.md`](docs/status.md) is the detail, and it lags `main` by construction** — it
describes the last session rather than the current tree, and it asserts rather than going quiet.
**Reconcile against `git log` and `gh issue list` at the start of every session.**

- ✅ **`0.12.0`, shipped 2026-10-05** (23 PRs, no new env vars, no migrations, **the
  compose file changed**). The **first** deploy failed at step 3c (`service "worker" is not
  running`), because the Droplet still held the v0.11.0 compose file. Production stayed up on
  0.12.0 with the safe single-worker path. After a tag-pinned, hash-checked copy, the failed job
  re-ran green: `redis:7-alpine` pulled, `worker-1` and `redis-1` started, `running version
  0.12.0` → `worker version 0.12.0 (matches 0.12.0)` → `Nothing to apply for phase after-pull`
  → `Announced 0.12.0 to 3 device(s).` Live `/healthz` 200, and the CSP header is served.
  ⏳ **Not yet checked by Sean:** the installed PWA under the CSP, `docker compose logs worker`
  showing `Scheduler running`, the `[INFO] [<id>]` lines (#404), and the first live model call on
  **`anthropic` 1.11.0** (request shape compared offline against 1.8.0: identical apart from the
  version strings).
- ✅ **`0.11.0`, shipped and verified 2026-09-24** (16 PRs, no new env vars, **one
  migration**). The deploy log read, in order: `backup ok` → `applying 38_money_is_finite.sql ...
  ok` → `running version 0.11.0 (matches 0.11.0)` → `Nothing to apply for phase after-pull` →
  `Announced 0.11.0 to 3 device(s).`, and live `/healthz` returned 200. `sql/38` was rehearsed
  first against a restore of that morning's production dump, because it fails the deploy if a NaN
  exists. The What's-new strip holds one block (the NaN refusal), on Sean's call: nothing else in
  the release is user-facing. ⚠️ **`anthropic` is now 1.7.0 (#379) and still has made no live
  model call**, so the first round trip is still a manual check in prod. ⚠️ The Release was
  **drafted, then published only after `main` was confirmed at the prep commit**. The tag is
  created at publish time, so a merge in between would have shipped a commit the prep never saw.
- ✅ **`0.10.0`, shipped and verified 2026-09-11** (13 PRs, no new env vars, no new
  migrations). `/healthz` 200 on the first attempt; `Announced 0.10.0 to 3 device(s).` Unlike
  `0.9.0` it carries **user-facing change** — the What's-new strip holds three blocks rather than
  one admin-only note. ⚠️ **`anthropic` STILL has not made a live model call**, now on `1.3.0`
  after two bumps (#342, #366); CI sets no key, so the first round trip remains a manual check in
  prod. ⚠️ **The release-prep PR must merge BEFORE the Release is published** — `release.yml`
  builds the release's *target commit*, so publishing first ships an image stamped with the new
  version whose What's-new strip names the old one, and **every check still passes**.
- ✅ **`0.9.0`, shipped and verified 2026-09-02** (35 commits, no new env vars, no
  new migrations). **All three of its first-time mechanisms passed in the real deploy log**, in
  this order: `Nothing to apply for phase before-pull — up to date.` → `running version 0.9.0
  (matches 0.9.0)` → `Nothing to apply for phase after-pull — up to date.`, then `/healthz` 200
  on the first attempt and `Announced 0.9.0 to 3 device(s).` The empty-pass migration case and
  the version-stamp assertion are now **proven**, not just written down. ⚠️ **Still unproven:
  `anthropic` 1.0.0 has made no live model call** — CI sets no key, so the first real round trip
  is the month read and the Ask box being exercised in prod by hand. ⚠️ The release carried **no
  user-facing feature at all**: the single `### Added` was the admin-only version stamp, so the
  What's-new strip holds one block naming a `/settings` card, and every `### Fixed` was a patch
  fix. The `0.8.0` milestone is closed at 47 issues; `0.9.0`'s stays open for its backlog.
  Dependabot **#342 was deliberately held out** of the bundle and rides `0.10.0`. The bundle
  briefly also held the ai-atlas landing card (#280); **#288 removed that card again on
  2026-08-24** after the project was abandoned and its repo deleted, and since it never shipped
  in a release its changelog entry was deleted outright rather than answered with a
  `### Removed` line — which is why nothing in `0.9.0`'s notes mentions it
- **The tracker is NO LONGER empty**, and its contents are now mostly **#309's output rather than
  #309 itself**. ✅ **The full-repo read is COMPLETE** (closed 2026-08-31, ten tranches): every
  tracked file — `app/`, `sql/`, `scripts/`, `tests/`, `.github/`, `.claude/` and the root build
  config — has been read once, on purpose. It filed **twelve issues**: **#312**, **#314**,
  **#315**, **#316**, **#319** (tranches 1–4), **#323**, **#324**, **#326**, **#328**, **#329**
  (5–7), **#333** (8) and **#339** (9). **#315** is still the one to read first (two categories
  sharing a name merge into one budget row and invent an overrun, which the AI month read then
  narrates as ground truth); **#328** is the loudest (the `scripts/` ingest pipeline cannot insert
  a row and has not been able to since the app gained users). **#36** stays date-parked to
  ~Dec 2026 with no milestone. (The open milestone at the time was `0.10.0`.)
  ⚠️ **`docs/status.md` carried the review's resume point, and it went stale THREE times** (#331,
  #337, #343) — every time because a tranche merged without touching it. **The loop ended because
  the work finished, not because the problem was solved**; the next tranche-shaped piece of work
  will reproduce it unless something mechanical links merging to updating
- ⚠️ **`anthropic` is on `1.0.0`** (#286, merged 2026-08-25) alongside the minor/patch group
  (#285). The major bump was read rather than rubber-stamped, because the mocked `_call_*_model()`
  seams mean a green suite says nothing about an SDK change. **`tests/test_sdk_call_shape.py`
  now closes that gap** — it introspects the installed SDK and asserts it still accepts what
  `ai.py` passes, with no network call and no key. ⚠️ #285 also proved the ruff double-pin real:
  Dependabot bumped `requirements-dev.txt` alone and CI went red until `ci.yml` matched
- ⚠️ **A green PR does not predict a green `main`.** The `changes` classifier fails open on a
  push to `main`, so an inert PR (docs) skips the expensive STEPS and meets them for
  the first time AFTER merge — that is #281, found by a landing-page change. Check the `main` run
  after every squash-merge, and do not re-run a red one before reading it: a race goes green on
  re-run and hides. ⚠️ **A skipped job reports `pass`, not `skipped`** — the steps skip inside a
  job that still succeeds, so `gh pr checks` prints "Tests pass" for a run that executed no
  tests, and it is indistinguishable from a real one at a glance (seen on #288). The number to
  trust is the `main` run, or the classifier's own `app=/image=/sql=` line in the "What changed"
  job log
- ✅ **The deploy handle is the version stamp, not `css_v`** (#305). The image is built with
  `--build-arg APP_VERSION`/`APP_COMMIT`, so `/settings` reports which build is serving and both
  deploy workflows fail if the running container is not the release they just deployed. **A
  release no longer needs a handle chosen before cutting** — which `css_v` did, and it proved
  nothing at `0.4.1` or `0.7.0` and worked at `0.8.0` only because the front-end overhaul
  happened to rewrite the stylesheet.
  ⚠️ **It is a build arg, NOT a `.env` variable** — `TAG` there records what compose was *told*
  to pull, which a stale or hand-restored `.env` can make lie. Do not add it to `.env.example`.
  ⚠️ **It is admin-only and must never reach `/healthz`.** This file proposed exactly that for
  months, against a deliberate decision written in `main.healthz` and `admin.integration_status`;
  `tests/test_version_stamp.py` now states the boundary so the suggestion cannot come back
- **Standing decisions that must not be re-opened** are listed in `docs/status.md`. Check there
  before re-filing anything that looks like an obvious improvement

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
  chart). ~~**CSV import**~~ was on this list too, and **was reversed by Sean on 2026-10-05**
  (#445): no reason for the rejection was ever recorded, and what shipped is a
  reconcile-and-fill tool rather than a bulk loader. It compares a statement with the ledger and
  adds nothing that isn't shown and checked.
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
