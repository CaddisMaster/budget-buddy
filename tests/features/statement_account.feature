@issue-461
Feature: Work out which account a statement belongs to
  A statement names its account: an OFX carries its number, a PDF or a
  screenshot prints "ending in 1234". The upload no longer needs the account
  chosen first. An account learns its last four digits when an import carrying
  them is applied to it, and the last apply wins.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"
    And user A has an account "Discover"

  Scenario: A known account is detected
    Given "Discover" is known to end in 1234
    And an OFX statement for an account ending in 1234 lists "COFFEE CO" for $4.50 out 3 days ago
    When user A uploads it without choosing an account
    Then the review is for "Discover"

  Scenario: The first import teaches the account
    Given no account is known to end in 1234
    And an OFX statement for an account ending in 1234 lists "COFFEE CO" for $4.50 out 3 days ago
    When user A uploads it for "Discover"
    And applies the review as shown
    Then "Discover" is known to end in 1234

  Scenario: An unknown account asks me
    Given no account is known to end in 9999
    And an OFX statement for an account ending in 9999 lists "COFFEE CO" for $4.50 out 3 days ago
    When user A uploads it without choosing an account
    Then user A is asked which account it is for

  Scenario: A CSV with no account number asks me
    Given a CSV for "Checking" with "Debit" and "Credit" columns lists "GROCERY MART" debited $82.17 3 days ago
    When user A uploads it without choosing an account
    Then user A is asked which account it is for
