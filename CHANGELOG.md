# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses the `0.x` versioning scheme described in
[`VERSIONING.md`](VERSIONING.md).

## [Unreleased]

### Fixed

- **The deploy workflows ran parts of their own comments on the build machine,
  and the rollback path leaked the server key into the command it sent.** Both
  workflows hand their remote script to `ssh` as a single double-quoted
  argument. Inside double quotes the shell still performs command substitution,
  and it does not care that the text is a comment — a `#` there is a comment to
  the *remote* shell and ordinary text to the local one. So backticks anywhere
  in that block, including inside an explanatory comment, ran on the runner
  before the command was ever sent.
  The release workflow had one such comment, and it was visible in the `0.9.0`
  deploy log as a stray "No such file or directory" for a path that is not a
  command — noise, and harmless only by luck. The rollback workflow had sixteen
  unescaped backticks, one of them around `printenv`, which meant a rollback
  substituted the build machine's entire environment — the Droplet's private
  key among it — into the string sent over the wire. Being several lines long,
  that also broke out of the comment it sat in and would have been run as
  commands at the far end, so the rollback would very likely have failed
  outright.
  Nothing had gone wrong, because the rollback workflow has not run since the
  version stamp added that comment block, and the version stamp shipped in
  `0.9.0`. The escaping convention that prevents all of this was already in use
  three times in the same file; it was simply applied unevenly, which is the
  kind of rule that wants a test rather than a comment. There is now a test that
  reads both workflows, finds each remote command, and fails on an unescaped
  backtick — or on the same defect written as `$(…)`, which is clean today and
  is asserted so it stays that way.

## [0.9.0] - 2026-09-02

### Added

- **The app now says which version it is running, and the deploy checks it.**
  Nothing it served did before, so "did the deploy land?" reached for a
  different improvised handle every release — and the handle kept not being
  there. The stylesheet hash proved nothing at `0.4.1` or `0.7.0`, because
  neither release touched a static asset, and worked at `0.8.0` only because
  the front-end overhaul happened to rewrite the CSS.
  The image is now built with the version and commit baked in, and `/settings`
  reports both in an admin-only Deployment card beside Integrations. A build
  that came from no release says `dev` in words rather than showing a blank
  cell. Both deploy workflows ask the running container what it is and fail if
  the answer is not the release they just deployed — in the release, before the
  destructive migrations run, since dropping tables against a container that is
  silently still the old image is the outage that phasing exists to prevent.
  Two deliberate asymmetries. The version is a **build argument, not an
  environment variable**: the `TAG` line on the server records what Compose was
  *told* to pull, which a stale or hand-restored file can make lie, and that is
  exactly the gap that once left production three releases behind with every
  indicator green. And a **rollback warns where a deploy fails** — every image
  built before this change reports nothing, and those are precisely the
  versions a rollback reaches for, so refusing them mid-incident would be worse
  than the missing information. A *wrong* version still fails both.
  It is admin-only and deliberately absent from `/healthz`, which is the one
  URL guaranteed reachable by anyone and therefore the last place to name a
  build. The project's own guidance had proposed putting it there; that
  contradicted a decision already written into the code twice, and the boundary
  is now a test rather than a comment.

### Changed

- **The linter that edits code on save was two versions behind the one that
  judges it.** The project pins its linter in three places so that a check
  passing locally means it will pass in CI — that is the whole point of pinning
  it. Two of the three agreed. The third, which is the copy that runs
  automatically before a commit and *rewrites* code as it goes, was two releases
  older. So the oldest linter in the project was the one making edits while the
  newest decided whether they were acceptable, which is exactly the gap the
  pinning exists to close. The test guarding this compared two of the three and
  was named for it; it now compares all three, so a fourth copy cannot slip in
  unnoticed.

- **The pre-commit rules protected a file that no longer exists and left the
  real one uncovered.** A charting library was swapped out months ago; the rule
  telling the automatic formatters to leave vendored third-party files alone was
  never updated, so it exempted the deleted library and not the 563 KB
  replacement that is actually in the repository. Nothing had been damaged, but
  it was one upstream update away from mattering.

- **The test runner's tuning notes described a machine that stopped running the
  tests two weeks ago.** They explained, with measurements, why the suite uses a
  capped number of parallel workers on a 15-core laptop. Development moved to an
  8-core virtual machine, where the cap is now higher than the core count and
  the advice to "run it unbounded if you want it faster" would actually run it
  with *fewer* workers. Re-measured on the machine that runs it today, with both
  sets of figures kept and labelled, and a note to check which machine you are on
  before trusting either.

- **The automated helpers were still being sent to a file that stopped holding
  the answers eleven days after they were written.** The project's main
  instructions file was split into a short index plus a set of reference
  documents last month. The reference documents were updated; the four automated
  things that read the instructions with nobody watching were not. One of them
  exists specifically to check a change against the project's list of
  known-dangerous mistakes — and it was being pointed at a section that no longer
  exists, so it would have found four general rules where the real list holds
  around twenty, and reported all clear. A check that quietly stops checking is
  worse than no check, because it still produces a reassuring answer. All four
  now point at the right documents, and a test fails if that drifts again.

- **The release checklist would have given wrong instructions on the next
  release.** It still said that a database change which deletes something has to
  be applied by hand, separately from the deploy — which stopped being true when
  the deploy pipeline learned to do both halves itself. That is the third place
  that same out-of-date sentence has been found and corrected, and this was the
  one that would have said it out loud at exactly the wrong moment. The same
  checklist also still explained how to confirm a deploy landed using a technique
  that was replaced, and which had twice proved nothing.

- **A deploy note contradicted itself, and the wrong half recommended the thing
  that caused an outage.** One comment in the release process correctly recorded
  that the server now refuses to start without being told which version to run —
  a safeguard added after production silently sat three releases out of date with
  every indicator green. Seventy lines later, another comment described the old
  behaviour as current and suggested the exact shortcut that caused it. Corrected,
  with the history kept.

- **Part of the test suite quietly stopped running on the last day of every
  month.** Five tests covering the month-ahead projection — the arithmetic
  behind "where is this month going", which feeds both the home page's written
  summary and the Ask box — began with "if today is the last day of the month,
  skip". They had done so for as long as they existed, which means roughly
  twelve days a year the projection went unchecked, and those were precisely
  the days its month-end arithmetic was most likely to be wrong. Found by
  noticing the suite reported five fewer passing tests than it had three days
  earlier, with the same total. The projection now takes its date from the test
  rather than from the calendar, so all five run every day.

- **A test that said it checked the projection could not fail.** It set up a
  bill and then asserted that the total still to be paid was "zero or more" — a
  sum of positive amounts, so always true — and it dated the bill for today,
  which the projection deliberately ignores, since anything due today has
  already been recorded. Measured rather than assumed: the bill it created was
  invisible on every day of the month, and it passed regardless. It now checks
  the actual figure. Two more tests of the same shape were strengthened, and a
  real gap they were hiding is now covered: the rule that stops a bill being
  forecast past the date its schedule ends had no test that failed when it was
  removed, so a schedule finishing mid-month could have started predicting
  payments that can never be charged.

- **Every page is now checked for its login requirement, rather than ten of
  them.** The suite verified that signed-out visitors are turned away by
  working through a hand-written list of ten pages. The app has seventy-nine
  ways in once you count every form submission and every delete — so a new page
  shipped without its protection would have been checked by nothing, and the
  list would have gone quieter as the app grew rather than going red. Every
  route the app registers is now required to turn away a signed-out visitor
  unless it is on a short list of deliberately public ones (the login page, the
  health check, the service worker, two legacy redirects), each with its reason
  written beside it. Nothing was actually unprotected — all seventy-nine were
  checked — but nothing could have noticed if that changed. Confirmed by
  removing the protection from the CSV export: the old list passed, the new
  check fails.

