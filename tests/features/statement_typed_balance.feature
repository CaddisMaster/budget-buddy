@issue-474
Feature: Tell the import what balance to check
  A statement that carries no balance, such as most CSV exports, used to end
  the import with no check at all. The upload form now takes the balance from
  the statement or the bank's app, and the date it applies to. A typed balance
  is used even over the file's own, and the review says which one it will check.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"

  Scenario: A typed balance is checked when the file has none
    Given "Checking" holds "Opening balance" for $1000.00 in 30 days ago
    And a CSV for "Checking" with "Debit" and "Credit" columns lists "GROCERY MART" debited $82.17 3 days ago
    And user A enters a closing balance of $917.83 today
    When user A uploads it
    And applies the review as shown
    Then user A is told the ledger agrees with the balance they entered

  Scenario: A typed balance is used over the file's own
    Given "Checking" holds "Opening balance" for $1000.00 in 30 days ago
    And a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    And it closes with a balance of $500.00
    And user A enters a closing balance of $917.83 today
    When user A uploads it
    And applies the review as shown
    Then user A is told the ledger agrees with the balance they entered

  Scenario: The review says which balance it will check
    Given a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    And user A enters a closing balance of $917.83 today
    When user A uploads it
    Then the review says it will check $917.83, entered by user A

  Scenario: A balance that is not an amount is refused before the file is read
    Given a CSV for "Checking" with "Debit" and "Credit" columns lists "GROCERY MART" debited $82.17 3 days ago
    And user A enters "twelve dollars" as the closing balance
    When user A uploads it
    Then user A is told the balance could not be read
    And nothing was sent to the AI

  Scenario: A balance dated in the future is refused
    Given a CSV for "Checking" with "Debit" and "Credit" columns lists "GROCERY MART" debited $82.17 3 days ago
    And user A enters a closing balance of $917.83 tomorrow
    When user A uploads it
    Then user A is told the balance's date cannot be in the future
    And nothing was sent to the AI
