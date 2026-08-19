"""The money agent: an autonomous weekly investigation of the user's finances.

The model free-hunts the last week through the SAME read-only, user-scoped
ask-tools the "Ask your finances" box uses (blueprints/ask.py is still the one
security boundary — dispatch validates every arg and forces user_id), dismisses
what is explicable, and submits at most 3 evidence-cited findings. "Nothing
notable this week" is the expected outcome most weeks.

⚠️ It runs ONE way now: inside the weekly digest send (digests.py calls
run_money_agent per recipient, reusing this week's cached run if one exists).
#232 removed the dashboard card and the `/agent/run` route along with the other
Home AI surfaces — Sean's call was that the digest keeps its investigation and
Home keeps one panel. This module therefore defines NO blueprint any more.

Each run is cached one-row-per-(user, week) in `agent_runs`, keyed by the week's
Sunday (most_recent_sunday, the digest's period boundary), so a re-send never
re-pays for a run. Gated on ai_enabled(); every failure degrades to a ParseError
the caller handles (a digest without the section — never a dead email).
"""
import json
from datetime import date

from app.ai import AGENT_MODEL, investigate_finances
from app.blueprints.ask import TOOL_SPECS, dispatch
from app.db import db_cursor
from app.helpers import most_recent_sunday


def run_money_agent(user_id, *, today=None):
    """Run one investigation for `user_id` and cache it for this week.

    Callable with no request context (the digest runs it on the scheduler
    thread) — user_id is passed explicitly and bound into the dispatch closure,
    exactly like /ask does with current_user. Upserts agent_runs on
    (user_id, period_start) so a re-run overwrites this week's row. Returns
    {summary, findings, tools_used, period_start, created_at}. Raises
    ParseError on any model/loop failure (nothing is written then).
    """
    today = today or date.today()

    def _dispatch(tool_name, raw_input):
        return dispatch(user_id, tool_name, raw_input)

    result = investigate_finances(TOOL_SPECS, _dispatch, today=today)

    period_start = most_recent_sunday(today)
    content = {
        'summary': result['summary'],
        'findings': result['findings'],
        'tools_used': result['tools_used'],
    }
    with db_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO agent_runs (user_id, period_start, content, model)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, period_start)
            DO UPDATE SET content = EXCLUDED.content, model = EXCLUDED.model,
                          created_at = now()
            RETURNING created_at
        """, (user_id, period_start, json.dumps(content), AGENT_MODEL))
        created_at = cursor.fetchone()[0]

    content['period_start'] = period_start
    content['created_at'] = created_at
    return content


def load_agent_run(cursor, user_id, period_start=None):
    """Return the user's cached run as {summary, findings, tools_used,
    period_start, created_at}, or None. Latest run by default; pass
    `period_start` to require a specific week's (the digest does, so it never
    mails stale findings). Takes an existing cursor so the dashboard reuses its
    connection (the load_insight pattern)."""
    if period_start is not None:
        cursor.execute("""
            SELECT content, period_start, created_at FROM agent_runs
            WHERE user_id = %s AND period_start = %s
        """, (user_id, period_start))
    else:
        cursor.execute("""
            SELECT content, period_start, created_at FROM agent_runs
            WHERE user_id = %s
            ORDER BY period_start DESC LIMIT 1
        """, (user_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    data = json.loads(row[0])
    data.setdefault('findings', [])
    data.setdefault('tools_used', [])
    data['period_start'] = row[1]
    data['created_at'] = row[2]
    return data
