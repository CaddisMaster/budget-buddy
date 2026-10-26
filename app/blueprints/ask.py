"""The "Ask your finances" box (tool-use).

A plain-English question box on Home, answered from the user's REAL data. The
model orchestrates; the app computes. This module is only the /ask route: it
binds current_user.id into `dispatch` and hands the tools to ai.answer_question.

⚠️ The SECURITY BOUNDARY (the read-only, user-scoped tools and the `dispatch`
that validates every argument) is services/ask_tools.py since #489, because
the weekly money agent drives the same tools with no request. Gated on
ai_enabled() and degrades gracefully (an error fragment + toast, never a broken
page) when the key/model is unavailable.
"""
from datetime import date

from flask import Blueprint, make_response, render_template, request
from flask_login import current_user, login_required

from app import limiter
from app.ai import ParseError, answer_question
from app.helpers import hx_toast
from app.services.ask_tools import TOOL_SPECS, dispatch

bp = Blueprint('ask', __name__)


# Bound the question itself (#316). Nothing on this path capped it — not the
# route, not ai.answer_question (which only rejects an EMPTY question), and not
# app-wide, since MAX_CONTENT_LENGTH is unset — so the whole POST body became the
# first user message of a BILLED call, re-sent on every one of ASK_MAX_TURNS
# round-trips. Output was already bounded (max_tokens, the turn cap, MAX_LIMIT
# above); the input was the one side with nothing on it.
#
# The realistic trigger is an accident — pasting a statement or a document into
# the box — not an attack: the route is @login_required and rate-limited, and
# this is a single-maintainer app. The only signal when it happens is the bill.
#
# A question to a finance assistant is a sentence or two, so this is generous.
# feedback.py's 5000 is the precedent for the SHAPE, not the number: a bug report
# is genuinely long-form, and it can afford to truncate silently because the tail
# of a report is expendable.
#
# ⚠️ REFUSE, never truncate. A truncated question gets answered as though it were
# the whole question, and neither the user nor the model knows the difference —
# strictly worse than being told to shorten it.
MAX_QUESTION = 1000


@bp.route('/ask', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def ask():
    """Answer a free-text finance question from the user's own data.

    Page-agnostic HTMX endpoint — the box on /dashboard posts here and swaps the
    answer into #ask-answer. Any failure falls back to a graceful decline + an
    error toast, so the page never breaks."""
    question = request.form.get('question', '').strip()

    def _answer(result):
        return make_response(render_template(
            'partials/_ask_answer.html', result=result))

    def _toast_only(message):
        # Fire an error toast WITHOUT swapping, so a previously shown answer in
        # #ask-answer survives an empty submit or a transient failure.
        resp = hx_toast(make_response('', 200), message, 'error')
        resp.headers['HX-Reswap'] = 'none'
        return resp

    if not question:
        return _toast_only('Type a question first')
    # Beside the empty check on purpose (#316), so both ways of asking nothing
    # useful read the same way — and BEFORE answer_question, because the whole
    # point is that the billed call is never made.
    if len(question) > MAX_QUESTION:
        return _toast_only(
            f'That question is too long — keep it under {MAX_QUESTION:,} '
            f'characters')

    # Bind the user id into the dispatch callback — ai.py never sees it.
    def _dispatch(tool_name, raw_input):
        return dispatch(current_user.id, tool_name, raw_input)

    try:
        result = answer_question(question, TOOL_SPECS, _dispatch, today=date.today())
    except ParseError:
        return _toast_only("Couldn't answer that right now")

    return _answer(result)
