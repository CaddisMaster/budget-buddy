"""Which hue each category gets on a chart, and which categories get one (#489).

Home's category chart and the /categories swatch must agree on a category's
colour, so both run this one pipeline: fold the tail to PALETTE_SIZE real
slices, then assign slots so no two slices on screen share a hue. It lived in
main.py, and categories.py imported it lazily to dodge a cycle, until #489.
"""

# Doughnut tail fold (#108). A doughnut is a part-to-whole-at-a-glance form and
# is past its readable limit around six slices: #83 validated the palette with a
# CVD/contrast checker and only ~4 hues clear all-pairs separation, so the
# seven-slice production chart had pairs below the "hard to tell apart even with
# full colour vision" floor. Showing fewer segments is the fix; a ninth hue is
# not (see the --series-N comment in style.css).
#
# Presentation only, and deliberately NOT pushed into the SQL rollup — every
# other surface (budgets, insights, forecasts, the hero figures) needs the
# complete per-category figures. The card's own total is derived from cash_flow,
# not from this list, so folding cannot move it.
#
# ⚠️ The fold does NOT by itself fix the palette wrap, contrary to what #108
# assumed — but it is what makes the real fix possible. Capping the chart at 6
# real slices against 8 hues guarantees a free hue exists, which is the
# precondition assign_series_slots() below relies on (#111). Before the fold,
# probing for a free slot could simply fail.
# The palette is eight tokens (--series-1..8 in style.css), and that number is
# now the ONLY limit on how many categories are drawn (#257).
#
# ⚠️ The fold used to cut at SIX, and the reason has expired. #108 chose six
# because "a doughnut is past its readable limit around six slices" — and #223
# deleted the doughnut. Category data is a server-rendered vertical list of
# ranked bars now (`.cat-bar-row` in dashboard.html); eight rows in a list is
# not crowded the way eight slices of a pie were. The number outlived its
# reason, and the cost was real: an 8-category account had two categories
# folded into grey EVERY month, and measured against real data one of them had
# never once been drawn in five months.
#
# Tying the fold to the palette makes the rule state itself — draw as many
# categories as we have distinct hues for, and no more. Do not re-split these
# into two numbers: `assign_series_slots` can only guarantee distinct colours
# while the drawn set fits the palette, so a limit above PALETTE_SIZE
# reintroduces the collision #111 exists to prevent.
PALETTE_SIZE = 8


def fold_chart_tail(rows, limit=PALETTE_SIZE, label='Other'):
    """Top `limit` rows by total; the remainder summed into one entry. Pure.

    Takes and returns the chart payload's {'category', 'total'} dicts. The
    folded entry carries is_other=True — the template colours it neutral off
    that FLAG, never off the label, because a user may legitimately have a
    category actually named "Other" and it must keep its own hue.

    `limit` or fewer rows are returned unchanged, with no folded entry at all —
    the common case. Sorts defensively: every caller's query already ends
    ORDER BY total DESC, but a pure function shouldn't depend on that."""
    if len(rows) <= limit:
        return rows
    ranked = sorted(rows, key=lambda r: r['total'], reverse=True)
    tail = ranked[limit:]
    return ranked[:limit] + [{
        'category': label,
        'total': sum(r['total'] for r in tail),
        'is_other': True,
        'folded': len(tail),
    }]


