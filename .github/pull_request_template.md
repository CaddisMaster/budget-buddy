Closes #

## What this changes

<!-- A sentence or two. The diff shows what; explain why. -->

## How it was verified

<!-- What did you actually run and look at? "CI is green" alone is not enough —
     CI does not click through the app. -->

- [ ] `docker compose up --build` and exercised the change in the browser
- [ ] `./test.sh` passes in full
- [ ] New behaviour has a test that fails without this change
- [ ] Every `Scenario:` in the issue's acceptance criteria is claimed — a `@issue-<n>` tagged
      `.feature` scenario, a `@pytest.mark.criterion(<n>, "<title>")` test, or a line below
      (the `Acceptance criteria` check enforces this; see `docs/testing.md`)

<!-- For a criterion no test can hold (docs, process), one line each:
Verified by hand: #<n> "<Scenario title>" — what you checked
-->

## Checklist

- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] Any new query is scoped to the current user
- [ ] Any amount input goes through `parse_positive_amount()` / `parse_signed_amount()`
- [ ] A schema change includes **both** a numbered `sql/` migration and the `schema.sql` update
- [ ] No secrets, credentials, or real financial data in the diff

## Anything reviewers should look at closely

<!-- Optional. A decision you were unsure about, a trade-off you made, or a
     piece you would like a second opinion on. -->