- **The test suite's shared setup file was being loaded twice.** Nine test files
  referred to it by one name and thirty-seven by another; both work, and Python
  treats them as two unrelated copies of the same file — proven, not inferred,
  by comparing the two objects at runtime. Nothing was broken by it, because
  everything that file defines is worked out fresh each time and came out
  identical. It is the kind of thing that stays harmless until it suddenly is
  not, so all nine now use the same name and a check keeps it that way.

- **Fourteen of dark mode's colours were not being checked.** The suite has a
  list of the colours that must have a separate dark-theme value, because one
  that does not gets rendered with its light value on a dark background — a
  failure that looks like "dark mode is a bit off" rather than like a bug. The
  list named twelve. The stylesheet had grown to twenty-six, and nothing made
  the list grow with it, so every colour added since it was written went
  unwatched: deleting the dark step for the "over budget" tint would have put a
  pale pink on a dark surface with the whole suite still green. Verified by
  doing exactly that — it passes twenty out of twenty against the old list and
  fails against the new one.
  The eight chart colours are the ones that mattered most. Their dark values
  exist specifically so each stays legible against the dark background, which
  the light values do not, so losing them would have quietly made every chart
  harder to read. The list is now held to the stylesheet from both directions:
  a colour that loses its dark value fails, and a colour that gains one without
  being added to the list fails too, so it cannot fall behind again. No colour
  changed.

- **The dev-data generator's cleanup list is now checked against the schema.**
  Wiping a seeded user means deleting from every table that user owns, in an
  order that respects the foreign keys — and that list existed in three separate
  places. Two of them were reconciled a release ago after one silently went stale
  and tore down nothing at all; the third lives in a script that deliberately
  imports nothing from the test suite, so it was missed. It happens to be correct
  today. What made it worth pinning is that drifting is not reliably noisy: a
  missed table whose foreign key cascades gets swept up by the final delete, so
  the list can rot with no symptom until someone adds a table that does not
  cascade, and then the failure points at the wrong file.
  Also removed a constant and an import in the drift checker that survived the
  landing page moving to its own repository, and corrected its output, which
  still claimed to be comparing production against the checkout it was run
  from — something it stopped doing at the same time. No behaviour changed.

- **Fourteen migration files no longer tell you to deploy them by hand.** Each
  header carried "Apply BY HAND to prod (pg_dump backup first) BEFORE pulling
  the new image" — true when it was written, and wrong since the deploy pipeline
  took that job over. The same sentence lived in the project's own guidance until
  it sent a session down the manual path during a release, so this is a known
  way to lose an afternoon rather than a hypothetical one. The wording stays,
  because each header is a dated record of what actually happened and one of them
  is the reasoning the current rule is built on; every one is now marked as
  history rather than instruction.
  The schema file's header says how a fresh database is really built, which it
  did not: loading it is only half, and without the second step the migration
  runner refuses to start. It also states plainly that the numbered files are
  **not** a replayable history — most of them fail against an empty database,
  because the users table is created only in the schema file and one migration
  alters a column nothing creates. That was known, but the only place it was
  written down was a comment inside a CI job.
  No schema changed and nothing in the app moved.

- **The stylesheet was read end to end, and it no longer claims things that are
  not true.** The other vendored bundle, htmx, now ships the licence it is used
  under — the chart library and both typefaces already did, and htmx was the one
  exception. That was never a compliance problem: htmx 2.x is 0BSD, which asks
  for nothing in return. It is a provenance one. htmx **changed licence across a
  major version** — 1.x was BSD-2-Clause, which does require the notice be kept
  — and that is the same trap the chart library carries a loud warning about,
  except htmx had no version pinned anywhere and no licence text to compare a
  bump against. Both are now recorded and both are asserted, and all three
  vendored bundles were verified byte-identical to the releases they claim to
  be.
  Twelve lines of dead CSS went with it: two colour tokens and five classes
  left behind when the home page was rebuilt, none of them reachable from any
  template. One of those classes was being kept alive by a test that required
  it — a check that had been passing against a selector matching nothing since
  the rebuild, and that now names the elements the page really renders.
  Four comments were corrected rather than deleted. They described the app's
  AI surfaces, and each one had outlived a feature that was removed: they named
  a coach that no longer exists, counted rules that were deleted, and gave a
  total that was one too high. Nothing about how the app looks or behaves
  changed.

- **History's two figure columns are styled by name instead of by position.**
  The right-alignment on the Amount and Balance headers was written as "the
  fourth column" and "the eighth", which is true only for as long as nothing is
  inserted to their left. The cells underneath already carried names; the
  headers do now too, and a test holds the two halves together. This is the
  third place that table's column order is load-bearing, and it was the only one
  still counting.

- **The History table's column order is now checked against the row shape it
  fills.** History is the one screen where a database row reaches the page by
  position rather than by name, which means the query's column order and the
  row's field order are a single unit: add a column to one without the other
  and every value after it shifts along by one. Nothing errors — the row still
  has the right number of values, they are just the wrong ones — so the first
  sign would be wrong figures on screen. There are also two queries filling
  that same shape, not one, and the second only supplies the pending rows
  pinned to the top of page one, so a mismatch there would show on the handful
  of rows least likely to be in front of whoever made the change. Both are now
  asserted. No behaviour changed.

- **The names the AI seams are known by are now checked, not just written
  down.** Every model call, email, push and GitHub call goes through a single
  isolated `_call_*()` function so tests can stub it, which makes those names
  the map anyone reads to understand how any of it is tested — and they get
  cited constantly, in module docstrings, in the comments explaining a call's
  arguments, and in the tests doing the stubbing. Nothing executed those
  citations, and two had gone stale: one counted the seams as they stood before
  three of them were removed, and one pointed at a seam under a name it has
  never had. Both are corrected, and a test now asserts that every such name
  mentioned anywhere resolves to a seam that exists, so the next one fails in
  its own pull request instead of misleading a reader months later. No
  behaviour changed.

- **The Anthropic SDK moved to `1.0.0`** (from `0.122.0`). A major version, so
  it was read rather than rubber-stamped: `1.0.0` removes `temperature`,
  `top_p`, `top_k`, `messages.parse(stream=...)`, the Text Completions API and
  its `HUMAN_PROMPT`/`AI_PROMPT` constants, several renamed exports, and moves
  the HTTP layer from `httpx` to `httpx2`. This app used none of them, and its
  Python floor was already well above the new 3.10 minimum. No behaviour
  changed and no AI surface moved.
  The gap worth naming is that none of that was checkable here: every model
  call goes through an isolated `_call_*_model()` seam and every test stubs it,
  so a green suite would have said exactly as much about a bump that broke
  everything. `tests/test_sdk_call_shape.py` closes that — it introspects the
  installed SDK and asserts it still accepts the arguments `app/ai.py` actually
  passes, without a network call, an API key, or any cost.

### Fixed

- **A credit card with a corrupt limit turned the whole "available credit"
  figure into `nan`.** PostgreSQL's `numeric` accepts `NaN`, and NaN compares
  False to everything — so it slipped a plain `<= 0` check and came out the far
  side as a utilization dict full of `nan` with a healthy-looking tier. One such
  card was enough to poison the cross-card total on /accounts, which is the same
  one-NaN-poisons-the-sum failure the amount validators were written to prevent.
  Nothing can write one today, but the column is older than the validator, and
  the sibling function that computes card interest already guarded against
  exactly this and said why. The two now guard identically: three sites across
  two files had spelled one rule three different ways — twice as `x != x`, once
  not at all — and that divergence is how the missing one stayed invisible.
- **A transfer with an unparseable date reported a server fault instead of a bad
  date.** The one-off transfer form checked only that its date field was
  non-empty and handed the string to PostgreSQL, whose complaint was then
  correctly treated as an unexpected write failure. So a typo produced
  "Something went wrong — please try again" — unactionable, since resubmitting
  fails identically — and put an exception traceback in the log for what was
  ordinary bad input. Every other form in the app already parsed its date,
  including the recurring-transfer form in the same file; this one now gives the
  same answer they do. Nothing was ever written in either case.

