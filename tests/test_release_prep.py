"""Tests for scripts/release_prep.py — the mechanical half of release prep (#154).

Release prep is the same short sequence of edits every time, and it has already
generated three near-identical tracking issues (#112, #127, #147). The parts that
are genuinely mechanical are automated here; the two that are **not** — choosing
the version number, and writing the user-facing prose — stay human, which is why
nothing in this file asserts anything about the wording of a changelog entry or a
What's-new block.

Everything of consequence is pure: `roll_changelog()`, `rewrite_strip()` and
`format_env_report()` take text and return text. `main()` is a thin writer over
them, and takes `--root` so it can be pointed at a throwaway tree — the same
shape as `scripts/seed_dev.py` (pure `build_seed_plan()`, thin `write_plan()`).

⚠️ The last two tests read the REAL `CHANGELOG.md` and `dashboard.html`. A parser
that matches the fixtures in this file but not the actual repo would pass
everything above them and still be useless on the day it is needed.
"""
import re
from datetime import date

import pytest

from scripts import release_prep

RELEASE_DATE = date(2026, 8, 10)

# Trimmed from the real CHANGELOG.md, including its genuinely stale link refs:
# `[Unreleased]` still compares from v0.4.1 and there is no [0.5.0] line at all,
# because cutting 0.5.0 by hand dropped that step.
CHANGELOG = """\
# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- **Settings now says when each background job last actually ran.** Some work
  happens on a schedule rather than when you click something.

## [0.5.0] - 2026-08-03

### Added

- **The Settings page now says which optional features are switched on.**

## [0.4.1] - 2026-07-31

### Added

- **In-app bug reports.**

[Unreleased]: https://github.com/CaddisMaster/budget-buddy/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/CaddisMaster/budget-buddy/compare/v0.4.0...v0.4.1
"""

# Trimmed from the real dashboard.html strip, keeping the three things that
# carry a version and one block of prose that must not be touched.
STRIP = """\
{% extends 'base.html' %}
{% block content %}
<h1>Home</h1>

<div class="card whatsnew" id="whatsnew" data-version="v0.5.0" hidden>
  <div class="whatsnew-head">
    <strong>What's new in v0.5.0</strong>
    <span class="recurring-badge">2026-08-03</span>
    <span class="whatsnew-actions">
      <button type="button" class="btn btn-sm" id="whatsnew-toggle">Details</button>
    </span>
  </div>
  <div class="whatsnew-body" hidden>
    <div class="whatsnew-block">
      <strong>Better guesses when sorting your spending</strong>
      <ul class="whatsnew-list">
        <li><strong>Auto-Categorize thinks harder:</strong> v0.5.0 was a good release.</li>
      </ul>
    </div>
  </div>
</div>
{% endblock %}
"""


# ── The version-string asymmetry ─────────────────────────────────────────────

def test_one_bare_version_in_two_output_conventions():
    """The caller passes `0.6.0` once; the two files spell it differently.

    The changelog heading is bare (`## [0.6.0]`), the strip is v-prefixed
    (`data-version="v0.6.0"`). Getting one right and the other wrong is exactly
    the half-forgotten step #154 exists to remove, so it is asserted first.
    """
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    strip = release_prep.rewrite_strip(STRIP, "0.6.0", RELEASE_DATE)

    assert "## [0.6.0] - 2026-08-10" in rolled
    assert "## [v0.6.0]" not in rolled

    assert 'data-version="v0.6.0"' in strip
    assert 'data-version="0.6.0"' not in strip


def test_a_v_prefixed_version_argument_is_accepted_too():
    """`v0.6.0` and `0.6.0` must mean the same thing — the tag carries the v."""
    bare = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    prefixed = release_prep.roll_changelog(CHANGELOG, "v0.6.0", RELEASE_DATE)
    assert bare == prefixed


# ── Rolling the changelog ────────────────────────────────────────────────────

def test_unreleased_content_moves_under_the_dated_heading():
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    body = rolled.split("## [0.6.0] - 2026-08-10")[1].split("## [0.5.0]")[0]
    assert "Settings now says when each background job last actually ran" in body
    assert "### Added" in body


