"""Every model call the app makes, and nothing else.

One isolated `_call_*_model()` seam per feature, so tests monkeypatch a single
function per beat and a missing ANTHROPIC_API_KEY — or the anthropic package not
being installed — degrades to a ParseError instead of breaking app import.

⚠️ This module NEVER touches the database and is NEVER handed a user id. The
tool-use beats (Ask, the money agent) drive their loop here and reach data only
through a `dispatch` callback the blueprint supplies — blueprints/ask.py is the
security boundary, not this file.

⚠️ The v9 natural-language quick-add (`parse_transaction_text`) was REMOVED in
#232 along with its surface: it went unused, and every AI feature on Home is now
the one Ask panel. `_match_id` below survived it — auto-categorize and the budget
proposer both resolve model-chosen names through it.
"""
import json
import math
import os
from datetime import date

from pydantic import BaseModel

# Cheap model on purpose — Sean's explicit cost call for the narration beats (the
# month read and the digest) and for Ask. Don't "upgrade" to Opus.
MODEL = "claude-haiku-4-5"


class ParseError(Exception):
    """Raised on any failure (no key, package missing, API error, bad output).
    Every caller degrades gracefully — an error toast, a section left out of an
    email — instead of erroring the page."""


def _match_id(name, rows):
    """Case-insensitive exact match of `name` against rows carrying .id/.name →
    id, else None. This is the server-side guard that the model can only select
    a row the current user actually owns. Account feeders alias their columns
    (account_id AS id, account_name AS name) so one attribute contract serves
    both row shapes."""
    if not name:
        return None
    target = str(name).strip().lower()
    for row in rows:
        if str(row.name).strip().lower() == target:
            return row.id
    return None


# ---------------------------------------------------------------------------
# #232 — the month read: the ONE narrated line on Home.
#
# Supersedes v10.1 "Insight" and v10.2 "Forecast", which were two cards narrating
# two halves of one month (and, with the v10.10 money agent, three AI surfaces
# stacked in one panel). The app still computes every figure deterministically —
# insights.build_read_facts() merges compute_month_facts() with
# compute_forecast() — and the model only writes prose over them. It must NOT
# recompute or invent numbers, so nothing it returns is ever used as a figure.
# Kept in this module so a missing key or package degrades gracefully
# (ParseError) instead of breaking the dashboard.
#
# ⚠️ There is deliberately NO `tips` field. The panel is one short line and then
# the Ask box; advice is what the box is for, on demand, rather than tips nobody
# asked for on every load. The Goal Coach was the last feature that pushed
# unsolicited tips and it was removed in #262 — re-adding a `tips` field here
# rebuilds exactly that.
# ---------------------------------------------------------------------------

class _MonthRead(BaseModel):
    """The narrative shape we ask Claude to return (structured outputs). Treated
    as untrusted text — coerced/trimmed in generate_month_read()."""
    summary: str            # 2-3 sentence plain-English read of the month


def generate_month_read(facts, *, today=None):
    """Turn already-computed month figures + projection into one short read.

    `facts` is the deterministic dict from insights.build_read_facts(): the
    month's own figures at the top level, the forward projection under
    `projection`. Returns {"summary": str}. Raises ParseError on any failure (no
    key, package missing, API/output error) so the caller can fall back to the
    panel with no read + an error toast.
    """
    today = today or date.today()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    parsed = _call_month_read_model(facts, today, api_key)
    if parsed is None:
        raise ParseError("Model returned no structured output")

    summary = (parsed.summary or "").strip()
    if not summary:
        raise ParseError("Model returned an empty summary")
    return {"summary": summary}


