"""v10.9 Dashboard consolidation — /analytics merged into /dashboard, which
became the home page (/) in the v10.13 merge.

Covers: the /analytics redirect (bookmarks live, month filter carried), the
moved Ask box, the two ported sections (spending by day of week, year over
year), and the tojson switch — chart data is HTML-escaped into the script
block, where the old json.dumps + |safe let a crafted category name break
out of it.

Also the doughnut's colour assignment (#83): a server-built creation-order slot
per category, which replaced hashing the name into the palette.
"""
import json
import re
from datetime import date
from pathlib import Path

from app.blueprints.main import assign_series_slots, fold_chart_tail
from tests.conftest import create_category, create_transaction

# --- the redirect -------------------------------------------------------------

def test_analytics_redirects_home(client_a):
    # Retargeted to / in v10.13 (no double hop through /dashboard).
    response = client_a.get("/analytics")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_analytics_redirect_carries_month_filter(client_a):
    response = client_a.get("/analytics?month=2026-05")
    assert response.status_code == 302
    assert "month=2026-05" in response.headers["Location"]


def test_analytics_redirect_for_anonymous_lands_on_login(anon_client, users):
    # The hop itself isn't auth-gated; / bounces anons to /login.
    response = anon_client.get("/analytics", follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/login"


def test_nav_has_no_analytics_link(client_a):
    response = client_a.get("/")
    assert b'href="/analytics"' not in response.data


def test_the_chart_library_is_vendored(client_a):
    # v10.13: the chart library ships from /static/, not a CDN. ⚠️ #234 swapped
    # Chart.js for ApexCharts; what is asserted is the PROPERTY (vendored, no
    # CDN) plus the file actually referenced, so a future swap fails here loudly
    # rather than leaving a dead <script> nobody notices.
    response = client_a.get("/")
    assert b"apexcharts.min.js" in response.data
    assert b"chart.umd.min.js" not in response.data      # the retired library
    for cdn in (b"cdn.jsdelivr", b"unpkg.com", b"cdnjs."):
        assert cdn not in response.data


def test_charts_section_is_collapsible(client_a):
    # v10.13 de-scroll: the chart grid sits inside a <details> (open on
    # desktop, collapsed on mobile via JS); the canvases are still always
    # server-rendered.
    # ⚠️ #223 moved the CATEGORY chart out of this section entirely — it is now
    # server-rendered ranked bars in the page's own flow (see
    # test_dashboard_layout.py). This asserts a plot that is still in the
    # drawer, so it keeps testing the drawer rather than the doughnut.
    # (#234: the plots are ApexCharts <div>s now, not <canvas> — same ids.)
    response = client_a.get("/")
    assert b'id="charts-details"' in response.data
    assert b'id="accountBar"' in response.data
    assert b'id="spendingPie"' not in response.data


# --- the moved Ask box ----------------------------------------------------------

def test_ask_box_renders_on_dashboard(client_a, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    response = client_a.get("/")
    assert response.status_code == 200
    assert b"Ask your finances" in response.data


def test_ask_box_hidden_without_key(client_a, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client_a.get("/")
    assert response.status_code == 200
    assert b"Ask your finances" not in response.data


# --- ported sections ------------------------------------------------------------

def test_day_of_week_chart_renders(client_a):
    # The seeded 42.50 expense is dated today, so today's day name must appear
    # in the chart payload (TO_CHAR 'Day' is blank-padded; the route strips it).
    response = client_a.get("/")
    assert response.status_code == 200
    assert b'id="dowBar"' in response.data
    assert date.today().strftime("%A").encode() in response.data


def test_yoy_card_shown_when_month_filtered_with_history(client_a, users):
    a = users["a"]
    today = date.today()
    last_year = today.replace(year=today.year - 1, day=1)
    create_transaction(a["id"], a["account_id"], 100.00, last_year,
                       category_id=a["category_id"])

    response = client_a.get(f"/?month={today.strftime('%Y-%m')}")
    assert response.status_code == 200
    # #223 collapsed three stat cards into one line: the "Year over year"
    # heading and the "Same month last year" card label went with them, but the
    # comparison itself — and last year's figure — must still be on the page.
    # #223 collapsed three stat cards into one line; #225 made that line one of
    # three PEER tiles (year-over-year, budget used, still to post). The
    # comparison and last year's figure must still be on the page either way.
    assert b'class="stat-row"' in response.data
    assert b"same month last year" in response.data
    assert b"$100.00" in response.data


def test_yoy_absent_in_all_time_view(client_a, users):
    a = users["a"]
    today = date.today()
    create_transaction(a["id"], a["account_id"], 100.00,
                       today.replace(year=today.year - 1, day=1),
                       category_id=a["category_id"])

    response = client_a.get("/")
    assert response.status_code == 200
    assert b"Year over year" not in response.data


def test_yoy_absent_when_no_prior_year_data(client_a):
    # Month selected, but last year is empty — no card, not a $0.00 card.
    response = client_a.get(f"/?month={date.today().strftime('%Y-%m')}")
    assert response.status_code == 200
    assert b"Year over year" not in response.data


# --- income-by-category toggle (v10.12) --------------------------------------------

def test_income_toggle_renders_with_categorized_income(client_a, users):
    a = users["a"]
    inc = create_category(a["id"], "Salary", kind="income")
    create_transaction(a["id"], a["account_id"], 2500.00, date.today(),
                       transaction_type="income", category_id=inc)
    response = client_a.get("/")
    assert response.status_code == 200
    assert b'id="categoryChartToggle"' in response.data
    assert b"Salary" in response.data           # in the income payload
    assert b"2500.0" in response.data


def test_income_toggle_hidden_without_categorized_income(client_a, users):
    a = users["a"]
    # Uncategorized income exists, but the rollup JOINs categories — no payload,
    # no toggle (the fixture's seeded expense keeps the chart grid rendering).
    create_transaction(a["id"], a["account_id"], 900.00, date.today(),
                       transaction_type="income", category_id=None)
    response = client_a.get("/")
    assert response.status_code == 200
    assert b'id="categoryChartToggle"' not in response.data


def test_income_payload_respects_month_filter(client_a, users):
    # Income in another month -> empty income payload for the filtered view, so
    # the category name is absent and the toggle doesn't render. (The amount
    # itself still legitimately shows in the all-time net-balance trend.)
    a = users["a"]
    inc = create_category(a["id"], "Salary", kind="income")
    create_transaction(a["id"], a["account_id"], 1111.00, date(2025, 3, 15),
                       transaction_type="income", category_id=inc)
    response = client_a.get(f"/?month={date.today().strftime('%Y-%m')}")
    assert response.status_code == 200
    assert b"Salary" not in response.data
    assert b'id="categoryChartToggle"' not in response.data


def test_income_chart_json_escapes_script_breakout(client_a, users):
    # The income payload goes through |tojson like the other seven.
    a = users["a"]
    evil = "</script><script>alert(2)</script>"
    inc = create_category(a["id"], evil, kind="income")
    create_transaction(a["id"], a["account_id"], 7.00, date.today(),
                       transaction_type="income", category_id=inc)
    response = client_a.get("/")
    assert response.status_code == 200
    assert b"</script><script>alert(2)</script>" not in response.data
    assert b"\\u003c/script\\u003e" in response.data


# --- tojson hardening -------------------------------------------------------------

def test_chart_json_escapes_script_breakout(client_a, users):
    # A category named to close the script tag must not escape the JSON block.
    a = users["a"]
    evil = "</script><script>alert(1)</script>"
    cat_id = create_category(a["id"], evil)
    create_transaction(a["id"], a["account_id"], 5.00, date.today(),
                       category_id=cat_id)

    response = client_a.get("/")
    assert response.status_code == 200
    assert b"</script><script>alert(1)</script>" not in response.data
    # The name still made it into the payload — just \u-escaped by |tojson.
    assert b"\\u003c/script\\u003e" in response.data


# --- category chart colours (#83) ---------------------------------------------
#
# The doughnut used to hash the category NAME into a 7-entry palette, which
# collided: seven categories issued only five distinct colours, so two pairs of
# slices (and their legend swatches) were byte-identical. Colours now come from a
# server-built creation-order slot map, which cannot collide below palette size.
# The palette itself lives in style.css as --series-1..8.

def _slot_map(body, var="spendingData"):
    """category -> palette slot, read off the rendered chart payload (#111).

    Replaced a shared CATEGORY_SLOTS literal: slots are now assigned per view,
    so the expense and income charts legitimately differ and a single global map
    can no longer express the truth."""
    return {r["category"]: r["slot"] for r in _chart_rows(body, var)
            if not r.get("is_other")}


def _chart_rows(body, var="spendingData"):
    """Pull a chart payload literal out of the rendered dashboard."""
    match = re.search(rf"const {var} = (\[.*?\]);", body, re.S)
    assert match, f"{var} missing from the dashboard"
    return json.loads(match.group(1))


def test_category_slots_are_distinct_per_category(client_a, users):
    # The whole bug: two categories must never share a slot. Seven of them is
    # the count that was actually broken in production. The doughnut now folds
    # its tail (#108) so only six are drawn, but the invariant is unchanged for
    # whatever IS drawn.
    a = users["a"]
    names = ["Shopping", "Monthly Bills", "Housing", "Food & Dining",
             "Entertainment", "Transportation", "Personal Care"]
    # Distinct, descending amounts on purpose: with a fold in play, tied totals
    # would leave "which six survive" to SQL tie-breaking, which flakes.
    for rank, name in enumerate(names):
        cat_id = create_category(a["id"], name)
        create_transaction(a["id"], a["account_id"], 700.00 - rank * 100,
                           date.today(), category_id=cat_id)

    slots = _slot_map(client_a.get("/").data.decode())
    drawn = [n for n in names if n in slots]
    assert len(drawn) == 6, f"expected six drawn after the fold, got {drawn}"
    assigned = [slots[n] for n in drawn]
    assert len(set(assigned)) == len(drawn), f"colliding slots: {slots}"
    assert max(assigned) < 8, "slot past the palette wraps to a duplicate colour"


def test_category_slot_survives_a_new_category(client_a, users):
    # Adding a category must not repaint an existing one — the property that
    # ruled out probing for a free slot within the rendered set.
    a = users["a"]
    first = create_category(a["id"], "Groceries")
    create_transaction(a["id"], a["account_id"], 25.00, date.today(),
                       category_id=first)
    before = _slot_map(client_a.get("/").data.decode())["Groceries"]

    later = create_category(a["id"], "Pet Supplies")
    create_transaction(a["id"], a["account_id"], 40.00, date.today(),
                       category_id=later)
    after = _slot_map(client_a.get("/").data.decode())
    assert after["Groceries"] == before
    assert after["Pet Supplies"] != before


def test_category_slot_is_stable_across_the_month_filter(client_a, users):
    # A month filter changes which categories are on screen. The surviving
    # category must keep its colour, so the slot cannot depend on the set.
    a = users["a"]
    old = create_category(a["id"], "Old Thing")
    kept = create_category(a["id"], "Kept Thing")
    create_transaction(a["id"], a["account_id"], 10.00, date(2026, 1, 15),
                       category_id=old)
    create_transaction(a["id"], a["account_id"], 20.00, date(2026, 2, 15),
                       category_id=kept)

    both = _slot_map(client_a.get("/").data.decode())["Kept Thing"]
    filtered = _slot_map(client_a.get("/?month=2026-02").data.decode())["Kept Thing"]
    assert filtered == both


def test_category_slots_are_per_user(client_a, users):
    # B's categories must not occupy slots in A's map, or A's colours would
    # shift when an unrelated user creates a category.
    create_category(users["b"]["id"], "Someone Elses Category")
    a_cat = create_category(users["a"]["id"], "Mine")
    create_transaction(users["a"]["id"], users["a"]["account_id"], 15.00,
                       date.today(), category_id=a_cat)

    slots = _slot_map(client_a.get("/").data.decode())
    assert "Someone Elses Category" not in slots
    # B's category was created between A's fixture category and "Mine", so a
    # map built without a user_id filter would push "Mine" a slot further along.
    assert slots["Mine"] == 1


def test_series_palette_is_eight_distinct_hues_in_both_modes():
    # The palette is CSS, so no request renders it — assert the stylesheet
    # itself. A duplicate hex here is the one way the collision bug returns.
    css = (Path(__file__).resolve().parents[1]
           / "app" / "static" / "style.css").read_text()
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)
    for mode, block in (("light", light), ("dark", dark)):
        hexes = re.findall(r"--series-\d:\s*(#[0-9a-fA-F]{6})", block)
        assert len(hexes) == 8, f"{mode}: expected 8 series slots, got {len(hexes)}"
        assert len({h.lower() for h in hexes}) == 8, f"{mode}: duplicate hex in {hexes}"


# --- the folded tail (#108) ---------------------------------------------------
#
# A doughnut is past its readable limit around six slices, so the payload
# builders keep the top six categories and roll the rest into one neutral
# "Other". Folding is presentation ONLY — it happens after the SQL rollup, so
# every other surface still sees complete per-category figures, and the card's
# own total cannot move.

def test_fold_leaves_a_short_list_untouched():
    rows = [{"category": f"c{i}", "total": float(i)} for i in range(6)]
    assert fold_chart_tail(rows) == rows
    assert not any("is_other" in r for r in fold_chart_tail(rows))


def test_fold_rolls_the_tail_into_one_flagged_entry():
    rows = [{"category": f"c{i}", "total": float(10 - i)} for i in range(9)]
    folded = fold_chart_tail(rows)

    assert len(folded) == 7, "six real categories plus one Other"
    other = folded[-1]
    assert other["category"] == "Other"
    assert other["is_other"] is True
    assert other["folded"] == 3
    # Gherkin: the combined segment equals the sum of what it replaced.
    assert other["total"] == sum(r["total"] for r in rows[6:])
    # ...and the whole is conserved, which is what keeps the card honest.
    assert sum(r["total"] for r in folded) == sum(r["total"] for r in rows)


def test_fold_ranks_by_total_not_input_order():
    # The callers' SQL already sorts, but the helper must not depend on it.
    rows = [{"category": "small", "total": 1.0}] + [
        {"category": f"big{i}", "total": 100.0 + i} for i in range(6)]
    folded = fold_chart_tail(rows)
    assert [r["category"] for r in folded][-1] == "Other"
    assert folded[-1]["total"] == 1.0
    assert "small" not in [r["category"] for r in folded]


def test_fold_marks_only_the_synthetic_row():
    # A user may own a real category NAMED "Other" — it must stay a normal row
    # with its own colour, which is why the template keys on the flag.
    rows = [{"category": "Other", "total": 999.0}] + [
        {"category": f"c{i}", "total": float(i)} for i in range(8)]
    folded = fold_chart_tail(rows)
    real = folded[0]
    assert real["category"] == "Other" and "is_other" not in real


def test_dashboard_doughnut_never_draws_more_than_seven_segments(client_a, users):
    a = users["a"]
    for rank in range(9):
        cat_id = create_category(a["id"], f"Cat {rank}")
        create_transaction(a["id"], a["account_id"], 900.00 - rank * 100,
                           date.today(), category_id=cat_id)

    rows = _chart_rows(client_a.get("/").data.decode())
    assert len(rows) == 7, f"expected a folded chart, got {len(rows)} segments"
    assert rows[-1]["category"] == "Other"
    assert rows[-1]["is_other"] is True


def test_dashboard_fold_preserves_the_card_total(client_a, users):
    # Gherkin: the card's total is unchanged from before the fold. The fixture
    # already seeds one 42.50 expense, so total against the DB, not a constant.
    a = users["a"]
    seeded = 42.50
    for rank in range(9):
        cat_id = create_category(a["id"], f"Cat {rank}")
        amount = 900.00 - rank * 100
        seeded += amount
        create_transaction(a["id"], a["account_id"], amount, date.today(),
                           category_id=cat_id)

    rows = _chart_rows(client_a.get("/").data.decode())
    assert round(sum(r["total"] for r in rows), 2) == round(seeded, 2)


def test_dashboard_does_not_fold_a_short_category_list(client_a, users):
    # Most users never hit the limit; they must not grow a stray "Other".
    a = users["a"]
    for rank in range(3):
        cat_id = create_category(a["id"], f"Cat {rank}")
        create_transaction(a["id"], a["account_id"], 300.00 - rank * 100,
                           date.today(), category_id=cat_id)

    rows = _chart_rows(client_a.get("/").data.decode())
    assert not any(r.get("is_other") for r in rows)
    assert "Other" not in [r["category"] for r in rows]


def test_income_view_folds_too(client_a, users):
    # The pill toggle's second payload shares the canvas, palette and slot map,
    # so it needs the same fold.
    a = users["a"]
    for rank in range(8):
        cat_id = create_category(a["id"], f"Income {rank}", kind="income")
        create_transaction(a["id"], a["account_id"], 800.00 - rank * 100,
                           date.today(), transaction_type="income",
                           category_id=cat_id)

    rows = _chart_rows(client_a.get("/").data.decode(), "incomeByCategoryData")
    assert len(rows) == 7
    assert rows[-1]["is_other"] is True
    assert rows[-1]["folded"] == 2


def test_folded_slice_is_coloured_off_the_flag_not_a_series_slot(client_a, users):
    # If this ever regresses to matching the LABEL, a real category named
    # "Other" gets greyed out and the synthetic one steals its slot.
    a = users["a"]
    for rank in range(9):
        cat_id = create_category(a["id"], f"Cat {rank}")
        create_transaction(a["id"], a["account_id"], 900.00 - rank * 100,
                           date.today(), category_id=cat_id)

    body = client_a.get("/").data.decode()
    # ⚠️ #223 moved this from the doughnut's JS colour function to a class on
    # the server-rendered bar. The INVARIANT is unchanged and is the reason this
    # test exists: the folded row is coloured off its is_other FLAG, never off
    # the label "Other", so a user's real category of that name keeps its hue.
    assert 's-other' in body, "the folded row does not paint from the fold token"
    # The synthetic row must not carry a palette slot of its own.
    assert "Other" not in _slot_map(body)


def test_a_surviving_category_keeps_its_colour_across_months(client_a, users):
    # Gherkin: a category among the largest six in two different months keeps
    # the same colour. The fold changes WHICH categories are drawn, so this is
    # the #83 stability property re-asserted against the new moving part.
    a = users["a"]
    kept = create_category(a["id"], "Kept Thing")
    for month, amount in ((1, 500.00), (2, 400.00)):
        create_transaction(a["id"], a["account_id"], amount,
                           date(2026, month, 15), category_id=kept)
    # Noise that is only present in February, reshuffling that month's set.
    for rank in range(8):
        noise = create_category(a["id"], f"Noise {rank}")
        create_transaction(a["id"], a["account_id"], 100.00 - rank * 10,
                           date(2026, 2, 15), category_id=noise)

    january = _slot_map(client_a.get("/?month=2026-01").data.decode())
    february = _slot_map(client_a.get("/?month=2026-02").data.decode())
    assert january["Kept Thing"] == february["Kept Thing"]


def test_folded_slice_has_a_neutral_hue_in_both_modes():
    # Same reasoning as the palette test: CSS is never rendered by a request.
    css = (Path(__file__).resolve().parents[1]
           / "app" / "static" / "style.css").read_text()
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)
    for mode, block in (("light", light), ("dark", dark)):
        match = re.search(r"--series-other:\s*(#[0-9a-fA-F]{6})", block)
        assert match, f"{mode}: --series-other is not defined"
        hue = match.group(1).lower()
        series = {h.lower() for h in
                  re.findall(r"--series-\d:\s*(#[0-9a-fA-F]{6})", block)}
        assert hue not in series, f"{mode}: Other reuses a category hue"
        # Achromatic on purpose — it must read as "not a category".
        r, g, b = (int(hue[i:i + 2], 16) for i in (1, 3, 5))
        assert max(r, g, b) - min(r, g, b) <= 24, f"{mode}: {hue} is not neutral"


# --- distinct colours for everything drawn (#111) -----------------------------
#
# Creation order alone WRAPPED at eight: slot was `creation_index % 8`, so a
# user's 1st and 9th categories painted the same hex and, with both drawn, the
# chart showed two identical slices. Hit in production on 0.3.0.
#
# ⚠️ The fix deliberately abandons #83's "colour never depends on the set drawn"
# rule, because that rule cannot coexist with "no duplicates": a fixed
# per-category assignment cannot keep an arbitrary 6-subset distinct with only 8
# hues. Creation order remains the PREFERENCE, so the tests above (a new
# category does not repaint, a month filter does not repaint) still hold in the
# ordinary no-collision case — which is why they are unchanged.

def test_assign_slots_keeps_preferred_slot_when_free():
    rows = [{"category": c, "total": 1.0} for c in ("a", "b", "c")]
    order = {"a": 0, "b": 3, "c": 7}
    assert [r["slot"] for r in assign_series_slots(rows, order)] == [0, 3, 7]


def test_assign_slots_breaks_a_wrap_collision():
    # The production bug: index 1 and index 9 both prefer slot 1.
    rows = [{"category": "first", "total": 9.0}, {"category": "ninth", "total": 8.0}]
    order = {"first": 1, "ninth": 9}
    slots = {r["category"]: r["slot"] for r in assign_series_slots(rows, order)}
    assert slots["first"] == 1, "the earlier-created category keeps its hue"
    assert slots["ninth"] != slots["first"], "the collision must be broken"


def test_assign_slots_never_repeats_across_the_drawn_set():
    # Six categories whose creation indices collide pairwise mod 8.
    order = {f"c{i}": i for i in (0, 8, 1, 9, 2, 10)}
    rows = [{"category": c, "total": 1.0} for c in order]
    slots = [r["slot"] for r in assign_series_slots(rows, order)]
    assert len(set(slots)) == len(slots), f"duplicate slots: {slots}"


def test_assign_slots_leaves_the_folded_row_alone():
    rows = [{"category": "a", "total": 5.0},
            {"category": "Other", "total": 1.0, "is_other": True}]
    out = assign_series_slots(rows, {"a": 0})
    assert "slot" not in out[-1], "the Other row paints from its own token"
    assert out[0]["slot"] == 0


def test_dashboard_never_draws_two_slices_the_same_colour(client_a, users):
    # The production regression, end to end: nine categories where the 1st and
    # 9th created are BOTH in the top six, so both preferred slot 1.
    a = users["a"]
    amounts = [900, 800, 700, 600, 500, 400, 300, 200, 850]
    for rank, amount in enumerate(amounts):
        cat_id = create_category(a["id"], f"Cat {rank}")
        create_transaction(a["id"], a["account_id"], float(amount), date.today(),
                           category_id=cat_id)

    rows = _chart_rows(client_a.get("/").data.decode())
    drawn = [r for r in rows if not r.get("is_other")]
    slots = [r["slot"] for r in drawn]
    assert len(drawn) == 6
    assert len(set(slots)) == len(slots), f"two slices share a colour: {slots}"
    # Both halves of the colliding pair really are on screen.
    names = {r["category"] for r in drawn}
    assert {"Cat 0", "Cat 8"} <= names


def test_expense_and_income_views_are_coloured_independently(client_a, users):
    # The two views share one canvas but are never shown together, and their
    # union can exceed the palette — so each is assigned on its own.
    a = users["a"]
    for rank in range(5):
        cat_id = create_category(a["id"], f"Spend {rank}")
        create_transaction(a["id"], a["account_id"], 500.0 - rank * 10,
                           date.today(), category_id=cat_id)
    for rank in range(5):
        cat_id = create_category(a["id"], f"Earn {rank}", kind="income")
        create_transaction(a["id"], a["account_id"], 900.0 - rank * 10,
                           date.today(), transaction_type="income",
                           category_id=cat_id)

    body = client_a.get("/").data.decode()
    for var in ("spendingData", "incomeByCategoryData"):
        slots = list(_slot_map(body, var).values())
        assert len(set(slots)) == len(slots), f"{var} repeats a colour: {slots}"
