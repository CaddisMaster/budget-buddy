"""The Goal Coach is gone (#262).

The last of the v10.8-era per-card AI features. #232 folded Home's insight,
forecast and money-agent cards into one panel; the coach was the only survivor
of that shape, and it goes now.

⚠️ Most of this file asserts ABSENCE, which is the weakest kind of test — an
absence assertion passes just as happily when the thing was never there, or
when the page 500s. Each one here is paired with something POSITIVE from the
same response, so a broken /goals cannot make the suite look green.
"""
import re
from pathlib import Path

import pytest

from tests.conftest import create_goal

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"


# --- the page and the route ---------------------------------------------------


def test_goals_renders_without_any_coaching_card(client_a, users, monkeypatch):
    """⚠️ Seeds a goal FIRST, and that is the whole point. The coach was gated
    on `ai_enabled() and incomplete_count > 0`, so against the default fixture
    user — who owns no goals — this assertion passed happily BEFORE the feature
    was removed. An absence test that never had anything to find is not a test.
    Paired with a positive assertion for the same reason."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    create_goal(users["a"]["id"], users["a"]["account_id"], 5000)
    response = client_a.get("/goals")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Add a goal" in html, "/goals no longer renders its own content"
    assert "goal-coach" not in html
    assert "Goal Coach" not in html
    assert "Generate coaching" not in html


def test_the_generate_route_is_gone(client_a):
    response = client_a.post("/goals/coach/generate")
    assert response.status_code == 404


def test_goals_still_works(client_a, users):
    """The removal must not take the page with it."""
    response = client_a.get("/goals")
    assert response.status_code == 200
    assert "Your goals" in response.get_data(as_text=True) or \
        "No goals yet" in response.get_data(as_text=True)


# --- the collapse machinery goes with its last consumer -----------------------


def test_no_page_carries_the_read_state_collapse_script(client_a):
    """⚠️ docs/gotchas.md stated this rule before the coach was removed: it was
    the LAST consumer of initAiCollapse, and the instruction was to delete the
    script rather than leave dead JS on every page. base.html renders on every
    page, so this shipped bytes to every request for one card."""
    html = client_a.get("/goals").get_data(as_text=True)
    assert "initAiCollapse" not in html
    assert "bb-ai-seen" not in html
    assert "data-ai-key" not in html


def test_the_stylesheet_carries_no_collapse_chrome():
    css = (ROOT / "app" / "static" / "style.css").read_text()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert "summary.ai-head" not in css, \
        "the disclosure chevron outlived the only card that used it"
    assert "goal-coach-card" not in css


def test_the_coach_partial_is_deleted():
    assert not (TEMPLATES / "partials" / "_goal_coach_card.html").exists()


# --- the model seam ----------------------------------------------------------


def test_ai_exposes_no_goal_coach_entry_point():
    """⚠️ ai.py is where a removed feature is most likely to leave a seam
    behind: nothing imports it, so nothing fails, and it sits there looking
    load-bearing. Asserted against the module's own namespace."""
    import app.ai as ai
    for name in ("generate_goal_coach", "_call_coach_model", "_Coach"):
        assert not hasattr(ai, name), f"ai.{name} outlived the feature"


def test_goals_imports_nothing_from_the_retired_seam():
    src = (ROOT / "app" / "blueprints" / "goals.py").read_text()
    assert "generate_goal_coach" not in src
    assert "goal_coach" not in src


# --- what must NOT have gone with it ------------------------------------------


@pytest.mark.parametrize("template,marker", [
    ("partials/_cleanup_banner.html", "ai-card"),
    ("budgets.html", "ai-banner"),
])
def test_the_other_ai_surfaces_survive(template, marker):
    """`.ai-card` is shared: the cleanup banner and the budget-review banner
    still use it, so removing the coach must not take the class with it."""
    src = (TEMPLATES / template).read_text()
    assert marker in src


def test_the_budget_review_banner_still_carries_the_ai_material(admin_client):
    css = (ROOT / "app" / "static" / "style.css").read_text()
    assert ".ai-surface" in css, "the shared AI material went with the coach"
    assert ".ai-card {" in css, "the AI card class went with the coach"