def _call_month_read_model(facts, today, api_key):
    """The single network call for the month read — isolated so tests stub it
    without hitting the API. Returns a _MonthRead (or None); wraps any SDK,
    network, or missing-package error in ParseError."""
    system = (
        "You are a friendly, encouraging personal-finance coach. You are given "
        "one month's already-computed figures as JSON: the month itself at the "
        "top level (income, expenses, net, savings_rate, top_categories, budget "
        "overruns, the previous month under `prev`, and any credit cards), and "
        "the rest of the month under `projection` (month-to-date actuals, "
        "projected end-of-month income/expenses/net, and the scheduled items "
        "still to land). Do NOT recompute, re-add, or invent any numbers — treat "
        "the figures as ground truth and only describe them. Write AT MOST 3 "
        "sentences and FEWER THAN 60 WORDS in total — this is one short "
        "paragraph at the top of a page, not a report. Open with where the "
        "month stands, then where it is heading: how it compares with the "
        "month before, and what is still to leave the account before the month "
        "ends. Lead with the single most useful thing and leave the rest out — "
        "you are choosing what matters, not listing every figure. Frame "
        "anything from `projection` as a projection ('on pace to…', 'that "
        "would leave you…'), never as a fact. NEVER give advice, suggestions, "
        "next steps or an instruction of any kind, even a gentle one, and "
        "never close with a question — a separate Ask box handles all of that, "
        "and a tip here duplicates it. If the "
        "credit_cards list has entries you may note a card's utilization (these "
        "are CURRENT balances against each card's limit, not month-specific "
        "figures) or roughly what carrying it costs where an "
        "est_monthly_interest figure is given; if the list is empty, do not "
        "mention cards, limits, utilization or interest at all. Today is "
        f"{today.isoformat()} and the month may still be in progress."
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.parse(
            model=MODEL,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
            output_format=_MonthRead,
        )
        return response.parsed_output
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


# ---------------------------------------------------------------------------
# v10.3 — "Ask your finances" (tool-use).
#
# The capstone LLM technique and the only one not built before: multi-turn tool
# use. The user asks a plain-English question; the model answers ONLY by calling
# the app's read-only, per-user-scoped query tools — it never computes or invents
# a figure, it calls a tool and reports what the tool returns. Same split of
# responsibility as Insight/Forecast, but multi-turn:
#
#   model -> tool_use -> app runs the tool -> tool_result -> model -> answer
#
# The security boundary is the tool surface, which lives in blueprints/ask.py
# (where current_user.id is bound). This module owns ONLY the conversation loop
# and its single isolated network seam — it never touches the DB and is never
# handed a user id. `dispatch` is a callback the blueprint supplies (a closure
# over the current user) that validates args and runs one fixed query.
# ---------------------------------------------------------------------------

ASK_MODEL = MODEL          # Haiku — Sean's cost call, same as the other AI beats.
ASK_MAX_TURNS = 6          # hard cap on model<->tool round-trips (loop guard).


def answer_question(question, tool_specs, dispatch, *, today=None):
    """Answer a free-text finance question by letting Claude orchestrate the
    read-only query tools and narrate the result.

    `tool_specs` is the list of tool JSON definitions; `dispatch(name, raw_input)`
    is the blueprint's per-user callback returning a (content_str, is_error)
    tuple for each tool call. Returns {"answer": str, "tools_used": [name, ...]}.
    Raises ParseError on any failure (no key/package, API error, empty answer,
    or exceeding the turn cap) so the caller can fall back gracefully.
    """
    today = today or date.today()
    question = (question or "").strip()
    if not question:
        raise ParseError("No question to answer")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    messages = [{"role": "user", "content": question}]
    tools_used = []

    for _ in range(ASK_MAX_TURNS):
        response = _call_ask_model(messages, tool_specs, today, api_key)
        if response is None:
            raise ParseError("Model returned no response")

        blocks = list(response.content or [])
        tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]

        # Drive purely off the presence of tool_use blocks — the actual contract.
        # (stop_reason can disagree with the block list on a malformed turn; acting
        # on it would post an empty tool_result message the API then rejects.)
        if not tool_uses:
            text = " ".join(
                b.text.strip() for b in blocks
                if getattr(b, "type", None) == "text" and getattr(b, "text", "")
            ).strip()
            if not text:
                raise ParseError("Model returned an empty answer")
            return {"answer": text, "tools_used": tools_used}

        # Echo the assistant turn (incl. its tool_use blocks) back, then run each
        # tool and return ALL results in a single user message (the API contract
        # for parallel tool calls).
        messages.append({"role": "assistant", "content": blocks})
        results = []
        for tu in tool_uses:
            content, is_error = dispatch(tu.name, tu.input)
            # Only credit a tool that actually returned data — a failed call the
            # model recovered from shouldn't show in the "Computed from your data"
            # footer.
            if not is_error:
                tools_used.append(tu.name)
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": content,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": results})

    raise ParseError("Exceeded tool-use turn limit")


