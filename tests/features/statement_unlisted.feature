@issue-457
Feature: Show ledger entries the statement does not list
  Matching runs from the statement to the ledger, so a ledger entry the
  statement never mentions used to go unseen. That hides what a reconcile is
  for: something entered twice, a typo'd amount, a charge that was cancelled.
  The review now lists those entries. It changes nothing by itself.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"
    And a statement for "Checking" covers 30 days ago to today

  Scenario: An entry missing from the statement is shown
    Given "Checking" holds "Gym" for $30.00 out 20 days ago
    And it lists "PAYROLL" for $2000.00 in 5 days ago
    When user A uploads it
    Then "Gym" is shown as in my ledger but not on the statement

  Scenario: Entered twice shows the second copy
    Given "Checking" holds "Coffee" for $4.50 out 27 days ago, twice
    And it lists "BLUE BOTTLE" for $4.50 out 27 days ago
    When user A uploads it
    Then exactly one "Coffee" is shown as in my ledger but not on the statement

  Scenario: Matched entries are not shown
    Given "Checking" holds "Rent" for $1500.00 out 30 days ago
    And it lists "RENT PMT" for $1500.00 out 30 days ago
    When user A uploads it
    Then "RENT PMT" is shown as already recorded
    And "Rent" is not shown as in my ledger but not on the statement

  Scenario: An entry from the period's last days is not flagged
    Given "Checking" holds "Groceries" for $50.00 out yesterday
    And it lists "PAYROLL" for $2000.00 in 5 days ago
    When user A uploads it
    Then "Groceries" is not shown as in my ledger but not on the statement

  Scenario: Balance check-ins are not flagged
    Given a balance check-in adjusted "Checking" by $25.00 out 15 days ago
    And it lists "PAYROLL" for $2000.00 in 5 days ago
    When user A uploads it
    Then "Balance check-in" is not shown as in my ledger but not on the statement

  Scenario: Screenshot uploads leave the section out
    Given "Checking" holds "Gym" for $30.00 out 20 days ago
    And a screenshot of "Checking" shows "PAYROLL" for $2000.00 in 25 days ago
    And it shows "COFFEE" for $4.50 out 2 days ago
    When user A uploads the screenshot
    Then nothing is shown as in my ledger but not on the statement
