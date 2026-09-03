"""v10.3 tests — "Ask your finances" (tool-use, /ask + app.ai).

No real Anthropic API calls: the single network seam, app.ai._call_ask_model, is
monkeypatched to feed canned tool_use / text responses, so the real multi-turn
loop, the per-user tool dispatch, and the argument validation all run end-to-end
while CI (no key) stays offline. The dispatch + validators are also tested
directly against seeded users — the security boundary is the tool surface, so
that's where the isolation tests live.
"""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import app.ai as ai
import app.blueprints.ask as ask
from app.ai import ParseError, answer_question
from tests.conftest import create_account, create_category, create_schedule, create_transaction

HX = {"HX-Request": "true"}


# --- fake model blocks/responses (shape the SDK returns) --------------------

def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, tid="t1"):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=tool_input)


def _resp(blocks, stop_reason):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


# --- pure dispatch + argument validation ------------------------------------

def test_dispatch_unknown_tool_is_error():
    content, is_error = ask.dispatch(1, "no_such_tool", {})
    assert is_error is True
    assert "Unknown tool" in json.loads(content)["error"]


def test_dispatch_bad_month_is_error(users):
    content, is_error = ask.dispatch(
        users["a"]["id"], "spending_by_category", {"month": "May"})
    assert is_error is True
    assert "month" in json.loads(content)["error"]


def test_dispatch_list_categories_returns_own(users):
    content, is_error = ask.dispatch(users["a"]["id"], "list_categories", {})
    assert is_error is False
    assert json.loads(content)["categories"] == [{"name": "cat-A", "kind": "expense"}]


