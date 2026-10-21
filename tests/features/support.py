"""Shared step vocabulary for tests/features/steps/ — types and lookups, NO steps.

⚠️ WHY THIS FILE EXISTS (#389). behave hands every step matcher ONE shared type
registry, and compiles each pattern lazily. So a step file could use `{who:Who}`
only if the file registering `Who` happened to be loaded first — and behave loads
`steps/` in alphabetical order. That worked for one step file by accident of
naming. Every step file now imports this module FIRST, so the types it uses are
registered before its own decorators run, whatever the files are called.

⚠️ It lives OUTSIDE `steps/` on purpose, and defines no steps. behave executes
every file in `steps/`; a step file imported by another would run twice and
register every step twice, which behave reports as an ambiguous step. Shared
STEPS go in `steps/common_steps.py`, which no other file imports.
"""
import re
import threading
from datetime import date, timedelta

from behave import register_type

from tests.helpers import warm_the_pool

HX = {"HX-Request": "true"}


# ── Relative dates ──────────────────────────────────────────────────────────
#
# Scenarios speak in days relative to today, because every rule here is
# relative to today: "due yesterday" means the same thing on any date the suite
# runs, where a literal date would go stale or need a frozen clock.

def _pattern(regex):
    """What `parse.with_pattern` does, without importing `parse` — it reaches
    this environment only as a dependency of behave, and nothing pins it."""
    def mark(func):
        func.pattern = regex
        return func
    return mark


_REL = r"yesterday|today|tomorrow|in \d+ days?|\d+ (?:day|week)s? ago"


@_pattern(_REL)
def _relative_date(text):
    today = date.today()
    fixed = {"yesterday": -1, "today": 0, "tomorrow": 1}
    if text in fixed:
        return today + timedelta(days=fixed[text])
    count, unit = re.search(r"(\d+) (day|week)", text).groups()
    delta = timedelta(days=int(count)) if unit == "day" else timedelta(weeks=int(count))
    return today + delta if text.startswith("in ") else today - delta


register_type(Rel=_relative_date)


# ⚠️ Constrained types, not bare `{}`. parse's default field is a lazy `.+?`
# that happily spans spaces, so "has a paused monthly expense schedule" also
# matches "has a {frequency} {kind} schedule" with frequency="paused" — and the
# generic step, registered first, wins. Each field below matches only the words
# it can legitimately hold. `Who` is shared; each step file registers its own
# vocabulary beside the steps that use it.

@_pattern(r"[AB]")
def _who(text):
    return text


register_type(Who=_who)


def _user(context, who):
    return context.users[who.lower()]


# ── Races ───────────────────────────────────────────────────────────────────

def race(context, workers):
    """Start every worker on one barrier so they hit the same window together.

    Each call holds its own connection, so this is real interleaving against
    whatever the code under test serialises on (a `FOR UPDATE` lock, a claim). The
    pool is warmed first so no thread starts behind the others (#401).
    """
    warm_the_pool(len(workers))
    barrier = threading.Barrier(len(workers))
    context.errors = []

    def run(work):
        barrier.wait()
        try:
            work()
        except Exception as err:  # surfaced by "no run failed"
            context.errors.append(err)

    threads = [threading.Thread(target=run, args=(work,)) for work in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