- **A tampered `?page` on History returned a 500 instead of a page.** The
  pagination guard clamped the bottom end only — page 0 or -1 became page 1,
  because a negative SQL `OFFSET` is a database error. Python integers have no
  ceiling, though, so `?page=100000000000000000000` was carried through at full
  width, multiplied by the page size, and handed to Postgres, which answered
  `bigint out of range`. That is the same failure the clamp was written to
  prevent, arriving through the end nobody had bounded, and it was reachable by
  any logged-in user editing a query string. Pages are now clamped at both
  ends. Nothing a real account can reach changes: past the last page the query
  returned an empty list before and returns one now.
- **The month-filter labels went wrong past thirteen months.** `recent_months()`
  built each label by subtracting from the current month and correcting a
  negative result once — exactly one year of correction — so a two-year window
  counted down through `2025-00` and into `2025--10`. Every caller asks for
  twelve months or fewer, so nothing on screen was ever wrong; the arithmetic
  now counts in absolute months, which cannot go wrong at any size, and a test
  asserts every label is a real month rather than checking the list's length.
- **A schema migration that drops a table can no longer run against the code
  that still reads it.** The deploy applied every pending migration *before*
  pulling the new image. That is the right order for an additive migration — the
  column has to exist before the code that queries it arrives — and exactly
  backwards for a `DROP`, which made the object vanish while the *old* container
  was still serving and kept serving until the swap finished. `sql/36` shipped
  through that window at `0.8.0`, dropping two tables the running `v0.7.0` image
  still queried; `/` and `/goals` would have returned 500 for the length of the
  image pull, accepted at the time only because this app has one user who could
  be told not to load two pages for a minute.
  A migration now declares which side of the swap it belongs on with a header
  line, `-- deploy: after-pull`; silence means additive, as before. The deploy
  runs the migration runner twice, either side of the container swap, and
  `scripts/migrate.py --phase` selects between them. Forgetting the declaration
  fails the test suite rather than production — the rule was previously a
  comment in the workflow that claimed drops were "deliberately NOT automated",
  while the step below it applied them anyway. No user-facing change.

- **CI no longer fails at random on a Postgres startup race.** The image job
  waited for its database with `pg_isready` over the unix socket. The
  `postgres:16` entrypoint runs `initdb` against a temporary server that listens
  on the socket and not on TCP, then shuts it down and starts the real one — so
  the probe reported ready against a server about to disappear, the wait broke
  early, and loading the schema failed with `FATAL: the database system is
  shutting down`. The probe now uses TCP, which the temporary server never
  serves, so the race is removed rather than made less likely; an exhausted wait
  now also fails naming the real problem instead of falling through to a
  confusing `psql` error. CI only — nothing under `app/` changed.

## [0.8.0] - 2026-08-20

### Security

- **Changing your password now signs out your other devices.** It previously
  signed out nothing at all: a session lasts a year by design (so an installed
  app does not ask you to log in every time you open it), and the sign-in cookie
  identified you by user id alone — there was nothing in it the server could
  revoke. So the one thing you would do if you were worried about a device you
  no longer have had no effect on it. Changing your password now invalidates
  every other session immediately, while leaving you signed in where you changed
  it.

  ⚠️ **Upgrading signs everyone out once.** Sessions that predate this release
  carry no token to check, so every device is asked to log in one final time
  after the update. This happens once.

### Added

- **Every category shows the colour it is drawn in.** Categories now carries a
  swatch matching the colour that category appears in on Home this month, so the
  chart and the list finally agree. A category that is not on this month's charts
  says so plainly rather than claiming a colour it does not have.

  **And more categories get a colour at all.** Home used to chart your top six
  and roll everything else into a grey "Other" — a rule inherited from the
  doughnut chart it replaced, where more than six slices stopped being readable.
  As a list of ranked bars, eight reads no worse than six, so the limit is now
  the number of distinct colours available: eight. On an eight-category account
  nothing is greyed out any more. Nothing about the figures changed — folding
  only ever affected what was drawn, never what was counted.

- **History can be filtered by account.** A third filter joins the search box
  and the month picker in the page header, so you can look at one account's
  ledger on its own. It combines with the other two, the running balance column
  re-nets to just that account, and the chip above the table names the account
  you are looking at so the filter is never invisible.

### Fixed

- **Export CSV now honours a search filter.** When a search was active but no
  month was selected, the Export CSV link was built without a `?`, so the
  download quietly ignored the filter the page was visibly applying and returned
  every transaction. Every History link is now assembled from one place, which
  also means Export carries the new account filter.

### Removed

- **Two unused database tables are gone.** `forecasts` and `goal_coach` cached
  the wording of two AI features that no longer exist — the month-ahead forecast
  card and the Goal Coach. Both stored narration only; every figure they
  described was always recalculated, so nothing was lost. No visible change.

- **The Goal Coach is gone.** The AI card on Goals that wrote a short recap of
  how your savings goals were tracking has been retired, along with its
  Generate button. Everything it described — pace, what's behind, what you'd
  need to set aside each month — is still on the page, computed rather than
  narrated, and the Ask box on Home answers questions about it on demand.

### Changed

- **Groundwork for signing out other devices.** The `users` table gains a
  `session_token` column (`sql/37`). Nothing reads it yet and no behaviour
  changes; it is what lets a future password change invalidate sessions on
  devices you are no longer holding.

- **The `verify` skill tears down what it creates again.** Its cleanup was a
  hand-maintained copy of the test suite's teardown, and it still named two
  tables that `sql/36` dropped — so it aborted on the first missing one and
  removed nothing, leaving the throwaway user behind. It now calls the suite's
  own `_delete_user` rather than repeating the list, and a test fails if either
  copy comes back or names a table the schema no longer declares. **Developer
  tooling only; nothing in the application changed.**

- **`./test.sh` lints before it tests.** Ruff was only ever run by CI, so an
  unused import — the cheapest possible defect — was caught four minutes away in
  the most expensive possible place, and re-ran the whole pipeline for a one-line
  fix. It now runs first and stops the run if it fails; `SKIP_LINT=1 ./test.sh`
  skips it when you want the test signal first. The ruff version is pinned to the
  same value in `requirements-dev.txt` and in CI, so a green local run predicts a
  green remote one. **No change to the application or to what ships.**

- **The AI features look like one feature.** Home's "Ask your finances" panel
  has been a dark purple surface since the redesign, but the Goal Coach on Goals
  and the AI budget review on Budgets were ordinary cards with a thin purple
  edge — so the same feature looked like two different things depending on which
  page you were on. All three now use the same material, in both light and dark
  mode.

- **The login page looks like the app it opens.** It carries the Budget Buddy
  mark, the same one in the sidebar on every other page, instead of a bare
  heading.

- **Profile is grouped by what each part affects** — your account, your data,
  and how Budget Buddy contacts you — rather than four equal sections where the
  weekly digest sat beside a count of your transactions. Changing your password
  now sits with the rest of your account details. Nothing about what the
  notification or feedback sections *say* has changed.

- **Settings uses one set of status labels.** The integrations panel and the
  scheduled-jobs panel now share one vocabulary with four levels, and two states
  that used to look identical are told apart: a job that is *overdue* (it has
  worked before and is late) no longer looks the same as one that has *never
  run*. A job switched off on this server still reads as switched off rather
  than as a fault.

- **User management says where it belongs** — it is part of Settings, and links
  back — and has an empty state rather than a table heading above nothing.

- **Add transaction leads with the amount.** It used to sit fourth, behind a
  type dropdown you had to answer first — and the two edge-case checkboxes
  (exclude from analytics, pending amount) carried more explanation between them
  than the whole rest of the form. Amount, description and category come first
  now, and the two flags are behind a "Adjustment or pending amount?" section
  you open when you need it. Every field the form had is still there.

