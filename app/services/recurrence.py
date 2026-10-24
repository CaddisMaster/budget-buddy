"""Recurrence date arithmetic: when a schedule is next due, and every date it
falls on in a window. Pure: dates in, dates out, no database (#489).

Schedules, recurring transfers, the month projection, the weekly digest and the
due-tomorrow reminder all walk the same calendar, so it lives in one place.
Until #489 it was split across transactions.py, schedules.py and main.py, and
the blueprints imported each other to reach it.
"""
import calendar
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

# Recurring frequencies the app accepts. 'semimonthly' is the only one that
# needs a second pay day (recur_second_day); the rest are fixed intervals.
VALID_FREQUENCIES = ('weekly', 'biweekly', 'semimonthly', 'monthly', 'quarterly', 'annually')

# Human-friendly labels for the recurring badge in the history table.
FREQUENCY_LABELS = {
    'weekly': 'Weekly',
    'biweekly': 'Bi-weekly',
    'semimonthly': 'Semi-monthly',
    'monthly': 'Monthly',
    'quarterly': 'Quarterly',
    'annually': 'Annually',
}


def clamp_to_month(year, month, day):
    """Build a date, clamping the day to the last valid day of that month
    (so a '31st' pay day lands on the 28th/30th in shorter months)."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def compute_next_due(current, frequency, anchor_day=None, second_day=None):
    """Return the next due date (a date) strictly after `current` for the given
    frequency. For 'semimonthly', anchor_day and second_day are the two pay days
    of the month (e.g. 15 and 31); the function alternates between them."""
    if frequency == 'weekly':
        return current + timedelta(weeks=1)
    if frequency == 'biweekly':
        return current + timedelta(weeks=2)
    if frequency == 'monthly':
        return current + relativedelta(months=1)
    if frequency == 'quarterly':
        return current + relativedelta(months=3)
    if frequency == 'annually':
        return current + relativedelta(years=1)
    if frequency == 'semimonthly' and anchor_day and second_day:
        lo, hi = sorted((anchor_day, second_day))
        # Compare against the clamped date, so a "31 = last day" pay day that
        # lands on the 28th/30th doesn't keep re-targeting the same month.
        hi_this_month = clamp_to_month(current.year, current.month, hi)
        if current < hi_this_month:
            return hi_this_month
        nxt = current + relativedelta(months=1)
        return clamp_to_month(nxt.year, nxt.month, lo)
    # Fallback keeps a recurring row moving forward even on unexpected data.
    return current + relativedelta(months=1)


def compute_initial_semimonthly_due(anchor_day, second_day, today):
    """First semi-monthly pay day on or after `today` — never back-fills.
    Picks the earlier of the two pay days that hasn't passed this month, else
    rolls to the earlier pay day next month."""
    lo, hi = sorted((anchor_day, second_day))
    lo_date = clamp_to_month(today.year, today.month, lo)
    hi_date = clamp_to_month(today.year, today.month, hi)
    if today <= lo_date:
        return lo_date
    if today <= hi_date:
        return hi_date
    nxt = today + relativedelta(months=1)
    return clamp_to_month(nxt.year, nxt.month, lo)


# Occurrence walkers over compute_next_due: the weekly digest's upcoming-week
# enumerator and the due-tomorrow reminder use them. They lived in main.py, and
# both callers imported them lazily to dodge a cycle, until #489.
def advance_past(occ, frequency, anchor_day, second_day, day, guard=500):
    """First occurrence strictly after `day`, walking from `occ` with
    compute_next_due. Pure. The guard caps runaway walks on bad data."""
    steps = 0
    while occ <= day and steps < guard:
        occ = compute_next_due(occ, frequency, anchor_day=anchor_day,
                               second_day=second_day)
        steps += 1
    return occ


def upcoming_occurrences(next_due, frequency, anchor_day, second_day,
                         window_start, window_end, guard=500, end_date=None):
    """All occurrence dates with window_start < occ <= window_end. Pure.

    Start is exclusive (anything due today was already materialized by the
    due-runners); end is INCLUSIVE — a bill due on payday still has to be paid
    out of this check. Each phase gets its own guard budget so a long catch-up
    can't starve the collection loop (the forecasts._remaining_scheduled
    precedent).

    `end_date` is the schedule's optional last day (#32): nothing on or after it
    is enumerated, so a schedule finishing mid-window is truncated rather than
    advertised forever. It defaults to None — no end date — which is every
    schedule created before #32 and every caller that doesn't pass one."""
    occ = advance_past(next_due, frequency, anchor_day, second_day,
                        window_start, guard)
    if end_date is not None and end_date < window_end:
        window_end = end_date
    out = []
    steps = 0
    while occ <= window_end and steps < guard:
        out.append(occ)
        occ = compute_next_due(occ, frequency, anchor_day=anchor_day,
                               second_day=second_day)
        steps += 1
    return out
