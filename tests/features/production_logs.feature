@issue-404
Feature: Production says what it is doing
  The app logs to stderr, which is what `docker compose logs` shows on the
  Droplet. Every scenario here reads the app's REAL log handler at the level
  production runs at. Nothing lowers the level for the test, which is how an
  audit line went missing for months while the test that asserted it passed.

  Scenario: An info-level event, such as a database export, reaches the log
    Given user A is an admin and signed in
    When user A exports the database
    Then the log says "backup: database exported by user"
    And every line the export logged carries its request ID

  Scenario: Every log line from one request carries the same request ID
    Given user A is signed in
    And the dashboard logs two lines of its own while it renders
    When user A opens the dashboard twice
    Then each visit's lines all carry that visit's request ID
    And the two visits have different request IDs

  Scenario: An unhandled error is logged with its traceback and request ID, and never with form data
    Given user A is signed in
    And saving a transaction fails with an unexpected error
    When user A adds a transaction with the memo "memo-that-must-not-be-logged"
    Then the response is a server error
    And the log carries the traceback and the request's ID
    And the log never mentions "memo-that-must-not-be-logged"