- **The password rules are stated before you submit, not after.** Changing your
  password and creating a user both silently required at least 8 characters and
  at most 72 bytes, and told you only by rejecting what you typed. Both pages
  now say so up front, and count as you type — including for accented letters
  and emoji, which take more than one byte each, so "40 characters" can be over
  the limit while looking well under it.

- **Creating an admin says what that grants.** It was an ordinary checkbox
  labelled "Admin". It now spells out that an admin can reach Settings, download
  a full database backup, and create, delete and promote other users — including
  removing your own access.

- **History reads as a ledger.** The amount and the running balance — the two
  things you actually scan a row for — are now the most legible things in it,
  and they line up on the decimal point down the column. The date, category and
  account step back rather than competing. The search box and month filter have
  moved up into the page header instead of sitting as two loose forms above the
  table, and whatever you are filtering by is now stated on the page, as a chip
  you can click to clear, rather than being knowable only from the URL.

- **Budgets shows your position rather than making you work it out.** The page
  opens with where the month stands overall — spent against budgeted, what is
  left, and how many categories are over — before the per-category list. Each
  category with a budget shows how much of it is used as a bar, so being over
  is a proportion you can see rather than a subtraction you perform.

  **Going over budget no longer depends on noticing a colour.** An over-budget
  category says "Over by $23" in words, and the budget report's months are
  marked ✓ or ! rather than only tinted — its legend used to read "Green =
  stayed under, red = went over", which is an instruction to read by colour and
  nothing else.

- **Five pages now open with what you have, not with the form for adding more.**
  Accounts, Categories, Goals, Scheduled and Transfer all used to lead with an
  "Add X" form and put your existing things underneath it — so the thing you use
  least occupied the top of every one of those pages. Your accounts, categories,
  goals and schedules come first now, and adding is one line at the bottom that
  opens when you click it. It opens by itself when you have nothing yet, so a
  new account still starts on the form.

  **Accounts are cards rather than table rows.** Each account shows its balance,
  and its credit limit and monthly interest where you have set them, as one
  object — the utilization bar used to be buried in a table cell. On a phone
  they stack instead of scrolling sideways.

  **Categories are grouped into Expense and Income** rather than mixed in one
  list with a Kind column, and adding an income category now files it under
  Income instead of dropping it at the top of the list.

  **Scheduled says what is due next**, above the table. Its rows are ordered by
  type first, so the soonest item was routinely not the top row.

  **The two kinds of transfer are told apart.** "Transfer now" and "Transfer
  every month" are separate sections instead of two forms stacked with nothing
  between them, and an automatic transfer states its direction as
  "Everyday Checking → Savings" rather than as two columns to pair up yourself.

  **Goals puts the coach below your goals** rather than above them.

- **Every page has one header row instead of two.** Home stacked two bars before
  any content — a ☰ with an Add button, then a greeting with the month picker —
  which is 133 pixels of furniture for what is really a page title and a page
  action. They share a line now: the title on the left, the month picker and Add
  on the right, on every page rather than just Home. Roughly 50 pixels come back
  on each one, and the greeting, the month picker and the Add button all stay
  exactly where you can reach them.

  **The ☰ menu button is gone on desktop**, where the sidebar is always on
  screen and the button only implied it might not be. It still opens the
  navigation drawer on a phone, and closes it again. On a narrow screen the row
  wraps rather than squeezing the title, so "Good afternoon" stays on one line.

- **The Home page opens with the answer now.** It used to open with a What's-new
  strip, a quick-add box and a month dropdown, and only then tell you where the
  month stood — and it printed your net position twice, three lines apart, in the
  same panel. The page now leads with the month's net position; the strip sits
  underneath it, and Net is stated once, with income and expenses beside it
  rather than repeated below.

  **Where your money went is now a ranked list rather than a doughnut.** Six
  categories, longest first, each with its name on the row and its amount beside
  it — no legend to read across, and the same colours as before. The
  expenses/income switch works exactly as it did.

  **The four AI features are one feature now: "Ask your finances".** The monthly
  insight, the month-ahead forecast, the weekly money check and the Ask box were
  four separate cards, each with its own Generate button, saying four things
  about one month. There is one panel instead. It opens with a short read of
  where your month stands and where it is heading — how it compares with last
  month, and what is still to leave the account before the month ends — and the
  question box sits underneath it.

  The read writes itself the first time you open Home in a new month, and
  Refresh rewrites it whenever you want a fresh one; loading the page never
  waits on it. Asking a question can now reach everything the three retired
  cards showed, because the same figures they were built on are wired straight
  into the box — so "am I on pace to make it to the end of the month, and how
  does that compare with last month?" is one question rather than two cards to
  read and reconcile yourself.

  **Your weekly digest email is unchanged.** The money check still runs there,
  with its findings, exactly as before — it is only the card on Home that has
  gone.

  **The year-over-year comparison is one line** instead of three full-height
  cards, leading with the percentage, which is the number the comparison exists
  to give you.

  **The page now leads with a composed summary rather than a list of panels.**
  The month's net position sits on a gradient panel with the shape of your
  balance behind it and, when you are looking at a single month, how it compares
  with the month before. Money in and money out are two cards beside it rather
  than small print underneath. Below them sit up to three facts of equal weight
  — how this month compares with the same month last year, how far through your
  budget you are, and what is still due to leave the account — each appearing
  only when there is something to say. Goals are rings rather than bars, and
  "Ask your finances" is a panel of its own material, so it is obvious at a
  glance which part of the page was written for you and which part is your own
  arithmetic.

  **The charts look like part of the app now.** They were the one section that
  never got the redesign: every plot boxed in graph-paper gridlines, legends
  floating over charts that had one thing to name, day names printed on a slant
  because the card was too narrow, raw `7000`s on the axes of an app that
  writes `$7,000` everywhere else, and a right-hand third of the row left empty.
  All five are redrawn — one set of faint horizontal rules instead of a
  lattice, money on every axis, short day names that sit straight, colours taken
  from the same palette as the rest of the page in both light and dark, and a
  hover readout on every chart. Net balance over time is now drawn the way the
  figure at the top of the page is drawn, because it is the same figure.

  Your goals now sit under the two columns rather than inside the narrower one,
  side by side rather than stacked — which is what lets the ranked bars and the
  AI panel end on the same line instead of leaving a column of empty page
  beside one of them. A row of month facts with only one fact to show now
  spans the row rather than sitting in a third of it. The six charts are unchanged and still live in
  the section at the bottom, which still opens on desktop and stays closed on a
  phone — where the category bars now put each name and amount on one line with
  the bar beneath, instead of squeezing the bar into a stub too small to compare.

- **A bug report or suggestion sent from inside the app now asks for an automated
  first look.** Nothing about what you send changes, and nothing more is
  collected — the issue it files simply carries a label that runs an automated
  first pass over the relevant code, so a report arrives with some of the
  groundwork already done rather than waiting for someone to start from scratch.

- **Budget Buddy looks like something now, rather than like a form.** The app has
  always been legible — sensible colours, a real dark mode, charts you can read —
  but every surface carried the same weight, the same one blue and the same
  corner, so nothing on a page ever told you where to look first. This is the
  first half of that: the shared foundation every page draws from, changed once
  so that every page changes with it.

  Headings and money are now set in **Space Grotesk**, with **Instrument Sans**
  for reading — two typefaces that ship inside the app itself rather than being
  fetched from anyone else's servers. Amounts use tabular figures, so a column of
  them finally lines up on the decimal point instead of shifting about row to
  row. The navigation rail is dark in both light and dark mode, which gives the
  page an edge to sit against. The month's net position gets a deep gradient
  panel of its own — the one thing on the page that does not look like a card,
  because it is the one number you came to see. Cards lift slightly under the
  pointer and settle in when a page loads.

  If you have asked your system for reduced motion, none of that moves: every
  animation and transition in the app is switched off in one place, and nothing
  is communicated by movement alone.

  Nothing about what any page *does* has changed, and no figure is computed
  differently. The Home page's own layout — which is a separate complaint, and a
  fair one — is next.

