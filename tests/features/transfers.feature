@issue-389
Feature: Moving money between your own accounts
  A transfer is money moving between two of your own accounts. It is recorded
  as a linked pair of ledger rows: one out of the source, one into the
  destination. The pair is created, changed and deleted together. It moves
  balances, but it is neither income nor spending, so it never shows up in the
  month's figures.

  Every user starts with one "main account" holding a single $42.50 expense.

  Scenario: A transfer moves money out of one account and into the other
    Given user A is signed in
    And user A has a second account "Savings"
    When user A transfers $150.00 from their main account to "Savings" dated today
    Then user A's main account has gone down by $150.00
    And user A's "Savings" has gone up by $150.00

  Scenario: A transfer to the same account is refused
    Given user A is signed in
    When user A transfers $50.00 from their main account to their main account dated today
    Then the transfer is refused with "From and To accounts must be different"
    And no transfer has been recorded for user A

  Scenario: A transfer with an impossible date is refused with a clear reason
    Typing a date wrong is the user's mistake, not the server's, so the reason
    names the date instead of the generic "something went wrong".

    Given user A is signed in
    And user A has a second account "Savings"
    When user A transfers $5.00 from their main account to "Savings" dated "not-a-date"
    Then the transfer is refused with "Date must be a valid date"
    And no generic error is shown
    And no transfer has been recorded for user A

  Scenario: A transfer is not counted as income or spending
    Given user A is signed in
    And user A has a second account "Savings"
    And user A has already transferred $500.00 from their main account to "Savings"
    When user A opens the dashboard
    Then the dashboard shows $42.50 spent and $0.00 earned
    And user A's "Savings" has gone up by $500.00

  Scenario: Editing a transfer changes both sides
    Given user A is signed in
    And user A has a second account "Savings"
    And user A has already transferred $100.00 from their main account to "Savings"
    When user A changes that transfer to $250.00
    Then that transfer has 2 sides
    And user A's main account has gone down by $250.00
    And user A's "Savings" has gone up by $250.00

  Scenario: Deleting a transfer removes both sides
    Given user A is signed in
    And user A has a second account "Savings"
    And user A has already transferred $75.00 from their main account to "Savings"
    When user A deletes that transfer
    Then that transfer has 0 sides
    And user A's main account is unchanged
    And user A's "Savings" is unchanged

  Scenario: Nobody can delete someone else's transfer
    Given user B has a second account "Emergency"
    And user B has already transferred $60.00 from their main account to "Emergency"
    And user A is signed in
    When user A deletes that transfer
    Then it is not found
    And that transfer has 2 sides

  Scenario: Nobody can change someone else's transfer
    Given user B has a second account "Emergency"
    And user B has already transferred $60.00 from their main account to "Emergency"
    And user A is signed in
    When user A changes that transfer to $9999.00
    Then it is not found
    And user B's "Emergency" has gone up by $60.00

  Scenario: A transfer that does not exist is not found
    Given user A is signed in
    When user A opens a transfer that does not exist
    Then it is not found

  Scenario: The history shows a transfer as a transfer
    Given user A is signed in
    And user A has a second account "Savings"
    And user A has already transferred $80.00 from their main account to "Savings"
    When user A opens their transaction history
    Then the history shows that transfer with a Transfer badge and a link to it
