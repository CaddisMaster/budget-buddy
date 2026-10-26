"""#232 — the month read: the ONE narrated line in Home's "Ask your finances".

Home has one AI panel: a single read of the month, then the Ask box.

The split of responsibility is unchanged and is the whole point:

  * build_read_facts() produces every NUMBER, deterministically, by merging the
    two existing builders — compute_month_facts() (this month, last month,
    overruns, cards) and forecasts.compute_forecast() (what is still to land).
    This is the only source of figures.
  * app.ai.generate_month_read() turns those facts into ONE short paragraph — it
    never does arithmetic, so nothing it returns is trusted as a figure.

Cached one-row-per-(user, month) in `insights`, as the insight card was, so the
dashboard renders it instantly and a page load never calls the model; the panel
asks for a missing read itself (hx-trigger="load"), and Refresh overwrites via
the unique upsert. Gated on ai_enabled() and degrades gracefully (an error toast
and a still-usable Ask box, never a broken page).

⚠️ compute_month_facts() and category_spending() are ALSO the Ask tools'
`month_summary` / `spending_by_category` feeders — the read and the box are
wired to the same numbers on purpose, so they can never disagree.
"""
import json
from datetime import datetime

from flask import Blueprint, make_response, render_template
from flask_login import current_user, login_required

from app import limiter
from app.ai import MODEL, ParseError, generate_month_read
from app.db import db_cursor
from app.helpers import hx_toast
from app.services.month_read import build_read_facts, month_worth_reading

bp = Blueprint('insights', __name__)


@bp.route('/insights/read', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def read():
    """Generate (or Refresh) this month's read and swap the whole panel.

    Always the CURRENT month — the panel is Home's "how is this month going",
    and taking a month from the form would be a tamperable parameter with no
    caller. The dashboard renders a cached read directly; this endpoint is hit
    two ways, both HTMX: the panel's own hx-trigger="load" when nothing is
    cached yet, and the Refresh button.

    ⚠️ It returns the ENTIRE panel, Ask box included — the read and the box are
    one feature, and a fragment carrying only the paragraph would swap the input
    out of the page.
    """
    today = datetime.today()
    year, month = today.year, today.month

    def _panel(read_row, just_generated=False):
        return make_response(render_template(
            'partials/_ask_panel.html',
            read=read_row, ai_enabled=True, read_pending=False,
            just_generated=just_generated))

    facts = build_read_facts(current_user.id, year, month)
    if not month_worth_reading(facts['income'], facts['expenses'],
                               facts['projection']['remaining_items']):
        return hx_toast(_panel(None), 'Not enough data this month yet', 'error')

    try:
        result = generate_month_read(facts)
    except ParseError:
        return hx_toast(_panel(None), "Couldn't read this month right now", 'error')

    with db_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO insights (user_id, year, month, content, model)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, year, month)
            DO UPDATE SET content = EXCLUDED.content, model = EXCLUDED.model,
                          created_at = now()
            RETURNING created_at
        """, (current_user.id, year, month, json.dumps(result), MODEL))
        created_at = cursor.fetchone().created_at

    read_row = dict(result)
    # The DB timestamp, not datetime.today() — the panel prints it, and a
    # route-local clock would disagree with what the next page load reads back.
    read_row['created_at'] = created_at
    return _panel(read_row, just_generated=True)