### Removed

- **The natural-language quick-add box is gone**, from both Home and the Add
  Transaction page. You could type "spent 42 on groceries at Safeway yesterday"
  and have the form filled in for you; in practice nobody did, and it took up
  the space at the top of both pages. Adding a transaction by hand is unchanged,
  and so is everything else the app does with your text.

## [0.7.0] - 2026-08-17

### Added

- **Bills whose amount changes every month can now tell you when they post.**
  A scheduled bill posts on its due date for the amount you set up — which is
  exactly right for a subscription, and wrong every single month for something
  like an electricity bill, where the day is fixed but the figure never is. The
  transaction lands carrying last month's number and nothing says so, so it sits
  in your history being quietly wrong until you happen to notice.

  A schedule can now be marked **"the amount changes every time."** Nothing about
  the schedule behaves differently — it still posts the amount you set — but if
  you have notifications turned on, you get one once it has posted, telling you
  to check what it actually came to. Tapping it opens your transactions, where
  you can correct the amount in place.

  This is the same notification switch you already have on the Profile page,
  which now names all three kinds it covers: the reminder the evening before a
  bill is due, this nudge after one has posted, and the note when the app is
  updated.

  The alert arrives with the daily pass rather than the moment the bill posts,
  and it is deliberately not tied to whether you happened to open the app that
  day — a bill posted by your morning visit is still flagged that evening.

### Fixed

- **Pending transactions now stay at the top of your history, however far back
  they are.** A pending charge is pinned to the top of the list so it can't be
  forgotten before you correct the amount — but the pinning only ever applied to
  the page you happened to be looking at. Once you had more than a page of newer
  transactions, an older pending charge quietly sat at the top of page three or
  four instead, which is exactly where you would never look for it. Every pending
  transaction now appears together at the top of the first page, and only there,
  so nothing is shown twice. Balances are unaffected — a pending amount still
  counts towards every figure exactly as it did.

- **A maintenance command on the server can no longer put an old version of the
  app back into production by accident.** Deployments have always named the exact
  version to run, but a hand-typed command that left the version out fell back to
  a tag that nothing keeps up to date — so instead of leaving things alone, it
  quietly swapped the running app for an older build. It happened once, in August:
  production went back three releases, and nothing looked wrong. The site was up,
  every health check was green, and the app worked, because the older code copes
  with the newer database.

  A command with no version now either does nothing new or refuses outright, and
  the server records which version it is running so the answer can be read off the
  box. Nothing about the automated deploy changes.

## [0.6.0] - 2026-08-10

### Added

- **Settings now says when each background job last actually ran.** Some work
  happens on a schedule rather than when you click something — most importantly,
  creating the transactions for your recurring bills and paychecks, which happens
  once a day for everyone whether or not anybody opens the app.

  Until now Settings could only tell you that the scheduler was switched on. That
  is not the same thing as it having done anything, and the difference matters:
  if it ever stopped, every page would still load, the app would still report
  itself healthy, and your recurring transactions would simply stop appearing —
  which looks exactly like a week where nothing happened to be due. You would
  most likely notice via a balance that looked wrong, weeks later.

  There is now a line per job saying when it last finished and what it did. A job
  that has not run recently enough is marked overdue. A job that is switched off
  on this server says so plainly instead of reporting as a fault — being off is a
  legitimate state, and a panel that cried wolf about it would be worth ignoring.

- **`scripts/release_prep.py`** — the mechanical half of preparing a release, in
  one command. It rolls `## [Unreleased]` under a dated heading, repairs the
  link-reference block at the bottom of this file, and moves the version and date
  carried by the dashboard's What's-new strip. Nothing user-facing changes.

  It deliberately does **not** write prose or choose the version number; those
  stay a human decision, and the strip's blocks are left byte-identical.

  The part that earns its keep is the third check: it reports any environment
  variable added to `.env.example` since the last release. A gated feature whose
  variable was never set on the server deploys completely invisible — the feature
  simply is not there, which looks identical to it being broken. That check
  caught `FEEDBACK_GITHUB_TOKEN` by hand before `0.4.1`; doing it by hand is what
  this removes. It reports three states rather than two, because "could not tell"
  (no git, unknown tag) must not read as "nothing new".

## [0.5.0] - 2026-08-03

### Added

- **The Settings page now says which optional features are actually switched on.**
  Several features — the AI cards, the weekly digest email, push notifications and
  in-app bug reports — only appear once their credentials are set on the server.
  That is deliberate, but it means a feature that was never configured looks
  exactly like a feature that is broken: in both cases there is simply nothing
  there. Until now the only way to tell them apart was to log into the server.

  An admin-only table on Settings answers it directly, one line per feature. It
  also distinguishes a third case that used to be invisible: a credential that is
  present but too short to be real — a half-finished copy-paste. That one is worse
  than a missing credential, because the feature appears, accepts what you type,
  and then fails every time.

  **No credential is ever shown** — not the value, not the first few characters,
  not a masked version. Each line says only whether the feature is on, off, or
  misconfigured.

### Changed

- **Auto-Categorize and the weekly money check run on a newer model.** These are
  the two places the app asks for judgement rather than a summary — which
  category a transaction belongs in, and whether a week's spending is worth
  mentioning — so they run on a more capable model than the rest. That model has
  been superseded, and both now use its replacement.

  The newer model reasons before it answers, which takes room it did not need
  before, so both were given more of it. Without that, a long answer would have
  been cut off mid-sentence and quietly discarded — the app would have shown you
  nothing rather than something wrong, but shown you nothing all the same.

- **The update notification no longer says "Budget Buddy" twice.** On a phone
  with the app installed, the notification carried the name in its own heading
  *and* again on the line underneath — so it read "Budget Buddy 0.4.1 is live /
  from Budget Buddy". That second line is written by the browser itself and
  cannot be turned off, so the heading gives it up instead: it now reads
  "Version 0.4.1 is live". The closing line gained an exclamation point.

## [0.4.1] - 2026-07-31

Carries everything in `0.4.0`, which was tagged but **never deployed** — it was
withdrawn at the approval gate, before reaching the server, so no version of it
ever reached anyone.

### Changed

- **The update notification now says the same thing every time** — "Check out
  what's new in the app." — instead of trying to summarise the release in the
  notification itself. A lock-screen notification is glanced at, not read, and
  the detail is already waiting on the dashboard where the notification leads.

  Beyond the wording, this removes the machinery that summarising required: the
  release notes no longer travel from the build system to the server at all, so
  the text that a human types when publishing a release can no longer influence
  anything that runs there.

## [0.4.0] - 2026-07-31

### Added

- **A notification when Budget Buddy is updated.** When a new version goes
  live, every device that has notifications turned on gets a short note saying
  so, with a one-line summary of what changed. Tapping it opens the dashboard,
  where the fuller "what's new" summary for that release is waiting.

  This uses the notification switch you already have — the one on the Profile
  page — rather than adding a second one to manage. That section now says so
  plainly: it covers bill reminders **and** update notices, and it is the same
  single switch for both. Nothing changes for anyone who has notifications
  turned off.

- **Report a bug or suggest a feature from inside the app.** A form on the
  Profile page files the report straight into the project's issue tracker, so a
  problem can be reported in the reporter's own words rather than relayed
  second-hand.

  The form states plainly, above the fields, that **what you write is published
  publicly on GitHub**. Nothing is attached automatically — not your username,
  not an account name, not a balance, not a single transaction. Only the words
  you type are sent.

  The feature is invisible unless a GitHub token is configured, so it is off by
  default and stays off in local development and CI.

