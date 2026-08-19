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
CSS_PATH = STATIC / "style.css"

# Colours that may legitimately appear as a literal outside the token blocks:
# pure white and pure black are not theme decisions, they are the ink on top of
# a saturated fill (a primary button's label) and never vary by mode.
LITERAL_ALLOWED = {"#fff", "#ffffff", "#000", "#000000"}

# Tokens whose whole purpose is to differ between light and dark. A token that
# exists only in :root renders the dark theme with a light-theme value — the
# failure is silent and looks like "dark mode is a bit off" rather than a bug.
THEME_TOKENS = (
    "--bg", "--surface", "--surface-2", "--surface-raise",
    "--text", "--text-muted", "--border",
    "--grad-hero", "--grad-accent",
    "--shadow-sm", "--shadow-md", "--shadow-lg",
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
    for needed in ("td", ".hero-net", ".stat-value", ".hero-stat-val"):
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


def test_the_retired_chart_library_is_gone():
    """Chart.js was replaced, not merely unreferenced — 208KB of dead JS in the
    image is 208KB every deploy ships and every browser may cache."""
    assert not (STATIC / "chart.umd.min.js").exists()


# --- the AI material is one material (#225 follow-up) -------------------------

# Every surface where a model is the one speaking, and the element that must
# carry the shared material. Listed rather than discovered so that ADDING an AI
# surface without giving it the material fails here.
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
AI_SURFACES = (
    ("partials/_ask_panel.html", 'id="ask-panel"'),
    ("partials/_goal_coach_card.html", 'id="goal-coach-card"'),
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
