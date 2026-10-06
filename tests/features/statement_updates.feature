@issue-456
Feature: Fix an entry instead of duplicating it
  A possible match is usually the same transaction for a different amount: a
  tip added to a restaurant bill, a currency conversion, a hold that posted for
  more. Ticking it can now update my entry to what the bank recorded, rather
  than adding a second row beside it.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"
    And "Checking" holds "Dinner" for $40.00 out 5 days ago

  Scenario: Updating my entry changes it rather than adding one
    Given a statement for "Checking" lists "TRATTORIA" for $42.80 out 4 days ago
    When user A uploads it
    And chooses "Update my entry" for "TRATTORIA" and applies
    Then "Checking" holds "Dinner" for $42.80 out 4 days ago
    And exactly 0 transactions have been added to "Checking"

  Scenario: An updated entry is recognised on re-import
    Given a statement for "Checking" lists "TRATTORIA" for $42.80 out 4 days ago
    And user A has uploaded it and chosen "Update my entry" for "TRATTORIA"
    When user A uploads it again
    Then every line is shown as already recorded

  Scenario: A pending entry that posted for more is fixed and marked posted
    Given "Checking" holds a pending "Lunch" for $20.00 out 3 days ago
    And a statement for "Checking" lists "BISTRO" for $21.50 out 2 days ago
    When user A uploads it
    And chooses "Update my entry" for "BISTRO" and applies
    Then "Checking" holds "Lunch" for $21.50 out 2 days ago
    And "Lunch" is no longer pending

  Scenario: Adding as new still works
    Given a statement for "Checking" lists "TRATTORIA" for $42.80 out 4 days ago
    When user A uploads it
    And chooses "Add as a new transaction" for "TRATTORIA" and applies
    Then exactly 1 transaction has been added to "Checking"
    And "Checking" still holds "Dinner" for $40.00 out 5 days ago, unchanged

  Scenario: A row from another account cannot be updated
    Given user A has an account "Savings"
    And "Savings" holds "Treat" for $40.00 out 5 days ago
    And a statement for "Checking" lists "TRATTORIA" for $42.80 out 4 days ago
    When user A uploads it
    And applies "TRATTORIA" as an update of "Treat" in "Savings"
    Then "Savings" still holds "Treat" for $40.00 out 5 days ago, unchanged
    And "Checking" still holds "Dinner" for $40.00 out 5 days ago, unchanged
    And exactly 0 transactions have been added to "Checking"
