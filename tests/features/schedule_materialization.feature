Feature: A scheduled transaction posts itself when it falls due
  A schedule is a recurring template, not a ledger row. When one falls due,
  a real transaction is posted for each missed occurrence and the schedule's
  next due date moves past today, so running again posts nothing new.

  Scenario: A due schedule posts once and moves on
    Given user A has a monthly income schedule due yesterday
    When user A's due schedules run
    Then 1 transaction has been posted from user A's schedules
    And the schedule's next due date is after today
    When user A's due schedules run again
    Then 1 transaction has been posted from user A's schedules

  Scenario: A schedule that fell behind catches up on every missed occurrence
    Given user A has a weekly expense schedule due 3 weeks ago
    When user A's due schedules run
    Then 4 transactions have been posted from user A's schedules

  Scenario: A schedule that is not yet due posts nothing
    Given user A has a weekly expense schedule due in 7 days
    When user A's due schedules run
    Then 0 transactions have been posted from user A's schedules

  Scenario: A paused schedule posts nothing
    Given user A has a paused monthly expense schedule due yesterday
    When user A's due schedules run
    Then 0 transactions have been posted from user A's schedules

  Scenario: One user's run never posts another user's schedule
    Given user A has a monthly expense schedule due yesterday
    When user B's due schedules run
    Then 0 transactions have been posted from user A's schedules
    When user A's due schedules run
    Then 1 transaction has been posted from user A's schedules

  Scenario: Page loads racing each other post the occurrence exactly once
    Given user A has a monthly expense schedule due yesterday
    When 4 page loads run user A's due schedules at the same instant
    Then 1 transaction has been posted from user A's schedules
    And no run failed
    And the schedule's next due date is after today

  Scenario: The daily job racing a page load posts the occurrence exactly once
    Given user A has a monthly expense schedule due yesterday
    When 2 page loads and 2 daily jobs run at the same instant
    Then 1 transaction has been posted from user A's schedules
    And no run failed
