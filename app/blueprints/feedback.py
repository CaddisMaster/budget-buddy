"""#64 — file a bug report or feature request from inside the app.

One route. The user types a title and a description, and an issue appears in the
repository's tracker. The network seam lives in app/github.py; this module owns
validation, the label set, and — most importantly — what is allowed into the
issue body.

⚠️ THE PRIVACY DECISION IS LOAD-BEARING (settled 2026-07-30, recorded on #64).

The repository this files into is PUBLIC. The body therefore carries ONLY what
the user typed: no username, no account names, no balances, no transaction rows,
no request context, no app version, no user agent. That is a deliberate choice
against this issue's own original note that "the reporting username belongs in
the body, or every report looks self-filed" — linking a GitHub issue to a Budget
Buddy account name is itself a disclosure, and the account name is often a real
person's name.

Two consequences were accepted knowingly with it, and are NOT bugs:

  1. Every report appears self-filed by the token owner, and an ambiguous report
     cannot be clarified with its reporter. It stands on what was typed or it
     gets closed.
  2. The standing warning in the form is the ONLY control on what a user types
     into a public tracker. The `from-app` label makes such issues easy to find
     and delete if one arrives carrying more than it should.

So: do not "improve triage" by attaching context here. That is the feature being
declined, not an oversight.
"""
from flask import Blueprint, current_app, flash, redirect, request, url_for
from flask_login import login_required

from app import limiter
from app.github import FROM_APP_LABEL, TRIAGE_LABEL, GitHubError, create_issue, feedback_enabled
from app.helpers import GENERIC_ERROR

bp = Blueprint('feedback', __name__)

# The form offers exactly these two, and the posted value is resolved against
# this map rather than passed through — an arbitrary string from a form must
# never become a GitHub label.
KINDS = {'bug': 'bug', 'enhancement': 'enhancement'}

# Generous enough for a real report, bounded so the API is never handed
# something absurd. GitHub's own title cap is 256.
MAX_TITLE = 200
MAX_BODY = 5000


@bp.route('/feedback', methods=['POST'])
# Well below the global 60/min: filing an issue is a deliberate act, and one
# impatient double-click must not open two issues. Deliberately per-hour rather
# than per-minute — a burst of five in an hour is already more than honest use.
@limiter.limit("5 per hour")
@login_required
def submit():
    if not feedback_enabled():
        # Not something the user can act on — the server simply has no token.
        # The form is not rendered in this state, so reaching here means a
        # hand-made request.
        flash('Feedback is not configured')
        return redirect(url_for('auth.profile'))

    kind = KINDS.get(request.form.get('kind', ''), 'bug')
    title = request.form.get('title', '').strip()[:MAX_TITLE]
    description = request.form.get('description', '').strip()[:MAX_BODY]

    if not title:
        flash('Give your report a short title')
        return redirect(url_for('auth.profile'))
    if not description:
        flash('Describe what happened so it can be looked into')
        return redirect(url_for('auth.profile'))

    # The body is the user's own words and a fixed provenance line — nothing
    # else. See the module docstring before adding anything to this string.
    body = (
        f'{description}\n\n---\n'
        'Submitted from inside Budget Buddy.\n'
    )

    try:
        # TRIAGE_LABEL: a report typed by a user has had no code read at all,
        # so it is exactly the kind that benefits from the automated first
        # pass — see the constant's note in app/github.py.
        issue = create_issue(title, body,
                             [KINDS[kind], FROM_APP_LABEL, TRIAGE_LABEL])
    except GitHubError:
        # The API's text can name the repository and the token's scopes, so it
        # goes to the log and never to the browser.
        current_app.logger.exception('feedback issue creation failed')
        flash(GENERIC_ERROR)
        return redirect(url_for('auth.profile'))

    flash(f'Thanks — your report was sent (#{issue["number"]})')
    return redirect(url_for('auth.profile'))