def test_a_fresh_empty_unreleased_is_left_at_the_top():
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    assert "## [Unreleased]" in rolled
    # Nothing between the new Unreleased heading and the dated one it precedes.
    between = rolled.split("## [Unreleased]")[1].split("## [0.6.0]")[0]
    assert between.strip() == ""


def test_the_new_heading_is_followed_by_a_blank_line():
    """The seam between heading and body, not just its contents (#174).

    The first real outing wrote `## [0.6.0] - 2026-08-10` with `### Added` on
    the very next line, making the newest entry the only one in the file that
    did not match the others. `test_unreleased_content_moves_under_the_dated_
    heading` passed throughout, because asserting a substring *is present* says
    nothing about the shape around it.
    """
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    assert "## [0.6.0] - 2026-08-10\n\n### Added" in rolled


def test_the_new_entry_is_spaced_like_the_ones_already_in_the_file():
    """Stated as a comparison rather than a literal, so it keeps holding if the
    file's conventions ever change."""
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    new_seam = rolled.split("## [0.6.0] - 2026-08-10")[1][:2]
    existing_seam = rolled.split("## [0.5.0] - 2026-08-03")[1][:2]
    assert new_seam == existing_seam


def test_the_released_content_appears_once_not_twice():
    """A roll that copies rather than moves would leave the entry duplicated."""
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    assert rolled.count("Settings now says when each background job") == 1


def test_earlier_releases_are_untouched():
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    assert "## [0.5.0] - 2026-08-03" in rolled
    assert "## [0.4.1] - 2026-07-31" in rolled


# ── Link references ──────────────────────────────────────────────────────────

def test_the_new_version_gains_a_link_ref():
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    assert ("[0.6.0]: https://github.com/CaddisMaster/budget-buddy/compare/"
            "v0.5.0...v0.6.0") in rolled


def test_unreleased_ref_is_repointed_at_the_new_tag():
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    assert ("[Unreleased]: https://github.com/CaddisMaster/budget-buddy/compare/"
            "v0.6.0...HEAD") in rolled
    assert "compare/v0.4.1...HEAD" not in rolled


def test_the_previous_version_comes_from_the_headings_not_the_stale_ref():
    """The repair, and the reason this is worth automating at all.

    The fixture's `[Unreleased]` ref says v0.4.1 while the newest release is
    actually 0.5.0 — real drift, copied from the repo as it stands today. A tool
    that derived "previous" from that ref would compare 0.6.0 against the wrong
    tag and silently propagate the error forward. The version headings are the
    authority; the refs are the thing being corrected.
    """
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    assert "compare/v0.5.0...v0.6.0" in rolled
    assert "compare/v0.4.1...v0.6.0" not in rolled


def test_a_missing_link_ref_for_an_earlier_release_is_added_back():
    """0.5.0 has no ref line in the fixture because cutting it by hand dropped
    one. Rolling forward should leave the ref block complete rather than
    carrying the gap into the next release."""
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    assert ("[0.5.0]: https://github.com/CaddisMaster/budget-buddy/compare/"
            "v0.4.1...v0.5.0") in rolled


# ── Trailing newlines (#203) ─────────────────────────────────────────────────
#
# `_LINK_REF_RE` ends in `\s*$`, and `\s*` matches newlines — so on the LAST ref
# line in the file it swallows the file's trailing newlines into the captured
# text. `roll_changelog` preserves the oldest version's ref verbatim, then adds
# one more newline of its own, so the tail grows by one every single run.
#
# ⚠️ These assert the END of the file only. The spacing BETWEEN entries is
# pinned by test_the_new_entry_is_spaced_like_the_ones_already_in_the_file,
# which exists because #174 was exactly a whitespace regression here.

def _trailing_newlines(text):
    return len(text) - len(text.rstrip("\n"))


def test_rolling_does_not_add_a_trailing_blank_line():
    """A file ending in a single newline after its last link reference should
    still end in a single newline after rolling."""
    tidy = CHANGELOG.rstrip("\n") + "\n"
    rolled = release_prep.roll_changelog(tidy, "0.6.0", RELEASE_DATE)
    assert _trailing_newlines(rolled) == 1


