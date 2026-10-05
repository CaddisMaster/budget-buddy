@issue-447
Feature: Fill in missing transactions from screenshots
  With no export to hand, screenshots of a banking app's transaction list feed
  the same review as a statement file. The model reads the lines; the app
  re-checks them, decides what is missing, and never pre-ticks a line the model
  found hard to read. The images are never kept.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"

  Scenario: Lines read from a screenshot are reviewed like a statement
    Given a screenshot of "Checking" shows "GROCERY MART" for $82.17 out 3 days ago
    And "Checking" has nothing for $82.17
    When user A uploads the screenshot
    Then "GROCERY MART" is shown as missing and checked

  Scenario: A line the model could not read cleanly is not pre-checked
    Given a screenshot of "Checking" shows "GROCERY MART" for $82.17 out 3 days ago
    And it shows "TACO TRUCK" for $12.30 out 2 days ago, cut off
    When user A uploads the screenshot
    Then "TACO TRUCK" is shown flagged and unchecked
    And "GROCERY MART" is shown as missing and checked

  Scenario: A date without a year is never placed in the future
    Given a screenshot of "Checking" shows "PREORDER" dated a few days from now, with no year
    When user A uploads the screenshot
    Then "PREORDER" is dated a year before that day

  Scenario: No closing balance means no balance check
    Given a screenshot of "Checking" shows "GROCERY MART" for $82.17 out 3 days ago
    When user A uploads the screenshot
    And applies the review as shown
    Then exactly 1 transaction has been added to "Checking"
    And no balance comparison is shown

  Scenario: A model failure adds nothing
    Given reading screenshots fails
    When user A uploads a screenshot to "Checking"
    Then user A is told the screenshots could not be read
    And exactly 0 transactions have been added to "Checking"
