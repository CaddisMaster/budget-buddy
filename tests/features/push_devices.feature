@issue-437
Feature: A device that stops getting notifications is visible, not silent
  On 2026-10-05 a release announcement reported three devices while Sean's
  phone got nothing: its subscription had been wiped on the phone, and Apple
  kept accepting messages for the old one. Nothing could tell. Each device now
  carries a name and a last-seen time, Profile lists them, and Home carries a
  prompt that the browser shows when this device has fallen off.

  Scenario: Subscribing records which device it is
    Given push is configured
    And user A is signed in
    When user A subscribes a device from an iPhone
    Then user A's Profile lists a device called "iPhone · Safari"

  Scenario: Opening Home marks a known device as seen
    Given push is configured
    And user A has a device that was never seen since tracking began
    And user A is signed in
    When that device reports itself seen
    Then the server answers that it knows the device
    And the device's last seen time is today

  Scenario: A device the server has forgotten is told so
    Given push is configured
    And user A is signed in
    When a device user A never registered reports itself seen
    Then the server answers that it does not know the device
    And user A has no registered devices

  Scenario: A device cannot be marked seen through another account
    Given push is configured
    And user B has a device that was never seen since tracking began
    And user A is signed in
    When user B's device reports itself seen through user A's session
    Then the server answers that it does not know the device
    And user B's device was never seen since tracking began

  Scenario: Profile lists every registered device
    Given push is configured
    And user A has a device that was never seen since tracking began
    And user A has a device called "Mac · Safari" seen today
    And user A is signed in
    When user A opens Profile
    Then it lists "Mac · Safari", last seen today
    And it lists "Unknown device", not seen since tracking began

  Scenario: A device can be removed from the list
    Given push is configured
    And user A has a device called "Mac · Safari" seen today
    And user A is signed in
    When user A removes that device
    Then user A has no registered devices

  Scenario: Another user's device cannot be removed
    Given push is configured
    And user B has a device called "Mac · Safari" seen today
    And user A is signed in
    When user A tries to remove user B's device
    Then the response is 404
    And user B still has 1 registered device

  Scenario: Home carries the prompt only for an account with push on somewhere
    Given push is configured
    And user A has a device called "Mac · Safari" seen today
    And user A is signed in
    When user A opens Home
    Then the page carries the device prompt, hidden until the browser decides

  Scenario: Home never prompts an account that never turned push on
    Given push is configured
    And user A is signed in
    When user A opens Home
    Then the page carries no device prompt