def test_repeated_releases_do_not_accumulate_blank_lines():
    """The defect compounds — 2 → 3 → 4 → 5 → 6 across four cycles measured
    against the real file. Refill `[Unreleased]` between rolls the way a real
    cycle does, so each iteration is a genuine release rather than a refusal."""
    refill = "## [Unreleased]\n\n### Added\n\n- A new thing.\n"
    current = CHANGELOG.rstrip("\n") + "\n"
    counts = []
    for version in ("0.6.0", "0.7.0", "0.8.0", "0.9.0"):
        current = release_prep.roll_changelog(current, version, RELEASE_DATE)
        counts.append(_trailing_newlines(current))
        current = current.replace("## [Unreleased]\n", refill, 1)
    assert counts == [1, 1, 1, 1], f"tail grew across cycles: {counts}"


def test_a_file_already_carrying_extra_blank_lines_is_not_made_worse():
    """Rolling a file that already ends in several blank lines must not add
    another — the tool stops the growth rather than perpetuating it."""
    padded = CHANGELOG.rstrip("\n") + "\n\n\n\n"
    rolled = release_prep.roll_changelog(padded, "0.6.0", RELEASE_DATE)
    assert _trailing_newlines(rolled) <= _trailing_newlines(padded)


def test_the_rest_of_the_roll_is_unchanged_apart_from_the_final_newlines():
    """The fix must touch the end of the file and nothing else: the dated
    heading, the moved content and the link-reference block stay byte-identical
    to what a differently-padded input produces."""
    tidy = CHANGELOG.rstrip("\n") + "\n"
    padded = CHANGELOG.rstrip("\n") + "\n\n\n\n"
    a = release_prep.roll_changelog(tidy, "0.6.0", RELEASE_DATE)
    b = release_prep.roll_changelog(padded, "0.6.0", RELEASE_DATE)
    assert a.rstrip("\n") == b.rstrip("\n")


def test_the_real_changelog_does_not_grow_a_newline():
    """⚠️ Against the REAL file, which is where this was found. The fixture
    above is trimmed, and a fix that satisfies it without handling the real
    file's tail would be useless on the day the release is cut."""
    real = (release_prep.__file__.rsplit("/scripts/", 1)[0] + "/CHANGELOG.md")
    with open(real, encoding="utf-8") as fh:
        text = fh.read()
    rolled = release_prep.roll_changelog(text, "0.7.0", RELEASE_DATE)
    assert _trailing_newlines(rolled) == 1


# ── Refusals ─────────────────────────────────────────────────────────────────

def test_an_empty_unreleased_section_refuses():
    """Cutting a release with nothing in it is always a mistake."""
    empty = CHANGELOG.replace(
        """### Added

- **Settings now says when each background job last actually ran.** Some work
  happens on a schedule rather than when you click something.
""",
        "",
    )
    with pytest.raises(release_prep.ReleasePrepError) as exc:
        release_prep.roll_changelog(empty, "0.6.0", RELEASE_DATE)
    assert "unreleased" in str(exc.value).lower()


def test_rolling_the_same_version_twice_refuses():
    rolled = release_prep.roll_changelog(CHANGELOG, "0.6.0", RELEASE_DATE)
    with pytest.raises(release_prep.ReleasePrepError) as exc:
        release_prep.roll_changelog(rolled, "0.6.0", RELEASE_DATE)
    assert "0.6.0" in str(exc.value)


def test_a_changelog_with_no_unreleased_heading_refuses():
    with pytest.raises(release_prep.ReleasePrepError):
        release_prep.roll_changelog("# Changelog\n\nnothing here\n",
                                    "0.6.0", RELEASE_DATE)


def test_html_with_no_strip_refuses():
    with pytest.raises(release_prep.ReleasePrepError):
        release_prep.rewrite_strip("<html><body>no strip</body></html>",
                                   "0.6.0", RELEASE_DATE)


# ── Rewriting the What's-new strip ───────────────────────────────────────────

def test_strip_version_heading_and_badge_all_move():
    """All three, or the strip is half-updated — which is the documented trap."""
    out = release_prep.rewrite_strip(STRIP, "0.6.0", RELEASE_DATE)
    assert 'data-version="v0.6.0"' in out
    assert "<strong>What's new in v0.6.0</strong>" in out
    assert '<span class="recurring-badge">2026-08-10</span>' in out
    assert "v0.5.0" not in out.split("whatsnew-body")[0]


