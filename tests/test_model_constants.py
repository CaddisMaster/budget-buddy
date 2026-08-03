"""#140 tests — which model each beat runs on, and the request parameters that
the Sonnet 5 move made load-bearing.

⚠️ Read the ceiling on this file before adding to it. Every `_call_*_model` seam
is monkeypatched across the suite by design, so a green run proves the constants
are spelled right and the kwargs are what we intended — it says NOTHING about
whether real model output fits inside max_tokens. The live gate is a real
Auto-Categorize scan and a real agent run against real data; see the #140 issue.

What IS worth pinning here is the coupling that is invisible in the code: Sonnet 5
runs adaptive thinking BY DEFAULT (ai.py never passes `thinking`, and 4.6 did not
think by omission), and max_tokens then bounds thinking AND the response together.
So `max_tokens=4096` and the explicit `effort` are not slack that can be tidied
away — dropping either reintroduces silent truncation into the ParseError
fallback. These tests fail if someone does.
"""
from datetime import date
from types import SimpleNamespace

import pytest

import app.ai as ai
from app.ai import AGENT_MODEL, ASK_MODEL, CATEGORIZE_MODEL, MODEL

# --- the constants ---------------------------------------------------------

def test_both_sonnet_beats_name_the_current_model():
    """#140 scenario 1 — the two judgment-heavy beats are on Sonnet 5."""
    assert CATEGORIZE_MODEL == "claude-sonnet-5"
    assert AGENT_MODEL == "claude-sonnet-5"


def test_the_haiku_beats_are_untouched():
    """#140 scenario 2 — there is no Haiku 5; the cheap beats stay put."""
    assert MODEL == "claude-haiku-4-5"
    assert ASK_MODEL is MODEL


def test_no_beat_still_names_the_superseded_sonnet():
    """A catch-all: the move is only done if the old id is gone from ai.py."""
    for name in ("MODEL", "ASK_MODEL", "CATEGORIZE_MODEL", "AGENT_MODEL"):
        assert getattr(ai, name) != "claude-sonnet-4-6"


# --- the request parameters the move made load-bearing ---------------------

class _Recorder:
    """Stands in for anthropic.Anthropic — records the request kwargs and never
    touches the network. Returns a shape each caller can live with."""

    def __init__(self, result, **_kwargs):
        self.calls = []
        self.messages = SimpleNamespace(
            create=self._record(result),
            parse=self._record(result),
        )

    def _record(self, result):
        def call(**kwargs):
            self.calls.append(kwargs)
            return result
        return call


def test_categorize_leaves_room_for_thinking(monkeypatch):
    """Sonnet 5 thinks by default, and max_tokens caps thinking + the parsed JSON
    together. 2048 is what the beat used against 4.6, which did NOT think."""
    import anthropic

    recorder = _Recorder(SimpleNamespace(parsed_output=SimpleNamespace(suggestions=[])))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: recorder)

    ai._call_categorize_model(
        rows=[{"id": 1, "description": "Coffee", "amount": 4.5}],
        category_names=["Food & Dining"],
        today=date(2026, 8, 3),
        api_key="test-key",
    )

    (sent,) = recorder.calls
    assert sent["model"] == "claude-sonnet-5"
    assert sent["max_tokens"] >= 4096, "thinking + JSON share this budget"
    assert sent["output_config"]["effort"] == "low", (
        "Sonnet 5 defaults to 'high' — leaving effort implicit is a silent cost jump"
    )


def test_agent_leaves_room_for_thinking(monkeypatch):
    """Same coupling as the categorize beat, but spent once per turn (12 of them)."""
    recorder = _Recorder(SimpleNamespace(content=[], stop_reason="end_turn"))
    monkeypatch.setattr(ai, "_get_ask_client", lambda *a, **kw: recorder)

    ai._call_agent_model(
        messages=[{"role": "user", "content": "go"}],
        tool_specs=[],
        today=date(2026, 8, 3),
        api_key="test-key",
    )

    (sent,) = recorder.calls
    assert sent["model"] == "claude-sonnet-5"
    assert sent["max_tokens"] >= 4096, "thinking + tool calls share this budget"
    assert sent["output_config"]["effort"] == "medium"


def test_no_beat_passes_a_sampling_parameter(monkeypatch):
    """Sonnet 5's OTHER breaking change: a non-default temperature/top_p/top_k is
    rejected outright. ai.py has never passed one — this keeps it that way."""
    import anthropic

    recorder = _Recorder(SimpleNamespace(parsed_output=SimpleNamespace(suggestions=[])))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: recorder)
    monkeypatch.setattr(ai, "_get_ask_client", lambda *a, **kw: recorder)
    today = date(2026, 8, 3)

    ai._call_categorize_model(rows=[], category_names=[], today=today, api_key="k")
    recorder.messages = SimpleNamespace(
        create=recorder._record(SimpleNamespace(content=[], stop_reason="end_turn")),
        parse=recorder.messages.parse,
    )
    ai._call_agent_model(messages=[], tool_specs=[], today=today, api_key="k")

    for sent in recorder.calls:
        for banned in ("temperature", "top_p", "top_k"):
            assert banned not in sent, f"{banned} is a 400 on Sonnet 5"


def test_a_seam_failure_still_degrades_to_parse_error(monkeypatch):
    """The graceful-degradation contract is unchanged by the model move."""
    import anthropic

    def boom(**kwargs):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: SimpleNamespace(
        messages=SimpleNamespace(parse=boom)))
    with pytest.raises(ai.ParseError):
        ai._call_categorize_model(
            rows=[], category_names=[],
            today=date(2026, 8, 3), api_key="k",
        )
