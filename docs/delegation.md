# Delegation and automated issue triage

> Split out of `CLAUDE.md` on 2026-08-17. **Read before you run `gh issue create`**, before you
> edit `.github/workflows/claude-triage.yml`, and before you spawn a worker agent.
> ⚠️ The first of those triggers was added 2026-08-18: this file previously said "touching issue
> triage", which a session filing an issue did not recognise as describing itself.

## Automated issue triage (`.github/workflows/claude-triage.yml`)

An issue **carrying the `triage` label** gets an automated first-pass comment in ~2 minutes; one
without it gets nothing (see the opt-in note below). Two prompts behind a label branch, resolved
by querying the issue's labels:

- **`enhancement` → SPECIFY** — restates the idea against real files, drafts Gherkin acceptance
  criteria, names the file surface, lists the applicable gotchas from this file, and raises the
  open design questions. Built for a rough idea filed from the GitHub phone app; it is instructed
  NOT to invent requirements and to put real choices in Open questions instead.
- **anything else → TRIAGE** — diagnoses: what the code actually does, whether a stated cause
  holds, what a fix would touch. (An issue with no labels at all only reaches this branch via a
  dispatch, since the automatic run now requires `triage`.)

**READ-ONLY by construction**, and it is enforced in two independent places: `permissions:` gives
`contents: read` with no `pull-requests` at all, and `--allowedTools` grants exactly
`Bash(gh issue view:*),Bash(gh issue comment:*)`. It comments; it never edits the issue, pushes,
or opens a PR. **Do not widen the allowlist to `Bash(gh:*)`** — that reaches `gh issue edit`,
`gh pr create` and `gh api`, i.e. write access to everything, through the one control you thought
was constraining it. `contents: read` blocks the git half; the allowlist is the only thing
blocking the API half.

