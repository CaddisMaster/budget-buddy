Closes #

## What this changes

<!-- A sentence or two. The diff shows what; explain why. -->

## How it was verified

<!-- What did you actually run and look at? "CI is green" alone is not enough —
     CI does not click through the app. -->

- [ ] `docker compose up --build` and exercised the change in the browser
- [ ] `./test.sh` passes in full
- [ ] New behaviour has a test that fails without this change

## Checklist

- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] Any new query is scoped to the current user
- [ ] Any amount input goes through `parse_positive_amount()` / `parse_signed_amount()`
- [ ] A schema change includes **both** a numbered `sql/` migration and the `schema.sql` update
- [ ] No secrets, credentials, or real financial data in the diff

## Anything reviewers should look at closely

<!-- Optional. A decision you were unsure about, a trade-off you made, or a
     piece you would like a second opinion on. -->
