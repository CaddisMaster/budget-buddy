"""#64 — outbound GitHub issue creation (the in-app feedback send path).

The fourth outbound seam, shaped exactly like app/mailer.py and app/pusher.py: a
single isolated network call (`_call_github`) that tests monkeypatch, a
`feedback_enabled()` gate twinned with mail_enabled() / push_enabled() /
ai_enabled(), and a GitHubError that callers catch so an API failure never
breaks a request. The module NEVER touches the DB and never sees a user id —
the caller owns everything user-scoped.

⚠️ Two naming choices here are deliberate and worth not "tidying":

`FEEDBACK_GITHUB_TOKEN`, not `GITHUB_TOKEN`. The latter is a magic name in
GitHub Actions, where it is injected with a completely different meaning and
scope. Naming the app's own variable the same invites a confusing collision the
first time app code runs inside a workflow.

`urllib.request`, not `requests`. Creating an issue is one POST, and requests is
NOT declared in requirements.txt — it is only present transitively via
pywebpush. The pydantic note in that file records the standing rule: a direct
import gets declared. Using the stdlib avoids adding a dependency at all, which
also means one less wheel to verify on a future runtime bump.
"""
import json
import os
import urllib.error
import urllib.request

# The repository issues are filed into. Overridable so a fork or a test
# environment never has to edit code to point somewhere else.
FEEDBACK_REPO = os.getenv('FEEDBACK_REPO', 'CaddisMaster/budget-buddy')

# Applied to every issue this module creates, on top of the kind label. In-app
# reports do not follow the Gherkin issue templates, so they need to be
# identifiable as a group rather than looking like a contributor who ignored
# the template.
FROM_APP_LABEL = 'from-app'

# A slow GitHub must not hold a worker open. The route is user-facing, so this
# is deliberately short — a failure is reported to the user, not retried.
TIMEOUT_SECONDS = 10


class GitHubError(Exception):
    """Raised on any failure (no token, API error, network, malformed response).
    Callers catch it and show GENERIC_ERROR, so the API's own text — which can
    name the repository and the token's scopes — never reaches the browser."""


def feedback_enabled():
    """True when in-app feedback is configured — i.e. a token is present.
    Routes and templates gate the form on this so the feature stays invisible
    until the token is set (the mail_enabled() / push_enabled() pattern). The
    app must run normally without it, which is how it behaves locally and in
    CI."""
    return bool(os.getenv('FEEDBACK_GITHUB_TOKEN'))


def create_issue(title, body, labels):
    """File one issue. Returns {'number': int, 'url': str}.

    Raises GitHubError on any failure so the caller can show a friendly message.
    `body` is passed through verbatim — assembling it, and deciding what may go
    in it, belongs to the caller (see blueprints/feedback.py, which posts only
    what the user typed).
    """
    token = os.getenv('FEEDBACK_GITHUB_TOKEN')
    if not token:
        raise GitHubError('FEEDBACK_GITHUB_TOKEN is not set')
    title = (title or '').strip()
    if not title:
        raise GitHubError('An issue needs a title')

    payload = {'title': title, 'body': body or '', 'labels': list(labels or [])}
    result = _call_github(token, FEEDBACK_REPO, payload)

    # GitHub returns the created issue object. Anything without a number is a
    # failure we surface rather than silently "succeeding".
    number = (result or {}).get('number') if isinstance(result, dict) else None
    if not number:
        raise GitHubError('GitHub returned no issue number')
    return {'number': number, 'url': result.get('html_url', '')}


def _call_github(token, repo, payload):
    """The single network call to the GitHub API — isolated so tests stub it
    without hitting the network (CI has no token). Returns the parsed response;
    wraps any network, HTTP or decode error in GitHubError."""
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/issues',
        data=json.dumps(payload).encode('utf-8'),
        method='POST',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
            'User-Agent': 'budget-buddy',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        # 401/403 = the token is wrong or unscoped, 404 = the repo is not
        # visible to it, 422 = a label does not exist. All are operator errors
        # that must reach the log and never the browser.
        raise GitHubError(f'GitHub API returned {e.code}') from e
    except Exception as e:  # network, timeout, malformed JSON, anything else
        raise GitHubError(str(e)) from e
