@issue-445
Feature: Fill in missing transactions from a statement
  When the ledger has fallen behind, a statement export from the bank says what
  really happened. Uploading one compares it with the account's ledger, line by
  line, and offers only what is missing. Nothing is added until it is checked
  and confirmed, and the file itself is never kept.

  The AI is stubbed in every scenario: it names a CSV's columns and suggests
  categories, and never decides what is missing.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"
    And "Checking" holds "Coffee Shop" for $4.50 out 7 days ago

  Scenario: Lines already in the ledger are recognised
    Given a statement for "Checking" lists "COFFEE SHOP #123" for $4.50 out 6 days ago
    When user A uploads it
    Then "COFFEE SHOP #123" is shown as already recorded
    And "COFFEE SHOP #123" is not offered for adding

  Scenario: A missing line is offered, checked, with a suggested category
    Given a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    When user A uploads it
    Then "GROCERY MART" is shown as missing and checked
    And "GROCERY MART" carries a suggested category

  Scenario: Only checked lines are added
    Given a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    And it lists "HARDWARE BARN" for $19.99 out 2 days ago
    When user A uploads it
    And unchecks "HARDWARE BARN" and applies
    Then exactly 1 transaction has been added to "Checking"
    And "Checking" holds "GROCERY MART" for $82.17 out 3 days ago

  Scenario: Nothing is added without applying
    Given a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    When user A uploads it
    Then "GROCERY MART" is shown as missing and checked
    And exactly 0 transactions have been added to "Checking"

  Scenario: Identical lines need identical ledger rows
    Given a statement for "Checking" lists "Coffee Shop" for $4.50 out 7 days ago
    And it lists "Coffee Shop" for $4.50 out 7 days ago
    When user A uploads it
    Then line 1 is shown as already recorded
    And line 2 is shown as missing and checked

  Scenario: A near miss is offered as a possible match, not added
    Given "Checking" holds "Electric" for $120.00 out 9 days ago
    And a statement for "Checking" lists "CITY POWER" for $121.40 out 9 days ago
    When user A uploads it
    Then "CITY POWER" is shown as a possible match beside "Electric"
    And "CITY POWER" is unchecked

  Scenario: A pending ledger row can be marked posted instead of duplicated
    Given "Checking" holds a pending "Gas" for $40.00 out 2 days ago
    And a statement for "Checking" lists "SHELL" for $40.00 out 1 day ago
    When user A uploads it
    And applies the review as shown
    Then "Gas" is no longer pending
    And exactly 0 transactions have been added to "Checking"

  Scenario: Re-importing an OFX statement adds nothing
    Given a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    And it lists "HARDWARE BARN" for $19.99 out 2 days ago
    And user A has uploaded it and applied the review as shown
    When user A uploads it again
    Then every line is shown as already recorded

  Scenario: A CSV with separate debit and credit columns keeps its directions
    Given a CSV for "Checking" with "Debit" and "Credit" columns lists "RENT" debited $900.00 5 days ago
    And it lists "REFUND" credited $25.00 4 days ago
    When user A uploads it
    Then "RENT" is shown as money out
    And "REFUND" is shown as money in

  Scenario: The closing balance is compared after applying
    Given a statement for "Checking" lists "COFFEE SHOP" for $4.50 out 7 days ago
    And it lists "GROCERY MART" for $82.17 out 3 days ago
    And it closes with a balance of -$96.67
    When user A uploads it
    And applies the review as shown
    Then user A is told the ledger is $10.00 above the statement's closing balance

  Scenario: An adjustment that may already cover the gap is pointed out
    Given a balance check-in adjusted "Checking" by $82.17 out 3 days ago
    And a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    When user A uploads it
    Then the review warns about the adjustment of $82.17 3 days ago

  Scenario: An unreadable file is refused and nothing is kept
    When user A uploads a file that is not a statement to "Checking"
    Then user A is told the file could not be read
    And exactly 0 transactions have been added to "Checking"

  Scenario: Another user's account cannot be targeted
    Given a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    When user A uploads it to user B's main account
    Then it is not found

  Scenario: The feature is hidden when AI is not configured
    Given no Anthropic API key is set
    When user A opens their transaction history
    Then there is no statement import
    And the statement import page is not found
