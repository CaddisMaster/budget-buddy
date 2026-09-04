"""#339 — the Stop hook's fail-open logic, which had no test at all.

`.claude/hooks/changelog-guard.sh` blocks once when a branch changes `app/`
without `CHANGELOG.md`. Its most carefully-reasoned property is that it **fails
open on anything it cannot understand**, and until #339 that was false for the
one case its own comment named: a valid JSON object whose `stop_hook_active`
key had been renamed or dropped. `.get()` answered `None` without raising, so
`{"stopHookActive": false}` was indistinguishable from an explicit `false` and
the hook blocked — the stop-loop the comment exists to prevent.

⚠️ **These tests run the hook's REAL embedded parser, extracted from the script
rather than copied into this file.** A copy would drift, and a test that passes
against a copy of the fixed logic while the shipped script keeps the broken
logic is worse than no test.

⚠️ **They deliberately do NOT drive the whole script.** The dev image has bash
but no `git`, and the hook's first act is `command -v git || exit_open` — so
every payload would exit 0 in this container and the whole table would pass
vacuously. Exit codes are verified by hand on the host instead (recorded in
#339's pull request); what is pinned here is the classification itself, which
is the thing that was actually wrong, plus the shell line that turns each
answer into an exit.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "changelog-guard.sh"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

pytestmark = pytest.mark.skipif(not HOOK.exists(), reason=_NOT_IN_IMAGE)

_MARKER = "python3 -c '"


def _embedded_parser() -> str:
    """The exact python the hook runs, lifted out of the script.

    The program is delimited by single quotes in the shell, so it can never
    contain one — which is what makes this extraction unambiguous.
    """
    body = HOOK.read_text(encoding="utf-8")
    start = body.index(_MARKER) + len(_MARKER)
    return body[start:body.index("'", start)]


def _classify(payload: str) -> str:
    """What the hook decides a payload means: "0", "1" or "unknown"."""
    result = subprocess.run(
        [sys.executable, "-c", _embedded_parser()],
        input=payload, capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Without this one, every test below could pass against nothing at all.
# ---------------------------------------------------------------------------


def test_the_embedded_parser_is_found_and_is_the_real_one():
    program = _embedded_parser()

    assert "stop_hook_active" in program, "extracted the wrong block from the script"
    assert "json" in program
    assert "'" not in program, (
        "the embedded program contains a single quote, which would end the shell "
        "string that delimits it — the script is broken, not this test"
    )


# ---------------------------------------------------------------------------
# The classification. "0" is the ONLY answer that goes on to block.
# ---------------------------------------------------------------------------


def test_an_ordinary_stop_payload_is_read_as_not_yet_blocked():
    """The shape Claude Code actually sends, measured against the installed
    binary: the field is written unconditionally, alongside these siblings."""
    payload = json.dumps({
        "session_id": "s", "transcript_path": "/tmp/t.jsonl", "cwd": "/repo",
        "permission_mode": "default", "hook_event_name": "Stop",
        "stop_hook_active": False, "last_assistant_message": "done",
    })

    assert _classify(payload) == "0"


def test_a_payload_saying_it_already_blocked_is_honoured():
    """This is what makes a block a nudge rather than a trap."""
    assert _classify('{"stop_hook_active": true}') == "1"


def test_a_renamed_field_is_unknown_rather_than_false():
    """#339. Valid JSON, field renamed — `.get()` answered None and the hook
    blocked, which is precisely the failure its comment argues hardest against."""
    assert _classify('{"stopHookActive": false}') == "unknown"


def test_a_dropped_field_is_unknown_rather_than_false():
    assert _classify("{}") == "unknown"


@pytest.mark.parametrize("payload", ["garbage", "[]", "null", '"a string"', "", "   "])
def test_anything_that_is_not_a_readable_object_is_unknown(payload):
    assert _classify(payload) == "unknown"


# ---------------------------------------------------------------------------
# The shell half: what each answer does. Without this the classification could
# be perfect and the script could still block on "unknown".
# ---------------------------------------------------------------------------


def test_only_an_explicit_not_yet_blocked_gets_past_the_gate():
    """`[ "$already_blocked" = "0" ] || exit_open` — stated as the property,
    because any other comparison would let "unknown" through to the block."""
    body = HOOK.read_text(encoding="utf-8")

    assert '[ "$already_blocked" = "0" ] || exit_open' in body, (
        "the gate that turns 'unknown' into a quiet exit has moved or changed shape"
    )
    assert 'echo unknown' in body, "the shell fallback for a crashed python3 is gone"


def test_the_hook_still_reaches_the_block():
    """The guard has to still guard. A fail-open change is one edit away from
    disabling the hook entirely, and an always-open hook looks exactly like a
    working one from every angle except the day you needed it."""
    body = HOOK.read_text(encoding="utf-8")

    assert "exit 2" in body, "nothing in the hook blocks any more"
    assert "CHANGELOG guard:" in body


def test_the_readme_documents_a_payload_that_can_actually_block():
    """`echo '{}'` was the documented smoke test and, under the fix, exercises
    the fail-open path — so it can never show a block. A doc whose test cannot
    demonstrate the behaviour it describes is how nobody notices the behaviour
    left."""
    readme = HOOK.parent.parent / "README.md"
    if not readme.exists():
        pytest.skip(_NOT_IN_IMAGE)

    text = readme.read_text(encoding="utf-8")
    smoke = [line for line in text.splitlines() if "changelog-guard.sh" in line and "echo" in line]

    assert smoke, "the README no longer shows how to test the hook"
    assert any('"stop_hook_active"' in line for line in smoke), (
        "every documented smoke test uses a payload that now fails open, so none "
        "of them can ever show the hook blocking"
    )
