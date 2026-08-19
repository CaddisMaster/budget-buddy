# Testing

> Split out of `CLAUDE.md` on 2026-08-17. Read before adding or changing tests,
> and before any change that sweeps across files.

## Testing

Run with **`./test.sh`** (args pass through to pytest, e.g. `./test.sh -k semimonthly`).

⚠️ **`test.sh` REFUSES TO RUN while another run is in flight** (#206) — an advisory
`flock` on fd 9, taken before anything expensive and never released, so it survives the
final `exec docker compose …` and the kernel drops it when the run ends (including a
`kill -9`, which a marker file would not). This is the guard that actually holds, because
`test.sh` is the one place **every** path goes through. Set `TEST_SH_LOCKFILE` to override
the path; a machine with no `flock(1)` warns and continues rather than refusing.

⚠️ **`runtests` is GONE** (2026-08-17). It was a machine-local wrapper that sent the suite to a
visible tmux pane; tmux was retired with the terminal front end, so **`./test.sh` is the only
path** and a stale `runtests` on `PATH` should be deleted rather than repaired. The `flock` above
is unaffected — it always was the guard that actually held, and it lives in `test.sh` itself.

It runs in a throwaway `web` container on prod's Python 3.14 — no local venv;
`requirements-dev.txt` adds just `pytest`. Needs the dev `db` container up (route/isolation
tests hit it). Also runs in **GitHub Actions CI** on every push/PR (`.github/workflows/ci.yml`,
`postgres:16` service + `schema.sql`) — but **only when the diff can affect behaviour** (the
`changes` job's `app` flag; a docs-only PR skips the suite and the job still reports success).
**When `Dockerfile`/`requirements*.txt` change, the suite ALSO runs inside the built image**
(`docker-build`'s "Test suite runs inside the shipped image") — the runner's Python is not
necessarily the shipped one, so a base-image bump used to go green having tested the runtime it
was replacing.

**`test.sh` takes one of two paths and says which:** if the dev stack is up it runs the suite
inside the **live `web` container** (`docker compose exec`) — no container created, no image
built, no dependencies installed; otherwise it falls back to a throwaway `run --rm --build`. A
container started before the `dev` stage existed has no pytest, so the script probes for it and
falls back rather than dying on an import error. Both paths run the same image.

**⚠️ `tests/conftest.py`'s `TEST_PREFIX` is PER-WORKER** — `"__pytest__" + PYTEST_XDIST_WORKER`
— and that is the single thing making `-n auto` safe. Workers are separate processes sharing ONE
database; with a shared prefix they all create and tear down the same three users, and a run
produces hundreds of errors (verified: 424 errors + 1 failure with the prefix hardcoded). The
env var is absent serially, so the prefix is then exactly `__pytest__` and nothing changes.
**Never hardcode `__pytest__` in a test** — build names from `TEST_PREFIX`. Two files had done
so and were fixed in #71 (`test_admin_backup.py`'s log assertion, `test_seed_dev.py`'s
`SEED_USER`); a third literal in `test_hardening.py` is a deliberately-nonexistent username and
is fine.

**⚠️ The prefix rule is WIDER than usernames: it covers any GLOBALLY UNIQUE column** (#128,
2026-07-31). `TEST_PREFIX` protects rows scoped by `user_id`; it does nothing for a column whose
uniqueness spans every worker by definition. `push_subscriptions.endpoint` is the one such column
today, and `test_push_reminders.py` hardcoded endpoints for months — two workers then addressed
ONE row and the helper's `ON CONFLICT (endpoint) DO UPDATE SET user_id` silently reassigned a
device mid-test. Every endpoint that reaches the DB now goes through `EP()`; `test_release_announce.py`
builds its own from `TEST_PREFIX` for the same reason. ⚠️ A **broadcast** test compounds this — it
SELECTs every row globally, so assert on **your own endpoints, never a total count**, and make a
failing seam fail **selectively by endpoint** (an unconditional `PushGone` stub deletes a parallel
worker's subscriptions).

**⚠️ Adding tests can expose another file's latent race** by changing xdist's work distribution —
#126's 22 new tests turned #128 red, in a file that PR never touched. Before assuming you broke it
(or that it is "just flaky"): `git diff --stat main~1 main -- <file>` and `grep -c TEST_PREFIX <file>`.
A file that writes shared state and references the prefix **zero** times is latently broken whether
or not it fails today. Verify a fix with **five consecutive full runs** — a one-in-three race passes
a single re-run often enough to look fixed.

**⚠️ The `Dockerfile` is multi-stage and `prod` MUST STAY LAST.** `base` → `dev` (adds
`requirements-dev.txt`) → `prod` (empty, `base` under a name). A build with no `--target` gets
the FINAL stage, and three things build with no target: CI's `docker-build` job, the release
workflow, and a bare `docker build .`. Make `dev` last and all three silently ship pytest to
production. `docker-compose.override.yml` (local only, never on the Droplet) selects
`target: dev`. CI's "Shipped image carries no test dependencies" step is the enforcement —
it asserts `import pytest` FAILS in the built image.

**Gotcha — `python -m pytest`, never bare `pytest`:** going through the module guarantees the
interpreter holding the dependencies is the one that runs them. (In the `dev` stage they are
installed as root into system site-packages, so bare `pytest` would in fact resolve there — but
CI installs `requirements-dev.txt` into the *prod* image as `appuser`, where console scripts land
in `/home/appuser/.local/bin`, which is NOT on `PATH`.) `test.sh` also passes
`-p no:cacheprovider`: `/app` is root-owned (`WORKDIR` created it before the `COPY --chown`) so
pytest can never write its cache there, and passing it turns two warnings per run into none.

**⚠️ Test-run economy is OBSOLETE — a full run costs ~35 SECONDS.** **Just run the full
suite.** The old rationing advice — targeted `-k` runs for iteration signal, one full run per
commit as the gate, batching mechanical commits coarser so each full run gates more work,
capturing red output to a file because re-running was expensive — was written when a run cost
2:40 and is now actively counterproductive. Delete it from your habits; a `-k` run saves
~30 seconds and risks missing the thing that broke.

⚠️ **`./test.sh` defaults to a BOUNDED `-n 10`, deliberately NOT `-n auto`** (`test.sh:20`
carries the measurements: `-n auto` pins every core at ~21s, `-n 10` leaves five cores for the
rest of the machine at ~29s, `-n 4` is ~67s). Pass `-n auto` explicitly to buy the difference
back for one run. **CI is deliberately unaffected** — `ci.yml` invokes `pytest -q -n auto`
directly, where pinning every core is the point.

⚠️ **`pytest.ini` sets `--dist loadgroup`, not the default `load`** (#157/PR #158), and it
lives there rather than in `test.sh` because **CI invokes `pytest` directly** (twice), so
`test.sh` is not on every path this must hold for. Scheduling is identical for ungrouped
tests; a test marked `@pytest.mark.xdist_group("scheduler_sweep")` is guaranteed the SAME
worker as everything else in that group. That is the only way to stop two workers running a
**global sweep** simultaneously: `reminders.send_due_reminders()` reads
`SELECT DISTINCT user_id FROM push_subscriptions` with no user filter — correct, it is the
daily job for everybody — so one worker's call also processes every other worker's users, and
its `ON CONFLICT DO NOTHING` claim in `reminder_log` means the neighbour then delivers through
the **wrong process's mocked seam**. ⚠️ **`TEST_PREFIX` cannot help here** — it isolates *rows*,
and this is contention over a global *operation*. FIVE files drive such a sweep
(`test_push_reminders.py`, `test_job_runs.py`, `test_digest.py`, `test_money_agent.py`,
`test_variable_bills.py`) plus the broadcast in `test_release_announce.py`; all carry the group.

What still holds: the suite's content assertions are the only net for Jinja's
silent-empty-string failure mode, so a global change (a cursor-factory flip, a template-wide
sweep) must be gated on the full suite. And when planning a sweep, grep for the failure SHAPE
(e.g. `\$[0-9]{4}`), not just assertions near the feature — the `|money` sweep broke two
credit-limit assertions the feature-local grep missed.

**`-n0` is the serial escape** — reach for it when you need `pdb`, or when interleaved parallel
output makes a failure hard to read. Any explicit `-n` you pass is respected.

⚠️ **Two superseded claims, recorded so they are not re-derived:** the old
"`test.sh` re-`pip install`s each run (~15s recoverable)" note was simply WRONG — measured, the
install cost ~1.5s and total per-invocation overhead ~2.9s, cut to ~0.8s by #70. And xdist was
predicted to "roughly halve" the run; it actually cut it by ~12×, because the suite is
IO/DB-bound rather than CPU-bound and parallelises far better than a CPU-bound suite would.

**953 tests in `tests/`** (measured 2026-08-19, end of session; was 921 at `0.7.0`. ⚠️ **The in-image split below is from `0.7.0` and has NOT been re-measured** — recount it before quoting it; 5 of them skip on the last day of a month — `test_forecast.py`'s date guards. ⚠️ **Inside the shipped image the run is `910 passed, 11 skipped`** — real-file tests skipping because `.dockerignore` strips `*.md`. Two legitimately different numbers; do not "fix" the gap.) ⚠️ **Recount rather than trusting this number** — it has now been wrong four times (757 against a true 762 on 2026-08-03, 783 against a true 806 on 2026-08-04, 806 against a true 843 on 2026-08-10, 906 against a true 912 on 2026-08-17), so the drift is real and the month-end skips do not explain it. Cross-cutting patterns: **no real API calls anywhere** — every
`ai.py::_call_*_model` seam (and `mailer.py::_call_resend`) is monkeypatched with canned
`SimpleNamespace` responses; every feature file asserts **user isolation**; route tests assert
anon → 302. What each file covers:

- `test_variable_bills.py` — #191 end to end via the mocked `pusher._call_webpush` seam: the link column (stamped on materialization, NULL for hand-entered rows, surviving a schedule delete), which rows the pass selects (variable only, the 3-day window, per-user scoping), the payload, sending (idempotent per transaction, the `push_enabled()` gate claiming nothing, isolation, `PushGone` deletes vs `PushError` keeps), the daily job, and the form round-trip. ⚠️ **`test_a_bill_posted_by_a_page_load_is_still_alerted` is the load-bearing one** — it materializes the way a page load does, asserts `next_due` has already moved past today, and only then runs the job; it is the test that fails under the rejected schedule-side design. Carries `@pytest.mark.xdist_group("scheduler_sweep")` and builds every endpoint from `TEST_PREFIX`. ⚠️ Known to FAIL without the feature, not merely to pass with it: removing the one-line `schedule_id` stamp turns 11 of its 23 red
- `test_deploy_pinning.py` — #190: the compose image ref has **no `:-` default of any kind** (the property, not the string) and errors naming `TAG`; `.env.example` carries a `TAG=` line; both workflows rewrite the pin, `chmod 600` **before** the write, and the release pins **before** its first compose command. ⚠️ Every test SKIPS when the file it reads is absent, naming `.dockerignore` — `docker-compose*.yml` and `.env.*` are genuinely excluded from the shipped image (#176). Verified red against the pre-fix compose file
- `test_model_constants.py` — #140: which model each beat runs on, **and the request
  parameters the Sonnet 5 move made load-bearing**. Beyond the two constant scenarios it
  pins `max_tokens >= 4096` and the explicit `effort` at both Sonnet seams, so a later
  "tidy" back to 2048 fails loudly instead of silently reintroducing truncation. It does
  this by stubbing the **client factory** (`anthropic.Anthropic` / `ai._get_ask_client`)
  with a recorder rather than the `_call_*_model` seam itself — one level deeper than the
  usual convention, because the kwargs live *inside* the seam. Also asserts no sampling
  parameter is ever passed (a 400 on Sonnet 5). ⚠️ Its docstring states the ceiling: green
  proves the constants are spelled right and the kwargs are what we intended, and nothing
  about whether real output fits — the live run is the gate
- `test_restore_check.py` — #153: `scripts/restore_check.py`, entirely pure — the tool needs Docker, the suite runs inside an image that has none (the `.dockerignore` trap pointed at a new file), so every judgement is a pure function and everything that shells out is a `_`-seam. Covers dump selection (by the date **in the filename**, never mtime), role extraction, `role_statements()` skipping `COPY` blocks, and `evaluate()`. ⚠️ **Two tests exist to make a REVERSAL loud, the `test_release_announce.py` device:** `test_an_empty_non_core_table_is_not_a_failure` asserts `goal_coach`/`insights` are legitimately 0 (⚠️ `goal_coach` was DROPPED in `sql/36` and the assertion deliberately STAYS: it describes a real dump taken while the table existed, and every retained backup is such a dump. The tool must keep validating them) (the issue's own criterion said every table must be non-empty — false against a real dump), and `test_the_owner_is_not_the_only_role_a_dump_needs` asserts a dump names **both** `admin` and `budget_app`. The fixture `REAL_COUNTS` is the verbatim table from a real restore *because* two of its values are zero. ⚠️ Its docstring states the ceiling: green proves the judgement is right and nothing about whether a real dump restores — only running it against one does that
- `test_release_prep.py` — #154: `scripts/release_prep.py`, all pure except one stubbed seam. The changelog roll (content moved not copied, the fresh empty `[Unreleased]`, link refs derived from the version HEADINGS rather than the stale `[Unreleased]` ref), the strip rewrite (**the prose comes through byte-identical** — the load-bearing one, since the tool rewriting copy would be a defect), the refusals (empty `[Unreleased]`, double-roll), and the env report's **three** states. ⚠️ Three separate traps are stated as tests here, each having actually bitten: `test_the_new_entry_is_spaced_like_the_ones_already_in_the_file` compares the seam against a release ALREADY in the file rather than a literal `\n\n` (#174 — the previous assertion checked contents and never shape); `test_the_tool_is_never_told_which_variables_are_new` asserts the ABSENCE of a `--env-var` flag, the `test_release_announce.py` device; and the two real-file tests **skip when the file is absent** (#176 — see the `.dockerignore` gotcha) and assert the parser copes with the real FORMAT, never with today's contents, because the tool itself replaces those contents
- `test_job_runs.py` — #151: the pure `summarize_job_runs()` state machine (OK/STALE/NEVER/NOT_SCHEDULED boundaries, per-job thresholds, unknown job names filtered out), the writer (**upsert not append**, a swallowed `psycopg2.Error`, the naive-datetime cast), and the route (admin sees the panel, non-admin/anon don't). ⚠️ **`test_every_badge_actually_renders` is the load-bearing one** — every state must render real text, since a Jinja typo in a badge is an empty string rather than an error. Carries `@pytest.mark.xdist_group("scheduler_sweep")` — it drives a global sweep, see the loadgroup gotcha
- `test_integration_status.py` — #139: the pure three-state rule, the placeholder property
  (`github_pat_YOURTOKEN` must not read as configured — stated as the incident so swapping
  the length floor for a format check fails loudly), push needing *both* VAPID keys, the
  scheduler line, and non-admin/anon redirects. ⚠️ **The load-bearing one asserts the value,
  three prefix lengths of it, and the variable names are all absent from the response.**
  ⚠️ Every test sets the environment **explicitly** — the dev container legitimately
  carries a real `ANTHROPIC_API_KEY`, so a test assuming "unset by default" would pass or
  fail depending on whose machine it runs on
- `test_release_announce.py` — #115/#131/#133 via the mocked `pusher._call_webpush` seam: the fixed body, that **only the version varies** between two releases, that the title **does not contain "Budget Buddy"** (#133 — stated as a property, not a string, because every other assertion would read a regression as a mere wording change and the duplicate is only visible on a real device), and — the load-bearing one — that `build_release_notification` **takes exactly one parameter** (`inspect.signature`), so reintroducing release text is a visible regression rather than a quiet reopening of the injection surface; the CLI rejecting `--notes`; the `push_enabled()` gate; **reaching every user's devices** (the not-user-scoped property); `PushGone` deleting vs a transient `PushError` keeping; one bad device not aborting the batch; and the Profile consent copy. ⚠️ Assertions are written against **this worker's own endpoints**, never a global count — see the xdist gotcha above
- `test_feedback.py` — #64 end to end via the mocked `github._call_github` seam: the gate (no token → no UI on /profile and the route creates nothing), happy path + label set (`bug`/`enhancement` **plus `from-app`**), an arbitrary posted kind never becoming a label, validation, the input caps, `GitHubError` → GENERIC_ERROR with **the API text, status code and repo name all absent from the response**, the seam unit tests, anon → 302, and the rate-limit registration (`limiter._marked_for_limiting`, the test_admin_backup.py convention — the limiter is disabled under test). ⚠️ **`test_body_carries_only_what_the_user_typed` is the load-bearing one**: it asserts the username and the seeded account/category/transaction names and amount are all absent. The privacy decision is invisible in the code's shape, so a later change attaching context would look like an improvement and break nothing else
- `test_pending_transactions.py` — #86 end to end: create/badge (content-asserting — `HistoryRow` is positional), the page-scoped pin, several pending rows staying newest-first (the stable-sort property), pinning NOT overriding `?month`, the em-dash balance cell **anchored to the actual `<td>`** (a bare `"—"` also matches the edit selects' "— none —" and passes without the feature), **the walk-guard property** (posted rows' balance cells are character-identical whether a sibling is pending or posted — this fails if anyone moves the pin into SQL), page-1 top balance still equalling the full net, mark-posted (clears only, whole-tbody fragment, returns the row to date order), edit-preserves-the-flag, pending counting in month spending, a pending row still being an Auto-Categorize candidate (i.e. NOT behaving like `is_adjustment`), export + cleanup keeping pure date order, and the isolation/anon set
- `test_seed_dev.py` — `scripts/seed_dev.py` (#69): determinism (same seed+day → identical rows), dates derived from `today` not hardcoded, **the fixture scaling property** (parametrized 2–36 months — a fixed opening debt pays the card off entirely on a long window and a fixed goal target gets overshot), analytics-exclusion coverage, schedule `next_due` always FUTURE, plus a DB round-trip: persistence counts, transfer-pair shape, login + populated dashboard, **loading `/` materializes nothing**, refuse/force/dry-run paths
- `test_push_reminders.py` — #33 end to end via the mocked `pusher._call_webpush` seam: the `push_enabled()` gate, subscribe/unsubscribe routes (upsert on endpoint, payload validation, 503 when unconfigured, cross-user IDOR), the reminder window (honours #32's `end_date`), per-occurrence dedup, dead-subscription (404/410) cleanup vs a KEPT transient failure, per-user isolation, and the two materialization properties — a user who never logs in gets rows, and **materialization still runs with push unconfigured** (the gating trap). Plus the threaded daily-job-vs-page-load `FOR UPDATE` twin
- `test_apr.py` — APR: pure `_parse_apr`/`monthly_interest`, /accounts interest line (independent of the limit bar), ask enrichment, either/or facts, payoff "interest adds" render, edit error-path echo
- `test_goal_projection.py` — pure projection incl. the APR block (apr=None backward-compat, interest pushes pace date, pace ≤ interest → no date + Behind, amortized required/mo, 600-month cap terminates)
- `test_pwa.py` — /sw.js 200 + JS mimetype + anon-accessible, manifest scope "/", five icons, PWA head tags on BOTH shells, remember cookie
- `test_money_filter.py` — `|money` units + a rendered comma on /
- `test_history_balance.py` — running balance: pages connect (page 1 top = true full net), month-filtered continuity, tbody swap carries the seed
- `test_bulk_edit.py` — bulk category/delete: IDOR pair, transfer legs untouchable, garbage ids skipped, filter_qs preserved, page markup (transfer row has no checkbox)
- `test_param_hardening.py` — pure parsers + every pre-fix 500 as a route test, invalid-date errors, bcrypt 72-byte cap, GENERIC_ERROR (never exception text), base/login cache-bust lockstep
- `test_money_agent.py` — the agent loop via the mocked seam (grounding guard, nudge recovery, turn cap), `_normalize_findings`, `recent_transactions` dispatch, `run_money_agent` week-key upsert + isolation, `/agent/run` route, digest integration (cached run reused; agent failure → email still sends)
- `test_credit_limits.py` — pure `credit_utilization`/`_parse_credit_limit` (tier boundaries, over-limit, Decimal), edit error-path echo regression, /accounts bar rendering, ask enrichment, facts + isolation
- `test_budget_history.py` — `record_budget_change` at set/clear/review-apply, no-ops skipped, NULL = cleared, isolation
- `test_dashboard_merge.py` — /analytics redirect, Ask box on /, day-of-week chart, YoY gating, **the tojson regression** (a `</script>` category name arrives escaped); plus #83's colour slots (a new category does not repaint, a month filter does not repaint, per-user) and a **stylesheet** assertion that `--series-1..8` are eight distinct hexes in BOTH mode blocks (the palette is CSS, so no request renders it). Also #108's fold: pure `fold_chart_tail` (short list untouched, tail arithmetic, ranks by total not input order, **only the synthetic row flagged** — a real category named "Other" keeps its hue), route-level (never >7 segments, **total conserved against `SUM(amount)`**, no premature fold, income view folds too, a survivor keeps its colour across months), and `--series-other` being present, unused by any category, and actually neutral in both modes. ⚠️ The slot test seeds **distinct descending amounts** — tied totals leave "which six survive" to SQL tie-breaking, which flakes. Plus #111's distinct-colour set: pure `assign_series_slots` (preferred slot when free, collision broken, never repeats across the drawn set, folded row untouched) and the **production regression** — nine categories where the 1st and 9th created are BOTH in the top six, which is the shape that shipped broken in `0.3.0`
- `test_budget_report.py` — pure grid derivation (grades, streaks, ±10% trend, ordering) + DB loader window (seed dates derived from `_report_months()`, never `timedelta` — no month-boundary flake) + route smoke
- `test_occurrences.py` — pure walkers (`_advance_past` stale catch-up, `upcoming_occurrences` start-exclusive/end-INCLUSIVE, weekly multi-occurrence)
- `test_checkin.py` — check-in: one adjustment closes the gap, match = stamp only, **the trust property** (balance moves, month facts unchanged), cross-user 404s
- `test_amounts.py` — `parse_positive_amount`/`parse_signed_amount` + a `"nan"` POST against EVERY amount form asserting nothing written
- `test_digest.py` — digest facts (incl. double-occurrence week), recipient selection, idempotency, per-user try/except, opt-in route; two seams mocked
- `test_budget_review.py` — proposal normalization (snap bounds, unknown dropped), facts, scan/apply write-side guards, banner gating
- `test_autocategorize.py` — suggestion normalization, expense-only candidates, scan filter, apply write-side guards, banner count
- `test_transfer_schedules.py` — paired materialization + catch-up + gates + CRUD validation/IDOR + the FOR UPDATE concurrency twin
- `test_ask.py` — tool dispatch arg validation, per-user scoping, the multi-turn loop (turn cap, tools_used), /ask route
- `test_hardening.py` — `_csv_safe` + CSV end-to-end, security headers, cookie flags, constant-bcrypt path; plus #87's `Kind` column — pure `_export_kind()`, the header contract, per-row labelling, that transfer legs/adjustments are still PRESENT (a regression test against "fixing" it by filtering), and **the reconciliation arithmetic** (all rows minus blank-Kind rows == the transfer legs + adjustment exactly)
- `test_month_read.py` (replaced `test_insight.py` in #232) — `compute_month_facts()`, `build_read_facts()` (**the load-bearing one: the read must see BOTH builders**), the `/insights/read` route, **cache-hit (a page load never calls the model)**, the deferred first read (`hx-trigger="load"`, seam not called during GET), graceful failure, isolation, and the absence of the three retired cards + the quick-add
- `test_forecast.py` — now **arithmetic only** (#234 left it no routes and no model seam): pure `project_expenses()` and `compute_forecast()` incl. the end-date gate. Its narration half moved to `test_month_read.py`
- `test_schedules.py` — semimonthly init math, materialize/catch-up/gates, the **4-thread FOR UPDATE concurrency test** (verified red without the lock), CRUD
- `test_recurring.py` — `compute_next_due()`, all 6 frequencies (pure)
- `test_routes.py` — pages 200 logged-in / 302 anon; login/logout (POST 302, GET 405)
- `test_isolation.py` — A can't touch B's data (all tables); missing/other-user → 404; write-side IDOR
- `test_crud.py` — create/edit/delete happy paths (incl. kind CRUD, flip-clears-budget, credit-limit persist)
- `test_htmx.py` — fragment shape (no `<html>`), isolation 404s, admin 403 gaps
- `test_budget_vs_actual.py` / `test_budget_suggestions.py` — the two budget helpers
- `test_transfers.py` / `test_goals.py` — transfers + goals routes (payoff snapshot, balance-≥-0 rejection, payoff-edit lock; the analytics-exclusion test asserts the dashboard hero figures)

Fixtures (`conftest.py`) use the dev Postgres with `__pytest__`-prefixed users, torn down in
FK-safe order (child rows first — transactions→categories/account are `ON DELETE RESTRICT`).
CSRF + rate limiter disabled under test.

