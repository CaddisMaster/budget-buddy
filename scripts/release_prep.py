#!/usr/bin/env python3
"""Automate the mechanical parts of release prep (#154).

Cutting a release does the same short sequence of edits every time — roll the
`CHANGELOG.md` `[Unreleased]` section under a dated heading, repoint its link
references at the new tag, and move the version/date in the dashboard's
What's-new strip — and by hand it has already generated three near-identical
tracking issues (#112, #127, #147) for slipping one of those steps. This
script does the mechanical half. It deliberately does **not** choose the
version number or write any user-facing prose: those stay a human call.

Deliberately standalone, same reasoning as ``scripts/migrate.py`` and
``scripts/seed_dev.py``: no database, no Flask, no app import, standard
library only, so it can run — and be tested — with nothing else up.

The split below matters for testing: ``roll_changelog()``, ``rewrite_strip()``
and ``format_env_report()`` are **pure** — text in, text out, no filesystem.
``main()`` is the only thing that touches disk, and reads BOTH inputs and
computes BOTH outputs before writing either — a run that rolled the changelog
and then failed on the strip would leave the tree needing manual repair,
which is worse than not having run it at all.

Usage::

    python scripts/release_prep.py --version 0.6.0
    python scripts/release_prep.py --version 0.6.0 --date 2026-08-10
    python scripts/release_prep.py --version 0.6.0 --dry-run
"""

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class ReleasePrepError(Exception):
    """Raised for every refusal — a malformed input, or a step that would
    silently do the wrong thing rather than the intended one."""


# ── Shared helpers ────────────────────────────────────────────────────────────

def _normalize_version(version):
    """`0.6.0` and `v0.6.0` mean the same thing — the tag carries the `v`,
    nothing else does. Normalise once, at the boundary, to the bare form."""
    v = version.strip()
    if v[:1] in ('v', 'V'):
        v = v[1:]
    return v


# ── Rolling the changelog ────────────────────────────────────────────────────

# A version heading, e.g. "## [Unreleased]" or "## [0.5.0] - 2026-08-03".
# Deliberately requires the brackets, so "## Prior history" (no version) never
# matches and is carried through untouched as part of whichever section it
# trails.
# ⚠️ The trailing class is `[ \t]*`, NOT `\s*` (#174). Under re.MULTILINE `$`
# matches before a newline, but `\s*` is greedy and swallows it anyway — along
# with the blank line after it. The section body was then reassembled one
# newline short, so the new entry was the only one in the file with no blank
# line under its heading. Keep the whitespace anchored to the line.
_VERSION_HEADING_RE = re.compile(
    r'^## \[(?P<version>[^\]]+)\](?:[ \t]*-[ \t]*(?P<date>.+?))?[ \t]*$',
    re.MULTILINE,
)

# A changelog link reference, e.g.
# "[0.4.1]: https://github.com/CaddisMaster/budget-buddy/compare/v0.4.0...v0.4.1"
# Anchored to a real URL so it never matches an inline markdown link elsewhere
# in the prose.
_LINK_REF_RE = re.compile(
    r'^\[(?P<key>[^\]]+)\]:\s*(?P<url>https?://\S+)\s*$',
    re.MULTILINE,
)

_GITHUB_REPO_RE = re.compile(r'(https://github\.com/[^/]+/[^/]+)')


def _extract_link_refs(text):
    """Pull every link-reference line out of `text`, returning the remaining
    text (refs removed) and a dict of version/key -> the original, full ref
    line — kept verbatim so the oldest entry (which is not a compare link,
    see roll_changelog) can be preserved untouched."""
    refs = {}
    for m in _LINK_REF_RE.finditer(text):
        refs[m.group('key')] = m.group(0)
    cleaned = _LINK_REF_RE.sub('', text)
    return cleaned, refs


def _repo_url(refs):
    for line in refs.values():
        m = _GITHUB_REPO_RE.search(line)
        if m:
            return m.group(1)
    raise ReleasePrepError(
        'could not determine the repo URL — no github.com link reference found'
    )