_ASK_SYSTEM = (
    "You answer questions about the user's OWN personal finances. You may "
    "answer ONLY by calling the provided tools — never compute, estimate, or "
    "invent a figure yourself; call a tool and report exactly what it "
    "returns. Use list_categories first if you need the user's category "
    "names. If the available tools cannot answer the question, say so plainly "
    "rather than guessing. Keep the final answer concise, friendly, and in "
    "plain English — write plain text only, with NO Markdown, asterisks, "
    "bullet syntax, or other formatting. Today's date is {today}."
)

# One client per (api_key, timeout), reused across the turns of a tool-use loop
# (and across requests) so the loop keeps the httpx keep-alive pool warm instead
# of re-doing the TLS handshake to api.anthropic.com on every turn. Shared by
# the ask loop (30s) and the money-agent loop (60s — Sonnet turns run longer).
_ask_clients = {}


def _get_ask_client(api_key, timeout=30.0):
    client = _ask_clients.get((api_key, timeout))
    if client is None:
        import anthropic
        # Bound each call so a stuck request fails fast into the graceful fallback
        # rather than hanging the loop toward the gunicorn worker timeout.
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        _ask_clients[(api_key, timeout)] = client
    return client


def _call_ask_model(messages, tool_specs, today, api_key):
    """The single network call for the ask loop — isolated so tests can stub it
    without hitting the API (feeding canned tool_use/text responses). Returns the
    raw Message (with .content and .stop_reason); wraps any SDK, network, or
    missing-package error in ParseError."""
    try:
        client = _get_ask_client(api_key)
        return client.messages.create(
            model=ASK_MODEL,
            max_tokens=1024,
            system=_ASK_SYSTEM.format(today=today.isoformat()),
            tools=tool_specs,
            messages=messages,
        )
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


# ---------------------------------------------------------------------------
# v10.5 — "Auto-Categorize & Cleanup" (batch classification).
#
# Budget Buddy's fifth AI feature and its batch-classification beat. The app
# picks the candidate rows (uncategorized, plus recent rows that may be
# mis-filed) and owns the write; the model only PROPOSES a category name +
# confidence per row. Same split and graceful-degradation contract as the other
# beats: one isolated network seam, a pure normalizer that re-resolves every
# proposed name against the user's OWN categories (so the model can never invent
# a category or reach another user's rows), and ParseError on any failure. The
# user bulk-confirms in a review list — nothing the model returns is ever applied
# automatically. Sean's deliberate cost call: this is the ONE feature on Sonnet
# (categorization accuracy is worth more than pennies here), unlike the Haiku
# beats above.
# ---------------------------------------------------------------------------

# ⚠️ Sonnet 5 thinks BY DEFAULT (adaptive) because we never pass `thinking` — 4.6
# did not. max_tokens then caps thinking AND the JSON together, so the 4096 and the
# explicit effort at the call site are load-bearing, not slack. See _call_categorize_model.
CATEGORIZE_MODEL = "claude-sonnet-5"
_CONFIDENCES = ("high", "medium", "low")


class _Suggestion(BaseModel):
    """One proposed categorization. Treated as untrusted — re-resolved and
    re-validated in _normalize_suggestions()."""
    id: int                       # the transaction id we asked about
    category: str | None       # one of the user's category names, or null
    confidence: str               # "high" | "medium" | "low"


class _Suggestions(BaseModel):
    """The batch shape we ask Claude to return (structured outputs)."""
    suggestions: list[_Suggestion]


def classify_transactions(rows, categories, *, today=None):
    """Propose a category for each candidate transaction in one batched call.

    `rows` is the candidate payload — a list of dicts with at least
    {id, description, amount, type, current_category}. `categories` is the user's
    own rows as (id, name) tuples (the same shape new_transaction loads). Returns
    a list of resolved suggestions:

        [{id, category_id, category_name, confidence}, ...]

    Each category_id is matched ONLY against a category the user actually owns
    (server-side, case-insensitive); proposals naming nothing the user owns are
    dropped, as are ids outside the submitted set. Raises ParseError on any
    failure (no key, package missing, API/output error) so the caller can fall
    back to the un-scanned banner + an error toast.
    """
    today = today or date.today()
    if not rows:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    category_names = [c.name for c in categories]
    parsed = _call_categorize_model(rows, category_names, today, api_key)
    if parsed is None:
        raise ParseError("Model returned no structured output")

    valid_ids = {int(r["id"]) for r in rows}
    return _normalize_suggestions(parsed, categories, valid_ids)