def test_strip_prose_is_left_byte_identical():
    """The load-bearing one. The blocks are human-written and the tool must not
    touch them — not the wording, not a version named inside the prose. A tool
    that rewrote copy would be a defect, not a convenience."""
    out = release_prep.rewrite_strip(STRIP, "0.6.0", RELEASE_DATE)
    block_before = STRIP.split('<div class="whatsnew-body" hidden>')[1]
    block_after = out.split('<div class="whatsnew-body" hidden>')[1]
    assert block_before == block_after
    # Including the v0.5.0 that appears inside the prose itself.
    assert "v0.5.0 was a good release" in out


def test_everything_outside_the_strip_is_untouched():
    out = release_prep.rewrite_strip(STRIP, "0.6.0", RELEASE_DATE)
    assert out.startswith("{% extends 'base.html' %}")
    assert out.rstrip().endswith("{% endblock %}")


# ── The environment-variable report ──────────────────────────────────────────

def test_no_new_variables_is_stated_not_implied():
    """#154 is explicit: silence and 'nothing to do' must not look the same.
    This is the case 0.6.0 actually hits — .env.example is unchanged since
    v0.5.0 — so an empty report is the normal path, not an edge case."""
    report = release_prep.format_env_report([])
    assert report.strip()
    assert "no new environment variables" in report.lower()


def test_each_new_variable_is_named():
    report = release_prep.format_env_report(["FEEDBACK_GITHUB_TOKEN", "NEW_THING"])
    assert "FEEDBACK_GITHUB_TOKEN" in report
    assert "NEW_THING" in report


def test_the_report_says_the_variable_must_be_set_before_deploying():
    """The failure this guards is a gated feature deploying invisible — which
    is indistinguishable from that feature being broken."""
    report = release_prep.format_env_report(["FEEDBACK_GITHUB_TOKEN"])
    assert "droplet" in report.lower() or "server" in report.lower()


def test_undetermined_is_a_third_state_not_silence():
    """`None` means "could not tell" and must not read as "nothing new".

    Same three-state shape as `admin.integration_status()`, and for the same
    reason: collapsing "I don't know" into the reassuring answer is how a check
    goes quietly useless. A tool that cannot reach git must say so.
    """
    report = release_prep.format_env_report(None)
    assert report.strip()
    assert "no new environment variables" not in report.lower()
    assert "could not" in report.lower() or "unable" in report.lower()


# ── Working out what actually changed ────────────────────────────────────────

def test_the_tool_is_never_told_which_variables_are_new():
    """The load-bearing one for this section.

    A flag that takes the answer is worse than no check at all: anyone who
    remembers to pass `--env-var FEEDBACK_GITHUB_TOKEN` did not need to be told.
    The entire value is surfacing the variable you FORGOT, so the tool must work
    it out itself. Stated as the absence of an argument, so re-adding one is a
    visible regression rather than a quiet reopening — the same device as
    `test_release_announce.py::test_the_builder_takes_no_release_text`.
    """
    parser_args = release_prep.build_parser()._option_string_actions
    assert "--env-var" not in parser_args
    assert "--env-vars" not in parser_args


def test_previous_tag_comes_from_the_newest_version_heading():
    assert release_prep.previous_tag(CHANGELOG) == "v0.5.0"


# The real case the check was built for: FEEDBACK_GITHUB_TOKEN entered
# .env.example between v0.3.1 and v0.4.0. Without that check #64 would have
# deployed completely invisible — the feature simply absent, indistinguishable
# from broken.
ENV_BEFORE = """\
# Database
DB_HOST=db
DB_PASSWORD=change-me

# Optional integrations
ANTHROPIC_API_KEY=
"""

ENV_AFTER = """\
# Database
DB_HOST=db
DB_PASSWORD=rotated-value

# Optional integrations
ANTHROPIC_API_KEY=

# In-app feedback (#64). Deliberately NOT named GITHUB_TOKEN.
FEEDBACK_GITHUB_TOKEN=
"""


def test_new_env_vars_finds_an_added_variable():
    assert release_prep.new_env_vars(ENV_BEFORE, ENV_AFTER) == [
        "FEEDBACK_GITHUB_TOKEN"
    ]


