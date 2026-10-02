@issue-403
Feature: The browser refuses scripts the app did not ship
  Jinja autoescapes what it renders, and the policy is the second line behind
  it. If some HTML ever gets past the escaping, the browser still runs only the
  app's own files and the inline blocks carrying this response's nonce. Every
  page reachable by a plain GET is swept, derived from the app's routes, so a
  new page cannot quietly be left out.

  Scenario: Every page sends a script-src policy that allows no inline script without a nonce
    Given user A is an admin and signed in
    When user A opens every page the app serves
    Then every response sends a script-src of the app's own files and that response's nonce
    And every inline script on those pages carries that response's nonce
    And no two responses share a nonce