### Changed

- **The application image now runs Python 3.14** (was 3.11). No behaviour
  changes — this is the runtime the app is built and shipped on, and it moves
  three minor versions in one step so that it happens deliberately and with
  evidence rather than as a dependency bump waved through on a green tick.

  Every pinned dependency installs from a prebuilt wheel for 3.14, the full test
  suite passes inside the built image, and the app boots and serves with the
  background scheduler running.

## [0.3.1] - 2026-07-31

### Fixed

- **Two slices of the category doughnut could still be the same colour.** Fixed
  in `0.3.0` for up to eight categories, but the palette has eight hues and the
  colour was picked by *creation order* wrapped around that — so a user's 1st
  and 9th categories were handed the identical hue, and whenever both appeared
  in the chart's top six, two slices and their legend swatches were
  indistinguishable. Reported on production with **Monthly Bills** and **Food &
  Dining** both drawn in the same orange.

  Every slice now gets a colour no other slice on screen is using. Creation
  order is still the preference, so a category keeps the colour you are used to
  and only one that would have collided moves.

  This is guaranteed rather than merely likely: the chart draws at most six
  named categories plus "Other", against a palette of eight, so a free colour
  always exists.

## [0.3.0] - 2026-07-31

### Added

- **Local development now has a working editor and a live-reload loop.** The
  application's dependencies only ever existed inside the Docker image, so the
  language server could not resolve `flask`, `psycopg2` or any of the app's own
  modules — a 74-file Flask project was being written with no autocomplete, no
  go-to-definition and an unresolved-import warning on essentially every file.

  Fixed with a gitignored `.venv` that the editor reads and nothing is ever run
  from — the app and the tests still run in containers. VS Code auto-discovers
  it, so no editor settings are committed. Setup is in `CONTRIBUTING.md` §1.

  The source is also bind-mounted now, so a Python or template change is live
  instead of needing `docker compose up --build`. Editing `style.css` still
  needs `docker compose restart web`, because its cache-busting hash is computed
  at startup.

  **None of this reaches production** — it lives in
  `docker-compose.override.yml`, which exists only on a developer's machine. The
  one application change is a `TEMPLATES_AUTO_RELOAD` env gate, off unless
  explicitly set.

- **The test suite runs in parallel** (`pytest-xdist`, `-n auto` by default),
  taking a full run from **~204s to ~17s** on the maintainer's machine and
  applying to CI as well. `./test.sh -n0` forces serial when you need `pdb` or
  readable failure output; any explicit `-n` is respected.

  What made this possible: `tests/conftest.py` now derives its `TEST_PREFIX`
  from the xdist worker id, so each worker owns its own database rows. Workers
  are separate processes sharing one database, and with the previously
  hardcoded prefix they tore down each other's fixtures — a run in that state
  produces 424 errors. Two tests that hardcoded the prefix rather than deriving
  it were fixed at the same time.

- **`scripts/seed_dev.py`** — one command turns an empty database into a useful
  development dataset: four accounts (including two credit cards with limits and
  APRs), expense and income categories, six months of transactions, budgets,
  schedules, recurring transfers, and a savings goal plus a payoff goal that both
  show real progress. Nothing in it comes from a production or personal database;
  every merchant, amount and date is generated by a seeded PRNG.

  Until now the demo data existed only as rows in one machine's Docker volume, so
  a fresh clone or a second machine started schema-only and the only way to get a
  populated dashboard was to copy a dump of real financial data between machines.
  Dates are derived from today rather than hardcoded, so the dataset stays "the
  last six months" instead of ageing into an empty dashboard, and the fixture
  scales with `--months` rather than assuming the default window.

  Run it with `docker compose exec web python scripts/seed_dev.py`. It refuses to
  write into a database that already has the target user unless given `--force`.

- **Automated first-pass triage on newly opened issues** —
  `.github/workflows/claude-triage.yml` runs `anthropics/claude-code-action@v1`
  when an issue is opened and posts a single comment: what the code actually does,
  which files a fix would touch, the `CLAUDE.md` constraints that apply, and the
  decisions still left to a human.

  It is **read-only by design** — no branch, no commit, no pull request. The job
  holds `contents: read` and no `pull-requests` permission at all, so that is
  structural rather than a matter of instruction. The reason it stops at a comment
  is that a change here has to be verified in the running app as well as by the
  suite, and a runner can go green on a change that looks wrong.

  The motivating case was issue #83, filed with two competing hypotheses; checking
  which one was true took a mechanical pass over the real inputs, which is exactly
  the work a runner can do before anyone sits down.

  Nothing runs until a repository admin installs the Claude GitHub App and adds a
  `CLAUDE_CODE_OAUTH_TOKEN` secret; without it the workflow is inert. That token
  bills against the existing Claude subscription rather than metered API credits —
  the same budget local sessions draw on, which is why the trigger is
  issue-opened only and the run is capped by `--max-turns` and `timeout-minutes`.

- **A transaction can be marked Pending**, for a charge whose real amount lands
  later. A fuel pump authorises $1.00, so $1.00 is what gets entered, and the
  true figure only arrives a day or two later when the charge posts; restaurant
  tips and hotel holds behave the same way. Until now nothing marked those rows
  as provisional, so they sank into History in date order and a placeholder
  amount could sit in the ledger indefinitely, quietly wrong.

  Tick the box when adding the transaction. The row then carries a **Pending**
  badge and stays **pinned to the top of History** until you press **Mark
  posted** — so it is in front of you every time you open the page, rather than
  buried by whatever you have spent since.

  **A pending row counts normally in every figure.** The dashboard, budgets,
  insights, forecasts and your account balance all treat it as a real
  transaction, because the money genuinely left the account — a placeholder
  amount being briefly wrong is better than spend that silently does not count
  at all. It is a display flag, not an exclusion like "exclude from analytics".

  Correcting the amount does **not** clear the flag on its own; that is
  deliberate, so a corrected-but-not-yet-confirmed row does not quietly stop
  being flagged. Clearing it is always your call.

  Two details worth knowing: the balance column shows a dash for a pending row
  (a running balance printed out of date order reads as wrong, and the balance
  after a provisional amount is not a number worth showing), and the CSV export
  keeps plain date order rather than leading with pending rows.

### Changed

- **The dashboard's category doughnut now shows the six largest categories and
  folds the rest into a single "Other" slice**, in both the Expenses and Income
  views. A doughnut is a part-to-whole-at-a-glance form and stops being readable
  well before the ten-plus slices it was previously willing to draw: only about
  four hues clear all-pairs colour-vision separation, so past that, slices start
  reading as each other regardless of which palette is used.

  The card's total is unchanged — "Other" is exactly the sum of what it
  replaces, and hovering it says how many categories it stands for. Nothing else
  is affected: the fold happens when the chart payload is built, so budgets,
  insights, forecasts, the CSV export and every other surface still see complete
  per-category figures. Users with six or fewer categories see no change at all.

- The `Dockerfile` is now multi-stage: `base` → `dev` (adds the test
  dependencies) → `prod`. **The shipped image is unchanged** — a build with no
  explicit target still produces exactly what it produced before, and CI now
  asserts that by failing if `pytest` is importable in it. Local development
  selects the `dev` stage via `docker-compose.override.yml`, which never exists
  on the Droplet.

  `./test.sh` uses that to run the suite inside the already-running `web`
  container instead of creating a throwaway one and reinstalling pytest into it
  on every invocation. It falls back to the old behaviour when nothing is
  running, and says which path it took. Per-invocation overhead measured on the
  maintainer's machine: ~2.9s before, ~0.8s after. The full suite is unaffected —
  it is ~201s of pytest either way.

### Fixed