# Palette-slot assignment (#111). Creation order alone WRAPPED: slot was
# `creation_index % 8`, so a user's 1st and 9th categories painted the same hex,
# and if both were drawn the chart showed two identical slices — the exact bug
# #83 set out to kill, resurfacing at a different input size. Seen in production
# on 0.3.0 with "Monthly Bills" and "Food & Dining".
#
# ⚠️ The rule this REPLACES — "colour must be a pure function of the category,
# never of the set drawn" (#83) — is mathematically incompatible with "no two
# drawn slices share a colour". A fixed per-category assignment cannot keep an
# arbitrary 6-subset distinct unless there are as many hues as categories, and
# #83's own validation caps the palette at 8. One of the two had to give.
#
# What makes giving it up safe NOW is the #108 fold: the drawn set can never
# exceed the palette, so probing always succeeds. #83 rejected probing when 10+
# slices could be drawn and it would have failed anyway. The fold changed that
# arithmetic.
#
# ⚠️ This read "at most 6 real slices are drawn from 8 hues, so a free hue
# ALWAYS exists" — the pre-#257 numbers (#309). The fold cuts at PALETTE_SIZE
# now, so at 8 drawn categories there is no spare hue; there are exactly as
# many slots as rows, which is a different argument reaching the same
# conclusion. Pass 2 can only run out of slots with MORE rows than the palette,
# and the fold makes that unreachable — so state the relationship (drawn <=
# palette), never the two numbers, which is how this went stale in the first
# place.
#
# Creation order is still the PREFERENCE, so a category keeps its familiar hue
# and only a genuinely colliding one moves.
def assign_series_slots(rows, category_order, palette_size=PALETTE_SIZE):
    """Give every drawn row a DISTINCT palette slot. Pure.

    Preference is creation order (`category_order`), earliest-created wins a
    contested slot, and anything displaced takes the lowest free one. Rows
    flagged is_other are left alone — the folded slice paints from its own
    achromatic token, not from the palette.

    Only the drawn set is considered, so the expense and income views are
    assigned INDEPENDENTLY: they share one canvas but are never on screen
    together, and their union can exceed the palette."""
    real = [r for r in rows if not r.get('is_other')]
    ordered = sorted(real, key=lambda r: category_order.get(r['category'], 0))
    used, chosen = set(), {}
    for r in ordered:                       # pass 1 — preferred slot, if free
        pref = category_order.get(r['category'], 0) % palette_size
        if pref not in used:
            used.add(pref)
            chosen[r['category']] = pref
    for r in ordered:                       # pass 2 — displaced take lowest free
        name = r['category']
        if name in chosen:
            continue
        free = next((c for c in range(palette_size) if c not in used), None)
        if free is None:                    # >palette_size drawn: unreachable
            chosen[name] = category_order.get(name, 0) % palette_size
        else:
            used.add(free)
            chosen[name] = free
    return [r if r.get('is_other') else {**r, 'slot': chosen[r['category']]}
            for r in rows]


# One builder for the per-category rollup, and one place the filters live
# (#257). Four near-identical copies of this SELECT used to sit inline in
# `index` — month-scoped and all-time, expense and income — and `/categories`
# now needs the same rows to show a category the colour it is actually drawn
# in. A second hand-written copy is exactly how a surface starts disagreeing
# with the chart it claims to describe.
#
# ⚠️ Filtered on `t.transaction_type`, NOT on `c.kind`. Those are different
# questions: kind drives which lists a category is OFFERED in, while the chart
# sums the transactions' own direction. A category can therefore appear in both
# views — see `category_slot_map`, which has to cope with that.
def category_totals(cursor, user_id, transaction_type, year=None, month=None):
    """Rows of (name, total) for one transaction direction, newest rollup first.

    `year`/`month` scope it to one month; omitting both is the all-time view.
    Excludes transfers and adjustments, like every other analytics figure."""
    sql = """
        SELECT c.name, SUM(t.amount) AS total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = %s AND t.transaction_type = %s
          AND t.is_transfer = false AND t.is_adjustment = false
    """
    params = [user_id, transaction_type]
    if year and month:
        sql += (" AND EXTRACT(YEAR FROM t.transaction_date) = %s"
                " AND EXTRACT(MONTH FROM t.transaction_date) = %s")
        params.extend([year, month])
    sql += " GROUP BY c.name ORDER BY total DESC"
    cursor.execute(sql, params)
    return cursor.fetchall()


def category_slot_map(cursor, user_id, year, month):
    """{'expense': {name: slot}, 'income': {name: slot}} — the palette slots a
    month's categories are ACTUALLY drawn in on Home.

    ⚠️ Computed by running the real pipeline (`category_totals` →
    `fold_chart_tail` → `assign_series_slots`), never by recomputing
    `creation_index % PALETTE_SIZE`. That shortcut gives the *preferred* slot,
    which is what a category gets only when the slot is uncontested — so it
    would disagree with the chart precisely when a collision happened, and
    silently. #257 rejected it for that reason.

    A category missing from a view's map is not drawn there this month (it was
    folded into "Other", or had no transactions). Callers must render that as
    "not charted" rather than inventing a hue.

    The two views are assigned independently, exactly as `index` does it — they
    share one canvas but are never on screen together.
    """
    cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY id",
                   (user_id,))
    category_order = {row.name: i for i, row in enumerate(cursor.fetchall())}
    slots = {}
    for direction in ('expense', 'income'):
        rows = [{'category': r.name, 'total': float(r.total)}
                for r in category_totals(cursor, user_id, direction, year, month)]
        drawn = assign_series_slots(fold_chart_tail(rows), category_order)
        slots[direction] = {r['category']: r['slot']
                            for r in drawn if not r.get('is_other')}
    return slots