def roll_changelog(text, version, release_date):
    """Move the `[Unreleased]` section under a new dated heading, leave a
    fresh empty `[Unreleased]` above it, and repair the link-reference block
    at the bottom so every version heading has one comparing against the
    next-oldest release.

    Pure: takes the current file contents and returns the new contents.
    Raises `ReleasePrepError` rather than producing a wrong or partial
    changelog — an empty Unreleased section, a version already released, or
    a file with no Unreleased heading at all.
    """
    bare = _normalize_version(version)
    date_str = release_date.isoformat()

    cleaned, refs = _extract_link_refs(text)
    matches = list(_VERSION_HEADING_RE.finditer(cleaned))

    if not matches or matches[0].group('version') != 'Unreleased':
        raise ReleasePrepError(
            "CHANGELOG.md has no '## [Unreleased]' heading to roll"
        )

    existing_versions = [m.group('version') for m in matches[1:]]
    if bare in existing_versions:
        raise ReleasePrepError(f'{bare} already has a changelog entry')

    unreleased_end = matches[0].end()
    next_start = matches[1].start() if len(matches) > 1 else len(cleaned)
    unreleased_body = cleaned[unreleased_end:next_start]

    if not unreleased_body.strip():
        raise ReleasePrepError(
            'the Unreleased section is empty — nothing to release'
        )

    preamble = cleaned[:matches[0].start()]
    rest = cleaned[next_start:]

    new_section = f'## [Unreleased]\n\n## [{bare}] - {date_str}' + unreleased_body
    body = (preamble + new_section + rest).rstrip() + '\n'

    repo = _repo_url(refs)
    versions_in_order = [bare] + existing_versions

    ref_lines = [f'[Unreleased]: {repo}/compare/v{bare}...HEAD']
    for i, ver in enumerate(versions_in_order):
        if i < len(versions_in_order) - 1:
            prev = versions_in_order[i + 1]
            ref_lines.append(f'[{ver}]: {repo}/compare/v{prev}...v{ver}')
        else:
            # The oldest version in the document has no predecessor to
            # compare against — preserve whatever ref it already had
            # (in the real file, a releases/tag/ URL) rather than inventing
            # a compare link for it.
            existing = refs.get(ver)
            if existing:
                ref_lines.append(existing)

    return body + '\n' + '\n'.join(ref_lines) + '\n'


def previous_tag(text):
    """The newest ALREADY-RELEASED version heading in the changelog, v-prefixed.

    Pure — reads the changelog exactly the way `roll_changelog()` does, so the
    two never disagree about which release is "the last one". Raises if the
    changelog carries no released version at all (only `[Unreleased]`).
    """
    matches = list(_VERSION_HEADING_RE.finditer(text))
    releases = [m.group('version') for m in matches
                if m.group('version') != 'Unreleased']
    if not releases:
        raise ReleasePrepError(
            'CHANGELOG.md has no released version heading to compare against'
        )
    return f'v{releases[0]}'


# ── Rewriting the What's-new strip ───────────────────────────────────────────

_DATA_VERSION_RE = re.compile(r'data-version="v?[0-9]+(?:\.[0-9]+)*"')
_STRONG_HEADING_RE = re.compile(
    r"<strong>What's new in v?[0-9]+(?:\.[0-9]+)*</strong>"
)
_BADGE_RE = re.compile(r'<span class="recurring-badge">[^<]*</span>')


def rewrite_strip(html, version, release_date):
    """Move the version and date carried by the What's-new strip
    (`app/templates/dashboard.html`) — its `data-version` attribute, the
    version named in its heading, and the release-date badge — leaving
    everything else, especially the human-written `.whatsnew-block` prose
    inside `.whatsnew-body`, byte-identical.

    Only edits the strip's head (everything before `.whatsnew-body`), which is
    what keeps the prose untouched even though a version number can appear
    inside it too (e.g. a block praising a past release by name).
    """
    bare = _normalize_version(version)
    v_version = f'v{bare}'
    date_str = release_date.isoformat()

    if 'id="whatsnew"' not in html or 'whatsnew-body' not in html:
        raise ReleasePrepError(
            "no What's-new strip found (no id=\"whatsnew\" / whatsnew-body)"
        )

    head, marker, tail = html.partition('whatsnew-body')

    head, count = _DATA_VERSION_RE.subn(
        f'data-version="{v_version}"', head, count=1
    )
    if not count:
        raise ReleasePrepError(
            "no data-version attribute found in the What's-new strip"
        )

    head, count = _STRONG_HEADING_RE.subn(
        f"<strong>What's new in {v_version}</strong>", head, count=1
    )
    if not count:
        raise ReleasePrepError(
            "no \"What's new in vX.Y.Z\" heading found in the What's-new strip"
        )

    head, count = _BADGE_RE.subn(
        f'<span class="recurring-badge">{date_str}</span>', head, count=1
    )
    if not count:
        raise ReleasePrepError(
            "no release-date badge found in the What's-new strip"
        )

    return head + marker + tail


# ── Working out what actually changed ────────────────────────────────────────
# The tool must work this out itself rather than being told — a flag that
# takes the answer (`--env-var NAME`) is worse than no check at all, because
# only someone who already remembered the variable would think to pass it.

# A real `NAME=value` assignment — not a comment, not a blank line, and not a
# variable merely mentioned in a comment's prose.
_ENV_ASSIGN_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=', re.MULTILINE)