- **Two slices of the dashboard's category doughnut could be the same colour.**
  With seven categories on screen only five distinct colours were issued — two
  palette slots went unused while two were handed out twice — so two pairs of
  slices, and their legend swatches, were identical. There was no colour
  information distinguishing either pair at all.

  The cause was hashing the category *name* into a seven-entry palette. That
  kept a category's colour stable, which was the point, but seven names land in
  seven slots distinctly only about 0.6% of the time, so a collision was the
  expected outcome rather than an edge case. The comment in the code claimed
  collisions were only possible *past* seven categories, which is backwards and
  is probably why it shipped.

  Colours now come from each category's creation order, computed server-side, so
  they cannot collide while there are eight or fewer. That is also strictly more
  stable than the hash was: a category keeps its colour when a month filter
  changes which categories are on screen, and adding a new category gives it the
  next unused colour instead of shifting anyone else's.

  The palette itself was replaced with eight hues validated for colour-vision
  deficiency and contrast, and it now lives in `style.css` as `--series-1..8`
  with **separate steps chosen for dark mode** rather than reusing the light
  ones, which did not have enough contrast against the dark card background.

  Known limit, unchanged by this fix: a doughnut stops being readable somewhere
  around six slices no matter how good the palette is, and a ninth category
  wraps back onto the first one's colour. Both point at showing fewer segments
  rather than inventing more hues, which is filed separately.

- **The History CSV export now says what each row is.** It carries a **Kind**
  column: `transfer`, `adjustment`, or empty for an ordinary transaction.

  The export is a download of the History view, so it deliberately includes
  transfer legs and balance-check-in adjustments as rows — that is what the page
  shows. But History badges those rows and the CSV had no column that did, so
  once the file was open there was no way to tell them apart. Summing the Amount
  column therefore double-counted every transfer (both legs are in the file) and
  folded in adjustments, giving a total that silently disagreed with the app for
  the same month.

  The rows are unchanged — filtering them out would have broken "download what
  you see" and made the export disagree with the page it came from. Filtering to
  the empty Kind cells now reconciles against the app's own figures.

## [0.2.0] - 2026-07-28

The first feature release since the repository reboot. Two user-facing
additions — bill reminders and schedule end dates — plus the infrastructure work
that had been sitting on `main` since `0.1.0`.

### Added

- **Bill-due push reminders** to the installed app. A notification the evening
  before a scheduled bill or transfer is due, so there is still time to move
  money — the app already knew every due date, but that only reached you if you
  opened it or read the Sunday email. Opt in per device on the Profile page;
  a phone and a laptop are separate subscriptions. Reminders are deduplicated
  per occurrence, so a bill is never announced twice, and a subscription the
  push service reports as gone (the app was uninstalled, site data cleared) is
  deleted rather than retried forever.

  Push is optional. With no VAPID keys configured the opt-in UI does not appear
  and nothing is sent, exactly as the app already behaves without an Anthropic
  or Resend key. **On iPhone, Web Push only works once the app has been added
  to the home screen.**

- A recurring schedule can have an **end date**, on both scheduled
  income/expenses and automatic transfers. A car loan has a final payment and a
  membership gets cancelled; until now the only way to stop a schedule was to
  remember to delete it on the right day, and forgetting silently posted
  transactions that never happened — which then poison balances,
  budget-vs-actual and the forecast. The field is optional and blank means "runs
  indefinitely", so nothing about an existing schedule changes.

  A schedule that has run its course is shown as **Finished** rather than
  quietly going inactive, which keeps it distinct from one that was paused. The
  end date is honoured everywhere a schedule is read, not just where it posts:
  the dashboard, the month-ahead forecast, the weekly digest and Ask all stop
  advertising a bill that can never be charged again. A schedule whose next due
  date went stale *and* whose end date has since passed posts nothing at all on
  the next login, rather than back-filling every occurrence it "missed".

- The changelog is now enforced rather than merely requested: a pull request
  that changes `app/` without touching `CHANGELOG.md` fails, with a
  `skip-changelog` label as the escape hatch. This is what the repository
  reboot was originally started for — a convention nobody enforces decays, and
  the failure only becomes visible when someone tries to reconstruct a release
  and finds gaps.
- Migrations are applied automatically during deploy (`scripts/migrate.py`),
  tracked in a `schema_migrations` table. The deploy takes a verified `pg_dump`
  first and fails if that dump fails, then applies pending migrations **before**
  pulling the new image. `DROP`s remain deliberately manual — they must apply
  *after* the pull, which is the opposite order.
- A CI job asserting migrations apply cleanly to a database built from
  `schema.sql`, that the runner is idempotent, and that a PR adding a numbered
  migration also updates `schema.sql`. The last check closes a real drift risk:
  `schema.sql` builds every fresh database, so a migration missing from it means
  new environments silently lack the change.
- CI now classifies what a pull request changed and gates the expensive steps on
  it, so a docs-only change no longer pays for the full suite. When the runtime
  itself changes (`Dockerfile`, `requirements*.txt`) the suite additionally runs
  **inside the built image** — the runner's Python is not necessarily the one
  that ships, so a base-image bump used to go green having tested the runtime it
  was replacing.

### Changed

- **Due schedules now materialise server-side, once a day, for everyone.**
  Previously this happened only when a page was loaded, so a user who did not
  log in had transactions silently not posted — which then understated their
  weekly digest, their month-ahead forecast, and the agent's view of their own
  data. The daily job closes that. The login-triggered runners stay as well, so
  logging in still catches you up immediately.

  ⚠️ Note for anyone running their own instance: **the scheduler is no longer
  gated on an email key.** It starts on `ENABLE_DIGEST_SCHEDULER` alone and each
  job carries its own gate, because materialisation must not stop just because a
  third-party credential is missing. If you set `ENABLE_DIGEST_SCHEDULER=1`
  without a Resend key, nothing ran before; now the daily materialise pass does.

- Logging out asks for confirmation first. It is a nav item sitting beside
  ordinary navigation, and on the installed PWA a mis-tap costs a re-login on a
  touch keyboard. Logout remains a POST-only form with its own CSRF token; with
  JavaScript disabled it submits as before, without a prompt.

### Fixed

- The "Ask your finances" answer panel is readable in dark mode. It was styled
  inline against `var(--bg-subtle)`, a custom property defined nowhere in the
  stylesheet. That never failed loudly because the declaration carried a
  hard-coded `#f6f7f9` fallback — a pale grey that looks right in light mode and
  leaves light text on a light panel in dark mode. Presentation moved to a real
  `.ask-answer` rule using the theme-aware `--surface-2` token. A test now
  asserts that every `var(--token)` in the stylesheet and templates resolves to
  a token that is actually defined, so a phantom one cannot return unnoticed.

### Removed

- Mealie and Uptime Kuma were retired from the Droplet, and their cards removed
  from the landing page. The server now runs Budget Buddy alone. Their data was
  archived first — including Mealie's uploaded recipe images, which the nightly
  job had never covered — and the nightly backup no longer attempts to dump a
  database that is gone. Freed roughly 2 GB and dropped disk use from 64% to
  30%.
- `deploy.sh`, `promote.sh` and `docker-compose.staging.yml`. They built and
  promoted the Docker Hub image, which production no longer uses as of `0.1.0`;
  the staging step they fed is now the release workflow's `smoke` job, which
  tests the pushed artifact rather than a local rebuild. The Docker Hub image
  remains as an emergency fallback and the scripts stay in git history
  (`git show v0.1.0:deploy.sh`).

### Security

- Recorded that the Droplet's disk is **not** encrypted at rest. This had been
  documented as unverified; DigitalOcean states plainly that virtual disks on
  hypervisor local storage are not encrypted, and that encrypting them is the
  customer's responsibility. Affects the live Postgres volume, `.env`, and
  pre-deploy dumps. `RUNBOOK.md` now carries the finding, what it does and does
  not protect against, and the supported remedy (a LUKS-encrypted Block Storage
  Volume) should it ever be worth closing.