def _normalize_suggestions(parsed, categories, valid_ids):
    """Coerce/validate the model's batch into safe, resolved suggestions. Pure —
    no API, so the resolution and validation logic is directly unit-testable.
    Drops any proposal whose category doesn't resolve to a row the user owns or
    whose id wasn't one we asked about."""
    out = []
    seen = set()
    for s in (parsed.suggestions or []):
        try:
            sid = int(s.id)
        except (TypeError, ValueError):
            continue
        if sid not in valid_ids or sid in seen:
            continue
        category_id = _match_id(s.category, categories)
        if category_id is None:
            continue  # model named nothing the user actually owns
        confidence = (s.confidence or "").strip().lower()
        if confidence not in _CONFIDENCES:
            confidence = "low"
        seen.add(sid)
        out.append({
            "id": sid,
            "category_id": category_id,
            "category_name": _name_for(category_id, categories),
            "confidence": confidence,
        })
    return out


def _name_for(category_id, categories):
    """The owned category's display name for a resolved id (None if not found)."""
    for row in categories:
        if row.id == category_id:
            return row.name
    return None


def _call_categorize_model(rows, category_names, today, api_key):
    """The single network call for batch classification — isolated so tests can
    stub it without hitting the API. Returns a _Suggestions (or None); wraps any
    SDK, network, or missing-package error in ParseError."""
    system = (
        "You categorize personal-finance transactions. You are given a JSON list "
        "of transactions (each with an id, description, amount, type, and the "
        "current category if any) and the user's available category names. For "
        "EACH transaction, choose the single best-fitting category from the "
        "provided list and return its exact name, or null if none fits — do NOT "
        "invent a category that isn't in the list. Set confidence to 'high', "
        "'medium', or 'low' based on how clearly the description maps to the "
        "category. Return one suggestion object per input transaction, echoing "
        "its id. Today's date is " + today.isoformat() + ".\n"
        "Available categories: " + json.dumps(category_names)
    )
    try:
        import anthropic
        # Bound the call so a stuck request fails fast into the graceful fallback
        # rather than drifting toward the gunicorn worker timeout.
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
        response = client.messages.parse(
            # 4096, not 2048: Sonnet 5 thinks by default and max_tokens caps thinking
            # + the JSON together, so a long think would truncate the suggestions into
            # the ParseError fallback rather than failing loudly. Its tokenizer also
            # counts ~30% more per row. effort is explicit because Sonnet 5 defaults to
            # "high" — batch classification is exactly the scoped work "low" is for.
            # The SDK merges output_format INTO output_config as its "format" key, so
            # passing both is supported (verified in anthropic 0.120.0), not a clash.
            model=CATEGORIZE_MODEL,
            max_tokens=4096,
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": json.dumps(rows, default=str)}],
            output_format=_Suggestions,
        )
        return response.parsed_output
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


# ---------------------------------------------------------------------------
# v10.7 — "AI Budget Review" (right-sizing).
#
# The sixth AI feature: one batched review of the user's monthly budgets. The
# app computes every figure deterministically (compute_budget_review_facts —
# per-category monthly history, current budget, overrun record) and hands them
# to Claude as JSON; the model proposes ONE right-sized whole-dollar amount +
# a one-sentence reason per category, plus a short narrated summary. Unlike the
# narration-only beats, the model DOES pick the numbers here — so the pure
# normalizer is the guard: every proposed category is re-resolved against the
# user's OWN rows (_match_id, invents nothing), and every amount is snapped
# into a deterministic range derived from the facts themselves. Nothing is
# applied automatically — the blueprint shows a bulk-confirm review list.
# ---------------------------------------------------------------------------

BUDGET_REASON_MAX = 200


class _BudgetProposal(BaseModel):
    """One proposed monthly budget. Treated as untrusted — re-resolved and
    clamped in _normalize_budget_proposals()."""
    category: str                 # one of the user's category names (echoed back)
    amount: int                   # proposed whole-dollar monthly budget
    reason: str                   # one short plain-text sentence


