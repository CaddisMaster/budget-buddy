"""The three forms (#244, #249, #250).

Grouped because two of them share one rule that #250 says should be "written
once": bcrypt's cap. Add transaction joins them because all three are the same
kind of change — a form whose fields are in the wrong order of importance.

⚠️ The cap is 72 BYTES, not 72 characters, and that distinction is the whole
reason this needs more than a `maxlength`: HTML's maxlength counts UTF-16 code
units, so "é" × 72 passes it and is rejected by the server. Anything claiming
to warn before submit has to measure bytes.
"""
import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
RULES_PARTIAL = TEMPLATES / "partials" / "_password_rules.html"

# Both server rules, from auth.change_password and admin.create_user.
MIN_CHARS = 8
MAX_BYTES = 72


# --- #249 + #250: one rule, stated once ---------------------------------------


def test_the_password_rules_live_in_one_partial():
    """#250: "the same cap and guidance appear as on Change password" — and
    "should be written once". Two copies of a rule drift; this is the check
    that there is only one."""
    assert RULES_PARTIAL.exists(), "no shared password-rules partial"


@pytest.mark.parametrize("template", ["change_password.html", "create_user.html"])
def test_both_password_forms_include_the_shared_partial(template):
    src = (TEMPLATES / template).read_text()
    assert "partials/_password_rules.html" in src, \
        f"{template} does not use the shared rules partial"


def test_change_password_states_both_rules_before_you_submit(client_a):
    html = client_a.get("/change-password").get_data(as_text=True)
    assert str(MIN_CHARS) in html, "the minimum length is not stated"
    assert str(MAX_BYTES) in html, "the byte cap is not stated"


def test_create_user_states_both_rules_before_you_submit(admin_client):
    html = admin_client.get("/admin/create-user").get_data(as_text=True)
    assert str(MIN_CHARS) in html
    assert str(MAX_BYTES) in html


def test_the_warning_measures_bytes_not_characters(client_a):
    """⚠️ The load-bearing one. A maxlength attribute, or a `.length` check,
    counts UTF-16 code units — so 72 accented characters (144 bytes) would pass
    the client check and be rejected by the server, which is exactly the
    "told only after submitting" behaviour #249 exists to fix."""
    html = client_a.get("/change-password").get_data(as_text=True)
    assert "TextEncoder" in html, \
        "the client-side check does not measure UTF-8 bytes"
    field = re.search(r'<input[^>]*name="new_password"[^>]*>', html)
    assert field, "no new-password field"
    assert "maxlength" not in field.group(0), \
        "a maxlength would silently truncate a multi-byte password at the " \
        "wrong boundary and cannot express a byte cap"


def test_the_server_still_enforces_the_cap_it_now_advertises(client_a, users):
    """The client-side warning is guidance, never the guard. Verified here so
    that adding the message can never be mistaken for adding enforcement."""
    response = client_a.post("/change-password", data={
        "current_password": "test-password-123",
        "new_password": "e" * 73,
    }, follow_redirects=True)
    assert b"72 bytes or fewer" in response.data


def test_a_multibyte_password_within_the_character_count_is_still_rejected(
        client_a, users):
    """"é" is two bytes: 40 of them are 80 bytes, over the cap while being
    well under 72 characters. The server's rule is bytes, so this must fail —
    and it is the case the client-side counter has to agree with."""
    response = client_a.post("/change-password", data={
        "current_password": "test-password-123",
        "new_password": "é" * 40,
    }, follow_redirects=True)
    assert b"72 bytes or fewer" in response.data


# --- #249: the outcome is unambiguous -----------------------------------------


def test_a_successful_change_says_so_where_you_land(client_a, users):
    response = client_a.post("/change-password", data={
        "current_password": "test-password-123",
        "new_password": "a-brand-new-password",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Password updated" in response.data


# --- #250: what ticking admin grants ------------------------------------------


def test_the_admin_checkbox_states_what_it_grants(admin_client):
    """#250: "when I tick admin, what that grants is stated before I submit".
    Asserted as the specific powers rather than as the word "admin", which the
    label already carried while explaining nothing."""
    html = admin_client.get("/admin/create-user").get_data(as_text=True)
    box = html[html.index('name="is_admin"') - 600:]
    box = box[:1400]
    assert "Settings" in box, "the grant does not name the Settings page"
    assert re.search(r"backup", box, re.I), "the grant does not name backups"
    assert re.search(r"delete|remove", box, re.I), \
        "the grant does not say it allows removing other users"


# --- #244: the common case comes first ----------------------------------------


def _form(html):
    return re.search(r'<form method="post" action="/transactions/new".*?</form>',
                     html, re.S).group(0)


def test_amount_comes_before_the_fields_you_rarely_change(client_a):
    """#244: "amount, description and category are the primary fields". Amount
    used to sit fourth, behind the type select."""
    form = _form(client_a.get("/transactions/new").get_data(as_text=True))
    assert form.index('name="amount"') < form.index('name="description"')
    assert form.index('name="description"') < form.index('name="category_id"')


def _details_around(html, marker):
    """The <details> enclosing `marker`, or None — real nesting, not two
    strings that merely both appear."""
    at = html.find(marker)
    if at == -1:
        return None
    opens = list(re.finditer(r"<details\b[^>]*>", html[:at]))
    end = html.find("</details>", at)
    if not opens or end == -1:
        return None
    return html[opens[-1].start():end]


def test_the_two_edge_case_flags_no_longer_dominate_the_form(client_a):
    """They carried more explanatory prose than the whole rest of the form
    combined, and both are edge cases. Still reachable — see the next test —
    but behind a disclosure rather than occupying half the page."""
    form = _form(client_a.get("/transactions/new").get_data(as_text=True))
    for flag in ('name="is_adjustment"', 'name="is_pending"'):
        assert _details_around(form, flag) is not None, \
            f"{flag} is not behind a disclosure"


def test_every_field_the_form_had_is_still_reachable(client_a):
    """#244's second scenario, stated as a list so that "tidying" the form
    into a shorter one fails loudly."""
    form = _form(client_a.get("/transactions/new").get_data(as_text=True))
    for field in ('name="transaction_type"', 'name="amount"',
                  'name="description"', 'name="category_id"',
                  'name="account_id"', 'name="transaction_date"',
                  'name="is_adjustment"', 'name="is_pending"'):
        assert field in form, f"{field} disappeared from the form"


def test_the_disclosure_names_what_it_hides(client_a):
    """A collapsed section is only safe if its summary says what is inside.
    ⚠️ This form renders blank every time (the prefill plumbing went with the
    v9 quick-add in #232), so neither flag can ever arrive pre-ticked — which
    is what makes hiding them behind a summary safe at all. If a prefill is
    ever reintroduced, the disclosure must open when a flag is set."""
    form = _form(client_a.get("/transactions/new").get_data(as_text=True))
    summary = re.search(r"<summary[^>]*>(.*?)</summary>", form, re.S)
    assert summary, "the disclosure has no summary"
    text = summary.group(1).lower()
    assert "pending" in text or "adjust" in text or "option" in text, \
        "the summary does not say what is behind it"