- ⚠️ **TRIAGE IS OPT-IN — the `triage` label runs it** (inverted 2026-08-18; it was `skip-triage`
  opt-OUT before). The two `ISSUE_TEMPLATE` files carry `labels: [..., "triage"]` and
  `app/blueprints/feedback.py` adds `TRIAGE_LABEL`, so **anything filed through the web UI, the
  mobile app or the in-app form reviews itself**. An issue filed by hand with `gh issue create`
  carries only what you pass — and that is the set that should NOT be reviewed, because the code
  was just read in the session that filed it.
  - **Why it was inverted:** the opt-out was forgotten on the very issue that proposed fixing
    this (#225, 2026-08-18) — the run fired four seconds after creation, spent ~$0.50 and posted
    a comment restating analysis from the same session. The rule was correct, documented, and in
    a file that is not auto-loaded; the failure was that following it required recognising
    "filing an issue" as "touching issue triage" *before* acting. **Inverting the default removes
    the decision instead of documenting it** — forgetting the label now costs a review nobody
    asked for rather than money.
  - `skip-triage` still exists as a label and now does nothing. Left in place deliberately: it is
    on historical issues, and deleting it would rewrite their record.
  - A **dispatch deliberately ignores the label**, as it always did: hand-written issues are
    exactly the ones worth a second read, so honouring it there would make them the only issues
    that could never be reviewed.
- **Manual run:** `gh workflow run claude-triage.yml -f issue=<n>` — the only way to review an
  issue opened before the workflow existed, since `issues: opened` cannot reach it and reopening
  is not `opened`.
- **Cost ~$0.50 / ~9 turns per run**, billed against the Claude **subscription** (via
  `claude_code_oauth_token`), so it competes with local Claude Code usage — hence `opened`-only,
  the turn cap, and the opt-in label.

⚠️ **A change to this workflow CANNOT be verified before merge, and the failure is silent.**
`claude-code-action` refuses to run when the workflow file differs from the copy on the default
branch (a deliberate control — otherwise a branch could edit the workflow and exfiltrate the
token). `workflow_dispatch --ref <branch>` does not help. **The run reports SUCCESS while posting
nothing** — only the log says why. Merge small, verify immediately by dispatching, revert if
wrong; a workflow-only change reverts cleanly.

Two gotchas worth not rediscovering: `permissions:` needs **`id-token: write`** (the action swaps
an OIDC token for an installation token, and fails before reaching the model without it), and
**`github.event.issue.labels` does not exist on a dispatch** — an expression like
`contains(github.event.issue.labels.*.name, …)` silently evaluates false on every dispatched run,
which is why the label is queried with `gh` instead. A `#` comment inside `claude_args` is passed
through as a literal CLI argument; comments go outside the block.

## Delegation (worker agents)

Four agents are defined in `.claude/agents/`, all Sonnet. They split into **two executors**
that edit files and **two reporters** that only read:

| Agent | Tools | Does |
|---|---|---|
| `sweeper` | Read/Grep/Glob/**Edit** | Mechanical multi-file sweeps from an explicit spec file |
| `test-first` | Read/Grep/Glob/**Edit** | Makes an orchestrator-written failing test pass, editing app code only |
| `gotcha-auditor` | Read/Grep/Glob/**Bash** | Audits a branch diff against this file's Key Gotchas |
| `release-prep` | Read/Grep/Glob/**Bash** | Runs the pre-release checklist against `main`, reports go/no-go |

**Why delegate at all:** the scarce resource on this project is the orchestrator's context
window, not time. Wide reads are what exhaust it, and wide reads are exactly what the
gotchas demand (*grep for the failure SHAPE*, the ~23 `is_adjustment = false` filter lists).
An agent burns its own context and returns a conclusion. That is the entire trade — against a
cold start, since **an agent cannot see the session that spawned it**.

Policy:

- **Delegate:** mechanical multi-file sweeps with an explicit written spec (attribute-access
  conversions, url_for conversions, find-replace-shaped work), and wide read-only recon. Write
  the spec to a scratchpad file (file list, old→new table, exclusions, expected per-file counts)
  and pass the worker the spec path — specs are re-runnable and diffable.
- **Delegate feature work ONLY test-first**, via `test-first`: the orchestrator writes the
  failing test, runs the suite, and hands the worker the test path, the verbatim failure and
  an explicit file surface. The worker edits app code and **never the test**. This is the one
  delegable shape of feature work, and it is delegable *because the spec is machine-checkable*
  — a wrong build stays red instead of going quietly wrong. See the ⚠️ below for why
  plan-then-execute delegation is NOT the general pattern here.
- **Do NOT delegate:** anything touching ownership guards, row shapes mid-refactor,
  SQL/migrations, AI seams (`_call_*_model`), or exception handling. `test-first` carries this
  same list as a decline list and hands back rather than doing it carefully.
- **Do NOT delegate running the suite.** A full run is ~35 seconds — a background test agent
  has no win to capture. **Every worker batch gets `./test.sh` (full suite) run by the
  orchestrator before commit**, plus a spot-grep that the swept pattern is gone. Workers never
  run tests, never commit, never edit outside their spec. With `test-first` this is also the
  iteration loop: suite → relay the failure → worker fixes, which keeps the orchestrator's
  per-cycle cost at a suite result rather than a diff review.
- **Batch sizing:** don't spin one worker per tiny area — target batches that gate meaningful
  work per full-suite run; per-file expected counts + the leftover grep localize failures.
- **If the prompt approaches the length of the work, don't delegate.** The cold start is then
  pure loss, which is why the delegate list names sweeps, recon and test-first work — and
  nothing else. For `test-first` the test itself is most of the prompt, so the rule is
  already satisfied whenever the test was worth writing.

⚠️ **Plan-then-execute is deliberately NOT the general pattern**, despite being the popular
one. The orchestrator does not hand a written plan to a cheap worker and take the diff.
Two reasons, both specific to this repo. First, **the plan is the fragile part**: four
issues running (#87, #83, #86, #108) had a specified approach that was *wrong on contact
with the code*, so a worker that faithfully implements a plan produces convincingly wrong
code rather than obviously wrong code. Second, **delegation pays only when verification is
cheaper than the work** — that holds for a reporter's conclusion (spot-check a few claims)
and for a sweep (grep + suite), but reviewing a feature diff against these gotchas costs
about what writing it costs, and Jinja's silent-empty-string failure mode means a wrong
diff can look right. A **failing test** is what restores the asymmetry, which is the whole
reason `test-first` is scoped the way it is. Settled 2026-08-04.

✅ **`test-first` was first exercised on 2026-08-10** (PR #173, `scripts/release_prep.py`) and
the pattern held: 25 tests green on the first pass, 10 more after one round trip, the test file
never edited, and it did not claim the tests passed. Two things worth keeping. It **flagged its
own scope creep** — an unrequested `--env-var` flag — rather than leaving it to be found; that
flag was in fact wrong, and volunteering it is what surfaced the design error. And **the round
trip caught a wrong SPEC, not wrong code**: the orchestrator's replacement tests shelled out to
`git`, which can never pass because the suite runs in a container with no git binary. A faithful
worker would have stayed red rather than producing something convincing — which is the entire
argument for this shape, demonstrated in about ninety seconds. **`sweeper` is now the only agent
never run.**

⚠️ **The second `test-first` outing (2026-08-14) was a MIS-SIZING, and the agents were not the
problem.** Two ran in parallel, one per worktree, on two small throwaway issues. Both produced
correct code first pass with zero round trips, neither claimed the tests passed, and both reported
scope creep they had **declined** (one wanted to factor a shared helper out of a function five
other tests pin — correctly refused). And it was still the wrong call: **~195k tokens of agent
context for 22 and 51 lines of code**, with prompts longer than what came back. The rule that
should have stopped it is already above — *if the prompt approaches the length of the work, don't
delegate* — and it was broken in the act of demonstrating it. **Sizing heuristic, use it alongside
the verification rule rather than instead of it: delegate when READING the files is the expensive
part, not when writing is.** Verification cost alone does not discriminate here — checking a
20-line diff is cheap, so that rule says yes while the economics say no; both tests must pass.
⚠️ Running the two in **parallel bought nothing** (both finished in ~90s) and cannot help anyway:
the two branches queue for the suite regardless, because `TEST_PREFIX` separates xdist workers
within one run and not two runs from each other.

⚠️ **An agent working in a `git worktree` cannot run the suite at all** — `.env` is gitignored so
`worktree add` never copies it, `test.sh` does `cd "$(dirname "$0")"` so compose runs in the
worktree under a *different project name*, and `${TAG:?}` (#190) then refuses outright. This costs
nothing, because `test-first` has no Bash either way — but it means the orchestrator runs every
test. To test a branch that is live in a worktree, `git` refuses a normal checkout and a **detached**
one is the way in: `git checkout --detach <branch>` in the main clone, run, `git checkout main`.
⚠️ Commit in the worktree first — the bench sees the commit, not the worktree's working files.

⚠️ **The two executors are tool-constrained; the two reporters are only PROMPT-constrained.**
`sweeper` and `test-first` have no Bash, so "you cannot run tests or commit" is structurally
true. `gotcha-auditor` and `release-prep` need `git diff`/`gh` reads to be useful standalone,
so they carry Bash and their read-only discipline lives in their prompt — a weaker guarantee.
Under manual invocation that is an accepted trade. **If either is ever wired to automatic
routing, add `deny` rules for `Bash(git commit:*)`, `Bash(git push:*)` and
`Bash(gh release:*)` to `.claude/settings.json` first.**

⚠️ **Agents are not skills, and neither is a slash command.** `/verify` is a skill — it loads
instructions into the orchestrator's own turn. An agent is a separate instance with its own
context whose report comes back to the orchestrator, never straight to the user. There is no
`/gotcha-auditor`. Nothing invokes an agent on a schedule either: automatic firing would be a
**hook** in `settings.json`, deliberately not configured (a Sonnet agent on every commit is
expensive and noisy, and both reporters are naturally once-per-PR / once-per-release).

⚠️ **Gotcha: agent definitions register at session START.** A `.claude/agents/*.md` created
mid-session isn't callable until next session — fall back to a general-purpose agent with
`model: sonnet` and the relevant rules inlined.

