# Current status, standing decisions, and release history

> Split out of `CLAUDE.md` on 2026-08-17. Read at the START of a session —
> ⚠️ this file lags `main`; reconcile against `git log` and `gh issue list` rather than trusting it.

## Current Status

▶️ **NEXT SESSION: fix `rollback.yml` — #348.** The recovery path is broken, and `0.9.0` is
what it would be asked to roll back. Both deploy workflows send their remote command as a
**double-quoted `ssh` argument**, where bash performs command substitution regardless of `#` —
a `#` in that string is a comment to the *remote* shell and plain text to the local one.
`release.yml` escapes its backticks three times and misses once (line 269, harmless: `/healthz`
does not exist). `rollback.yml` escapes **none of 16**, and one pair is `` `printenv` ``, so the
runner substitutes its **entire step environment — including `DROPLET_SSH_KEY` — into the string
sent to the Droplet**. Measured, not inferred; #348 carries the probes. It last ran successfully
**2026-07-27**, and the block that broke it arrived with **#305**, which shipped in `0.9.0`, so
the recovery path has never run since the change that broke it.

⚠️ **Also still unproven: `anthropic` 1.0.0 has never made a live model call.** CI sets no key
and every seam is stubbed, so the shipped image's first real round trip is the month read and
the Ask box being exercised **by hand in production**. Do that before assuming the AI surfaces
survived the major bump.

