@issue-459
Feature: Check the balance for every kind of statement
  Only OFX used to carry a closing balance, so a CSV or screenshot import
  ended without saying whether the ledger was now right. A CSV's running
  balance and a screenshot's dated balance are now checked too, a card's
  amount owed is read in whichever sign the statement uses, and a gap that one
  line explains is named.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"

  Scenario: A CSV with a running balance is checked
    Given "Checking" holds "Opening balance" for $1300.00 in 30 days ago
    And a CSV for "Checking" with a running balance lists "GROCERY MART" debited $82.17 3 days ago, leaving $1217.83
    And it lists "COFFEE CO" debited $17.83 yesterday, leaving $1200.00
    When user A uploads it
    And applies the review as shown
    Then user A is told the ledger agrees with the statement

  Scenario: A newest-first CSV takes the newest row's balance
    Given "Checking" holds "Opening balance" for $1300.00 in 30 days ago
    And a CSV for "Checking" with a running balance lists "COFFEE CO" debited $17.83 yesterday, leaving $1200.00
    And it lists "GROCERY MART" debited $82.17 3 days ago, leaving $1217.83
    When user A uploads it
    And applies the review as shown
    Then the balance compared is $1,200.00

  Scenario: A gap that one unticked line explains is named
    Given "Checking" holds "Opening balance" for $1000.00 in 30 days ago
    And a statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    And it lists "TRATTORIA" for $42.80 out 2 days ago
    And it closes with a balance of $875.03
    When user A uploads it
    And unchecks "TRATTORIA" and applies
    Then user A is told the ledger is $42.80 above the statement's closing balance
    And user A is told the "TRATTORIA" line would close the gap

  Scenario: A screenshot's available balance is not used
    Given a screenshot of "Checking" shows "GROCERY MART" for $82.17 out 3 days ago
    And it shows an available balance of $950.00
    When user A uploads the screenshot
    And applies the review as shown
    Then no balance comparison is shown

  Scenario: A card statement agrees whichever sign it uses for the amount owed
    Given user A has a credit card account "Visa"
    And "Visa" holds "Purchases" for $523.10 out 10 days ago
    And "Visa" holds "Coffee" for $4.50 out 3 days ago
    And a statement for "Visa" lists "COFFEE CO" for $4.50 out 3 days ago
    And it closes with a balance of $527.60
    When user A uploads it
    And applies the review as shown
    Then user A is told the ledger agrees with the statement