class _BudgetReview(BaseModel):
    """The batch shape we ask Claude to return (structured outputs)."""
    summary: str                  # 2-3 sentence plain-text overview
    proposals: list[_BudgetProposal]


def propose_budgets(facts, categories, *, today=None):
    """Propose a right-sized monthly budget per category in one batched call.

    `facts` is the deterministic dict from compute_budget_review_facts();
    `categories` is the user's own rows as (id, name) tuples. Returns

        {"summary": str,
         "proposals": [{category_id, category_name, amount, reason}, ...]}

    Each category_id is matched ONLY against a category the user actually owns
    and each amount is snapped into a range derived from that category's own
    history, so the model can neither invent a category nor propose a wild
    figure. Raises ParseError on any failure (no key, package missing,
    API/output error) so the caller can fall back to an error toast.
    """
    today = today or date.today()
    if not facts.get("categories"):
        return {"summary": "", "proposals": []}

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    category_names = [c.name for c in categories]
    parsed = _call_budget_model(facts, category_names, today, api_key)
    if parsed is None:
        raise ParseError("Model returned no structured output")

    summary = (parsed.summary or "").strip()
    if not summary:
        raise ParseError("Model returned an empty summary")

    facts_by_name = {
        str(f["name"]).strip().lower(): f for f in facts["categories"]
    }
    return {
        "summary": summary,
        "proposals": _normalize_budget_proposals(parsed, categories, facts_by_name),
    }


def _normalize_budget_proposals(parsed, categories, facts_by_name):
    """Coerce/validate the model's proposals into safe, resolved rows. Pure —
    no API, so the resolution and clamping logic is directly unit-testable.
    Drops proposals naming a category the user doesn't own or one we didn't ask
    about; snaps out-of-range amounts to the nearest bound of a deterministic
    range built from the category's own facts (rather than dropping — the row
    stays reviewable with a sane figure)."""
    out = []
    seen = set()
    for p in (parsed.proposals or []):
        category_id = _match_id(p.category, categories)
        if category_id is None or category_id in seen:
            continue  # not the user's category, or a duplicate (first wins)
        fact = facts_by_name.get(str(p.category).strip().lower())
        if fact is None:
            continue  # model proposed for a category we didn't ask about

        # The clamp basis comes from the facts themselves, so ai.py stays
        # DB-free: half the typical month up to double it (widened to cover the
        # worst observed month, which is a legitimate "budget for the spike").
        basis = fact.get("avg_monthly") or fact.get("current_budget")
        if not basis:
            continue  # nothing sane to clamp against
        worst_month = max(
            (m.get("spent") or 0 for m in fact.get("monthly_totals", [])),
            default=0,
        )
        lo = max(1, math.floor(0.5 * basis))
        hi = max(math.ceil(2 * basis), math.ceil(worst_month), lo)

        try:
            amount = int(round(float(p.amount)))
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        amount = min(max(amount, lo), hi)

        reason = (p.reason or "").strip()[:BUDGET_REASON_MAX]
        if not reason:
            reason = "Based on your recent spending"

        seen.add(category_id)
        out.append({
            "category_id": category_id,
            "category_name": _name_for(category_id, categories),
            "amount": amount,
            "reason": reason,
        })
    return out


def _call_budget_model(facts, category_names, today, api_key):
    """The single network call for the budget review — isolated so tests can
    stub it without hitting the API. Returns a _BudgetReview (or None); wraps
    any SDK, network, or missing-package error in ParseError."""
    system = (
        "You are a friendly personal-finance coach right-sizing monthly "
        "category budgets. You are given JSON facts per category: its monthly "
        "spend totals over the last few months, the average, the current saved "
        "budget if any, and how many months exceeded it. The facts are ground "
        "truth — do NOT recompute, re-add, or invent figures. For each category "
        "propose ONE whole-dollar monthly budget grounded in its typical "
        "months, not one-off spikes: raise budgets that are chronically "
        "overrun, trim ones well above real spending. Note the current month "
        f"({facts.get('current_month')}) is still IN PROGRESS, so treat its "
        "total as partial. Pick category names ONLY from the provided list, "
        "exactly as written. Give a one-sentence reason per proposal and first "
        "a warm 2-3 sentence summary of the overall budget picture. Write "
        "plain text only, with NO Markdown, asterisks, bullet syntax, or other "
        f"formatting. Today's date is {today.isoformat()}.\n"
        "Categories: " + json.dumps(category_names)
    )
    try:
        import anthropic
        # Bound the call so a stuck request fails fast into the graceful fallback
        # rather than drifting toward the gunicorn worker timeout.
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
        response = client.messages.parse(
            model=MODEL,
            max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
            output_format=_BudgetReview,
        )
        return response.parsed_output
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