✅ **`release-prep` is trustworthy again as of #340.** Before that it would have told you a DROP
needs a manual out-of-band step (wrong since #277) and to verify the deploy with `css_v` (retired
by #305). If you are reading a session transcript older than 2026-08-31, distrust both.


### ✅ `0.9.0` SHIPPED AND LIVE (2026-09-02)

**Prod runs `0.9.0`, deployed and verified 2026-09-02.** 35 commits since `v0.8.0`, **no new env
vars and no new migrations**. The `0.9.0` milestone stays **open** for the #309 backlog.

**The three first-time mechanisms all passed in the real deploy log**, and the ordering is the
part that mattered:

```
Nothing to apply for phase before-pull — up to date.     ← #277, empty pass, exit 0
running version 0.9.0 (matches 0.9.0)                    ← #305, BEFORE the destructive phase
Nothing to apply for phase after-pull — up to date.      ← #277, empty pass, exit 0
/healthz -> 200 on the first attempt
Announced 0.9.0 to 3 device(s).
```

✅ **This is the first release verified by the pipeline itself rather than by an improvised
handle.** No `css_v` hash, no second witness on `/login`, nothing chosen before cutting — the
container was asked what it was and answered. That is the whole of #305 working as designed.

⚠️ **The release carried no user-facing feature at all**, which made the What's-new strip the
one real judgement call at prep. The single `### Added` was the admin-only version stamp and
every `### Fixed` was a patch fix — so by the letter of the rule the strip had **nothing** to
hold. Left at `v0.8.0` it stays dismissed on every device that has seen it, while
`release.yml`'s success notification ("Check out what's new in the app!") taps through to `/`
and would land on nothing. Sean's call: **one block, on the Deployment card**, rather than
padding it with fixes the rule excludes. Worth remembering as the shape, not the instance —
an infrastructure release still fires a notification that promises the user something.

**Deliberately held out:** Dependabot **#342** (minor-and-patch group, 6 updates). The release
was already exercising three mechanisms for the first time; six dependency bumps on top would
have added a variable to the run with the most first-time machinery in it. It rides `0.10.0`.

**Two PRs on release day:** #347 (roll the changelog, repoint the strip — closes #345) and the
one recording this. `scripts/release_prep.py --version 0.9.0` did the mechanical half and its
env-var report independently agreed with `git diff v0.8.0..HEAD -- .env.example`.

### ✅ `0.8.0` SHIPPED AND LIVE (2026-08-20)

**Prod runs `0.8.0`, deployed and verified 2026-08-20.** The `0.8.0` milestone is **closed at
47 issues**. *(That line used to read "no milestone is open"; `0.9.0`'s was opened during
#309 and is still open for its backlog.)*

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
**Decide the handle before cutting.**
✅ **Superseded for `0.9.0` onward by the version stamp (#305)** — the image carries the version
it was built as, `/settings` reports it, and both deploy workflows fail on a mismatch. `css_v` is
no longer the handle and no handle has to be chosen per release. The answer is deliberately
**not** on `/healthz`; see `CLAUDE.md`'s deploy bullet for why.

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

### On `main`, not yet deployed (2026-08-31) — the full-repo review, COMPLETE

✅ **#309 IS DONE. There is no resume point, and its absence means finished rather than
forgotten.** All ten tranches have merged and every tracked file the issue owned — `app/`, `sql/`,
`scripts/`, `tests/`, `.github/`, `.claude/` and the root build config — has been read once, on
purpose. The review filed **twelve issues** on the way; those, not a next tranche, are what it
leaves behind (listed below).

⚠️ **The resume point in this file went stale THREE TIMES while the review ran** — #331 named
tranche 5 three tranches on, #337 named tranche 8 two tranches on, and #343 is this one. #337
recorded the cause and could not fix it: **the file goes stale on precisely the step that makes it
stale — merging a tranche — and nothing connects the two.** The loop has ended here because the
work finished, **not because that problem was solved.** The next long-running, tranche-shaped
piece of work will reproduce it exactly unless something mechanical links the two steps. Worth
knowing before starting one.

| Tranche | PR | What it covered |
|---|---|---|
| 1 | #313 | `app/` core — `__init__.py`, `db.py`, `helpers.py`, `models.py` |
| 2 | #317 | `app/blueprints/` — all 21 files, 6366 lines |
| 3 | #318 | the seams — `ai.py`, `mailer.py`, `pusher.py`, `github.py`, `jobs.py` |
| 4 | #320 | `app/templates/` — all 47 files |
| 5 | #325 | `app/static/` — 18 files; vendored bundles got a provenance pass |
| 6 | #327 | `sql/` — 37 migrations + `schema.sql` |
| 7 | #330 | `scripts/` — all 10 files, 2207 lines |
| 8a | #334 | `tests/` — `conftest.py` + the 19 files that read a repo file, ~4,900 lines |
| 8b | #336 | `tests/` — the remaining 54 files, ~12,700 lines |
| 9 | #340 | `.github/` + `.claude/` — 21 files, 2,588 lines |
| 10 | #341 | root config — `Dockerfile`, compose x2, `test.sh`, `pyproject.toml`, `.dockerignore`, `.pre-commit-config.yaml`, `.env.example`, … 13 files |

**Recount rather than trusting any number here** (`git ls-files | wc -l`) — the commits this
review produces change it.

#### What tranches 5–7 actually found

**Tranche 5 (`app/static/`).** All three vendored bundles verified **byte-identical to upstream**
(ApexCharts 4.7.0 js + css, htmx 2.0.4) by fetching the pinned release and diffing.

- ⚠️ **htmx had no provenance at all** — no version recorded anywhere, no licence beside it, while
  ApexCharts had both plus two tests. Not a compliance gap (2.x is **0BSD**, which asks for
  nothing), but htmx **changed licence across a major** — 1.x was BSD-2-Clause — which is exactly
  the ApexCharts trap, unguarded. Now vendored, documented and asserted on both halves.
- History's Amount/Balance headers were aligned by `th:nth-child(4)`/`(8)`, the **third** place
  that table's column order is load-bearing and the only one still counting positions.
- Four comments named features that were removed (the Goal Coach, #232's stacked cards).

**Tranche 6 (`sql/`).** Both of #309's questions answered by measurement in throwaway containers.

- ⚠️ **The chain does NOT build `schema.sql` and cannot.** `users` is created only in `schema.sql`,
  `transactions.frequency` is `ALTER`ed by `sql/11` and created by nothing, `sql/09` re-adds
  `sql/04`'s constraints. Known and deliberate — but written down only inside a CI comment, so a
  reader of `sql/` found nothing. `schema.sql`'s header says it now, along with the
  `migrate.py --baseline` step it had been omitting.
- ⚠️ **Fourteen headers still said "Apply BY HAND to prod … BEFORE pulling the new image."** Wrong
  since #277. **The same sentence in `CLAUDE.md` cost a session's planning at `0.8.0` prep** — it
  was fixed there and left standing in fourteen files nobody re-read. Marked as history, not
  deleted: each header is a dated record and #277's rule is built on `sql/13`'s.
- Every DROP does carry its phase pragma. That half is sound.

**Tranche 7 (`scripts/`).** The four big scripts needed nothing — `migrate.py`, `release_prep.py`,
`restore_check.py` and `seed_dev.py` are each a documented pure/seam split with real tests.

- ⚠️ **`seed_dev.WIPE_ORDER` is the THIRD copy of the FK-safe delete order.** #267 reconciled the
  `verify` skill's copy against `conftest._delete_user` and pinned both; it could not reach this
  one, because `seed_dev.py` deliberately imports nothing from `tests/`. It is correct today, and
  it is now pinned — **drifting is not reliably loud**, since a missed table whose FK cascades is
  swept up by the final `DELETE FROM users`.
- `check_site_drift.py` carried a dead `REPO_ROOT` and still printed "agree with this checkout"
  after #299 removed the last thing that read the tree.

#### What tranche 8 (`tests/`) actually found

**The split was settled by evidence, not by size** (#309, comment of 2026-08-28): findings in
`tests/` cluster by **what a test reads and whether that reading can be empty**, not by feature
area. So 8a took `conftest.py` plus the 19 files that read a repo file, and 8b took the remaining
54. All the known instances lived in 8a's group, and the split held up.

**Tranche 8a (#334).** `THEME_TOKENS` in `test_design_system.py` named **twelve** tokens that must
carry a dark-mode value; the dark block redefines **twenty-six**. The fourteen unasserted ones
include all eight `--series-*` chart hues, whose dark steps exist specifically because the light
ones do not clear 3:1 against the dark surface — so losing them is a contrast failure in every
chart, silently. Same shape as `AI_SURFACES`, for the third time in this review: a hand-maintained
list can only ever fail for a member somebody added.

**Tranche 8b (#336).** Found by reconciling two numbers rather than by reading: the suite reported
**1232 passed / 9 skipped** against the **1237 / 4** 8a had recorded three days earlier. Same
total — five tests had moved from passing to *skipping*, because the date was 2026-08-31.

- ⚠️ **Five tests in `test_forecast.py` skip on the last day of every month.** They opened with
  `if today.day >= days_in_month: pytest.skip(...)`, so the month-ahead projection — which feeds
  Home's one AI panel **and** the `month_projection` Ask tool — went unchecked roughly **twelve
  days a year**, at precisely the boundary its month-end arithmetic is most likely to be wrong at.
  `compute_forecast()` reads `date.today()` directly and offers no seam, while
  `compute_goal_projection`, `recent_months`, `_report_months`, `compute_digest_facts` and
  `build_seed_plan` all take an injectable `today=`. A `forecast_today` fixture in `conftest.py`
  applies the seam from outside; all five run every day now.
- ⚠️ **`test_read_facts_projection_sees_a_bill_still_to_land` could not fail.** It asserted
  `remaining_scheduled_expense >= 0` — a sum of positive amounts — and dated its bill for *today*,
  which `_remaining_scheduled` deliberately excludes. Measured across three frozen dates: the bill
  was invisible on **every** day of the month.
- ⚠️ **That rewrite exposed a real gap.** `_remaining_scheduled` guards #32's end date twice — the
  SQL `WHERE` drops a *finished* schedule, `stop = min(month_last, end_date)` stops the walk for a
  *live* one. Deleting the `min()` left the whole file green, because the existing test only ever
  reached the SQL half. Now covered.
- ⚠️ **Login protection was asserted against 10 of 79 method/route pairs**, and **nothing in the
  suite derived from `url_map`**. Nothing was actually unguarded — all 79 were probed — but nothing
  could have noticed if that changed. Now derived, with a `PUBLIC_ROUTES` allowlist so a new route
  is **protected-by-default** and opening one is a visible edit with a reason. Unguarding
  `/transactions/export` gives `10 passed` against the old list and `FAILED` against the new sweep.
- ⚠️ **`conftest.py` was loaded TWICE**, as `conftest` and `tests.conftest`, from one file — nine
  files used the bare name and thirty-seven the dotted one. Proven at runtime: different module
  ids, `create_transaction` not even the same function object. Harmless today because every value
  it defines at module scope is derived deterministically, but
  `monkeypatch.setattr("tests.conftest.X", ...)` misses one copy. Normalized and guarded.
- A skip in `test_profile_settings_login.py` named `test_push_reminders.py` as the canonical
  always-running copy of the consent-record assertion. It lives in `test_release_announce.py` and
  never lived there — **a skip is not a failure**, so a reader checking whether the consent record
  was still covered would have found nothing where it said to look. The pointer is asserted now.

**Suite after 8b: 1233 passed, 4 skipped.** The four remaining skips each have a real,
non-calendar reason. ⚠️ **Recount rather than trusting that** —
`./test.sh --collect-only -q -n0 | tail -1`.

#### What tranches 9 and 10 found

**Tranche 9 (`.github/` + `.claude/`).** One cause, four victims: the 2026-08-17 CLAUDE.md split
updated the docs and never reached the things that read CLAUDE.md **with no human in the loop**.

- ⚠️ **`gotcha-auditor`, `test-first` and BOTH triage prompts were sent to sections that no longer
  exist** — "Key Gotchas" and "Project Structure", plus a "Roadmap" that never existed. The agents
  were written 2026-08-04, thirteen days before the split. `gotcha-auditor` exists to audit a diff
  against the Key Gotchas; pointed at the core file it finds **four** grouped Non-negotiables
  instead of the ~20 in `docs/gotchas.md`, and reports **clear** — indistinguishable from having
  checked. `tests/test_doc_claims.py` now derives the guard from `docs/*.md`'s own H1 titles.
- ⚠️ **`release.yml` contradicted itself, and the wrong half recommended the incident.** Its step-0
  comment records that `${TAG}` has had no default since #190; seventy lines later another said
  compose reads `${TAG:-latest}` "so a hand-run `docker compose up -d` still works". That is the
  command that reverted production three releases with every indicator green.
- ⚠️ **`release-prep` would have misdirected `0.9.0` twice** — it still said a DROP "needs a manual
  step out of band" (the third home of the sentence tranche 6 corrected in fourteen `sql/` headers),
  and its whole deploy-verification check was `css_v`, retired by #305.
- The `verify` skill named `/insights/generate`, removed by #232 and pinned at 404 by a test.
  `rollback.yml`'s `| tr -d '\r'` turns out to be load-bearing under `set -e` — without it, rolling
  back to any pre-#305 image aborts instead of warning, during an incident. `/wrap` omitted checking
  the run on `main` after a merge.

**Tranche 10 (root config).** Three findings, all the hand-maintained-set shape.

- ⚠️ **There are THREE ruff pins and the guard compared two.** `.pre-commit-config.yaml` had drifted
  to `v0.14.5` against `0.16.4` in `requirements-dev.txt` and `ci.yml` — and it is the copy that runs
  `--fix` **on commit**, so the oldest ruff in the project was the one EDITING code while the newest
  judged it. Exactly the property #264 exists to establish, reopened through the copy its test could
  not see. Same shape as `seed_dev.WIPE_ORDER` and `AI_SURFACES`.
- The pre-commit exclude protected `chart.umd.min.js`, deleted by #234, while the 563 KB
  `apexcharts.min.js` was covered by nothing — and the config's own large-file hook caps at 500 KB.
- ⚠️ **`test.sh`'s parallelism tuning was measured on a 15-core Mac; the suite runs on an 8-core VM.**
  So `-n auto` is now FEWER workers than the default and marginally faster, and the header's advice
  to "pass `-n auto` for the 21s back" is backwards. Re-measured, both tables kept and labelled with
  their machine. **Do not trust either without checking `nproc`.**

#### Issues the review has filed, all on `0.9.0`

**#315 is still the one to read first**, and **#328 is still the loudest**. Those from tranches 1–4
(#312, #314, #315, #316, #319) are described in the older section below; the rest were filed by
tranches 5–8 and are listed here. ⚠️ Deliberately not counted — a count in this file is invalidated
by the next tranche that files one, which is how the line above it went wrong:

| Issue | What |
|---|---|
| **#328** | **The `scripts/` ingest pipeline cannot insert a row** — `transactions.user_id` has been `NOT NULL` with no default since `sql/10`. Proven against the dev database, not inferred. It also swallows the failure and exits 0, `data/` has never existed, and `test_connection.py` is referenced by nothing. Deleting five files is a fork |
| #326 | A fresh database and production disagree about the sequences behind three PKs. `account.account_id` is the one that diverges by NAME — because it is the only PK not called `id`, the irregularity `CLAUDE.md` already warns about. Fix is a migration, so it stands alone |
| #329 | A 500 from `/healthz` is classified `UNREACHABLE`, which files no issue — so a certificate not covering its hostname pages you and the app being hard down does not |
| #323 | History's Auto-Categorize banner is the third AI surface and never got the shared material; `AI_SURFACES` omits it, so the guard passes vacuously |
| #324 | `.page-greeting`/`.page-sub` match no rule, so Home's subtitle is the only page lede in the app rendering at full body weight |
| **#339** | **`changelog-guard.sh` blocks on a reshaped Stop payload**, which its own comment and `.claude/README.md` both say it must not. Two of the three named fail-open cases work; a dict with the key RENAMED reads as false and blocks, because `.get()` returns None without raising. The fix is a fork — the obvious one breaks the README's documented test and may silently disable the hook — and the script is a declared twin of one in `material-list-import-tool` |
| **#333** | **`.dockerignore`'s `*.md` matches only the TOP LEVEL, so `docs/` and all of `.claude/` ship in the production image** — fifteen files. Found by tranche 8a running the suite inside the real artifact. `CLAUDE.md` is listed separately and *is* correctly absent, which is what made it invisible: the one file anyone would check for is gone. Not a disclosure (public repo), but two places state the wrong premise that `*.md` is stripped. Changes the shipped artifact, so it wants its own PR |

⚠️ **Nothing here is deployed.** Prod is still `0.8.0`, and this work sits in `## [Unreleased]`
alongside the four PRs that were already there.

### On `main`, not yet deployed (2026-08-25)

**Five PRs merged across two sessions; nothing shipped.** Prod is still `0.8.0`. A **`0.9.0` milestone is open** —
the first since `0.8.0` closed — and **#36 is the only issue left open**, correctly date-parked
to ~Dec 2026 with no milestone.

| PR | Closed |
|---|---|
| #302 | #277 — migrations declare which side of the image swap they run on |
| #285 | — Dependabot minor/patch group, plus the `ci.yml` ruff pin it missed |
| #286 | — `anthropic` 0.122.0 → **1.0.0**, read rather than rubber-stamped |
| #303 | #299 — `landing/` removed; the page now lives in its own repo |
| #306 | #305 — the image is stamped with its version, so a deploy can be verified |

⚠️ **THREE CHANGES HERE ARE UNVERIFIED IN PRODUCTION AND HAVE NO NATURAL CLOSING EVENT.** All
first execute at the *next* release, not on merge:

1. **The two-phase migration deploy.** `release.yml` now runs `migrate.py --phase before-pull`
   at step 2 and `--phase after-pull` at step 5. The next release exercises it for the first
   time — including the empty-pass case, which is the norm and must exit 0. **Watch the deploy
   log for both invocations**; a release carrying no migration should print
   `Nothing to apply for phase … — up to date.` twice.
2. **`anthropic` 1.0.0 inside the shipped image.** The call shapes were verified against the
   real package, but **no live API round trip was made** — the AI surfaces are gated on
   `ANTHROPIC_API_KEY`, which CI does not set. First real proof is a model call in production.
3. **#305's pipeline half.** The app half is fully verified locally (real images built stamped,
   bare and `--target dev`, with `printenv` read back from each; the card driven at
   `localhost:5001`). The workflow assertion cannot be: nothing in CI deploys. It was run by hand
   against live containers with the runner's quoting reproduced — match exits 0, mismatch exits 1,
   all four rollback branches checked — which proves the *script*, not that the *step* fires in
   place. **Watch the deploy log for `running version <version> (matches <version>)`** between
   `up -d` and the migration step. ⚠️ The irony worth keeping: this change exists because
   production-only state was unobservable, and it is itself production-only until it ships once.

**`## [Unreleased]` now holds four entries:** the CI Postgres probe fix (#282), the migration
phasing (#277), the SDK bump (#286) and the version stamp (#305). Whether that is a release is
Sean's call. ⚠️ **#305 is the first of the four with a user-facing surface**, so the "nothing in
it is user-facing" argument against cutting no longer holds on its own — and `0.9.0` would be the
first release the new handle can actually verify.

#### The deploy handle is no longer improvised (#305)

Every release since `0.4.1` reached for a different answer to *did the deploy land*, and the
handle kept not being there — `css_v` proved nothing at `0.4.1` or `0.7.0`, and worked at `0.8.0`
only because the front-end overhaul happened to rewrite the stylesheet. The image now carries
`APP_VERSION`/`APP_COMMIT` as build args, `/settings` renders an admin-only **Deployment** card,
and both deploy workflows fail if the running container is not the release they just deployed.

⚠️ **The answer is deliberately NOT on `/healthz`, and `CLAUDE.md` was wrong to propose it.**
That file recommended it in two places, against a decision written twice in the code —
`main.healthz`'s docstring ("the last place to leak anything") and rule 1 above
`admin.integration_status`. Sean took the admin panel plus a pipeline assertion. Both doc lines
are corrected and `tests/test_version_stamp.py` pins the boundary, because "just put the version
on `/healthz`" is exactly the obvious-looking improvement a future session re-makes.

Three things that are not obvious from the diff:

- **A build arg, not a `.env` variable.** `TAG` on the box records what compose was *told* to
  pull; a stale or hand-restored `.env` makes it lie, which is the gap #190 could not see.
  **Do not add it to `.env.example`.**
- **The release checks the version BEFORE the after-pull DROPs.** If the swap silently left the
  old image running, dropping tables it still SELECTs is the outage #277 exists to prevent.
- **`rollback.yml` warns where `release.yml` fails.** Every pre-#305 image reports no stamp, and
  those are exactly what a rollback reaches for — strictness there would refuse a rollback
  mid-incident. A *wrong* stamp still fails both.

#### `landing/` is gone from this repo

The page serves from **`/var/www/seandesmet.com`**, deployed on push from
**`CaddisMaster/seandesmet.com`**. Consequences worth knowing here:

- `scripts/check_site_drift.py` has **four** checks now, not five, and **reads no file from the
  working tree** — its result no longer depends on which branch you run it from. The apex/www
  **TLS checks stayed**: a certificate belongs to the Droplet, not to whichever repo supplies the
  HTML, and two repos watching one page would file two issues for one fault.
- Nginx is **two site files**, so an app config mistake can no longer take the portfolio down.
- ⚠️ **The `#299` issue body is wrong about the backup config lists.** It says a new nginx site
  file must be added to both or the tarball silently stops covering it, and flags that as the
  easiest thing to miss. Both lists name **whole directories** (`/etc/nginx/sites-available`,
  `sites-enabled`), so a new file inside them is captured with no edit. The real risk — the two
  scripts drifting from *each other* — is unchanged.

#### The guard that was written and deleted the same afternoon

`check_phase_order()` refused any pending batch whose phases were not numerically monotonic. It
rejected **both** real batches it was ever shown (`27`/`28`, `36`/`37`), neither of which shares a
table. Removed, with the reasoning in `migrate.py` and a test pinning those pairs as acceptable so
reinstating it goes red. **A rule with a 0/2 hit rate on real input is not conservative.**

#### `tests/test_sdk_call_shape.py`

New, and it closes a gap `CLAUDE.md` had only ever described in prose: every model call goes
through a stubbed `_call_*_model()` seam, so a green suite says **nothing** about an SDK upgrade.
It introspects the installed package — no network, no key, no cost — and asserts the `**kwargs`
precondition that stops the other assertions going vacuous.

⚠️ **#285 proved the ruff double-pin real.** Dependabot bumped `requirements-dev.txt` alone and CI
went red on `test_the_two_ruff_pins_agree` — the sole failure in 1143 passing. A bot only updates
the file it knows about.

Suite on `main`: **1209 passed, 4 skipped** after #305. ⚠️ Recount rather than trusting that —
`./test.sh --collect-only -q -n0 | tail -1`.

### ✅ Landing page redeployed OUTSIDE a release (2026-08-24)

**The ai-atlas card is gone from `seandesmet.com`, and the removal is verified live.**

`ai-atlas` was a third portfolio project, added to the landing page by #280 on 2026-08-20. It was
**abandoned and deleted on 2026-08-24** — local repo, GitHub repo and transcripts — so the card
linked a repo that returns `404`, on the front page of the portfolio. #288 removed it.

**The diff was the exact reverse of #280's hunk**, not a hand-written deletion: 50 lines added
there, 50 removed here, leaving `landing/index.html` **byte-identical to the pre-#280 file**. That
is a far stronger check than counting `<div>`s, and it is available whenever the change being
undone is a single additive commit — `git show <sha> -- <path> | git apply -R` proposes it, and
`git show <sha>^:<path> | diff -` proves it landed.

**The changelog entry was DELETED, not answered with a `### Removed` line.** A changelog records
what reached users, and this card never did: prod is `0.8.0`, and #280 was still sitting in
`## [Unreleased]`. Deleting the `### Added` entry outright leaves an accurate record of the next
version, and takes a second dead link with it, since the entry named the repo too. ⚠️ The
instinct to reach for `### Removed` is the wrong one here — **check whether the thing being
removed ever shipped** before recording its removal.

⚠️ **A LANDING-PAGE CHANGE IS NOT DEPLOYED BY MERGING IT.** `release.yml` never touches
`landing/`; Nginx serves the directory off disk, and the page updates only when the file is
copied to the Droplet by hand. `main` was green and correct for some time while the live page
still served the dead link. **The merge and the deploy are separate events here** — the same
shape as a production-only change, and it needs the same explicit "not yet live" marker until
someone has actually run the copy.

⚠️ **The copy cannot run from the dev VM, and the stale-clone trap is live.** The maintainer
machine's clone was last measured well behind `main`, so copying `landing/index.html` from it
would have re-shipped the card. The check that closes this is `grep -c ai-atlas` on the file
**before** it goes up, expecting `0`; fetching the file from the public repo's raw URL sidesteps
the clone entirely and is the better default.

**Verified after the copy**, not assumed: the live page is byte-identical to `landing/index.html`
on `main`, carries one project card, and greps clean for `ai-atlas`.

⚠️ **A SKIPPED JOB REPORTS `pass`, NOT `skipped`.** #288 was the inert shape (`landing/` +
`*.md`), so the classifier emitted `app=false image=false sql=false` — and `gh pr checks` still
printed **"Tests pass 30s"** and **"Image builds and runs as non-root pass 44s"**. The steps skip
*inside* jobs that succeed, so a run that executed nothing is visually indistinguishable from a
real one. This is the sharp edge of the #281 lesson below, and it is worse than "the expensive
jobs did not run": it actively looks like they did. **The `main` run is the one that means
something**, or read the `app=/image=/sql=` line in the "What changed" job log. The `main` run
for #288 (`32737263585`) did run all five jobs, and was green.

**Still no open milestone**, so #287 and #289 carry none. Prod still runs `0.8.0`.

### ✅ The docs became executable (2026-08-24, later the same day)

Four stale-doc defects surfaced in one day. The common factor was not carelessness — **nothing
executed the claim**. So two mechanisms merged, and both are now load-bearing.

**#296 — `tests/test_doc_claims.py`.** Five assertions on claims a machine can check: every path in
`CLAUDE.md`'s project map exists, the landing page names no retired library, `RUNBOOK.md`
hardcodes no versioned certbot lineage, and the vendored ApexCharts version *and licence* match
what `CLAUDE.md` claims. Two of those pin rules that had been written warnings and were violated
anyway.

⚠️ **Scope is structured claims, never prose.** Every assertion targets something with a shape. A
test that fails on a reworded paragraph is worse than no test — it trains the next person to
delete the file and take the useful assertions with it. The line worth copying is the cert one:
`RUNBOOK.md` **may** name `seandesmet.com-0001` while recounting the incident; it **may not** use
it as a config path. The regex requires the `/etc/letsencrypt/live/` prefix. **Assert the USE, not
the mention.**

⚠️ **It found a live defect in the disaster-recovery path.** The nginx snippet hardcoded
`/etc/letsencrypt/live/seandesmet.com-0001/` — the lineage **deleted** when the www handshake was
fixed — while §"Full rebuild" step 6 says "write the site files from §3". A rebuild copying it
verbatim writes a config pointing at a path that does not exist: nginx will not start, during a
rebuild, while the site is down. It had been **anticipated** (step 7 already warned about copying
paths verbatim) and left armed for a year. **The fix was not to update the path** — that resets
the expiry. It names `certbot certificates` and the property the lineage must have, so it cannot
drift again.

**#298 — `scripts/check_site_drift.py` + `site-drift.yml`.** Daily, and it needs **no secret and no
Droplet access** — every check is public HTTP/TLS, deliberately, because the Droplet is
unreachable from the VM by design and a monitor needing privileged access would become an argument
against that boundary. It byte-compares the live landing page against `main`, checks SAN coverage
per hostname, fails under an expiry threshold, and pings `/healthz`.

⚠️ **Drift files one deduped issue; unreachable files NOTHING** (exit 1 vs exit 2). A flaky runner
must never open an issue, or the tracker fills with noise until nobody reads it and the one real
report is lost. A genuine outage shows as a run of red scheduled runs.

The network sits behind two seams and everything else is pure, so 29 tests run with no network.
The workflow's own shell is asserted as text too — that it reads `PIPESTATUS` rather than `$?`
(which is `tee`'s status, always 0, and would make every drift report green), and that only exit 1
files an issue. **A monitor that costs money and can never fire is worse than none.** Proven in
both directions: clean against real production, and a side-labelled diff when handed a stale file.

⚠️ **THE `.dockerignore` TRAP, THIRD OCCURRENCE (#176, #218, now #298)** — and the new part is
*why the guard missed*. The test file carried `skipif(not SCRIPT.exists())`. **`scripts/` ships;
`landing/` does not.** So the guard never fired, the script short-circuited on the absent landing
file before reaching its fetch, and an assertion expecting UNREACHABLE quietly got OK. It passed
locally and failed in the image, again.

> **Guard the artifact the test READS, not the one it imports.**

Better than a second skip: remove the dependency. The test now writes its own file into `tmp_path`
and monkeypatches the constant, so it holds in any environment.

⚠️ And a warning about simulating the image: moving `landing/` aside locally made
`test_doc_claims.py` fail, which looked like a second bug and was not — the real image also strips
`*.md`, so that test skips there and the failure existed only in a half-way state no environment
has. **Reproduce the whole restriction, or trust the real in-image skip count.**

**#299 filed and PARKED** — move the landing page to its own repo, still on the Droplet. No date,
no milestone. Its ordering is written down, including the trap most likely to bite: the backup
config path list exists in two places, so a new nginx site file has to be added to both or the
portfolio's config silently stops being backed up.

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
