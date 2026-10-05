@issue-446
Feature: Record statement transfers as transfers
  A statement line like "PAYMENT - THANK YOU" on a card is money moving between
  two of your own accounts. Imported as an ordinary line it would count as
  income on the card, so the review lets you record it as a transfer instead:
  both legs, linked, and counted as neither spending nor income. When the other
  account already holds that money as an ordinary entry, that entry becomes the
  second leg rather than being duplicated.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"
    And user A has an account "Visa"

  Scenario: A card payment becomes a transfer pair
    Given a statement for "Visa" lists "PAYMENT - THANK YOU" for $500.00 in 2 days ago
    And "Checking" has nothing for $500.00
    When user A records "PAYMENT - THANK YOU" as a transfer from "Checking"
    Then a transfer of $500.00 from "Checking" to "Visa" 2 days ago has been added
    And neither leg counts as spending or income

  Scenario: An existing plain row becomes the other leg
    Given "Checking" holds "Visa payment" for $500.00 out 3 days ago
    And a statement for "Visa" lists "PAYMENT - THANK YOU" for $500.00 in 2 days ago
    When user A records "PAYMENT - THANK YOU" as a transfer from "Checking"
    Then "Visa payment" has become the "Checking" leg of that transfer
    And "Checking" holds only one $500.00 row

  Scenario: A transfer already recorded is recognised
    Given a transfer of $500.00 from "Checking" to "Visa" 2 days ago exists
    And a statement for "Visa" lists "PAYMENT - THANK YOU" for $500.00 in 2 days ago
    When user A uploads it
    Then "PAYMENT - THANK YOU" is shown as already recorded

  Scenario: The other account must be mine
    Given a statement for "Visa" lists "PAYMENT - THANK YOU" for $500.00 in 2 days ago
    When user A records "PAYMENT - THANK YOU" as a transfer from user B's main account
    Then it is not found
    And exactly 0 transactions have been added to "Visa"
