Feature: A schedule stops at its end date, and never starts in the past
  The end date is the last day a schedule runs (#32). Catching up after a long
  absence must stop there rather than running to today, and a new schedule is
  created from today forward, so setting one up never back-fills history.

  Scenario: Catching up stops at the end date
    Given user A has a weekly expense schedule due 3 weeks ago, ending 2 weeks ago
    When user A's due schedules run
    Then 2 transactions have been posted from user A's schedules

  Scenario: A schedule that finished while nobody looked posts nothing
    Given user A has a weekly expense schedule due 8 weeks ago, ending 9 weeks ago
    When user A's due schedules run
    Then 0 transactions have been posted from user A's schedules

  Scenario: The end date is the last day it runs, not the first day it doesn't
    Given user A has a weekly expense schedule due today, ending today
    When user A's due schedules run
    Then 1 transaction has been posted from user A's schedules

  Scenario: A new schedule can start from today forward
    Given user A is signed in
    When user A creates a monthly expense schedule starting in 3 days
    Then user A has 1 schedule
    And 0 transactions have been posted from user A's schedules

  Scenario: A new schedule cannot start in the past
    Given user A is signed in
    When user A creates a monthly expense schedule starting 2 days ago
    Then it is refused with "Next date cannot be in the past"
    And user A has 0 schedules
