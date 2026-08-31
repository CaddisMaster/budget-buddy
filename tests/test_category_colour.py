"""#257 — /categories shows the colour a category is actually drawn in.

Split out of #243, whose "each category displays the colour it is drawn with"
could not be built as written: colour belonged to the *drawn set*, not to the
category, and most categories had no drawn colour at all.

Two things changed. The fold now cuts at `PALETTE_SIZE` rather than six — #108
picked six to keep a **doughnut** readable and #223 had already replaced the
doughnut with a list of ranked bars — so on a normal account every category is
drawn and has a colour. And `/categories` reads the slot from the real pipeline
rather than recomputing it.

⚠️ **The load-bearing test is `test_the_swatch_shows_the_DRAWN_slot_not_the_preferred_one`.**
Every other test here also passes against the rejected shortcut
(`creation_index % PALETTE_SIZE`), which is right until two drawn categories
contest a hue — and then disagrees with the chart silently, which is precisely
the complaint #257 was filed about. That test builds the collision on purpose.

⚠️ Colour is still NOT a stored property of a category (#83 → #111). A swatch
is a rendering of what happened this month, never a column.
"""
import json
import re
from datetime import date
from pathlib import Path

import pytest

from app.blueprints.main import PALETTE_SIZE
from tests.conftest import create_category, create_transaction

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = REPO_ROOT / "sql/schema.sql"


def _home_slots(body, var="spendingData"):
    """category -> slot, off the rendered Home payload."""
    match = re.search(rf"const {var} = (\[.*?\]);", body, re.S)
    assert match, f"{var} missing from the dashboard"
    return {r["category"]: r["slot"] for r in json.loads(match.group(1))
            if not r.get("is_other")}


def _page_swatches(body):
    """category name -> slot number (1-based), or None when uncharted.

    Reads each row's swatch class out of the Name cell. Deliberately parses the
    RENDERED page rather than calling the helper: a Jinja mistake renders as an
    empty string rather than an error, so the only honest check is the markup.
    """
    out = {}
    for row in re.findall(r'<tr id="category-\d+">(.*?)</tr>', body, re.S):
        cls = re.search(r'class="cat-swatch ([^"]*)"', row)
        name = re.search(r'</span>\s*\n?\s*([^<\n]+?)\s*\n?\s*</td>', row, re.S)
        if not cls or not name:
            continue
        token = cls.group(1).strip()
        out[name.group(1).strip()] = None if token == "cat-swatch-none" else int(token[1:])
    return out


def _seed(users, count, prefix="Cat", kind="expense", ttype="expense", base=2000.0):
    """`count` categories with strictly descending totals.

    ⚠️ Descending and distinct on purpose — with a fold in play, tied totals
    leave "which survive" to SQL tie-breaking, which flakes.
    """
    a = users["a"]
    names = []
    for rank in range(count):
        cat_id = create_category(a["id"], f"{prefix} {rank}", kind=kind)
        create_transaction(a["id"], a["account_id"], base - rank * 100,
                           date.today(), transaction_type=ttype, category_id=cat_id)
        names.append(f"{prefix} {rank}")
    return names


# --- The acceptance criteria ------------------------------------------------

def test_the_swatch_matches_the_colour_drawn_on_home(users, client_a):
    """#257: "a swatch matches the chart"."""
    names = _seed(users, 5)
    home = _home_slots(client_a.get("/").data.decode())
    page = _page_swatches(client_a.get("/categories").data.decode())

    assert home, "no categories drawn on Home — the test proves nothing"
    for name in names:
        assert page[name] is not None, f"{name} is drawn on Home but uncharted on /categories"
        assert page[name] == home[name] + 1, (
            f"{name}: /categories says slot {page[name]}, Home draws {home[name] + 1}")


def test_the_swatch_shows_the_DRAWN_slot_not_the_preferred_one(users, client_a):
    """⚠️ The one that distinguishes this from the rejected shortcut.

    #111's production shape: two categories whose creation indices are congruent
    mod PALETTE_SIZE, both drawn. One wins its preferred slot; the other is
    displaced to the lowest free one. `creation_index % PALETTE_SIZE` would
    report the PREFERRED slot for both — i.e. two identical swatches for two
    categories the chart draws in different colours.

    The fixture seeds one category first, so "Cat 0".."Cat 8" take creation
    indices 1..9: "Cat 0" and "Cat 8" both prefer slot 1.

    ⚠️ "Cat 8" is given the SECOND-highest total deliberately. Seeded with the
    plain descending amounts it is the smallest, gets folded into "Other", and
    the collision never happens — the test then fails on its own setup rather
    than passing vacuously, which is how this was caught.
    """
    a = users["a"]
    amounts = [2000, 1900, 1800, 1700, 1600, 1500, 1400, 1300, 1950]
    for rank, amount in enumerate(amounts):
        cat_id = create_category(a["id"], f"Cat {rank}")
        create_transaction(a["id"], a["account_id"], float(amount), date.today(),
                           category_id=cat_id)
    home = _home_slots(client_a.get("/").data.decode())
    page = _page_swatches(client_a.get("/categories").data.decode())

    both = {"Cat 0", "Cat 8"}
    assert both <= set(home), f"the colliding pair is not drawn: {sorted(home)}"
    assert home["Cat 0"] != home["Cat 8"], "the collision did not occur; test is vacuous"
    # The displaced one is what the shortcut gets wrong.
    for name in both:
        assert page[name] == home[name] + 1, (
            f"{name}: /categories claims slot {page[name]}, chart draws {home[name] + 1}")
    assert page["Cat 0"] != page["Cat 8"], "two categories show the same swatch"


