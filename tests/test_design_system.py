"""The design system's load-bearing rules (#225).

CSS is never rendered by a request, so — like the series-palette tests in
test_dashboard_merge.py — these assert the stylesheet itself. Each one is a
rule from #225's acceptance criteria that nothing else can catch: a stylesheet
mistake does not raise, it just quietly looks wrong on someone's screen.

⚠️ These read `app/static/style.css` and `app/static/fonts/`, both of which
DO ship in the image (`.dockerignore` strips `*.md` and the compose files, not
`app/static/`), so no skipif guard is needed here. Check that again before
adding an assertion about any other repo file — see docs/testing.md.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
CSS_PATH = STATIC / "style.css"

# Colours that may legitimately appear as a literal outside the token blocks:
# pure white and pure black are not theme decisions, they are the ink on top of
# a saturated fill (a primary button's label) and never vary by mode.
LITERAL_ALLOWED = {"#fff", "#ffffff", "#000", "#000000"}

# Tokens whose whole purpose is to differ between light and dark. A token that
# exists only in :root renders the dark theme with a light-theme value — the
# failure is silent and looks like "dark mode is a bit off" rather than a bug.
#
# ⚠️ THIS LIST HAD FALLEN 14 BEHIND THE STYLESHEET (#309, tranche 8a). It named
# twelve tokens while the dark block redefined twenty-six, so fourteen deliberate
# dark steps were unasserted — delete `--danger-soft`'s and a pale pink (#fceceb)
# renders on the dark surface, with a green suite. The eight `--series-*` steps
# are the worst of them: their own comment in style.css says each "clears 3:1
# against --surface here, which the light steps do not, so they cannot simply be
# reused", so losing them is a contrast failure in every chart.
#
# The list stayed short because nothing made it grow. It is now held to the
# stylesheet from BOTH sides — see
# test_the_theme_token_list_has_not_fallen_behind_the_stylesheet. Derivation
# alone cannot replace it: a token dropped from the dark block simply stops
# being discovered, so an external record of "this must be themed" is the only
# thing that can notice a deletion.
THEME_TOKENS = (
    "--bg", "--surface", "--surface-2", "--surface-raise",
    "--text", "--text-muted", "--border",
    "--grad-hero", "--grad-accent",
    "--shadow-sm", "--shadow-md", "--shadow-lg",
    "--accent-soft", "--accent-2-soft",
    "--success-soft", "--danger-soft", "--danger-hover",
    "--series-1", "--series-2", "--series-3", "--series-4",
    "--series-5", "--series-6", "--series-7", "--series-8",
    "--series-other",
)


@pytest.fixture(scope="module")
def css():
    return CSS_PATH.read_text()


@pytest.fixture(scope="module")
def css_no_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _token_blocks(css_no_comments):
    """(light :root body, dark-mode block body) — where tokens are allowed to
    carry literal colours, because defining them IS their job."""
    light = re.search(r":root\s*\{(.*?)\n\}", css_no_comments, re.S)
    dark = re.search(
        r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{(.*?)\n  \}",
        css_no_comments, re.S)
    assert light, "no :root token block"
    assert dark, "no dark-mode :root block"
    return light.group(1), dark.group(1)


# --- typography ---------------------------------------------------------------

def test_both_faces_are_vendored_as_files_in_the_repo():
    # The app ships self-contained: a webfont fetched from a CDN would hand
    # every page view to a third party and break the moment that host does.
    for name in ("space-grotesk-latin-var.woff2", "instrument-sans-latin-var.woff2"):
        path = STATIC / "fonts" / name
        assert path.is_file(), f"{name} is not vendored"
        assert path.read_bytes()[:4] == b"wOF2", f"{name} is not a woff2"


def test_no_stylesheet_or_template_fetches_a_font_from_the_network(css):
    hosts = ("fonts.googleapis.com", "fonts.gstatic.com", "use.typekit",
             "//fonts.", "cdn.jsdelivr", "unpkg.com")
    for host in hosts:
        assert host not in css, f"style.css reaches out to {host}"

    templates = (Path(__file__).resolve().parents[1] / "app" / "templates")
    for tpl in templates.rglob("*.html"):
        text = tpl.read_text()
        for host in hosts:
            assert host not in text, f"{tpl.name} reaches out to {host}"


def test_font_faces_point_at_the_vendored_files(css):
    faces = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
    assert len(faces) >= 2, "expected a @font-face per vendored family"
    for face in faces:
        src = re.search(r"url\(([^)]+)\)", face)
        assert src, f"@font-face with no src: {face[:60]}"
        assert src.group(1).lstrip("'\"").startswith(("fonts/", "./fonts/", "../fonts/")), \
            f"@font-face src is not a vendored path: {src.group(1)}"
        # Without swap the first paint blocks on the font and the app flashes
        # blank text on a cold cache.
        assert "font-display: swap" in face, "a @font-face is missing font-display: swap"


def test_font_tokens_end_in_a_system_fallback(css_no_comments):
    light, _ = _token_blocks(css_no_comments)
    for token in ("--font-display", "--font-body"):
        match = re.search(rf"{token}:\s*([^;]+);", light)
        assert match, f"{token} is not defined"
        stack = match.group(1)
        assert re.search(r"(system-ui|sans-serif)\s*$", stack.strip()), \
            f"{token} has no system fallback: {stack}"


def test_money_figures_are_tabular(css_no_comments):
    # Proportional digits make a column of amounts wobble line to line, and the
    # decimal points stop lining up — the single clearest tell of a page that
    # was not designed for money.
    tabular = [rule for rule in re.findall(r"([^{}]+)\{([^}]*)\}", css_no_comments)
               if "font-variant-numeric: tabular-nums" in rule[1]]
    assert tabular, "nothing sets tabular-nums"
    selectors = " ".join(sel for sel, _ in tabular)
    # ⚠️ `.hero-stat-val` used to be in this list and was REMOVED in #309's
    # tranche 5, along with the rule it was pinning. No template has carried
    # that class since #227 replaced the hero's inline stats with the two flow
    # cards beside it — so this test was requiring a selector that matched
    # nothing, which is a check that cannot fail for the right reason. Every
    # name below is one a template actually renders; keep it that way.
    for needed in ("td", ".hero-net", ".stat-value", ".flow-val", ".stat-tile-val"):
        assert needed in selectors, f"{needed} does not get tabular figures"


# --- colour -------------------------------------------------------------------

def test_every_colour_outside_the_token_blocks_comes_from_a_token(css_no_comments):
    light, dark = _token_blocks(css_no_comments)
    body = css_no_comments.replace(light, "").replace(dark, "")
    stray = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", body)}
    stray -= LITERAL_ALLOWED
    assert not stray, (
        f"literal colours outside the token set: {sorted(stray)} — "
        "add a token instead, or dark mode will not follow them")


def test_theme_tokens_are_defined_in_both_modes(css_no_comments):
    light, dark = _token_blocks(css_no_comments)
    for token in THEME_TOKENS:
        assert re.search(rf"{token}:", light), f"{token} missing from :root"
        assert re.search(rf"{token}:", dark), \
            f"{token} has no dark step — dark mode renders it with the light value"


def test_the_theme_token_list_has_not_fallen_behind_the_stylesheet(css_no_comments):
    """THEME_TOKENS must name everything the dark block actually re-steps.

    The test above asserts every LISTED token is themed. Nothing asserted the
    list was complete, so it sat at twelve while the stylesheet moved to
    twenty-six — every token added to the dark block after the list was written
    went unwatched, which is the failure mode #309 keeps finding: a
    hand-maintained list that only ever fails for members someone remembered to
    add.

    Held from both sides deliberately. This direction catches the list falling
    behind; `test_theme_tokens_are_defined_in_both_modes` catches a dark step
    being deleted. Neither is redundant — a deleted dark step simply stops being
    discoverable, so derivation alone would go quiet at exactly the wrong moment.
    """
    _, dark = _token_blocks(css_no_comments)
    stepped = set(re.findall(r"(--[\w-]+)\s*:", dark))
    assert len(stepped) >= 12, (
        f"only parsed {len(stepped)} token(s) out of the dark block — the "
        "parser is broken, not the stylesheet. Without this floor an empty "
        "parse would make the assertion below vacuously true."
    )

    unwatched = sorted(stepped - set(THEME_TOKENS))
    assert not unwatched, (
        "the dark block re-steps these tokens and THEME_TOKENS does not name "
        f"them, so nothing would notice if the dark step were deleted: {unwatched}. "
        "Add them to THEME_TOKENS."
    )


def test_the_ai_accent_stays_exclusive_to_ai(css_no_comments):
    # --accent-2 signals "a model wrote this" everywhere in the app. The moment
    # an ordinary button or the hero borrows it, the badge stops meaning
    # anything — so the gradients deliberately stay in the blue family.
    light, dark = _token_blocks(css_no_comments)
    for name, block in (("light", light), ("dark", dark)):
        for grad in ("--grad-hero", "--grad-accent"):
            match = re.search(rf"{grad}:\s*([^;]+);", block)
            assert match, f"{name}: {grad} is not defined"
            assert "--accent-2" not in match.group(1), \
                f"{name}: {grad} borrows the AI accent"


# --- motion -------------------------------------------------------------------

def test_motion_is_suppressed_for_reduced_motion(css_no_comments):
    block = re.search(
        r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}",
        css_no_comments, re.S)
    assert block, "no prefers-reduced-motion block"
    body = block.group(1)
    for prop in ("animation", "transition"):
        match = re.search(rf"{prop}-duration:\s*([\d.]+)\s*(m?s)", body)
        assert match, f"reduced motion does not set {prop}-duration"
        ms = float(match.group(1)) * (1 if match.group(2) == "ms" else 1000)
        assert ms <= 1, f"reduced motion leaves {prop} running for {ms}ms"
    assert "transform: none" in body, "reduced motion leaves transforms running"


def test_entrance_animation_is_opacity_and_transform_only(css_no_comments):
    # A keyframe that animates height/width/top thrashes layout on every card
    # at once; the entrance is deliberately compositor-only.
    frames = re.findall(r"@keyframes[^{]+\{(.*?)\n\}", css_no_comments, re.S)
    # Non-vacuous on purpose: an empty findall would pass this test while the
    # entrance animation had been deleted (or never landed).
    assert frames, "no @keyframes in the stylesheet"
    for body in frames:
        props = {p.strip() for p in re.findall(r"([a-z-]+):", body)}
        assert props <= {"opacity", "transform"}, \
            f"a keyframe animates layout properties: {sorted(props)}"


# --- #234: the chart library is vendored, and its licence is one we can use --

def test_the_chart_library_and_its_licence_are_vendored():
    """The same self-contained rule the typefaces follow: no CDN, and the
    licence text ships beside the file it covers.

    ⚠️ The version matters and is not cosmetic. ApexCharts went dual-licensed
    at 5.x — 6.x carries a LicenseEnforcer that watermarks charts using premium
    features, and its terms bind on annual revenue. 4.7.0 is the last MIT
    release, and MIT is why this file can sit in a public repo. If you upgrade
    this library, read its LICENSE first; this test is the reminder.
    """
    lib = STATIC / "apexcharts.min.js"
    licence = STATIC / "apexcharts.LICENSE.txt"
    assert lib.is_file(), "the chart library is not vendored"
    assert licence.is_file(), "the chart library ships without its licence"

    text = licence.read_text()
    assert "MIT License" in text, "the vendored chart library is no longer MIT"
    assert "LicenseEnforcer" not in lib.read_text(errors="ignore"), \
        "this build carries the dual-license watermark enforcer"


def test_htmx_is_vendored_with_the_licence_it_ships_under():
    """The rule the typefaces and the chart library already follow, applied to
    the one vendored bundle that was exempt from it (#309, tranche 5).

    ⚠️ This is provenance, not compliance. htmx 2.x is **0BSD**, which asks for
    nothing in return — no notice, no attribution — so shipping the text beside
    the file was never an obligation and its absence broke no licence.

    It is here because htmx **changed licence across a major version**: 1.x was
    BSD-2-Clause, 2.x is 0BSD. That is the identical trap ApexCharts is guarded
    against two tests up (MIT through 4.7.0, dual-licensed at 5.x), and htmx had
    no version pinned anywhere and no licence text to compare a bump against.
    A vendored dependency whose terms you cannot look up without leaving the
    repo is one you will upgrade blind.
    """
    lib = STATIC / "htmx.min.js"
    licence = STATIC / "htmx.LICENSE.txt"
    assert lib.is_file(), "htmx is not vendored"
    assert licence.is_file(), "htmx ships without the licence it is used under"

    text = licence.read_text()
    assert "Zero-Clause BSD" in text, (
        "the vendored htmx licence is no longer the 0BSD text 2.x ships. "
        "1.x was BSD-2-Clause, which DOES require the notice be retained — if "
        "this is a downgrade, the redistribution terms changed with it."
    )


def test_the_retired_chart_library_is_gone():
    """Chart.js was replaced, not merely unreferenced — 208KB of dead JS in the
    image is 208KB every deploy ships and every browser may cache."""
    assert not (STATIC / "chart.umd.min.js").exists()


# --- the ledger's columns are addressed by name, not by index (#309, t5) ------


def test_history_headers_are_aligned_by_class_not_by_column_index(css_no_comments):
    """The stylesheet and the template have to agree about two columns, and a
    column INDEX is the one way of saying it that can go silently wrong.

    These rules were `.txn-table th:nth-child(4)` and `(8)` — right for Amount
    and Balance only while they are the 4th and 8th of the nine `<th>`s. Insert
    a column anywhere to their left and the stylesheet right-aligns two innocent
    headers over two left-aligned figures, which renders fine and is wrong.

    ⚠️ Not hypothetical, and not a fresh worry: #309's previous tranche added
    `test_history_row_shape.py` because the SAME table is filled POSITIONALLY on
    the Python side. This is the third place that table's column order is
    load-bearing, and it was the only one addressing it by number.

    ⚠️ Asserted against `css_no_comments` deliberately. The comment left in
    style.css explaining this change spells out `th:nth-child(4)`, so a scan of
    the raw stylesheet would match its own rationale and fail a correct file.
    """
    header_rules = re.findall(r"\.txn-table th[^,{]*", css_no_comments)
    assert header_rules, "nothing styles the history table's headers any more"

    positional = [r.strip() for r in header_rules if "nth-child" in r]
    assert not positional, (
        "the history table's headers are addressed by column index: "
        + ", ".join(positional)
        + ". The cells below them carry .c-amount/.c-bal; use the same classes "
        "so inserting a column cannot silently move the alignment."
    )

    # The other half of the agreement: the classes the sheet targets are really
    # on the header cells. Without this the test above passes on a stylesheet
    # whose selectors match nothing at all.
    history = (TEMPLATES / "history.html").read_text()
    thead = re.search(r"<thead>.*?</thead>", history, re.S)
    assert thead, "history.html has no <thead> to check"

    for cls, label in (("c-amount", "Amount"), ("c-bal", "Balance")):
        assert f'<th class="{cls}">{label}</th>' in thead.group(0), (
            f"history.html's {label} header does not carry .{cls}, so the "
            "stylesheet rule that right-aligns it matches nothing"
        )


# --- the AI material is one material (#225 follow-up) -------------------------

# Every surface where a model is the one speaking, and the element that must
# carry the shared material. Listed rather than discovered so that ADDING an AI
# surface without giving it the material fails here.
AI_SURFACES = (
    ("partials/_ask_panel.html", 'id="ask-panel"'),
    ("budgets.html", "ai-banner"),
)


@pytest.mark.parametrize("template,marker", AI_SURFACES,
                         ids=[t for t, _ in AI_SURFACES])
def test_every_ai_surface_uses_the_shared_material(template, marker):
    """#225's decision was that a model speaking is a change of MATERIAL, not a
    badge: Home's panel is a dark surface in both themes, like the hero. Goals
    and Budgets only ever got `border-left: 3px solid var(--accent-2)`, so one
    feature looked like two different things depending on the page.

    ⚠️ Asserted on the TEMPLATES, not on the stylesheet — the sharing is a
    class on an element, and a CSS-side check would pass while a card quietly
    stopped carrying it.
    """
    src = (TEMPLATES / template).read_text()
    # ⚠️ `class=` is required as well as the marker: _ask_panel.html's header
    # comment NAMES id="ask-panel" (telling you not to remove it), and matching
    # the first line containing the marker finds that comment, not the element.
    line = next((ln for ln in src.splitlines()
                 if marker in ln and "class=" in ln), None)
    assert line, f"{template}: no element matching {marker}"
    assert "ai-surface" in line, \
        f"{template}: the AI surface does not carry the shared material"


def test_the_shared_ai_material_exists_and_is_the_read_surface(css_no_comments):
    rule = re.search(r"\n\.ai-surface\s*\{([^}]*)\}", css_no_comments)
    assert rule, "no shared .ai-surface material"
    assert "--grad-read" in rule.group(1), \
        ".ai-surface does not use the read gradient Home established"


def test_the_ai_material_carries_its_own_ink(css_no_comments):
    """A dark surface in a light theme inherits light-theme ink, which is the
    silent way this breaks: the panel goes dark and the text stays near-black.
    ⚠️ `--text-muted` is the one that bites — it is used by the chevron, the
    facts strip and every timestamp inside these cards."""
    body = re.search(r"\n\.ai-surface\s*\{([^}]*)\}", css_no_comments).group(1)
    assert "--on-read" in body, ".ai-surface sets a background but not its ink"

    scoped = re.findall(r"\.ai-surface[^{}]*\{([^}]*)\}", css_no_comments)
    joined = " ".join(scoped)
    assert "--on-read-muted" in joined, \
        "nothing inside .ai-surface overrides the muted ink for a dark surface"


@pytest.mark.parametrize("template,marker", AI_SURFACES,
                         ids=[t for t, _ in AI_SURFACES])
def test_the_ai_badge_keeps_its_accent_on_the_dark_material(css_no_comments,
                                                            template, marker):
    """⚠️ A regression that actually happened while building this: a blanket
    `.ai-surface span { color: var(--on-read-muted) }` outranks `.ai-badge`
    (0,1,1 beats 0,1,0), so the badge rendered pale lilac ink on its own pale
    lilac pill — on Home too, which the change was not even meant to touch.
    Caught by reading computed styles in a browser, not by the suite.

    The property: nothing inside the AI material may capture the badge's ink.
    """
    blanket = re.search(
        r"\.ai-surface\s+span\s*\{([^}]*)\}", css_no_comments)
    assert not blanket, \
        "a blanket .ai-surface span rule will capture .ai-badge's colour"
    restore = re.search(r"\.ai-surface \.ai-badge\s*\{([^}]*)\}", css_no_comments)
    assert restore and "--accent-2" in restore.group(1), \
        "the badge does not keep the AI accent on the dark material"