# ---------------------------------------------------------------------------
# v10.8 — "Weekly Email Digest".
#
# Budget Buddy's first PUSH feature. The app computes every figure
# deterministically (compute_digest_facts — month-to-date spend/net/budget plus
# the next 7 days of scheduled items) and hands them to Claude as JSON; the model
# only narrates them into a short email recap + a tip or two. Same isolated-seam,
# narrate-don't-compute, graceful-ParseError contract as Insight/Forecast — it is
# just delivered by email instead of a dashboard card. Its own 7th seam so tests
# stub it independently.
# ---------------------------------------------------------------------------

class _Digest(BaseModel):
    """The narrative shape we ask Claude to return (structured outputs). Treated
    as untrusted text — coerced/trimmed in generate_digest()."""
    summary: str            # 2–3 sentence plain-English weekly recap
    tips: list[str]         # 1–2 short, practical tips


def generate_digest(facts, *, today=None):
    """Turn already-computed digest figures into a short email narrative.

    `facts` is the deterministic dict from compute_digest_facts(). Returns
    {"summary": str, "tips": [str, ...]} (tips capped at 2). Raises ParseError on
    any failure (no key, package missing, API/output error) so the caller can
    skip that user's digest rather than break the batch.
    """
    today = today or date.today()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    parsed = _call_digest_model(facts, today, api_key)
    if parsed is None:
        raise ParseError("Model returned no structured output")

    summary = (parsed.summary or "").strip()
    if not summary:
        raise ParseError("Model returned an empty summary")
    tips = [t.strip() for t in (parsed.tips or []) if t and t.strip()][:2]
    return {"summary": summary, "tips": tips}


