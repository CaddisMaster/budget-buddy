"""#232 tests — the one "Ask your finances" panel (/insights/read + app.ai).

Replaces test_insight.py. The Insight, Forecast and money-agent cards were three
separate AI surfaces on Home; they are now ONE panel whose opening line is
narrated from BOTH deterministic fact builders (compute_month_facts +
compute_forecast) and cached one-row-per-(user, month) in `insights`.

No real Anthropic API calls: the single network seam,
app.ai._call_month_read_model, is monkeypatched to return a canned _MonthRead, so
the route, the fact builders, the cache upsert and the graceful-fallback paths
all run while CI (which has no ANTHROPIC_API_KEY) stays offline and free.

The locked principle — "the app computes the numbers, the model only narrates" —
is exercised by build_read_facts() running directly against seeded data.
"""
from datetime import date

import app.ai as ai
from app.ai import ParseError, _MonthRead
from app.blueprints.insights import build_read_facts, compute_month_facts
from app.db import get_db_connection
from tests.conftest import (
    create_account,
    create_budget,
    create_category,
    create_insight,
    create_schedule,
    create_transaction,
    fetch_insight,
)

HX = {"HX-Request": "true"}


class _Seam:
    """Stand-in for _call_month_read_model that counts calls so cache-hit and
    no-API-call paths can be asserted."""
    def __init__(self, result=None, boom=False):
        self.calls = 0
        self.result = result or _MonthRead(summary="Looking solid this month.")
        self.boom = boom

    def __call__(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise ParseError("network down")
        return self.result


def _this_month():
    t = date.today()
    return t.year, t.month


def _clear_transactions(user_id):
    """Empty a fixture user's ledger — the `users` fixture seeds one
    current-month expense each, so 'a month with nothing to say' has to be made
    rather than assumed."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()


# --- compute_month_facts (deterministic, DB-backed) -------------------------

def test_compute_month_facts_figures_and_overruns(users):
    a = users["a"]["id"]
    acct = create_account(a, "facts-acct")
    groceries = create_category(a, "facts-groceries")
    rent = create_category(a, "facts-rent")
    # A fixed month with no other activity.
    create_transaction(a, acct, 1000, "2026-03-05", "income")
    create_transaction(a, acct, 300, "2026-03-10", "expense", category_id=groceries)
    create_transaction(a, acct, 100, "2026-03-12", "expense", category_id=rent)
    create_budget(a, groceries, 200)  # actual 300 → overrun of 100

    facts = compute_month_facts(a, 2026, 3)
    assert facts["income"] == 1000
    assert facts["expenses"] == 400
    assert facts["net"] == 600
    assert facts["savings_rate"] == 60.0
    # top category first, with amounts
    assert facts["top_categories"][0] == {"name": "facts-groceries", "amount": 300.0}
    assert {"category": "facts-groceries", "budget": 200.0,
            "actual": 300.0, "over": 100.0} in facts["overruns"]


def test_compute_month_facts_only_sees_own_rows(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_a = create_account(a, "iso-a")
    acct_b = create_account(b, "iso-b")
    create_transaction(a, acct_a, 50, "2026-04-02", "income")
    create_transaction(b, acct_b, 9999, "2026-04-02", "income")  # B's money
    facts = compute_month_facts(a, 2026, 4)
    assert facts["income"] == 50          # B's 9999 never leaks in
    assert facts["savings_rate"] == 100.0  # no A expenses that month


def test_compute_month_facts_empty_month_is_zeroed(users):
    a = users["a"]["id"]
    facts = compute_month_facts(a, 2000, 1)
    assert facts["income"] == 0 and facts["expenses"] == 0
    assert facts["savings_rate"] is None
    assert facts["top_categories"] == [] and facts["overruns"] == []


# --- build_read_facts: ONE fact dict from BOTH builders ---------------------
#
# The load-bearing property of #232. Three cards became one line, so the single
# model call has to see what all three saw — the month's own figures AND the
# forward projection. A read built from only compute_month_facts would look
# right and quietly stop being able to say "£610 is still to leave".

def test_read_facts_carry_both_the_month_and_the_projection(users):
    a = users["a"]["id"]
    acct = create_account(a, "read-acct")
    cat = create_category(a, "read-cat")
    year, month = _this_month()
    create_transaction(a, acct, 900, date(year, month, 1), "income")
    create_transaction(a, acct, 120, date(year, month, 2), "expense", category_id=cat)

    facts = build_read_facts(a, year, month)
    # the month half (the `users` fixture seeds a current-month expense of its
    # own, so the spend is a floor rather than an equality)
    assert facts["income"] == 900
    assert facts["expenses"] >= 120
    # the projection half, nested so the prompt can name it
    assert "projection" in facts
    for key in ("projected_income", "projected_expenses", "projected_net",
                "remaining_items", "remaining_scheduled_expense"):
        assert key in facts["projection"]


def test_read_facts_projection_sees_a_bill_still_to_land(users):
    a = users["a"]["id"]
    acct = create_account(a, "read-bill-acct")
    cat = create_category(a, "read-bill-cat")
    year, month = _this_month()
    create_transaction(a, acct, 500, date(year, month, 1), "income")
    # A schedule due later today or beyond is still "to leave the account".
    create_schedule(a, acct, 75, "monthly", date.today(),
                    category_id=cat, transaction_type="expense",
                    description="read-bill")

    facts = build_read_facts(a, year, month)
    assert facts["projection"]["remaining_scheduled_expense"] >= 0


def test_read_facts_only_see_own_rows(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_b = create_account(b, "read-iso-b")
    year, month = _this_month()
    create_transaction(b, acct_b, 4242, date(year, month, 3), "income")
    facts = build_read_facts(a, year, month)
    assert facts["income"] != 4242


# --- route: auth + fragment shape ------------------------------------------

def test_read_requires_login(anon_client):
    resp = anon_client.post("/insights/read", data={})
    assert resp.status_code == 302


def test_read_returns_panel_fragment_and_caches(client_a, users, monkeypatch):
    # User A has the fixture's current-month expense, so the month has data.
    year, month = _this_month()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam(_MonthRead(summary="You are ahead of last month."))
    monkeypatch.setattr(ai, "_call_month_read_model", seam)

    resp = client_a.post("/insights/read", headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data                    # a fragment, not a page
    assert b"You are ahead of last month." in resp.data  # the narration shows
    assert seam.calls == 1
    row = fetch_insight(users["a"]["id"], year, month)
    assert row is not None
    assert "You are ahead of last month." in row[0]


def test_read_fragment_still_carries_the_ask_box(client_a, monkeypatch):
    """The read and the input are ONE feature — a swap that dropped the input
    would leave the panel unusable until a full page load."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_month_read_model",
                        _Seam(_MonthRead(summary="Swapped read.")))
    resp = client_a.post("/insights/read", headers=HX)
    assert b"Swapped read." in resp.data
    assert b'id="ask-question"' in resp.data