def test_new_env_vars_ignores_a_changed_value():
    """DB_PASSWORD's value differs between the two fixtures. A variable that
    already existed is not new, however much its line changed — reporting it
    would train the reader to ignore the output."""
    assert "DB_PASSWORD" not in release_prep.new_env_vars(ENV_BEFORE, ENV_AFTER)


def test_new_env_vars_ignores_comments_and_blank_lines():
    """The added block carries a comment naming a variable in prose. Only real
    assignments count."""
    names = release_prep.new_env_vars(ENV_BEFORE, ENV_AFTER)
    assert names == ["FEEDBACK_GITHUB_TOKEN"]
    assert "GITHUB_TOKEN" not in names


def test_new_env_vars_is_empty_when_nothing_was_added():
    assert release_prep.new_env_vars(ENV_BEFORE, ENV_BEFORE) == []


def test_changed_env_vars_composes_the_seam_with_the_pure_part(monkeypatch):
    """`_read_env_example_at()` is the ONE subprocess seam — the same shape as
    `ai.py`'s `_call_*_model` seams, and for the same reason: it is stubbed
    here, so no test in this file needs git.

    ⚠️ That is not merely tidiness. The suite runs INSIDE the web container,
    which deliberately carries no git binary (#80 removed the dev container and
    with it the reason git was ever installed). A test that shelled out to git
    could never pass in the place the suite actually runs.
    """
    seen = []

    def fake_read(rev, root):
        seen.append(rev)
        return ENV_BEFORE if rev == "v0.3.1" else ENV_AFTER

    monkeypatch.setattr(release_prep, "_read_env_example_at", fake_read)
    names = release_prep.changed_env_vars(release_prep.REPO_ROOT,
                                          "v0.3.1", "v0.4.0")
    assert names == ["FEEDBACK_GITHUB_TOKEN"]
    assert seen == ["v0.3.1", "v0.4.0"]


def test_changed_env_vars_returns_none_when_the_seam_fails(monkeypatch):
    """No git, an unknown tag, a detached checkout — all the same answer:
    "could not tell". Returning [] here would be a lie in the reassuring
    direction, which is the one direction that matters."""
    def boom(rev, root):
        raise OSError("git: not found")

    monkeypatch.setattr(release_prep, "_read_env_example_at", boom)
    assert release_prep.changed_env_vars(release_prep.REPO_ROOT,
                                         "v0.3.1", "v0.4.0") is None


def test_the_real_seam_is_honest_about_a_missing_git(tmp_path):
    """The one test that exercises the real seam. It asserts only that the
    unknown case degrades to None rather than raising or lying — true whether
    or not git exists wherever this runs, which is what makes it safe both in
    the container and on a developer's machine."""
    assert release_prep.changed_env_vars(tmp_path, "v0.3.1", "v0.4.0") is None


# ── The thin writer ──────────────────────────────────────────────────────────

@pytest.fixture
def fake_repo(tmp_path):
    (tmp_path / "app" / "templates").mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "app" / "templates" / "dashboard.html").write_text(STRIP)
    return tmp_path


def test_dry_run_writes_nothing(fake_repo):
    before = ((fake_repo / "CHANGELOG.md").read_text(),
              (fake_repo / "app/templates/dashboard.html").read_text())
    release_prep.main(["--version", "0.6.0", "--date", "2026-08-10",
                       "--root", str(fake_repo), "--dry-run"])
    after = ((fake_repo / "CHANGELOG.md").read_text(),
             (fake_repo / "app/templates/dashboard.html").read_text())
    assert before == after


def test_main_writes_both_files(fake_repo):
    release_prep.main(["--version", "0.6.0", "--date", "2026-08-10",
                       "--root", str(fake_repo)])
    assert "## [0.6.0] - 2026-08-10" in (fake_repo / "CHANGELOG.md").read_text()
    assert 'data-version="v0.6.0"' in (
        fake_repo / "app/templates/dashboard.html").read_text()