def _call_digest_model(facts, today, api_key):
    """The single network call for the weekly digest — isolated so tests stub it
    without hitting the API. Returns a _Digest (or None); wraps any SDK, network,
    or missing-package error in ParseError."""
    system = (
        "You are a friendly, encouraging personal-finance coach writing a short "
        "WEEKLY email digest. You are given already-computed figures as JSON: the "
        "current month so far (income, expenses, net, top spending category, "
        "budget over/under) and the scheduled income and bills coming up in the "
        "next 7 days. Do NOT recompute, re-add, or invent any numbers — treat the "
        "figures as ground truth and only describe them. Write a warm 2-3 "
        "sentence recap of where the month stands and what's coming this week, "
        "then 1-2 short, specific, practical tips. Be supportive, not preachy. "
        "If the credit_cards list has entries, you may note a card's "
        "utilization — these are CURRENT balances against each card's limit "
        "(e.g. 'your Discover is at 72% of its limit'). Where an entry has an "
        "est_monthly_interest figure, you may note roughly what carrying that "
        "balance costs per month. If the credit_cards list is empty, do not "
        "mention credit cards, limits, utilization, or interest at "
        "all. Write plain text only, with NO Markdown, asterisks, bullet "
        "syntax, or other formatting. Today is "
        f"{today.isoformat()} and the month is still in progress, so treat its "
        "totals as partial."
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
        response = client.messages.parse(
            model=MODEL,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
            output_format=_Digest,
        )
        return response.parsed_output
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e


# ---------------------------------------------------------------------------
# v10.10 — the "Money agent" (autonomous weekly investigation).
#
# Budget Buddy's ninth AI feature and its first AUTONOMOUS tool-use: nobody
# types a question — once a week (or on demand) the model free-hunts the user's
# recent activity through the same read-only, user-scoped ask-tools the v10.3
# loop uses, dismisses what's explicable, and submits at most 3 findings, each
# citing the tool-returned figures as evidence. Still assist-not-autopilot: the
# tools are read-only, dispatch forces user_id, and everything the model writes
# is narrative — no figure it returns is ever used as a number.
#
# Because there is no deterministic pre-screen (Sean's free-hunt call,
# 2026-07-06), noise control lives in hard guardrails instead:
#   * the system prompt names the signal classes and demands verification;
#   * the run must end via the submit_findings tool (strict schema);
#   * a submit before ANY successful data-tool call is rejected as un-grounded;
#   * _normalize_findings() caps findings at 3 and drops any without evidence;
#   * AGENT_MAX_TURNS bounds the loop, ParseError degrades gracefully.
# Sonnet, not Haiku — the Auto-Categorize accuracy-over-pennies precedent:
# judging "explicable vs notable" unsupervised is the most judgment-heavy call
# in the app, and it runs once a week.
# ---------------------------------------------------------------------------

# ⚠️ Same Sonnet 5 thinking-by-default coupling as CATEGORIZE_MODEL, multiplied by
# AGENT_MAX_TURNS — see _call_agent_model. Thinking is deliberately left ON here:
# with it disabled Sonnet 5 reaches for tools LESS, and the grounding guard below
# turns a run with no successful data-tool call into a ParseError.
AGENT_MODEL = "claude-sonnet-5"
AGENT_MAX_TURNS = 12       # investigation needs more room than ASK_MAX_TURNS
AGENT_MAX_FINDINGS = 3
AGENT_TITLE_MAX = 120
AGENT_TEXT_MAX = 500

# The finish protocol: the model ends the run by "calling" this tool. The loop
# intercepts it locally (it is never dispatched to the app's tool handlers) —
# the strict schema makes the API validate the findings shape for us.
SUBMIT_FINDINGS_TOOL = {
    "name": "submit_findings",
    "description": "Submit your final report and end the investigation. Call "
                   "this exactly once, after you have verified your findings "
                   "with the data tools. An empty findings list is a normal, "
                   "expected result when nothing notable happened.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One or two plain-text sentences on the week "
                               "overall (or 'nothing notable' when the "
                               "findings list is empty).",
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string",
                                  "description": "Short plain-text headline"},
                        "detail": {"type": "string",
                                   "description": "What was found and why it "
                                                  "matters, 1-3 sentences"},
                        "evidence": {"type": "string",
                                     "description": "The specific tool-returned "
                                                    "dates/amounts this rests "
                                                    "on"},
                    },
                    "required": ["title", "detail", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "findings"],
        "additionalProperties": False,
    },
}

_AGENT_SYSTEM = (
    "You are a careful, read-only financial investigator reviewing one user's "
    "personal-finance data for the past week. You can ONLY learn about their "
    "finances by calling the provided data tools — never estimate, recompute, "
    "or invent a figure. Investigate the last 7 days in the context of the "
    "month: look for duplicate charges (same amount and payee close together), "
    "a recurring bill whose latest amount differs from its history, credit-card "
    "utilization that is high or newly crossed 30% or 80%, a carried card "
    "balance with a notable monthly interest cost, and budget "
    "categories on pace to be blown — plus anything else genuinely odd. "
    "VERIFY before you report: a 'duplicate' that matches an active schedule's "
    "cadence, a transfer leg, or a balance-adjustment row is explicable and "
    "must be dismissed, not reported (recent_transactions flags transfers and "
    "adjustments; upcoming_scheduled shows the schedules). When you are done, "
    "you MUST end by calling submit_findings exactly once: at most "
    f"{AGENT_MAX_FINDINGS} findings, each with evidence quoting the specific "
    "dates and amounts a tool returned. Most weeks nothing is wrong — an empty "
    "findings list with a one-line summary is the expected outcome, and a "
    "finding you could not verify is worse than no finding. Write plain text "
    "only, with NO Markdown, asterisks, bullet syntax, or other formatting. "
    "Today's date is {today}."
)