def test_a_folded_category_claims_no_hue(users, client_a):
    """#257: "/categories does not claim a hue for a category folded into Other"."""
    _seed(users, PALETTE_SIZE + 3)
    home = _home_slots(client_a.get("/").data.decode())
    page = _page_swatches(client_a.get("/categories").data.decode())

    uncharted = [n for n, slot in page.items() if slot is None]
    assert uncharted, "nothing was folded — the test proves nothing"
    for name in uncharted:
        assert name not in home, f"{name} reads as uncharted but Home draws it"


def test_a_category_with_no_transactions_this_month_claims_no_hue(users, client_a):
    create_category(users["a"]["id"], "Never Used")
    page = _page_swatches(client_a.get("/categories").data.decode())
    assert page["Never Used"] is None


def test_no_two_categories_show_the_same_swatch(users, client_a):
    """#257: "no two slices drawn together ever share a hue" — asserted on the
    page as well, since that is the surface making the claim."""
    _seed(users, PALETTE_SIZE + 2)
    page = _page_swatches(client_a.get("/categories").data.decode())
    drawn = [s for s in page.values() if s is not None]
    assert drawn, "nothing drawn"
    assert len(set(drawn)) == len(drawn), f"duplicate swatches: {sorted(drawn)}"
    assert max(drawn) <= PALETTE_SIZE


def test_colour_is_still_not_a_stored_property(users):
    """#257: "no change here reintroduces a per-category stored colour".

    #83 made colour a property of the category; #111 deliberately reversed it,
    because a fixed assignment cannot keep an arbitrary subset distinct. This
    asserts the reversal holds at the schema, where a regression would start.
    """
    if not SCHEMA.exists():
        pytest.skip("not present in the shipped image — .dockerignore excludes it")
    block = SCHEMA.read_text().split("CREATE TABLE public.categories")[1].split(");")[0]
    assert "colour" not in block.lower() and "color" not in block.lower(), (
        "categories has grown a stored colour column — see the slot gotcha in "
        "docs/gotchas.md before reintroducing #83's rule")


# --- The fold is now the palette size ---------------------------------------

def test_a_full_palette_of_categories_is_all_drawn(users, client_a):
    """The reason the swatch is worth having: with the fold at six, an account
    with eight categories had two permanently grey. Measured against real data,
    one of them had never been drawn once in five months."""
    names = _seed(users, PALETTE_SIZE - 1)   # + the fixture's own = PALETTE_SIZE
    page = _page_swatches(client_a.get("/categories").data.decode())
    for name in names:
        assert page[name] is not None, f"{name} is uncharted with only {PALETTE_SIZE} categories"


# --- The rendering paths ----------------------------------------------------

def test_every_row_swap_carries_the_swatch(users, client_a):
    """⚠️ The row partial tolerates a missing `colour_slots` so a swap cannot
    raise — which means a render site that forgets to pass it degrades every
    swatch to "not charted" SILENTLY. This is what stops that."""
    names = _seed(users, 3)
    cat_id = re.search(
        r'<tr id="category-(\d+)">(?:(?!</tr>).)*?Cat 0',
        client_a.get("/categories").data.decode(), re.S).group(1)

    row = client_a.get(f"/categories/{cat_id}/row").data.decode()
    assert 'class="cat-swatch s' in row, "the /row restore point lost its swatch"
    assert _page_swatches(row)["Cat 0"] is not None
    assert names  # seeded


def test_the_legend_only_appears_when_something_is_uncharted(users, client_a):
    """A legend explaining an empty state that is not on screen is noise."""
    _seed(users, 2)
    assert "cat-legend" not in client_a.get("/categories").data.decode()
    _seed(users, PALETTE_SIZE + 2, prefix="More")
    assert "cat-legend" in client_a.get("/categories").data.decode()


def test_another_users_categories_are_not_coloured_here(users, client_a, client_b):
    _seed(users, 3)
    page_b = _page_swatches(client_b.get("/categories").data.decode())
    assert not any(n.startswith("Cat ") for n in page_b)