## [0.1.0] - 2026-07-27

The first release of the rebuilt repository, and a **baseline snapshot** rather
than a feature release: the application is carried over unchanged from
`v10.15.0`, while everything around it — workflow, CI, CD, registry, versioning
and documentation — was rebuilt. Production moves from Docker Hub to
`ghcr.io/caddismaster/budget-buddy` with this release.

### Added

- Repository reboot: fresh history, `.env.example`, tracked
  `docker-compose.override.yml`, MIT license, and contributor documentation
  (`README.md`, `CONTRIBUTING.md`, `VERSIONING.md`, `RUNBOOK.md`, this file).
- The Docker image runs as an unprivileged user (`appuser`, uid 10001) instead
  of root.
- `RUNBOOK.md` — production topology, Nginx configuration, TLS, backup and
  restore procedures, and a rebuild-from-nothing checklist. None of this
  previously existed outside the server itself.
- `GET /healthz` — an unauthenticated liveness probe that performs a real
  database round-trip, returning 200 when healthy and 503 when Postgres is
  unreachable. Wired up as a Docker healthcheck. Monitoring a normal page is
  not equivalent: with the database stopped, `/login` still returns 200.
- Ruff linting, configured in `pyproject.toml` and enforced in CI.
- Pre-commit hooks (`.pre-commit-config.yaml`) — ruff plus hygiene checks,
  including `detect-private-key`. Local convenience; CI remains the
  enforcement point.
- CI now builds the Docker image, asserts the container runs as `appuser`, and
  boots it to confirm gunicorn serves a request. Previously CI installed
  dependencies on the runner and never built the Dockerfile at all.
- Continuous deployment (`.github/workflows/release.yml`). Publishing a GitHub
  Release builds the image, pushes it to `ghcr.io/caddismaster/budget-buddy`
  tagged with the version, the commit SHA and (for full releases only)
  `latest`, boots that **pushed** image against a throwaway database to prove
  `/healthz` answers, then pauses on a required-reviewer approval gate before
  deploying to the Droplet and verifying the public `/healthz`. Replaces the
  hand-run `deploy.sh` → staging → `promote.sh` sequence; the smoke job takes
  over the "prod runs the bytes you tested" guarantee that the local staging
  step used to provide.
- A rollback workflow (`.github/workflows/rollback.yml`) — dispatch a version
  and the Droplet is brought back up on that exact immutable tag, after
  confirming the image actually exists in the registry.
- Deploys pull only the `web` service. A bare `docker compose pull` also
  fetched `postgres:16`, and `up -d` then recreated the database container —
  so shipping application code could upgrade and restart the database engine
  as a side effect. A database upgrade is a deliberate action taken after a
  dump, not a consequence of a release.

### Changed

- The deployed image is pinned to an exact version at deploy time
  (`TAG=<version> docker compose up -d`) rather than tracking `latest`, so the
  running container is always traceable to a release. `docker-compose.yml`
  resolves `${TAG:-latest}`, keeping a hand-run `docker compose up -d` working.

- CI runs on `main` and pull requests only, with a concurrency group, so a
  branch push and its pull request no longer trigger duplicate runs.
- Workflows declare least-privilege `permissions`.

### Security

- The application can now connect as a least-privilege `budget_app` role
  (`sql/30_app_role.sql`) holding only `SELECT`/`INSERT`/`UPDATE`/`DELETE`,
  configured via `DB_APP_USER`/`DB_APP_PASSWORD`. Previously it authenticated
  as the database owner — a superuser — so any SQL injection or code execution
  inherited the ability to drop tables, read every database on the cluster, or
  run shell commands via `COPY ... FROM PROGRAM`. Falls back to `DB_USER` when
  unset, so existing deployments are unaffected until they opt in.
- `/admin/backup` — one authenticated GET returns the whole database as
  plaintext SQL, so it is now rate-limited to 5 per hour, returns `403` for
  non-admins instead of flashing and redirecting, and logs every export with
  the username. Previously a full-database export left no trace at all.
- Deployments no longer run as `root`. The production stack moved from
  `/root/budget-buddy` to `/opt/budget-buddy`, owned by a dedicated
  unprivileged `deploy` user that CI authenticates as with a restricted
  ed25519 key; the deploy secrets are scoped to the approval-gated
  `production` environment. Moving out of `/root` also let `/root` return to
  mode `0700` — it had been widened to `0755` so Nginx could serve the landing
  page from inside it, which left the production `.env` (database password,
  `SECRET_KEY`, API keys) readable by **every user on the host**. Both that
  file and Mealie's are now unreadable to anyone but their owner.
  (Server-side configuration change, recorded here for the history.)

### Fixed

- `https://www.seandesmet.com` failed the TLS handshake. Certbot had
  accumulated four certificate lineages for two names; the landing page's
  server block claimed `www` while presenting a certificate that did not cover
  it. Repointed at the lineage covering both names and deleted the orphan.
  (Server-side configuration change, recorded here for the history.)

- `./test.sh` invoked bare `pytest`, which is not on `PATH` under the non-root
  image — pip installs console scripts to `~/.local/bin`. Now invoked as
  `python -m pytest`.
- `.gitignore` listed `.DS_store`, which never matched the real `.DS_Store`
  (git patterns are case-sensitive).

---

## Prior history

Versions before `0.1.0` — the `v1` through `v10.15.0` era — were developed in a
different repository, archived read-only at
[CaddisMaster/budget-buddy-archive](https://github.com/CaddisMaster/budget-buddy-archive).
Its git tags and GitHub releases carry the full detail. A summary of that
lineage, most recent first:

- **v10.15.0** — APR on credit cards with an interest-aware payoff projection; AI-card read-state collapse
- **v10.14.0** — brand icon redo: coin-with-$ mark, full-bleed maskable and apple-touch sources
- **v10.13.0** — design pass: vendored Chart.js, money formatting filter, navigation regroup, collapsible charts, home/dashboard merge, PWA shell, mobile history cards
- **v10.12.0** — income categories (`categories.kind`); history running-balance fix; safe-to-spend removed
- **v10.11.x** — bulk edit; parameter-validation hardening; named-row refactor; automatic CSS cache-busting
- **v10.10.0** — credit limits; the Money agent (first autonomous tool-use loop)
- **v10.9.0** — debt-payoff goals; balance check-in; budget report; dashboard consolidation
- **v10.8.0** — weekly email digest (APScheduler + Resend); goal coach
- **v10.7.0** — AI budget review
- **v10.6.0** — design refresh: design tokens, dashboard hero, chart grid
- **v10.5.0** — auto-categorize (batch classification)
- **v10.4.0** — recurring account transfers
- **v10.3.0** — ask-your-finances (multi-turn tool use)
- **v10.2.0** — month-ahead forecast
- **v10.1.x** — monthly insight; security-hardening patch (write-side IDOR fix, security headers, cookie flags, CSV formula-injection sanitizer)
- **v10.0** — scheduled income and bills; recurrence moved out of the ledger
- **v9.0** — conversational transaction entry (first AI feature)
- **v1–v8** — core CRUD and deployment, UI overhaul, multi-user authentication, blueprints and pytest, ownership guards, transfers and goals, smart budgets, HTMX inline CRUD and CI

[Unreleased]: https://github.com/CaddisMaster/budget-buddy/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/CaddisMaster/budget-buddy/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/CaddisMaster/budget-buddy/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/CaddisMaster/budget-buddy/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/CaddisMaster/budget-buddy/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/CaddisMaster/budget-buddy/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/CaddisMaster/budget-buddy/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/CaddisMaster/budget-buddy/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/CaddisMaster/budget-buddy/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/CaddisMaster/budget-buddy/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/CaddisMaster/budget-buddy/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/CaddisMaster/budget-buddy/releases/tag/v0.1.0
