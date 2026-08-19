"""v10.15 tests — AI-card read-state collapse.

⚠️ ONE card is left. This file covered four (Insight, Forecast, Money agent,
Goal Coach); #232 folded Home's three into a single "Ask your finances" panel
that does not collapse at all, so the goal coach on /goals is now the only
consumer of the mechanism — and therefore the only thing keeping
initAiCollapse in base.html alive. If this card ever loses its <details>, delete
the script with it rather than leaving dead JS on every page.

The property, unchanged: a card renders CLOSED when cached content exists (the
client-side initAiCollapse opens unseen content — not exercisable here), OPEN
when there is nothing cached yet (the Generate button must be reachable without
JS) and on the fragment a generate route returns (just_generated). A cached card
carries data-generated = the DB row's created_at isoformat, which is what
localStorage read-state keys on — so the generate fragment and the next page
load must render the SAME value (the RETURNING created_at fix).

No real API calls — the coach seam is monkeypatched.
"""
import re
from datetime import date

import app.ai as ai
from app.ai import ParseError, _Coach
from tests.conftest import create_goal, create_goal_coach

HX = {"HX-Request": "true"}


def _details_attrs(html, card_id):
    """The attribute string of one card's <details …> opening tag."""
    marker = f'<details id="{card_id}"'
    assert marker in html, f"{card_id} details element missing"
    return html.split(marker, 1)[1].split(">", 1)[0]


def _generated_value(attrs):
    m = re.search(r'data-generated="([^"]+)"', attrs)
    return m.group(1) if m else None


class _Seam:
    def __init__(self, result=None, boom=False):
        self.calls = 0
        self.result = result or _Coach(summary="Seamed narration.", tips=[])
        self.boom = boom

    def __call__(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise ParseError("boom")
        return self.result


# --- cached content renders CLOSED with a read-state key --------------------

def test_cached_coach_card_closed_on_goals_page(client_a, users, monkeypatch):
    a = users["a"]
    create_goal(a["id"], a["account_id"], 1000, baseline=0)
    t = date.today()
    create_goal_coach(a["id"], t.year, t.month,
                      {"summary": "Coach collapse text.", "tips": []})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    html = client_a.get("/goals").get_data(as_text=True)
    attrs = _details_attrs(html, "goal-coach-card")
    assert 'data-ai-key="coach"' in attrs
    assert _generated_value(attrs)
    assert " open" not in attrs
    assert "Coach collapse text." in html
    assert "on track" in html                      # headline figure


# --- empty state renders OPEN (Generate reachable without JS) ----------------

def test_uncached_coach_card_renders_open(client_a, users, monkeypatch):
    a = users["a"]
    create_goal(a["id"], a["account_id"], 1000, baseline=0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    html = client_a.get("/goals").get_data(as_text=True)
    attrs = _details_attrs(html, "goal-coach-card")
    assert " open" in attrs                        # empty state must be open
    assert _generated_value(attrs) is None         # no read-state key yet


# --- the generate fragment: open + the DB timestamp --------------------------

def test_generate_fragment_open_and_timestamp_matches_next_load(client_a, users, monkeypatch):
    a = users["a"]
    create_goal(a["id"], a["account_id"], 1000, baseline=0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_coach_model",
                        _Seam(_Coach(summary="Fresh narration.", tips=[])))

    frag = client_a.post("/goals/coach/generate", headers=HX).get_data(as_text=True)
    assert "<html" not in frag                     # still a fragment
    attrs = _details_attrs(frag, "goal-coach-card")
    assert " open" in attrs                        # just generated → expanded
    generated = _generated_value(attrs)
    assert generated

    # The next page load must render the SAME data-generated, or the card reads
    # as unseen twice (the route-local-clock bug this test exists for).
    html = client_a.get("/goals").get_data(as_text=True)
    assert _generated_value(_details_attrs(html, "goal-coach-card")) == generated


# --- the panel that replaced the other three does NOT collapse ---------------

def test_the_home_ai_panel_is_not_a_details_element(client_a, monkeypatch):
    """#232 — the read is the first thing the panel says, so hiding it behind a
    disclosure triangle would defeat the point of folding four cards into one."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    html = client_a.get("/").get_data(as_text=True)
    assert 'id="ask-panel"' in html
    assert '<details id="ask-panel"' not in html
    # No ELEMENT on Home carries a read-state key. (`data-ai-key` unquoted also
    # appears in base.html's initAiCollapse selector, on every page — matching
    # that would make this assertion pass for the wrong reason.)
    assert 'data-ai-key="' not in html
