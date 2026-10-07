@issue-455
Feature: Clean descriptions for imported lines
  An imported line used to keep the bank's text, so the ledger filled with
  "SQ *BLUE BOTTLE 0423 SAN FRANCISCO CA" beside my own "Blue Bottle". A line
  now takes the name I already use for that merchant, or a cleaned version of
  the bank's text, and I can change it before applying.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"

  Scenario: My own name for a merchant is reused
    Given "Checking" holds "Blue Bottle" for $4.50 out 4 days ago
    And a statement for "Checking" lists "SQ *BLUE BOTTLE 0423" for $4.50 out 3 days ago
    And it lists "SQ *BLUE BOTTLE 0611 SAN FRANCISCO CA" for $5.25 out yesterday
    When user A uploads it
    And applies the review as shown
    Then the $5.25 transaction in "Checking" is described "Blue Bottle"

  Scenario: An unseen merchant gets a cleaned name
    Given a statement for "Checking" lists "TST* JOES PIZZA 00123 BROOKLYN NY" for $18.00 out 2 days ago
    When user A uploads it
    Then "TST* JOES PIZZA 00123 BROOKLYN NY" is proposed as "Joes Pizza"
    And the bank's text is shown beside "Joes Pizza"

  Scenario: I can change the proposed description
    Given an upload proposes "Joes Pizza" for "TST* JOES PIZZA 00123 BROOKLYN NY"
    When user A changes "Joes Pizza" to "Pizza night" and applies
    Then the $18.00 transaction in "Checking" is described "Pizza night"

  Scenario: Re-importing still recognises a line whose name was cleaned
    Given user A imported a statement and its lines were renamed
    When user A uploads it again
    Then every line is shown as already recorded