def investigate_finances(tool_specs, dispatch, *, today=None):
    """Run one autonomous investigation over the user's data via the ask-tools.

    `tool_specs` is the read-only tool list (blueprints/ask.py TOOL_SPECS);
    `dispatch(name, raw_input)` is the blueprint's per-user callback, exactly as
    in answer_question — this module never sees a user id. Returns

        {"summary": str,
         "findings": [{"title", "detail", "evidence"}, ...],   # capped at 3
         "tools_used": [name, ...]}

    Raises ParseError on any failure (no key/package, API error, the model
    never calling submit_findings, submitting without having looked at any
    data, or exceeding the turn cap) so callers degrade gracefully.
    """
    today = today or date.today()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ParseError("ANTHROPIC_API_KEY is not set")

    kickoff = (
        f"Please investigate my finances for the week ending {today.isoformat()} "
        "and submit your findings."
    )
    messages = [{"role": "user", "content": kickoff}]
    specs = list(tool_specs) + [SUBMIT_FINDINGS_TOOL]
    tools_used = []
    nudged = False

    for _ in range(AGENT_MAX_TURNS):
        response = _call_agent_model(messages, specs, today, api_key)
        if response is None:
            raise ParseError("Model returned no response")

        blocks = list(response.content or [])
        tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]

        submits = [tu for tu in tool_uses if tu.name == "submit_findings"]
        if submits:
            # The grounding guard: a report from a model that never successfully
            # read any data is noise by construction — reject the whole run.
            if not tools_used:
                raise ParseError("Model submitted findings without using any tool")
            result = _normalize_findings(submits[0].input or {})
            result["tools_used"] = tools_used
            return result

        if not tool_uses:
            # The model chatted instead of finishing. Remind it of the protocol
            # once; a second text-only turn means it isn't going to comply.
            if nudged:
                raise ParseError("Model ended without calling submit_findings")
            nudged = True
            messages.append({"role": "assistant", "content": blocks})
            messages.append({
                "role": "user",
                "content": "When you have finished investigating, call the "
                           "submit_findings tool with your summary and findings.",
            })
            continue

        # Data tools: same contract as answer_question — echo the assistant
        # turn, run each tool, return ALL results in one user message.
        messages.append({"role": "assistant", "content": blocks})
        results = []
        for tu in tool_uses:
            content, is_error = dispatch(tu.name, tu.input)
            if not is_error:
                tools_used.append(tu.name)
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": content,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": results})

    raise ParseError("Exceeded tool-use turn limit")


def _normalize_findings(raw):
    """Coerce/validate a submit_findings payload into the safe cached shape.
    Pure — no API, so the guard logic is directly unit-testable. Requires a
    non-empty summary (else ParseError); drops findings missing a title,
    detail, or evidence (an unevidenced finding is exactly the noise the
    guardrails exist to stop); trims lengths; caps at AGENT_MAX_FINDINGS."""
    summary = str(raw.get("summary") or "").strip()
    if not summary:
        raise ParseError("Model submitted an empty summary")

    findings = []
    for f in (raw.get("findings") or []):
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "").strip()[:AGENT_TITLE_MAX]
        detail = str(f.get("detail") or "").strip()[:AGENT_TEXT_MAX]
        evidence = str(f.get("evidence") or "").strip()[:AGENT_TEXT_MAX]
        if not (title and detail and evidence):
            continue
        findings.append({"title": title, "detail": detail, "evidence": evidence})
        if len(findings) == AGENT_MAX_FINDINGS:
            break

    return {"summary": summary[:AGENT_TEXT_MAX], "findings": findings}


def _call_agent_model(messages, tool_specs, today, api_key):
    """The single network call for the money agent — isolated so tests can stub
    it without hitting the API (feeding canned tool_use/text blocks, like the
    ask seam). Returns the raw Message; wraps any SDK, network, or
    missing-package error in ParseError."""
    try:
        client = _get_ask_client(api_key, timeout=60.0)
        return client.messages.create(
            # 4096 + explicit effort for the same reason as _call_categorize_model —
            # Sonnet 5 thinks by default and max_tokens bounds thinking + output
            # together. "medium" rather than "low" here: this beat is genuinely
            # agentic, and the spend is per TURN (AGENT_MAX_TURNS = 12).
            model=AGENT_MODEL,
            max_tokens=4096,
            output_config={"effort": "medium"},
            system=_AGENT_SYSTEM.format(today=today.isoformat()),
            tools=tool_specs,
            messages=messages,
        )
    except Exception as e:  # network, auth, malformed output, missing package
        raise ParseError(str(e)) from e