def _assigned_names(text):
    return [m.group(1) for m in _ENV_ASSIGN_RE.finditer(text)]


def new_env_vars(old_text, new_text):
    """Names ASSIGNED in `new_text` that were not assigned in `old_text`.

    Pure. A variable whose value changed (e.g. a rotated password) is not
    new — only its first appearance as an assignment is. Returned in the
    order they first appear in `new_text`.
    """
    old_names = set(_assigned_names(old_text))
    added = []
    seen = set()
    for name in _assigned_names(new_text):
        if name not in old_names and name not in seen:
            seen.add(name)
            added.append(name)
    return added


def _read_env_example_at(rev, root):
    """The ONE subprocess seam. `.env.example` as it stood at `rev`, via
    `git show {rev}:.env.example` run inside `root`.

    Raises on any failure (no git binary, unknown rev, `root` not a repo) —
    `changed_env_vars()` is what turns that into the "could not tell" answer.
    """
    result = subprocess.run(
        ['git', 'show', f'{rev}:.env.example'],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout


def changed_env_vars(root, previous, ref):
    """Thin composition of the seam and the pure diff. Calls the seam for
    `previous` THEN `ref` (in that order), and returns `None` — never `[]` —
    if the seam fails for any reason at all.

    A missing git binary, an unknown tag, and a checkout with no `.git` all
    mean the same thing here: "cannot tell", not "nothing changed". Reporting
    `[]` would be a lie in the reassuring direction, which is the one
    direction that matters.
    """
    try:
        old_text = _read_env_example_at(previous, root)
        new_text = _read_env_example_at(ref, root)
    except Exception:
        return None
    return new_env_vars(old_text, new_text)


# ── The environment-variable report ──────────────────────────────────────────

def format_env_report(names):
    """State, rather than imply, whether any new environment variable needs
    setting on the Droplet before this release deploys. #154 is explicit that
    silence and "nothing to do" must not look the same — a gated feature whose
    variable is unset deploys completely invisible, indistinguishable from
    that feature being broken.

    `names` carries a THIRD state, `None`, for "could not tell" (git was
    unreachable) — same three-state shape as `admin.integration_status()`,
    and for the same reason: collapsing "I don't know" into the reassuring
    answer is how a check goes quietly useless.
    """
    if names is None:
        return (
            'Could not tell whether any new environment variables were added '
            'since the last release (git was unreachable) — check '
            '.env.example by hand before deploying.\n'
        )

    if not names:
        return 'No new environment variables since the last release.\n'

    lines = ['New environment variable(s) since the last release:\n']
    for name in names:
        lines.append(
            f'  - {name} — must be set on the Droplet (server) before '
            'deploying this release.\n'
        )
    return ''.join(lines)


# ── The thin writer ──────────────────────────────────────────────────────────

def build_parser():
    """Factored out of `main()` so a test can inspect it directly — e.g. to
    assert `--env-var` does NOT exist (see the module docstring: a flag that
    takes the answer defeats the whole check)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True,
                        help='the release version, e.g. 0.6.0 or v0.6.0')
    parser.add_argument('--date', default=None,
                        help='ISO release date (default: today)')
    parser.add_argument('--root', default=None,
                        help='repo root to operate on (default: this repo)')
    parser.add_argument('--dry-run', action='store_true',
                        help='show what would change, write nothing')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    root = Path(args.root) if args.root else REPO_ROOT
    release_date = date.fromisoformat(args.date) if args.date else date.today()

    changelog_path = root / 'CHANGELOG.md'
    strip_path = root / 'app' / 'templates' / 'dashboard.html'

    changelog_text = changelog_path.read_text()
    strip_text = strip_path.read_text()

    # Compute BOTH new texts before writing EITHER file — see the module
    # docstring. If either raises, nothing on disk has been touched yet.
    rolled = roll_changelog(changelog_text, args.version, release_date)
    rewritten = rewrite_strip(strip_text, args.version, release_date)

    # Work out what's new ourselves, rather than being told — see
    # new_env_vars()'s docstring. `previous_tag()` reads the SAME changelog
    # already loaded above; `ref='HEAD'` is the about-to-be-released state,
    # since the new tag does not exist yet at prep time.
    previous = previous_tag(changelog_text)
    names = changed_env_vars(root, previous, 'HEAD')
    report = format_env_report(names)

    if args.dry_run:
        print(f'[dry-run] would write {changelog_path}')
        print(f'[dry-run] would write {strip_path}')
        print()
        print(report)
        return 0

    changelog_path.write_text(rolled)
    strip_path.write_text(rewritten)

    print(f'Wrote {changelog_path}')
    print(f'Wrote {strip_path}')
    print()
    print(report)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ReleasePrepError as exc:
        print(f'error: {exc}', file=sys.stderr)
        sys.exit(1)
