@issue-483
Feature: The weekly digest reaches each user once a week
  The digest is a Sunday email. However many runs of the job a week holds, and
  for whatever reason, each opted-in user is sent one. A run claims the user
  before it pays for the model or sends anything, so a second run finds the
  week already claimed and sends nothing.

  Scenario: Two digest runs in the same week send one email
    Given user A is opted in to the weekly digest
    When the weekly digest job runs twice at the same time
    Then user A has been sent 1 digest
    And no run failed

  Scenario: Opting in again does not duplicate the digest
    Given user A is opted in to the weekly digest
    And user A is signed in
    When user A saves the Profile form with the digest ticked again
    And the weekly digest job runs
    Then user A has been sent 1 digest
