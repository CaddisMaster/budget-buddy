@issue-460
Feature: Fill in missing transactions from a PDF statement
  The statement most people have is the PDF their bank emails each month. It
  now goes through the same review as a screenshot: the model reads the lines,
  the app re-checks them and decides what is missing. The page count and any
  password are checked before the model is asked. The PDF is never kept.

  Background:
    Given user A is signed in
    And the AI is available
    And user A has an account "Checking"

  Scenario: Lines read from a PDF are reviewed like any statement
    Given a PDF statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    And "Checking" has nothing for $82.17
    When user A uploads the PDF
    Then "GROCERY MART" is shown as missing and checked

  Scenario: A PDF's closing balance is checked
    Given "Checking" holds "Opening balance" for $1000.00 in 30 days ago
    And a PDF statement for "Checking" lists "GROCERY MART" for $82.17 out 3 days ago
    And it shows a closing balance of $917.83 yesterday
    When user A uploads the PDF
    And applies the review as shown
    Then the ledger is compared with a closing balance of $917.83 yesterday

  Scenario: A PDF with no readable transactions is refused
    Given a password-protected PDF statement for "Checking"
    When user A uploads the PDF
    Then user A is told the file could not be read
    And exactly 0 transactions have been added to "Checking"

  Scenario: A file named .pdf that is not a PDF is not treated as one
    Given a CSV for "Checking" with "Debit" and "Credit" columns lists "GROCERY MART" debited $82.17 3 days ago
    When user A uploads it as "statement.pdf"
    Then it is read as a CSV
    And "GROCERY MART" is shown as missing and checked