def test_dispatch_unknown_category_lists_valid(users):
    content, is_error = ask.dispatch(users["a"]["id"], "total_for_category", {
        "category": "Nope", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert is_error is True
    err = json.loads(content)["error"]
    assert "cat-A" in err          # the model is told the valid names to retry


def test_dispatch_search_limit_is_clamped(users):
    # An absurd limit must not error — it's clamped to MAX_LIMIT.
    content, is_error = ask.dispatch(users["a"]["id"], "search_transactions", {
        "text": "txn", "start_date": "2000-01-01", "end_date": "2100-01-01",
        "limit": 9999})
    assert is_error is False
    assert json.loads(content)["count"] >= 1


def test_dispatch_total_for_income_kind_sums_income(users):
    # v10.12 kind-awareness: an income-kind category totals what came IN
    # (was hard-coded to expense — income categories always answered $0).
    a = users["a"]
    inc = create_category(a["id"], "Freelance", kind="income")
    create_transaction(a["id"], a["account_id"], 800, date.today(),
                       transaction_type="income", category_id=inc)
    create_transaction(a["id"], a["account_id"], 25, date.today(),
                       category_id=inc)  # stray expense-typed row: not summed
    content, is_error = ask.dispatch(users["a"]["id"], "total_for_category", {
        "category": "Freelance", "start_date": "2000-01-01", "end_date": "2100-01-01"})
    assert is_error is False
    data = json.loads(content)
    assert data["kind"] == "income"
    assert data["total_received"] == 800.0
    assert "total_spent" not in data


def test_dispatch_total_for_expense_kind_unchanged(users):
    a = users["a"]
    content, is_error = ask.dispatch(a["id"], "total_for_category", {
        "category": "cat-A", "start_date": "2000-01-01", "end_date": "2100-01-01"})
    assert is_error is False
    data = json.loads(content)
    assert data["kind"] == "expense"
    assert data["total_spent"] == 42.5


# --- per-user scoping (the security boundary) -------------------------------

def test_dispatch_search_only_sees_own_rows(users):
    a_content, _ = ask.dispatch(users["a"]["id"], "search_transactions", {
        "text": "txn", "start_date": "2000-01-01", "end_date": "2100-01-01"})
    matches = json.loads(a_content)["matches"]
    descs = [m["description"] for m in matches]
    assert "txn-A" in descs
    assert "txn-B" not in descs          # B's transaction never leaks to A


def test_dispatch_cannot_total_another_users_category(users):
    # A names B's category — resolution must reject it (A doesn't own it).
    content, is_error = ask.dispatch(users["a"]["id"], "total_for_category", {
        "category": "cat-B", "start_date": "2000-01-01", "end_date": "2100-01-01"})
    assert is_error is True
    assert "No category named 'cat-B'" in json.loads(content)["error"]


# --- the multi-turn loop ----------------------------------------------------

def test_answer_question_runs_tool_then_answers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"n": 0}
    seen = {}

    def fake_seam(messages, tool_specs, today, api_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp([_tool_block("list_categories", {})], "tool_use")
        return _resp([_text_block("You have one category: Groceries.")], "end_turn")

    def fake_dispatch(name, raw):
        seen["name"] = name
        return json.dumps({"categories": ["Groceries"]}), False

    monkeypatch.setattr(ai, "_call_ask_model", fake_seam)
    out = answer_question("what are my categories?", [], fake_dispatch, today=date(2026, 6, 26))
    assert out["answer"] == "You have one category: Groceries."
    assert out["tools_used"] == ["list_categories"]
    assert seen["name"] == "list_categories"


def test_answer_question_direct_answer_no_tool(monkeypatch):
    # Model can decline / answer without a tool — rendered as-is, no crash.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_ask_model",
                        lambda *a, **k: _resp([_text_block("I can't answer that.")], "end_turn"))
    out = answer_question("what's the weather?", [], lambda n, r: ("", False),
                          today=date(2026, 6, 26))
    assert out["answer"] == "I can't answer that."
    assert out["tools_used"] == []


def test_answer_question_turn_cap_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Always asks for a tool → must give up after the cap instead of looping.
    monkeypatch.setattr(ai, "_call_ask_model",
                        lambda *a, **k: _resp([_tool_block("list_categories", {})], "tool_use"))
    try:
        answer_question("loop forever", [], lambda n, r: ("{}", False),
                        today=date(2026, 6, 26))
        raise AssertionError("expected ParseError")
    except ParseError:
        pass


def test_answer_question_no_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        answer_question("x", [], lambda n, r: ("", False))
        raise AssertionError("expected ParseError")
    except ParseError:
        pass


# --- route: auth + fragment + graceful fallback -----------------------------

def test_ask_requires_login(anon_client):
    resp = anon_client.post("/ask", data={"question": "x"})
    assert resp.status_code == 302


def test_ask_returns_answer_fragment(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_seam(messages, tool_specs, today, api_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp([_tool_block("list_categories", {})], "tool_use")
        return _resp([_text_block("Your only category is cat-A.")], "end_turn")

    monkeypatch.setattr(ai, "_call_ask_model", fake_seam)
    resp = client_a.post("/ask", data={"question": "my categories?"}, headers=HX)
    assert resp.status_code == 200
    assert b"<html" not in resp.data            # a fragment, not a full page
    assert b"Your only category is cat-A." in resp.data


def test_ask_api_error_falls_back(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def boom(*a, **k):
        raise ParseError("network down")
    monkeypatch.setattr(ai, "_call_ask_model", boom)
    resp = client_a.post("/ask", data={"question": "anything"}, headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")


def test_ask_empty_question_prompts(client_a, users):
    resp = client_a.post("/ask", data={"question": "  "}, headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")


def test_ask_answer_panel_is_class_styled_not_inline(client_a, users, monkeypatch):
    """#34 — the answer panel was inline-styled against var(--bg-subtle), a
    token defined nowhere. The inline fallback (#f6f7f9) hard-coded a pale grey,
    so dark mode rendered light text on a light panel. Presentation now lives in
    the .ask-answer rule, which uses the real --surface-2 token (theme-aware).

    This asserts the panel carries the class and no longer ships a background of
    its own; test_no_undefined_css_custom_properties (test_param_hardening.py)
    is what stops the phantom token coming back anywhere in the app."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_ask_model",
                        lambda *a, **k: _resp([_text_block("You spent $40.")], "end_turn"))
    resp = client_a.post("/ask", data={"question": "how much?"}, headers=HX)
    body = resp.data.decode()

    assert 'class="ask-answer"' in body
    assert "--bg-subtle" not in body
    assert "background:" not in body           # no inline background at all
    assert "You spent $40." in body


def test_upcoming_scheduled_excludes_a_finished_schedule(users):
    """#32 — a schedule past its end date is not upcoming. Without the filter
    the model would tell the user about a bill that can never be charged."""
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "ask-ended")
    create_schedule(a, acct, 4321, "monthly", today + timedelta(days=3),
                    transaction_type="expense",
                    end_date=today - timedelta(days=1))
    create_schedule(a, acct, 1234, "monthly", today + timedelta(days=3),
                    transaction_type="expense")

    content, _ = ask.dispatch(a, "upcoming_scheduled", {})
    amounts = [s["amount"] for s in json.loads(content)["scheduled"]]
    assert 1234.0 in amounts      # the live schedule is still reported
    assert 4321.0 not in amounts  # the finished one is not


# --- #232: the two tools that replace the retired cards ---------------------
#
# Folding Insight, Forecast and the money agent into one box only works if the
# box can reach what they reached. Both wrap the SAME deterministic builders the
# cards used, so the answer and the read can never disagree about a figure.

def test_month_summary_tool_reports_the_month_and_its_comparison(users):
    a = users["a"]["id"]
    acct = create_account(a, "tool-summary-acct")
    cat = create_category(a, "tool-summary-cat")
    create_transaction(a, acct, 800, "2026-05-04", "income")
    create_transaction(a, acct, 200, "2026-05-06", "expense", category_id=cat)
    create_transaction(a, acct, 100, "2026-04-06", "expense", category_id=cat)

    content, is_error = ask.dispatch(a, "month_summary", {"month": "2026-05"})
    assert is_error is False
    data = json.loads(content)
    assert data["month"] == "2026-05"
    assert data["income"] == 800.0
    assert data["expenses"] == 200.0
    # the prev-month comparison the Insight card used to render
    assert data["previous_month"]["expenses"] == 100.0
    assert "overruns" in data and "top_categories" in data


def test_month_projection_tool_reports_what_is_still_to_land(users, forecast_today):
    """The forecast card's whole point, now reachable from the Ask box.

    ⚠️ Strengthened in #309 tranche 8b. It asserted only that five KEYS were
    present, so it passed whatever the figures said — including on the last day
    of the month, when `today + 1 day` falls outside the month and the bill it
    sets up is not in the window at all. Key presence is worth keeping (the tool
    is a JSON contract) but it is not what the name promises, so the bill's own
    amount is asserted too, with the clock frozen to a day that has a window.
    """
    a = users["a"]["id"]
    today = forecast_today
    acct = create_account(a, "tool-proj-acct")
    cat = create_category(a, "tool-proj-cat")
    create_transaction(a, acct, 600, today.replace(day=1), "income")
    due = today + timedelta(days=1)
    create_schedule(a, acct, 90, "monthly", due,
                    transaction_type="expense", category_id=cat,
                    description="tool-proj-bill")

    content, is_error = ask.dispatch(
        a, "month_projection", {"month": f"{today.year}-{today.month:02d}"})
    assert is_error is False
    data = json.loads(content)
    for key in ("projected_income", "projected_expenses", "projected_net",
                "remaining_scheduled_expense", "remaining_items"):
        assert key in data
    # ...and the bill really is in the answer, which is the thing the Ask box
    # exists to be able to say.
    assert data["remaining_scheduled_expense"] == 90.0
    assert [i["due"] for i in data["remaining_items"]
            if i["amount"] == 90.0] == [due.isoformat()]


def test_the_new_tools_reject_a_bad_month(users):
    """⚠️ Assert the PARSE error specifically. An unregistered tool answers
    'Unknown tool: month_summary', which also contains the word "month" — so a
    looser assertion passes before either tool exists, which is exactly what it
    did while these were being written."""
    for name in ("month_summary", "month_projection"):
        content, is_error = ask.dispatch(users["a"]["id"], name, {"month": "nope"})
        assert is_error is True
        error = json.loads(content)["error"]
        assert "Unknown tool" not in error
        assert "YYYY-MM" in error


def test_the_new_tools_only_see_their_own_users_rows(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_b = create_account(b, "tool-iso-b")
    create_transaction(b, acct_b, 7777, "2026-06-03", "income")

    content, _ = ask.dispatch(a, "month_summary", {"month": "2026-06"})
    assert json.loads(content)["income"] != 7777.0


def test_every_tool_spec_has_a_handler():
    """The registry is built by zipping specs to handlers — a spec added without
    one would be advertised to the model and then fail every call."""
    assert set(ask._HANDLERS) == {spec["name"] for spec in ask.TOOL_SPECS}
    assert {"month_summary", "month_projection"} <= set(ask._HANDLERS)


# --- the question is bounded before the billed call (#316) -------------------
# /ask had no length bound anywhere on the path — not in the route, not in
# ai.answer_question (which only rejects an EMPTY question), and not app-wide
# (MAX_CONTENT_LENGTH is unset). So the whole POST body became the first user
# message of a BILLED call, multiplied by up to ASK_MAX_TURNS re-sends of the
# conversation. feedback.py, which posts to GitHub for free, bounds its text and
# says why; /ask, which costs money, was the one with no cap.
#
# Refuse rather than truncate: silently truncating a QUESTION means the model
# answers something the user did not finish asking, and neither of them knows.

def _counting_seam(calls):
    def seam(messages, tool_specs, today, api_key):
        calls["n"] += 1
        return _resp([_text_block("should never be reached")], "end_turn")
    return seam


def test_an_over_long_question_never_reaches_the_model(client_a, users, monkeypatch):
    """⚠️ The load-bearing assertion is calls["n"] == 0, NOT the toast.

    The whole point is the call not being paid for, and a test that only
    asserted the toast would still pass if the model were called first and the
    error raised afterwards. The key is set deliberately so this is a statement
    about the bound rather than about CI having no credentials — which is what
    the neighbouring empty-question test quietly relies on.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"n": 0}
    monkeypatch.setattr(ai, "_call_ask_model", _counting_seam(calls))

    resp = client_a.post("/ask", headers=HX,
                         data={"question": "a" * (ask.MAX_QUESTION + 1)})

    assert calls["n"] == 0, "the model was called with an over-long question"
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert "too long" in resp.headers.get("HX-Trigger", "")


def test_a_question_at_the_limit_is_accepted(client_a, users, monkeypatch):
    """The other side of the boundary — a bound set anywhere would satisfy the
    test above on its own."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"n": 0}

    def seam(messages, tool_specs, today, api_key):
        calls["n"] += 1
        return _resp([_text_block("An answer.")], "end_turn")

    monkeypatch.setattr(ai, "_call_ask_model", seam)

    resp = client_a.post("/ask", headers=HX,
                         data={"question": "a" * ask.MAX_QUESTION})

    assert calls["n"] == 1
    assert resp.status_code == 200
    assert "An answer." in resp.get_data(as_text=True)


def test_the_over_long_question_does_not_swap_away_a_previous_answer():
    """Refusals here go through _toast_only, which sets HX-Reswap: none so an
    answer already on screen survives — the same treatment the empty-question
    refusal gets, which is why the check belongs beside it."""
    import inspect
    src = inspect.getsource(ask.ask)
    assert "_toast_only" in src
    # The bound is checked before answer_question is ever named in the body.
    assert src.index("MAX_QUESTION") < src.index("answer_question(")


def test_the_input_mirrors_the_server_bound():
    """profile.html mirrors feedback.py's constants as maxlength; the Ask box
    should not be the one that makes the user find out by submitting."""
    from pathlib import Path
    panel = (Path(__file__).resolve().parents[1] / "app" / "templates"
             / "partials" / "_ask_panel.html").read_text()
    assert f'maxlength="{ask.MAX_QUESTION}"' in panel