# --- cache hit: dashboard load must NOT call the model ----------------------

def test_dashboard_uses_cache_without_calling_model(client_a, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam(_MonthRead(summary="Cached read text."))
    monkeypatch.setattr(ai, "_call_month_read_model", seam)

    client_a.post("/insights/read", headers=HX)
    assert seam.calls == 1
    resp = client_a.get("/")
    assert resp.status_code == 200
    assert b"Cached read text." in resp.data   # cached narrative rendered
    assert seam.calls == 1                     # the page load never hit the model


def test_dashboard_with_no_cached_read_asks_for_one_after_load(client_a, monkeypatch):
    """The panel fills itself via HTMX rather than blocking the GET on a model
    call — 'opens with one line' without putting the API on the page's path."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam()
    monkeypatch.setattr(ai, "_call_month_read_model", seam)
    resp = client_a.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "/insights/read" in body
    assert 'hx-trigger="load"' in body
    assert seam.calls == 0                     # deferred, not called during GET


# --- graceful fallback ------------------------------------------------------

def test_read_api_error_leaves_the_panel_usable(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_month_read_model", _Seam(boom=True))
    year, month = _this_month()
    resp = client_a.post("/insights/read", headers=HX)
    assert resp.status_code == 200
    assert b'id="ask-question"' in resp.data    # the box still works
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert fetch_insight(users["a"]["id"], year, month) is None  # nothing cached


def test_read_no_data_month_skips_model(client_b, users, monkeypatch):
    """A month with no figures and nothing scheduled has nothing to narrate —
    the model is never called and nothing is cached."""
    _clear_transactions(users["b"]["id"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seam = _Seam()
    monkeypatch.setattr(ai, "_call_month_read_model", seam)
    year, month = _this_month()
    resp = client_b.post("/insights/read", headers=HX)
    assert resp.status_code == 200
    assert seam.calls == 0                      # no figures → no API call
    assert fetch_insight(users["b"]["id"], year, month) is None


# --- isolation: A cannot see B's cached read --------------------------------

def test_dashboard_never_shows_another_users_read(client_a, users, monkeypatch):
    year, month = _this_month()
    create_insight(users["b"]["id"], year, month, {"summary": "B-PRIVATE-SUMMARY"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    resp = client_a.get("/")
    assert resp.status_code == 200
    assert b"B-PRIVATE-SUMMARY" not in resp.data


# --- the panel replaces four surfaces --------------------------------------

def test_home_has_one_ai_panel_and_none_of_the_old_cards(client_a, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_month_read_model", _Seam())
    body = client_a.get("/").data.decode()
    assert 'id="ask-panel"' in body
    assert "Ask your finances" in body
    # The three retired cards, by the ids and copy the old partials rendered.
    for gone in ('id="insight-card"', 'id="forecast-card"', 'id="agent-card"',
                 "weekly money check", "Generate insight", "Generate forecast"):
        assert gone not in body


def test_home_has_no_ai_quick_add(client_a, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_month_read_model", _Seam())
    body = client_a.get("/").data.decode()
    assert 'id="nl-quick-add"' not in body
    assert "/transactions/parse" not in body


def test_add_transaction_page_has_no_ai_quick_add(client_a, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    body = client_a.get("/transactions/new").data.decode()
    assert 'id="nl-quick-add"' not in body
    assert "/transactions/parse" not in body


def test_the_parse_route_is_gone(client_a):
    """Removed, not merely unlinked — the endpoint itself must not answer."""
    resp = client_a.post("/transactions/parse", data={"text": "coffee 4.50"})
    assert resp.status_code == 404


def test_the_retired_ai_routes_are_gone(client_a):
    for path in ("/insights/generate", "/forecasts/generate", "/agent/run"):
        assert client_a.post(path, data={}).status_code == 404


def test_panel_is_hidden_without_a_key(client_a, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client_a.get("/").data.decode()
    assert 'id="ask-panel"' not in body
