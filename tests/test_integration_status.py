"""#139 tests — the admin Settings panel that says which optional integrations
are actually configured.

The gap this closes: a missing env var is the one deploy failure with NO signal,
because a gated feature whose variable is unset is indistinguishable from that
feature working as designed. It has bitten twice — FEEDBACK_GITHUB_TOKEN unset
after #64 shipped, and a `github_pat_YOURTOKEN` placeholder that reached the
Droplet and would have rendered a form that accepts input and fails on every
submission.

⚠️ Every test here sets the environment EXPLICITLY rather than relying on the
ambient one. The dev container legitimately carries a real ANTHROPIC_API_KEY, so
a test that assumes "unset by default" passes or fails depending on whose machine
it runs on.
"""
import pytest

from app.blueprints.admin import (
    CONFIGURED,
    IMPLAUSIBLE,
    INTEGRATIONS,
    UNSET,
    integration_status,
    scheduler_enabled,
)

ALL_VARS = [var for _name, _desc, variables, _floor in INTEGRATIONS for var in variables]

# Long enough to clear every floor in INTEGRATIONS — stands in for a real value
# without being one. Distinctive so the "never rendered" assertion can find it.
PLAUSIBLE = "z9q" + "K" * 120


@pytest.fixture
def clean_env(monkeypatch):
    """No integration configured. The container's own ANTHROPIC_API_KEY would
    otherwise leak into every assertion."""
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ENABLE_DIGEST_SCHEDULER", raising=False)
    return monkeypatch


# The exact row names, so a rename fails loudly here rather than silently
# matching the wrong row (a substring match is a trap: "email" contains "ai").
AI, EMAIL, PUSH, FEEDBACK = [name for name, _d, _v, _f in INTEGRATIONS]


def _state(rows, name):
    (row,) = [r for r in rows if r["name"] == name]
    return row["state"]


# --- the pure rule ---------------------------------------------------------

def test_nothing_set_reads_as_not_configured(clean_env):
    rows = integration_status()
    assert len(rows) == len(INTEGRATIONS)
    assert {r["state"] for r in rows} == {UNSET}


def test_every_integration_reads_as_configured_when_plausibly_set(clean_env):
    for var in ALL_VARS:
        clean_env.setenv(var, PLAUSIBLE)
    assert {r["state"] for r in integration_status()} == {CONFIGURED}


def test_a_placeholder_is_not_mistaken_for_a_real_secret(clean_env):
    """The incident, stated literally. `github_pat_YOURTOKEN` is 22 characters
    against a real fine-grained PAT's ~93.

    ⚠️ This is also why the rule is LENGTH and not a prefix match: a
    `github_pat_` prefix check would have PASSED this exact placeholder. If
    anyone swaps the floor for a format check, this test is the net."""
    clean_env.setenv("FEEDBACK_GITHUB_TOKEN", "github_pat_YOURTOKEN")
    assert _state(integration_status(), FEEDBACK) == IMPLAUSIBLE


def test_implausible_is_distinct_from_unset(clean_env):
    """The two are different problems — a forgotten step versus a bad paste —
    and the second is the one that produces a broken-looking feature rather than
    an absent one. Collapsing them into a boolean loses the point of the panel."""
    clean_env.setenv("RESEND_API_KEY", "x")
    rows = integration_status()
    assert _state(rows, EMAIL) == IMPLAUSIBLE
    assert _state(rows, AI) == UNSET
    assert IMPLAUSIBLE != UNSET


def test_a_blank_or_whitespace_value_reads_as_unset(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "   ")
    assert _state(integration_status(), AI) == UNSET


def test_push_needs_both_vapid_keys(clean_env):
    """Half-configured push is broken, not off — push_enabled() requires both,
    so one key alone must not read as configured."""
    clean_env.setenv("VAPID_PUBLIC_KEY", PLAUSIBLE)
    assert _state(integration_status(), PUSH) == IMPLAUSIBLE

    clean_env.setenv("VAPID_PRIVATE_KEY", PLAUSIBLE)
    assert _state(integration_status(), PUSH) == CONFIGURED


def test_every_floor_rejects_a_placeholder_length(clean_env):
    """A floor that any short paste clears would not have caught incident 2.
    Nothing may be configured by a value under 20 characters."""
    for _name, _desc, variables, floor in INTEGRATIONS:
        assert floor >= 20, f"{variables} floor is too permissive to catch a placeholder"


def test_scheduler_is_reported_separately(clean_env):
    """Not a fifth integration row — it is not a credential, and its jobs each
    carry their own gate."""
    assert scheduler_enabled() is False
    clean_env.setenv("ENABLE_DIGEST_SCHEDULER", "1")
    assert scheduler_enabled() is True
    clean_env.setenv("ENABLE_DIGEST_SCHEDULER", "true")
    assert scheduler_enabled() is False, "only the literal '1' starts the scheduler"

    assert not any("scheduler" in r["name"].lower() for r in integration_status())


# --- the rendered page -----------------------------------------------------

def test_admin_sees_each_integration_and_its_state(admin_client, clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", PLAUSIBLE)
    clean_env.setenv("FEEDBACK_GITHUB_TOKEN", "github_pat_YOURTOKEN")
    body = admin_client.get("/settings").get_data(as_text=True)

    assert "Integrations" in body
    for name, _desc, _vars, _floor in INTEGRATIONS:
        assert name in body
    assert "Configured" in body                        # the AI row
    assert "Set, but does not look valid" in body       # the feedback row
    assert "Not configured" in body                     # email and push


def test_the_value_never_appears_in_the_response(admin_client, clean_env):
    """⚠️ The load-bearing one. The panel answers one boolean per integration —
    never the value, never a prefix, never a mask. A later change that "helpfully"
    shows the first few characters to aid debugging would break nothing else."""
    for var in ALL_VARS:
        clean_env.setenv(var, PLAUSIBLE)
    body = admin_client.get("/settings").get_data(as_text=True)

    assert PLAUSIBLE not in body
    for prefix_len in (4, 8, 12):
        assert PLAUSIBLE[:prefix_len] not in body
    for var in ALL_VARS:
        assert var not in body, "the variable NAME is a hint about the value's shape"


def test_the_scheduler_line_renders_both_ways(admin_client, clean_env):
    assert "not running" in admin_client.get("/settings").get_data(as_text=True)
    clean_env.setenv("ENABLE_DIGEST_SCHEDULER", "1")
    body = admin_client.get("/settings").get_data(as_text=True)
    assert "<strong>running</strong>" in body


def test_a_non_admin_cannot_see_integration_status(client_a, clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", PLAUSIBLE)
    resp = client_a.get("/settings")
    assert resp.status_code == 302
    assert "Integrations" not in resp.get_data(as_text=True)


def test_an_anonymous_visitor_is_redirected_to_login(anon_client, clean_env):
    resp = anon_client.get("/settings")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
