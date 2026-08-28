"""`helpers.recent_months()` — the 'YYYY-MM' labels behind the History and
Dashboard month filters (#309, tranche 1).

Pure arithmetic, no database. It exists as its own file because the bug it
guards is invisible from every caller: all three pass 12 or fewer months, and
the function was correct at exactly those sizes.
"""
from datetime import datetime

import pytest

from app.helpers import recent_months

# January is the only interesting month to count back from — every wrap the
# function can get wrong happens crossing a year boundary, and starting in
# January puts the first crossing at i=1 rather than eleven steps in.
JANUARY = datetime(2026, 1, 15)


def test_the_default_window_is_twelve_months_newest_first():
    months = recent_months(today=JANUARY)
    assert len(months) == 12
    assert months[0] == '2026-01'
    assert months[-1] == '2025-02'


def test_a_window_longer_than_a_year_still_produces_real_months():
    """The regression this file exists for.

    The old form subtracted the index from `today.month` and corrected a
    negative result with a single `+= 12` — which is exactly one year of
    correction. From January that held to thirteen months and then broke
    silently: count=14 ended '2025-00', and count=24 walked down to '2025--10'.

    Nothing on screen was ever wrong, because no caller asks for more than 12.
    That is precisely why it needs a test rather than a comment — a wrong month
    label does not raise, it just renders, and the next caller to want two
    years would have found out from a user.
    """
    two_years = recent_months(24, JANUARY)
    assert len(two_years) == 24
    assert two_years[-1] == '2024-02'
    assert two_years[12] == '2025-01'
    assert two_years[13] == '2024-12'


@pytest.mark.parametrize("count", [1, 7, 12, 13, 14, 24, 36])
def test_every_label_parses_back_as_a_date(count):
    """The property, rather than a list of expected strings: whatever window a
    caller asks for, every label it gets back is a month that exists. '2025-00'
    and '2025--8' both failed this, and neither would have tripped a length or
    ordering check."""
    months = recent_months(count, JANUARY)
    assert len(months) == count
    parsed = [datetime.strptime(label, '%Y-%m') for label in months]
    # Strictly descending, one month at a time, with no repeats or gaps.
    # strict=False is the point: pairing consecutive labels deliberately
    # drops the last one, so the two sequences differ in length by one.
    for newer, older in zip(parsed, parsed[1:], strict=False):
        assert (newer.year * 12 + newer.month) - (older.year * 12 + older.month) == 1


def test_the_window_is_anchored_on_today_by_default():
    """`today=None` reads the clock — the callers rely on it, so the default
    path needs an assertion of its own rather than only the injected one."""
    now = datetime.today()
    assert recent_months()[0] == f'{now.year}-{now.month:02d}'
