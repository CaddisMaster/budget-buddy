"""#225 — the pure arithmetic behind Home's composed hero and stat row.

Every one of these is a figure the page states as fact, and each has a shape
that is wrong in a way no screenshot would reveal: a percentage against zero, a
flat sparkline, a budget nobody set, a salary counted as a bill. They live here
rather than in a route test because they need neither a database nor a browser.
"""
from datetime import date

from app.blueprints.main import (
    bills_outstanding,
    budget_usage,
    days_left_in_month,
    net_change,
    sparkline,
)

# --- days left ----------------------------------------------------------------

def test_days_left_counts_whole_days_and_ends_at_zero():
    assert days_left_in_month(date(2026, 8, 18)) == 13
    assert days_left_in_month(date(2026, 8, 31)) == 0
    assert days_left_in_month(date(2026, 2, 1)) == 27      # non-leap February
    assert days_left_in_month(date(2024, 2, 1)) == 28      # leap February


# --- net change ---------------------------------------------------------------
#
# ⚠️ Takes two FIGURES, not a series. It used to read the last two months off
# the cash-flow payload — which the month picker filters — so the all-time view
# rendered "ALL TIME +$17,216" with "-122.5% vs. last month" beside it. Caught
# in a screenshot; the signature change is what makes the mistake unavailable.

def test_net_change_reads_two_figures():
    assert net_change(100, 150) == 50.0
    assert net_change(100, 50) == -50.0


def test_net_change_against_a_zero_month_is_undefined_not_enormous():
    # A percentage against zero is not a big number, it is meaningless — and
    # rendering it as one is how a quiet month claims a 4000% improvement.
    assert net_change(0, 500) is None


def test_a_shrinking_loss_reads_as_an_improvement():
    # -400 -> -200 is better, not worse. Dividing by a negative denominator
    # reports it as -50%, which is the opposite of the truth.
    assert net_change(-400, -200) == 50.0


def test_a_deepening_loss_reads_as_a_decline():
    assert net_change(-200, -400) == -100.0


# --- sparkline ----------------------------------------------------------------

def test_sparkline_needs_two_points():
    assert sparkline([]) is None
    assert sparkline([{'month': '2026-01', 'balance': 5}]) is None


def test_sparkline_spans_the_full_width_and_inverts_y():
    spark = sparkline([{'month': '2026-01', 'balance': 0},
                       {'month': '2026-02', 'balance': 10}], width=100, height=20, pad=0)
    pts = [tuple(float(n) for n in p.split(',')) for p in spark['line'].split()]
    assert pts[0][0] == 0 and pts[-1][0] == 100
    # SVG y grows downward, so the LARGER value must sit HIGHER on the canvas.
    assert pts[-1][1] < pts[0][1]
    assert spark['last'] == pts[-1]


def test_a_flat_series_is_centred_rather_than_dividing_by_zero():
    spark = sparkline([{'month': '2026-01', 'balance': 7},
                       {'month': '2026-02', 'balance': 7}], height=20, pad=0)
    ys = {float(p.split(',')[1]) for p in spark['line'].split()}
    assert ys == {10.0}


def test_the_area_path_closes_back_to_the_baseline():
    spark = sparkline([{'month': '2026-01', 'balance': 1},
                       {'month': '2026-02', 'balance': 2}], width=50, height=20)
    assert spark['area'].startswith('M0,20')
    assert spark['area'].endswith('L50,20 Z')


# --- budget usage -------------------------------------------------------------

def test_budget_usage_is_none_when_nothing_is_budgeted():
    assert budget_usage([]) is None
    assert budget_usage([{'category': 'x', 'budget': 0, 'actual': 40}]) is None


def test_budget_usage_totals_across_categories():
    rows = [{'category': 'a', 'budget': 100, 'actual': 50},
            {'category': 'b', 'budget': 300, 'actual': 90}]
    assert budget_usage(rows) == {'used': 140.0, 'total': 400.0, 'pct': 35}


def test_going_over_budget_is_not_clamped_to_a_hundred():
    # Being 130% through the month's budget is exactly the state worth seeing.
    rows = [{'category': 'a', 'budget': 100, 'actual': 130}]
    assert budget_usage(rows)['pct'] == 130


# --- bills still to post ------------------------------------------------------

def test_bills_outstanding_counts_expenses_only():
    # Netting a scheduled salary against the bills makes the figure meaningless:
    # the question is what is still going to LEAVE the account.
    items = [{'description': 'Rent', 'amount': 1450, 'type': 'expense', 'due': '2026-08-28'},
             {'description': 'Salary', 'amount': 2150, 'type': 'income', 'due': '2026-08-30'},
             {'description': 'Power', 'amount': 96.4, 'type': 'expense', 'due': '2026-08-29'}]
    assert bills_outstanding(items) == {'count': 2, 'total': 1546.4}


def test_bills_outstanding_with_nothing_due():
    assert bills_outstanding([]) == {'count': 0, 'total': 0}
