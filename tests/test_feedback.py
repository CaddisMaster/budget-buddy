"""#64 tests — in-app bug reports and feature suggestions.

No real GitHub calls: the single network seam app.github._call_github is
monkeypatched with a recording fake, exactly as test_digest.py does for
app.mailer._call_resend. CI has no token, so feedback_enabled() is False there
by default and every test that needs the feature on sets the env var itself.

The most important test in this file is test_body_carries_only_what_the_user_typed.
The repository issues are filed into is PUBLIC, and the settled design (#64,
2026-07-30) is that the body contains ONLY the user's own words — no username, no
account names, no balances. That decision is invisible in the code's shape: a
future change adding "helpful" triage context would look like an improvement and
break nothing else. That test is the net.
"""
import logging

import pytest

import app.github as github
from app.github import GitHubError, create_issue, feedback_enabled
from tests.conftest import USER_A


class _GitHubSeam:
    """Stand-in for _call_github that records payloads and can fail on demand."""

    def __init__(self, fail=False, response=None):
        self.fail = fail
        self.calls = []
        self.response = response if response is not None else {
            'number': 4242, 'html_url': 'https://github.com/x/y/issues/4242'}

    def __call__(self, token, repo, payload):
        self.calls.append({'token': token, 'repo': repo, 'payload': payload})
        if self.fail:
            # The shape of a real operator error — its text names the repo and
            # must never reach the browser.
            raise GitHubError('GitHub API returned 403 for CaddisMaster/budget-buddy')
        return self.response


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('FEEDBACK_GITHUB_TOKEN', 'test-token')


@pytest.fixture
def seam(monkeypatch):
    fake = _GitHubSeam()
    monkeypatch.setattr(github, '_call_github', fake)
    return fake


# --- the gate ---------------------------------------------------------------

def test_feedback_disabled_without_a_token(monkeypatch):
    monkeypatch.delenv('FEEDBACK_GITHUB_TOKEN', raising=False)
    assert feedback_enabled() is False


def test_feedback_enabled_with_a_token(enabled):
    assert feedback_enabled() is True


def test_profile_offers_no_feedback_ui_without_a_token(client_a, monkeypatch):
    """Acceptance criterion 2: with no token the app behaves exactly as before.
    An empty page here is the CORRECT result, not a broken setup."""
    monkeypatch.delenv('FEEDBACK_GITHUB_TOKEN', raising=False)
    body = client_a.get('/profile').get_data(as_text=True)
    assert 'Report a problem' not in body
    assert '/feedback' not in body


def test_profile_offers_the_form_with_a_token(client_a, enabled):
    body = client_a.get('/profile').get_data(as_text=True)
    assert 'Report a problem' in body
    assert 'action="/feedback"' in body


def test_the_form_warns_that_reports_are_public(client_a, enabled):
    """The warning is the ONLY control on what a user publishes. If this test
    fails because the copy moved, re-point it — do not delete it."""
    body = client_a.get('/profile').get_data(as_text=True)
    assert 'published publicly on GitHub' in body


def test_route_without_a_token_creates_nothing(client_a, seam, monkeypatch):
    monkeypatch.delenv('FEEDBACK_GITHUB_TOKEN', raising=False)
    response = client_a.post('/feedback', data={
        'kind': 'bug', 'title': 'x', 'description': 'y'}, follow_redirects=True)
    assert response.status_code == 200
    assert seam.calls == []


# --- the happy path ---------------------------------------------------------

