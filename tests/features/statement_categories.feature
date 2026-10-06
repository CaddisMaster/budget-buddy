@issue-454
Feature: Suggest categories for imported lines from my own history
  Before the AI is asked to categorise a statement line, the import checks how
  I filed the same merchant before. The bank prints one shop many ways
  (store numbers, card-processor prefixes), so lines are compared by merchant,
  not by exact text. Only merchants my history doesn't know go to the AI.

  The AI is stubbed in every scenario, and suggests a different category from
  the ones history would, so a history suggestion can't be mistaken for one.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"
    And user A has an expense category "Groceries"
    And user A has an expense category "Dining"

  Scenario: A recorded match teaches the category for the rest of the statement
    Given "Checking" holds "Coffee" in "Dining" for $4.50 out 7 days ago
    And a statement for "Checking" lists "SQ *BLUE BOTTLE 0423" for $4.50 out 6 days ago
    And it lists "SQ *BLUE BOTTLE 0611" for $5.25 out 2 days ago
    And it lists "HARDWARE BARN" for $19.99 out 1 day ago
    When user A uploads it
    Then "SQ *BLUE BOTTLE 0611" is suggested "Dining", from user A's history
    And "SQ *BLUE BOTTLE 0611" was not sent to the AI
    And "HARDWARE BARN" was sent to the AI

  Scenario: An earlier import teaches the category
    Given "Checking" holds an imported "KROGER #123" in "Groceries" for $55.00 out 4 weeks ago
    And a statement for "Checking" lists "KROGER #456" for $61.10 out 3 days ago
    When user A uploads it
    Then "KROGER #456" is suggested "Groceries", from user A's history

  Scenario: Known merchants are not sent to the model
    Given "Checking" holds an imported "KROGER #123" in "Groceries" for $55.00 out 4 weeks ago
    And "Checking" holds "Coffee" in "Dining" for $4.50 out 7 days ago
    And a statement for "Checking" lists "SQ *BLUE BOTTLE 0423" for $4.50 out 6 days ago
    And it lists "KROGER #456" for $61.10 out 3 days ago
    And it lists "SQ *BLUE BOTTLE 0611" for $5.25 out 2 days ago
    When user A uploads it
    Then "KROGER #456" is suggested "Groceries", from user A's history
    And "SQ *BLUE BOTTLE 0611" is suggested "Dining", from user A's history
    And no categorisation call is made

  Scenario: History of the wrong kind is not used
    Given "Checking" holds an imported "AMAZON MKTP" in "Groceries" for $20.00 out 3 weeks ago
    And a statement for "Checking" lists "AMAZON MKTP" for $20.00 in 2 days ago
    When user A uploads it
    Then "AMAZON MKTP" is not marked as from user A's history
    And "AMAZON MKTP" was sent to the AI