def test_main_refuses_without_writing_a_partial_result(fake_repo):
    """Both edits land or neither does. A run that rolled the changelog and then
    failed on the strip would leave the tree in a state needing manual repair,
    which is worse than not having run it."""
    (fake_repo / "app/templates/dashboard.html").write_text("<p>no strip</p>")
    with pytest.raises(release_prep.ReleasePrepError):
        release_prep.main(["--version", "0.6.0", "--date", "2026-08-10",
                           "--root", str(fake_repo)])
    assert "## [0.6.0]" not in (fake_repo / "CHANGELOG.md").read_text()
    assert "## [Unreleased]" in (fake_repo / "CHANGELOG.md").read_text()


# ── The real files ───────────────────────────────────────────────────────────
#
# ⚠️ These two are SKIPPED when the file they read is absent, and the reason is
# not laziness (#176). `.dockerignore` excludes `*.md`, so `CHANGELOG.md` is
# genuinely and deliberately missing from the shipped image — and CI runs this
# whole suite INSIDE that image whenever the Dockerfile or requirements change.
# Failing there would assert the image is wrong when it is right.
#
# It is worth knowing why this was not caught for an hour. Two masks, stacked:
#
#   1. It cannot fail locally. `docker-compose.override.yml` bind-mounts the
#      source over the image's COPY, so the file IS there in the dev container.
#      `./test.sh` passes on any developer machine regardless of .dockerignore.
#   2. It cannot fail on most PRs. The in-image step only runs when the
#      Dockerfile or requirements change, so the PR that introduced these tests
#      never ran them in the image at all.
#
# So the guard that exists to catch "the shipped image differs from dev" was
# itself skipped, while the bind mount hid the very difference it guards. The
# first dependency bump afterwards went red on a test that had nothing to do
# with it.
REAL_CHANGELOG = release_prep.REPO_ROOT / "CHANGELOG.md"
REAL_DASHBOARD = release_prep.REPO_ROOT / "app/templates/dashboard.html"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"


@pytest.mark.skipif(not REAL_CHANGELOG.exists(), reason=_NOT_IN_IMAGE)
def test_it_parses_the_real_changelog():
    """A parser tuned to the fixtures above and not to the actual file would
    pass every test in this module and still fail on the day it is used.

    ⚠️ Deliberately asserts nothing about WHICH version is newest, and seeds its
    own `[Unreleased]` entry. The first draft did neither, and both were traps:
    `roll_changelog()` correctly leaves an EMPTY `[Unreleased]` behind, so the
    moment the tool was used for real this test would have refused — failing on
    the release commit itself, looking exactly like a parser regression. Assert
    the parser copes with the real *format*; never with today's contents.
    """
    text = REAL_CHANGELOG.read_text()

    previous = release_prep.previous_tag(text)
    assert re.fullmatch(r"v\d+\.\d+\.\d+", previous)

    seeded = text.replace(
        "## [Unreleased]\n",
        "## [Unreleased]\n\n### Added\n\n- A seeded entry, so this test does not\n"
        "  depend on whether a release was just cut.\n",
        1,
    )
    rolled = release_prep.roll_changelog(seeded, "9.9.9", RELEASE_DATE)
    assert "## [9.9.9] - 2026-08-10" in rolled
    # Derived from the file, never hardcoded — see the warning above.
    assert f"compare/{previous}...v9.9.9" in rolled


@pytest.mark.skipif(not REAL_DASHBOARD.exists(), reason=_NOT_IN_IMAGE)
def test_it_parses_the_real_dashboard_strip():
    """Same rule: the property, not the prose.

    The first draft asserted a literal sentence from the current strip, which
    the next release replaces — so cutting any release would have turned this
    red for a reason unconnected to the parser.

    Carries the same guard as its sibling even though templates ARE shipped in
    the image — relying on "`.dockerignore` happens not to exclude this one"
    is exactly the kind of accident that stops being true quietly.
    """
    html = REAL_DASHBOARD.read_text()
    out = release_prep.rewrite_strip(html, "9.9.9", RELEASE_DATE)

    assert 'data-version="v9.9.9"' in out
    assert "<strong>What's new in v9.9.9</strong>" in out

    # Whatever the blocks currently say, they come through byte-identical.
    before = html.split('<div class="whatsnew-body"')[1]
    after = out.split('<div class="whatsnew-body"')[1]
    assert before == after
