@issue-458
Feature: A review that shows only what needs me
  A long card statement is mostly lines the ledger already holds. The review
  now opens with what applying will do, then only the lines that need a
  decision; everything else is still there, folded away and still editable.
  Nothing is written until I apply, as before.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"

  Scenario: The summary says what applying will do
    Given an upload finds 3 missing lines, 1 pending line and 5 recorded lines
    When the review is shown
    Then it says "Adding 3 transactions, marking 1 posted"

  Scenario: Lines needing a decision come first
    Given an upload finds a possible match and 10 missing lines
    When the review is shown
    Then the possible match is listed under "Needs you"
    And the missing lines are under a collapsed "Will be added" section

  Scenario: Recorded lines are out of the way
    Given an upload finds 40 recorded lines
    When the review is shown
    Then they are under a collapsed "Already in your ledger" section
    And none of them has a checkbox

  Scenario: Nothing needs me
    Given every missing line has a category and nothing is uncertain
    When the review is shown
    Then it says nothing needs me
