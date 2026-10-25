"""Blueprints import services, never each other; services import no blueprint (#489).

`app/blueprints/` used to hold the routes AND most of the domain logic, so the
blueprints imported each other (`main` reached into seven of them) and seven
`# lazy` function-level imports existed only to dodge the cycles that made.
The domain logic is moving into `app/services/` one piece per PR. These tests
hold the line while it does.

Each rule has an allowlist of the edges that predate it, and the allowlist is
checked in BOTH directions:

* an edge that is not listed fails — nothing new may join the tangle;
* a listed edge that no longer exists fails too — so a PR that removes one has
  to delete its line, and the list can only shrink. Without this half the list
  would stay long after the code got better, and stop meaning anything.

Each acceptance criterion was claimed only once its allowlist was EMPTY,
because until then the test did not prove it: `ALLOWED_LAZY` emptied in part 8
and `ALLOWED_EDGES` in part 12. Both are empty now, so these tests ARE the
criteria, and the two-direction check keeps them honest if either ever grows.

Each detector is proven to fire on a doctored snippet first, because a scan
that matches nothing passes against any tree.
"""
import ast
import re
from collections import Counter
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
LAYERS = ("blueprints", "services")

# "<importer> -> <imported>", both as "<layer>.<module>". Started at 28 edges
# on 2026-10-24; part 12 of #489 removed the last. Keep it empty: a new edge
# means moving the shared code into services/, not adding a line here.
ALLOWED_EDGES = set()

# Files under app/ and how many "# lazy" markers each holds. Empty since
# #489 part 8, which moved the due-runners: keep it that way.
ALLOWED_LAZY = {}

LAZY = re.compile(r"#\s*lazy\b")


def imported_modules(source, package):
    """Every `app.*` module `source` imports, at any depth, as a dotted name.

    `package` is the dotted package the source lives in (`app.blueprints`), so
    relative imports resolve. `from app.blueprints import main` yields
    `app.blueprints.main`: the imported NAME may be a module.
    """
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[:len(base) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return {m for m in found if m.startswith("app.")}


def edges_from(source, layer, name, existing):
    """`"<layer>.<name> -> blueprints.<x>"` for each other blueprint imported.

    `existing` is the set of blueprint module names, so `from app.blueprints.
    main import PALETTE_SIZE` counts once (as `main`) and not again as a
    module called `PALETTE_SIZE`.
    """
    out = set()
    for module in imported_modules(source, f"app.{layer}"):
        parts = module.split(".")
        if len(parts) >= 3 and parts[1] == "blueprints" and parts[2] in existing:
            if (layer, parts[2]) != ("blueprints", name):
                out.add(f"{layer}.{name} -> blueprints.{parts[2]}")
    return out


def _blueprint_names():
    return {p.stem for p in (APP / "blueprints").glob("*.py") if p.stem != "__init__"}


def actual_edges():
    existing = _blueprint_names()
    edges = set()
    for layer in LAYERS:
        for path in sorted((APP / layer).glob("*.py")):
            edges |= edges_from(path.read_text(), layer, path.stem, existing)
    return edges


def actual_lazy():
    counts = Counter()
    for path in APP.rglob("*.py"):
        n = len(LAZY.findall(path.read_text()))
        if n:
            counts[path.relative_to(APP).as_posix()] = n
    return dict(counts)


# --- the detectors fire -----------------------------------------------------

BLUEPRINTS = {"main", "budgets", "schedules", "ask"}


@pytest.mark.parametrize("snippet", [
    "from app.blueprints.budgets import compute_budget_vs_actual",
    "def f():\n    from app.blueprints.budgets import x  # lazy\n",
    "import app.blueprints.budgets",
    "from app.blueprints import budgets",
    "from .budgets import compute_budget_vs_actual",
    "from . import budgets",
])
def test_a_blueprint_importing_another_is_seen(snippet):
    assert edges_from(snippet, "blueprints", "main", BLUEPRINTS) == {
        "blueprints.main -> blueprints.budgets"}


def test_a_service_importing_a_blueprint_is_seen():
    # From services/, `..blueprints.x` is the relative spelling.
    for snippet in ("from app.blueprints.ask import dispatch",
                    "from ..blueprints.ask import dispatch"):
        assert edges_from(snippet, "services", "agent", BLUEPRINTS) == {
            "services.agent -> blueprints.ask"}


def test_what_is_not_an_edge_is_not_reported():
    source = "\n".join([
        "from app.services.forecasts import compute_forecast",  # the point
        "from app.blueprints.main import PALETTE_SIZE",          # self
        "from app.db import db_cursor",
        "import json",
    ])
    assert edges_from(source, "blueprints", "main", BLUEPRINTS) == set()


def test_the_lazy_marker_is_seen():
    assert LAZY.findall("x = 1  # lazy: avoids import cycle\n# lazy\n") == ["# lazy", "# lazy"]
    assert LAZY.findall("# lazily evaluated\n") == []


# --- the tree ---------------------------------------------------------------

def test_the_scan_reads_the_real_tree():
    # A scan that found nothing would pass the next test. Anchored on an import
    # the finished series keeps (a blueprint importing a service), because every
    # edge in ALLOWED_EDGES is on its way out: this used to assert
    # `main -> budgets`, and part 3 deleted it.
    source = (APP / "blueprints" / "main.py").read_text()
    assert "app.services.forecasts" in imported_modules(source, "app.blueprints")


@pytest.mark.criterion(489, "No blueprint imports another blueprint")
def test_no_new_import_crosses_a_boundary():
    # With ALLOWED_EDGES empty this is the scenario itself, and it also holds
    # the stronger rule that no service imports a blueprint.
    assert ALLOWED_EDGES == set()
    new = actual_edges() - ALLOWED_EDGES
    assert not new, (
        "These imports cross a module boundary (#489). Import from app/services/ "
        "instead, moving the code there first if it is still in a blueprint:\n  "
        + "\n  ".join(sorted(new)))


def test_the_edge_allowlist_only_shrinks():
    gone = ALLOWED_EDGES - actual_edges()
    assert not gone, (
        "These edges no longer exist. Delete them from ALLOWED_EDGES so they "
        "cannot come back:\n  " + "\n  ".join(sorted(gone)))


@pytest.mark.criterion(489, "No lazy import survives to dodge a cycle")
def test_lazy_imports_only_shrink():
    # Both directions at once: a new marker and a removed one both fail. With
    # ALLOWED_LAZY empty, this is the scenario itself: no marker anywhere.
    assert ALLOWED_LAZY == {}
    assert actual_lazy() == ALLOWED_LAZY