def test_bug_report_creates_an_issue(client_a, enabled, seam):
    response = client_a.post('/feedback', data={
        'kind': 'bug',
        'title': 'Balance is wrong on the history page',
        'description': 'It shows a dash where a number should be.',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert len(seam.calls) == 1
    payload = seam.calls[0]['payload']
    assert payload['title'] == 'Balance is wrong on the history page'
    assert 'It shows a dash where a number should be.' in payload['body']
    assert set(payload['labels']) == {'bug', 'from-app', 'triage'}
    assert '4242' in response.get_data(as_text=True)


def test_suggestion_carries_the_enhancement_label(client_a, enabled, seam):
    client_a.post('/feedback', data={
        'kind': 'enhancement', 'title': 'Add tags', 'description': 'Please.'},
        follow_redirects=True)
    assert set(seam.calls[0]['payload']['labels']) == {'enhancement', 'from-app', 'triage'}


def test_every_issue_carries_the_from_app_label(client_a, enabled, seam):
    """`from-app` is what makes in-app reports triageable as a group, and
    findable-and-deletable if one arrives carrying more than it should."""
    for kind in ('bug', 'enhancement'):
        client_a.post('/feedback', data={
            'kind': kind, 'title': 't', 'description': 'd'}, follow_redirects=True)
    assert all('from-app' in c['payload']['labels'] for c in seam.calls)


def test_an_arbitrary_kind_never_becomes_a_label(client_a, enabled, seam):
    """The posted kind is resolved against a fixed allowlist, never passed
    through — a form value must not be able to apply `security` or invent one."""
    client_a.post('/feedback', data={
        'kind': 'security', 'title': 't', 'description': 'd'}, follow_redirects=True)
    assert set(seam.calls[0]['payload']['labels']) == {'bug', 'from-app', 'triage'}


# --- the privacy decision (#64, settled 2026-07-30) -------------------------

def test_body_carries_only_what_the_user_typed(client_a, users, enabled, seam):
    """THE regression test for the settled privacy design.

    The tracker is public. The body must contain the user's words and nothing
    else — no username, no account names, no balances. A future change that
    attaches context "to make triage easier" is the thing this catches.
    """
    client_a.post('/feedback', data={
        'kind': 'bug',
        'title': 'Something looks off',
        'description': 'The number in the corner seems too high.',
    }, follow_redirects=True)

    payload = seam.calls[0]['payload']
    haystack = f"{payload['title']}\n{payload['body']}"

    assert 'The number in the corner seems too high.' in haystack
    # The reporting user is deliberately NOT identified.
    assert USER_A not in haystack
    assert users['a']['username'] not in haystack
    # Nor is any of their data. The seeded account/category/transaction names
    # and the transaction amount are the canaries.
    assert 'acct-A' not in haystack
    assert 'cat-A' not in haystack
    assert 'txn-A' not in haystack
    assert '42.50' not in haystack
    assert str(users['a']['id']) not in payload['body']


def test_no_request_context_is_attached(client_a, enabled, seam):
    """No user agent, no IP, no referrer, no session detail — the body is the
    description plus one fixed provenance line."""
    client_a.post('/feedback',
                  data={'kind': 'bug', 'title': 't', 'description': 'just this'},
                  headers={'User-Agent': 'SecretBrowser/9.9'},
                  follow_redirects=True)
    body = seam.calls[0]['payload']['body']
    assert 'SecretBrowser' not in body
    assert body.strip().startswith('just this')


# --- validation -------------------------------------------------------------

def test_a_report_needs_a_title(client_a, enabled, seam):
    client_a.post('/feedback', data={
        'kind': 'bug', 'title': '   ', 'description': 'something'},
        follow_redirects=True)
    assert seam.calls == []


def test_a_report_needs_a_description(client_a, enabled, seam):
    client_a.post('/feedback', data={
        'kind': 'bug', 'title': 'a title', 'description': ''},
        follow_redirects=True)
    assert seam.calls == []


def test_long_input_is_capped_before_the_api_sees_it(client_a, enabled, seam):
    client_a.post('/feedback', data={
        'kind': 'bug', 'title': 'T' * 500, 'description': 'D' * 9000},
        follow_redirects=True)
    payload = seam.calls[0]['payload']
    assert len(payload['title']) == 200
    assert payload['body'].count('D') == 5000


# --- failure handling -------------------------------------------------------

# The whole of the seam's error text. "the raw API text must not reach the
# browser" means THIS string, and asserting it whole is the form that cannot
# false-positive on a page that innocently contains one of its fragments.
_SEAM_ERROR = 'GitHub API returned 403 for CaddisMaster/budget-buddy'

_FLASH_OPEN = '<div class="flash">'


def _flash_region(body):
    """The one place base.html renders a flashed message into.

    Returns '' when the page rendered no flash at all — which is itself a
    finding, and one the caller should report rather than silently pass.
    """
    start = body.find(_FLASH_OPEN)
    if start == -1:
        return ''
    return body[start:body.find('</div>', start) + len('</div>')]


def _diagnose(response, tmp_path, label):
    """Everything needed to state the cause, written before the assert fires.

    ⚠️ This exists because #361 failed once under `-n 10`, showed only the
    `short test summary info` line, and then would not reproduce in eight
    consecutive runs — so there was nothing to reason from and every hypothesis
    was a guess. The next occurrence explains itself instead of costing another
    hunt. Do not simplify this back to a bare assert.
    """
    body = response.get_data(as_text=True)
    dump = tmp_path / 'feedback-failure.html'
    dump.write_text(body, encoding='utf-8')

    hits = []
    index = body.find('403')
    while index != -1:
        hits.append(f'  @{index}: ...{body[max(0, index - 120):index + 123]!r}...')
        index = body.find('403', index + 1)

    return '\n'.join([
        f'{label}',
        f'  status={response.status_code}',
        f'  flash={_flash_region(body)!r}',
        f'  raw seam text present={_SEAM_ERROR in body}',
        f'  body written to {dump}',
        f'  {len(hits)} occurrence(s) of "403":',
        *hits,
    ])


def test_github_failure_shows_a_friendly_message(client_a, enabled, monkeypatch,
                                                 tmp_path, caplog):
    """Acceptance criterion: a friendly failure, never an exception — and the
    raw API text reaches the log, never the browser.

    ⚠️ The leak check is scoped to the FLASH REGION, not to the whole document
    (#361). `'403' not in body` was an unanchored substring search over an
    entire rendered page, so any future page furniture that happened to contain
    those three digits would fail a test about a GitHub error — and the page is
    where a leak would have to appear as a flashed message anyway. The whole
    raw string is still asserted against the entire body, which is the form that
    actually states "no API text anywhere" and cannot false-positive.
    """
    monkeypatch.setattr(github, '_call_github', _GitHubSeam(fail=True))
    with caplog.at_level(logging.ERROR):
        response = client_a.post('/feedback', data={
            'kind': 'bug', 'title': 't', 'description': 'd'}, follow_redirects=True)

    assert response.status_code == 200, _diagnose(response, tmp_path, 'not a 200')
    body = response.get_data(as_text=True)
    flash = _flash_region(body)
    assert 'Something went wrong' in flash, _diagnose(response, tmp_path, 'no friendly message')

    # The seam's error text names the repo and the status code. Neither leaks.
    assert '403' not in flash, _diagnose(response, tmp_path, 'status code leaked')
    assert 'CaddisMaster/budget-buddy' not in body, _diagnose(response, tmp_path, 'repo leaked')
    assert _SEAM_ERROR not in body, _diagnose(response, tmp_path, 'raw API text leaked')

    # The other half of the docstring, which nothing asserted until #361: the
    # text has to go SOMEWHERE, and an operator error that reaches neither the
    # browser nor the log is a failure nobody can diagnose.
    logged = '\n'.join(record.getMessage() + str(record.exc_info or '')
                        for record in caplog.records)
    assert 'feedback issue creation failed' in logged, (
        'the failure was hidden from the browser and never written to the log')


def test_a_response_without_an_issue_number_is_a_failure(monkeypatch, enabled):
    """A 200 carrying an unexpected body must not read as success."""
    monkeypatch.setattr(github, '_call_github', _GitHubSeam(response={'ok': True}))
    with pytest.raises(GitHubError):
        create_issue('t', 'b', ['bug'])


# --- the seam itself --------------------------------------------------------

def test_create_issue_without_a_token_raises(monkeypatch):
    monkeypatch.delenv('FEEDBACK_GITHUB_TOKEN', raising=False)
    with pytest.raises(GitHubError):
        create_issue('t', 'b', ['bug'])


def test_create_issue_without_a_title_raises(enabled, seam):
    with pytest.raises(GitHubError):
        create_issue('   ', 'b', ['bug'])
    assert seam.calls == []


def test_create_issue_returns_number_and_url(enabled, seam):
    result = create_issue('t', 'b', ['bug'])
    assert result == {'number': 4242,
                      'url': 'https://github.com/x/y/issues/4242'}


def test_the_token_is_passed_to_the_seam_not_the_payload(enabled, seam):
    """The token authenticates the call; it must never end up in issue text."""
    create_issue('t', 'b', ['bug'])
    call = seam.calls[0]
    assert call['token'] == 'test-token'
    assert 'test-token' not in str(call['payload'])


# --- auth, isolation, rate limiting ----------------------------------------

def test_anonymous_cannot_file_an_issue(anon_client, enabled, seam):
    response = anon_client.post('/feedback', data={
        'kind': 'bug', 'title': 't', 'description': 'd'})
    assert response.status_code == 302
    assert '/login' in response.headers['Location']
    assert seam.calls == []


def test_feedback_is_rate_limited():
    """One impatient double-click must not open two issues. The limiter is
    disabled under test (see conftest), so this asserts the registration rather
    than the behaviour — the same approach as test_admin_backup.py."""
    from app import limiter

    marked = {name.rsplit('.', 1)[-1] for name in limiter._marked_for_limiting}
    assert 'submit' in marked, 'no rate limit registered on /feedback'


def test_every_issue_opts_in_to_the_automated_review(client_a, enabled, seam):
    """`triage` is what runs .github/workflows/claude-triage.yml on an issue.

    ⚠️ It is applied HERE and by the two ISSUE_TEMPLATE files, never by the
    workflow's own default — triage was opt-OUT until 2026-08-18, and the
    opt-out was forgotten on the very issue that proposed fixing it, spending
    ~$0.50 of subscription budget to restate analysis that had just been done in
    the session that filed it.

    A report typed by a user is the opposite case: no code has been read at all,
    so it is exactly the kind that benefits from the automated first pass. If
    this label ever stops being sent, in-app reports silently stop being
    reviewed — which looks like nothing at all, which is why it is asserted."""
    for kind in ('bug', 'enhancement'):
        client_a.post('/feedback', data={
            'kind': kind, 'title': 't', 'description': 'd'}, follow_redirects=True)
    assert all('triage' in c['payload']['labels'] for c in seam.calls)
