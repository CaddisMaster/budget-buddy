"""The Anthropic SDK still accepts the arguments `ai.py` actually passes it.

⚠️ **This is the check a green suite otherwise does not perform.** Every model
call in `app/ai.py` goes through an isolated `_call_*_model()` seam, and every
test stubs that seam — so nothing in the suite ever constructs a real client or
calls a real method. A dependency bump can therefore remove a parameter, rename a
method, or drop a whole API, and the suite stays green from top to bottom. That
gap is why `CLAUDE.md` says a major SDK bump is not a rubber stamp.

So these tests introspect the **installed** SDK. They make no network call, need
no `ANTHROPIC_API_KEY`, and cost nothing — they only ask whether the functions
`ai.py` calls would accept the arguments it passes.

The check has teeth because neither `messages.parse()` nor `messages.create()`
declares `**kwargs`: an argument the SDK has dropped is a `TypeError` at the call
site, not a silently ignored key. `test_neither_method_accepts_arbitrary_kwargs`
asserts that precondition, because if the SDK ever grew a catch-all these tests
would start passing vacuously.

Written during the `anthropic` 0.122.0 → 1.0.0 bump (#286). That release removed
`temperature`/`top_p`/`top_k`, `messages.parse(stream=...)`, the whole Text
Completions API, and moved the HTTP layer to `httpx2` — none of which this
codebase used, but nothing in the repo could demonstrate that at the time.
"""
import inspect

import pytest

anthropic = pytest.importorskip("anthropic")


def _params(method):
    """Parameter names of an unbound SDK method, and whether it takes **kwargs."""
    signature = inspect.signature(method)
    accepts_any = any(
        p.kind is p.VAR_KEYWORD for p in signature.parameters.values()
    )
    return set(signature.parameters), accepts_any


@pytest.fixture(scope="module")
def messages():
    """A client built with a dummy key — constructed, never called.

    Constructing it is itself part of the check: `ai.py` builds every client as
    `Anthropic(api_key=..., timeout=...)`, and a changed constructor signature
    would fail here.
    """
    client = anthropic.Anthropic(api_key="not-a-real-key", timeout=60.0)
    return type(client.messages)


def test_neither_method_accepts_arbitrary_kwargs(messages):
    """The precondition that makes every other test in this file meaningful."""
    for name in ("parse", "create"):
        _, accepts_any = _params(getattr(messages, name))
        assert not accepts_any, (
            f"messages.{name}() now accepts **kwargs, so a removed parameter would "
            "be silently ignored instead of raising. The assertions below no "
            "longer prove anything — replace them with a real call against a "
            "recorded response."
        )


def test_parse_accepts_what_the_structured_beats_pass(messages):
    """`_call_month_read_model`, `_call_categorize_model`, `_call_budget_review_model`
    and `_call_digest_model` all call `messages.parse()` with these."""
    names, _ = _params(messages.parse)
    for kw in ("model", "max_tokens", "system", "messages", "output_format"):
        assert kw in names, f"messages.parse() no longer accepts {kw!r}"


def test_parse_takes_output_config_alongside_output_format(messages):
    """The one call shape carrying a comment claiming it was verified by hand.

    `_call_categorize_model` passes BOTH — `output_config={"effort": "low"}` to
    scope the spend and `output_format=_Suggestions` for the schema. The SDK
    merges the latter into the former as its `format` key. The comment in
    `ai.py` used to say "verified in anthropic 0.120.0", which is precisely the
    sort of claim that goes stale silently.
    """
    names, _ = _params(messages.parse)
    assert {"output_config", "output_format"} <= names


def test_create_accepts_what_the_tool_use_beats_pass(messages):
    """`_call_ask_model` and `_call_agent_model` use `messages.create()` with tools."""
    names, _ = _params(messages.create)
    for kw in ("model", "max_tokens", "system", "tools", "messages", "output_config"):
        assert kw in names, f"messages.create() no longer accepts {kw!r}"


def test_ai_py_passes_no_parameter_the_sdk_has_dropped():
    """The removals in `anthropic` 1.0.0, asserted against our own source.

    Stated over `ai.py` rather than over the SDK: the SDK dropping these is its
    business, and only matters here if we send one. A future bump that removes
    something else is caught by the signature tests above; this one pins the
    parameters whose removal was the reason #286 needed reading at all.

    `temperature` is also asserted absent by `test_model_constants.py`, for a
    different reason — Sonnet 5 rejects a non-default value. Both should hold.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "app" / "ai.py"
    text = source.read_text()

    for removed in ("temperature=", "top_p=", "top_k=", "max_tokens_to_sample=",
                    "HUMAN_PROMPT", "AI_PROMPT", "completions.create",
                    "with_raw_response", "compaction_control"):
        assert removed not in text, (
            f"app/ai.py uses {removed!r}, which anthropic 1.x removed"
        )
