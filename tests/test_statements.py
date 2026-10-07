"""#445 — app/statements.py: reading statement exports and matching them to the
ledger. Pure, so every rule is tested here directly; the user-facing flow is in
tests/features/statement_import.feature.
"""
from collections import namedtuple
from datetime import date
from decimal import Decimal

import pytest

from app.statements import (
    DATE_FORMATS,
    MAX_LINES,
    CsvMapping,
    Line,
    Review,
    StatementError,
    adjustments_within,
    apply_summary,
    clean_name,
    compare_balance,
    csv_ref,
    csv_rows,
    decode,
    explain_gap,
    find_counterpart,
    history_categories,
    image_type,
    lines_from_screenshots,
    looks_binary,
    looks_like_ofx,
    match_lines,
    merchant_key,
    money,
    parse_csv,
    parse_ofx,
    proposed_names,
    resolve_date,
    review_plan,
    sample_rows,
    unlisted_rows,
    validate_mapping,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

# OFX 1.x is SGML: leaf elements are NOT closed, aggregates are.
OFX_SGML = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>USD
<BANKTRANLIST>
<DTSTART>20260901120000.000
<DTEND>20260930120000.000
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260904120000.000[-5:EST]
<TRNAMT>-4.50
<FITID>FIT-001
<NAME>COFFEE SHOP #123
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260915
<TRNAMT>1500.00
<FITID>FIT-002
<NAME>PAYROLL &amp; CO
<MEMO>DIRECT DEP
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>2345.67
<DTASOF>20260930
</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

# OFX 2.x is XML: every element closed.
OFX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS>
<BANKTRANLIST><DTSTART>20260901</DTSTART><DTEND>20260930</DTEND>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20260910</DTPOSTED><TRNAMT>-82.17</TRNAMT><FITID>X1</FITID><NAME>GROCERY MART</NAME></STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>-82.17</BALAMT><DTASOF>20260930</DTASOF></LEDGERBAL>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>
"""

Row = namedtuple("Row", "id transaction_date amount transaction_type description "
                        "is_pending is_adjustment import_ref")


def _row(id, when, amount, kind="expense", pending=False, adjustment=False, ref=None,
         description="x"):
    return Row(id, when, Decimal(amount), kind, description, pending, adjustment, ref)


def _line(when, amount, direction="out", description="x", ref=None):
    return Line(when, Decimal(amount), direction, description, ref)


D = date(2026, 9, 10)


def _on(day):
    return date(2026, 9, day)


# ── money() ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("12.34", Decimal("12.34")),
    ("-12.34", Decimal("-12.34")),
    ("$1,234.56", Decimal("1234.56")),
    ("(12.00)", Decimal("-12.00")),
    ("12.00-", Decimal("-12.00")),
    ("+5", Decimal("5.00")),
    (" 7.1 ", Decimal("7.10")),
])
def test_money_reads_the_shapes_banks_use(text, expected):
    assert money(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "NaN", "nan", "inf", "-Infinity", "1e5",
                                  "1.2.3", "--5", "12,34x"])
def test_money_refuses_anything_that_is_not_a_plain_amount(text):
    """NaN and infinities are refused by construction: only digits reach
    Decimal(). A NaN reaching the ledger poisons every SUM (#314)."""
    assert money(text) is None


# ── OFX ─────────────────────────────────────────────────────────────────────

def test_sgml_ofx_is_read_with_directions_refs_period_and_balance():
    s = parse_ofx(OFX_SGML)
    assert [(ln.date, ln.amount, ln.direction, ln.ref) for ln in s.lines] == [
        (_on(4), Decimal("4.50"), "out", "FIT-001"),
        (_on(15), Decimal("1500.00"), "in", "FIT-002"),
    ]
    assert s.lines[1].description == "PAYROLL & CO", "NAME wins over MEMO, unescaped"
    assert (s.start, s.end) == (_on(1), _on(30))
    assert s.closing_balance == Decimal("2345.67")


def test_xml_ofx_from_a_card_is_read_the_same_way():
    s = parse_ofx(OFX_XML)
    assert [(ln.amount, ln.direction, ln.description) for ln in s.lines] == [
        (Decimal("82.17"), "out", "GROCERY MART")]
    assert s.closing_balance == Decimal("-82.17")


def test_ofx_holding_two_accounts_is_refused():
    two = OFX_SGML.replace("</STMTRS>", "</STMTRS><STMTRS><BANKTRANLIST></BANKTRANLIST></STMTRS>")
    with pytest.raises(StatementError, match="more than one account"):
        parse_ofx(two)


def test_ofx_with_no_transactions_is_refused():
    with pytest.raises(StatementError, match="no transactions"):
        parse_ofx("OFXHEADER:100\n<OFX><BANKTRANLIST></BANKTRANLIST></OFX>")


def test_an_ofx_line_with_a_bad_amount_is_skipped_and_counted():
    bad = OFX_SGML.replace("<TRNAMT>-4.50", "<TRNAMT>NaN")
    s = parse_ofx(bad)
    assert [ln.ref for ln in s.lines] == ["FIT-002"]
    assert s.skipped == 1


def test_an_overlong_fitid_is_hashed_to_fit_the_column():
    long = OFX_SGML.replace("<FITID>FIT-001", "<FITID>" + "z" * 300)
    ref = parse_ofx(long).lines[0].ref
    assert ref.startswith("sha:") and len(ref) <= 255


def test_a_statement_over_the_line_cap_is_refused():
    block = "<STMTTRN><DTPOSTED>20260904<TRNAMT>-1.00<FITID>F{n}</STMTTRN>\n"
    body = "".join(block.format(n=n) for n in range(MAX_LINES + 1))
    with pytest.raises(StatementError, match=str(MAX_LINES)):
        parse_ofx(f"OFXHEADER:100\n<OFX><BANKTRANLIST>{body}</BANKTRANLIST></OFX>")


def test_ofx_is_recognised_and_csv_is_not():
    assert looks_like_ofx(OFX_SGML) and looks_like_ofx(OFX_XML)
    assert not looks_like_ofx("Date,Description,Amount\n2026-09-01,x,1\n")


def test_decode_handles_a_bom_and_windows_1252():
    assert decode(b"\xef\xbb\xbfDate") == "Date"
    assert decode("Caf\xe9".encode("cp1252")) == "Café"


# ── CSV ─────────────────────────────────────────────────────────────────────

SIGNED = csv_rows("Bank export\nDate,Description,Amount\n"
                  "09/03/2026,COFFEE SHOP,-4.50\n"
                  "09/15/2026,PAYROLL,1500.00\n"
                  "Total,,1495.50\n")


def _signed(out_is_negative=True):
    return CsvMapping(header_row=1, date_col=0, date_format="%m/%d/%Y",
                      description_col=1, amount_col=2, out_is_negative=out_is_negative)


def test_a_signed_csv_reads_negative_as_money_out():
    s = parse_csv(SIGNED, _signed())
    assert [(ln.date, ln.amount, ln.direction) for ln in s.lines] == [
        (_on(3), Decimal("4.50"), "out"), (_on(15), Decimal("1500.00"), "in")]
    assert s.skipped == 1, "the Total footer is counted, not silently lost"
    assert s.closing_balance is None


def test_a_card_csv_with_positive_purchases_flips_the_reading():
    s = parse_csv(SIGNED, _signed(out_is_negative=False))
    assert [ln.direction for ln in s.lines] == ["in", "out"]


def test_separate_debit_and_credit_columns_keep_their_directions():
    rows = csv_rows("Date,Description,Debit,Credit\n"
                    "2026-09-03,COFFEE,4.50,\n"
                    "2026-09-15,PAYROLL,,1500.00\n"
                    "2026-09-16,BOTH,1.00,2.00\n")
    mapping = CsvMapping(header_row=0, date_col=0, date_format="%Y-%m-%d",
                         description_col=1, debit_col=2, credit_col=3)
    s = parse_csv(rows, mapping)
    assert [(ln.description, ln.direction) for ln in s.lines] == [
        ("COFFEE", "out"), ("PAYROLL", "in")]
    assert s.skipped == 1, "a row with both columns filled is ambiguous and skipped"


def test_identical_csv_lines_get_distinct_but_stable_refs():
    rows = csv_rows("Date,Description,Amount\n"
                    "2026-09-03,Coffee Shop,-4.50\n"
                    "2026-09-03,Coffee Shop,-4.50\n")
    mapping = CsvMapping(header_row=0, date_col=0, date_format="%Y-%m-%d",
                         description_col=1, amount_col=2)
    first = [ln.ref for ln in parse_csv(rows, mapping).lines]
    again = [ln.ref for ln in parse_csv(rows, mapping).lines]
    assert first[0] != first[1]
    assert first == again, "a re-import must produce the same refs"
    assert first[0] == csv_ref(_on(3), Decimal("4.50"), "out", "Coffee Shop", 1)


def test_sample_rows_bounds_what_the_model_sees():
    rows = [["c" * 500] * 50] * 40
    sample = sample_rows(rows)
    assert len(sample) == 10 and len(sample[0]) == 20 and len(sample[0][0]) == 80


# ── validate_mapping ────────────────────────────────────────────────────────

GOOD = {"header_row": 1, "date_col": 0, "date_format": "%m/%d/%Y",
        "description_col": 1, "amount_col": 2, "out_is_negative": True,
        "debit_col": None, "credit_col": None}


def test_a_sound_mapping_is_accepted():
    assert validate_mapping(GOOD, SIGNED) == _signed()


@pytest.mark.parametrize("change", [
    {"header_row": 9},                         # past the end of the file
    {"date_col": 3},                           # past the header's width
    {"description_col": -1},
    {"amount_col": None},                      # no way to read amounts
    {"debit_col": 1, "credit_col": 2},         # two ways at once
    {"date_format": "%s"},                     # outside the allowlist
    {"date_col": True},                        # a bool is not a column
    {"date_col": "0"},
])
def test_a_mapping_the_file_does_not_bear_out_is_refused(change):
    with pytest.raises(StatementError):
        validate_mapping({**GOOD, **change}, SIGNED)


def test_every_allowlisted_date_format_is_a_real_format():
    for fmt in DATE_FORMATS:
        assert date(2026, 9, 3).strftime(fmt)


# ── match_lines ─────────────────────────────────────────────────────────────

def _statuses(lines, ledger):
    return [(r.status, r.match.id if r.match else None) for r in match_lines(lines, ledger)]


def test_the_same_amount_within_three_days_is_recorded_and_four_is_not():
    ledger = [_row(1, _on(10), "4.50")]
    assert _statuses([_line(_on(13), "4.50")], ledger) == [("recorded", 1)]
    assert _statuses([_line(_on(7), "4.50")], ledger) == [("recorded", 1)]
    assert _statuses([_line(_on(14), "4.50")], ledger) == [("missing", None)]


def test_direction_must_agree():
    ledger = [_row(1, D, "4.50", kind="income")]
    assert _statuses([_line(D, "4.50", "out")], ledger) == [("missing", None)]


def test_each_ledger_row_matches_at_most_one_line():
    lines = [_line(D, "4.50"), _line(D, "4.50")]
    assert _statuses(lines, [_row(1, D, "4.50")]) == [("recorded", 1), ("missing", None)]


def test_the_closest_date_wins_whatever_order_the_rows_come_in():
    lines = [_line(_on(10), "4.50"), _line(_on(12), "4.50")]
    near_first = [_row(1, _on(12), "4.50"), _row(2, _on(10), "4.50")]
    assert _statuses(lines, near_first) == [("recorded", 2), ("recorded", 1)]
    assert _statuses(lines, list(reversed(near_first))) == [("recorded", 2), ("recorded", 1)]


def test_a_matching_pending_row_is_offered_as_pending():
    ledger = [_row(7, _on(12), "40.00", pending=True)]
    assert _statuses([_line(_on(13), "40.00")], ledger) == [("pending", 7)]


def test_an_import_reference_matches_before_anything_else():
    ledger = [_row(1, _on(1), "99.00", ref="FIT-9"), _row(2, D, "4.50")]
    lines = [_line(D, "4.50", ref="FIT-9")]
    assert _statuses(lines, ledger) == [("recorded", 1)]


def test_a_near_amount_is_a_possible_match_up_to_ten_percent():
    assert _statuses([_line(D, "121.40")], [_row(1, D, "120.00")]) == [("possible", 1)]
    assert _statuses([_line(D, "110.00")], [_row(1, D, "100.00")]) == [("possible", 1)]
    assert _statuses([_line(D, "111.12")], [_row(1, D, "100.00")]) == [("missing", None)]


def test_an_exact_match_is_never_taken_by_a_possible_one():
    lines = [_line(D, "121.40"), _line(D, "120.00")]
    ledger = [_row(1, D, "120.00")]
    assert _statuses(lines, ledger) == [("missing", None), ("recorded", 1)]


def test_a_check_in_adjustment_never_matches():
    ledger = [_row(1, D, "4.50", adjustment=True)]
    assert _statuses([_line(D, "4.50")], ledger) == [("missing", None)]


def test_adjustments_inside_the_period_are_reported():
    ledger = [_row(1, _on(5), "50", adjustment=True),
              _row(2, date(2026, 8, 31), "60", adjustment=True),
              _row(3, _on(5), "70")]
    assert [r.id for r in adjustments_within(ledger, _on(1), _on(30))] == [1]


@pytest.mark.parametrize("description, expected", [
    ("PAYMENT - THANK YOU", True),
    ("ONLINE TRANSFER TO SAVINGS", True),
    ("AUTOPAY DISCOVER", True),
    ("GROCERY MART", False),
    ("TRANSFERWISE FEE", False),
])
def test_transfer_looking_lines_are_flagged(description, expected):
    review = match_lines([_line(D, "500", description=description)], [])[0]
    assert review.transfer_like is expected


def test_a_binary_file_is_recognised_before_anything_reads_it():
    assert looks_binary(decode(b"\x89PNG\r\n\x1a\n\x00\x00"))
    assert looks_binary(decode(b"%PDF-1.7\n\x00binary"))
    assert not looks_binary("Date,Description,Amount\r\n2026-09-01,x\t1,-1\n")
    assert not looks_binary(OFX_SGML)


# ── find_counterpart (#446) ─────────────────────────────────────────────────

Other = namedtuple("Other", "id account_id transaction_date amount transaction_type "
                            "is_transfer is_adjustment description")


def _other(id, when, amount, kind="expense", transfer=False, adjustment=False):
    return Other(id, 99, when, Decimal(amount), kind, transfer, adjustment, "x")


def test_a_counterpart_runs_the_opposite_way_for_the_same_amount():
    """Money INTO the card is money OUT of checking."""
    line = _line(D, "500", "in")
    assert find_counterpart(line, [_other(1, D, "500", "expense")]).id == 1
    assert find_counterpart(line, [_other(1, D, "500", "income")]) is None
    assert find_counterpart(line, [_other(1, D, "499.99", "expense")]) is None


def test_a_counterpart_is_within_three_days_and_the_closest_wins():
    line = _line(_on(10), "500", "in")
    # The closest row has the MIDDLE id, so neither id order can pick it by
    # accident (the first version of this test let a reverse-id sort pass).
    rows = [_other(1, _on(7), "500"), _other(2, _on(11), "500"), _other(3, _on(13), "500")]
    assert find_counterpart(line, rows).id == 2
    assert find_counterpart(line, [_other(3, _on(14), "500")]) is None


def test_a_counterpart_is_never_a_transfer_leg_an_adjustment_or_already_used():
    line = _line(D, "500", "in")
    assert find_counterpart(line, [_other(1, D, "500", transfer=True)]) is None
    assert find_counterpart(line, [_other(1, D, "500", adjustment=True)]) is None
    assert find_counterpart(line, [_other(1, D, "500")], used={1}) is None



# ── Screenshots (#447) ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    (b"\x89PNG\r\n\x1a\n....", "image/png"),
    (b"\xff\xd8\xff\xe0....", "image/jpeg"),
    (b"GIF89a....", "image/gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
    (b"%PDF-1.7\n", None),
    (b"Date,Description,Amount\n", None),
    (b"\x00\x00\x00\x18ftypheic", None),     # HEIC: the API cannot read it
])
def test_an_image_is_known_by_its_bytes_not_its_name(raw, expected):
    assert image_type(raw) == expected


def test_a_date_without_a_year_is_never_in_the_future():
    jan5 = date(2026, 1, 5)
    assert resolve_date(12, 30, None, jan5) == date(2025, 12, 30)
    assert resolve_date(1, 5, None, jan5) == date(2026, 1, 5), "today is not the future"
    assert resolve_date(1, 2, None, jan5) == date(2026, 1, 2)


def test_a_shown_year_is_kept_and_an_impossible_date_is_refused():
    today = date(2026, 10, 5)
    assert resolve_date(12, 30, 2026, today) == date(2026, 12, 30)
    assert resolve_date(2, 30, None, today) is None
    assert resolve_date(13, 1, None, today) is None
    assert resolve_date(None, 1, None, today) is None
    assert resolve_date(2, 29, None, date(2028, 3, 1)) == date(2028, 2, 29)


def test_feb_29_without_a_year_is_the_most_recent_one():
    """Found writing the test above: the first version tried THIS year's Feb 29,
    failed to construct it, and gave up, so on Jan 1 2029 a real 2028-02-29
    line was dropped."""
    assert resolve_date(2, 29, None, date(2029, 1, 1)) == date(2028, 2, 29)
    assert resolve_date(2, 29, None, date(2028, 2, 28)) == date(2024, 2, 29)


def _shot(**over):
    return {"month": 10, "day": 3, "year": None, "description": "GROCERY",
            "amount": "82.17", "direction": "out", "legible": True, **over}


TODAY = date(2026, 10, 5)


def test_screenshot_lines_are_rechecked_and_refed():
    s = lines_from_screenshots([_shot(), _shot()], TODAY)
    a, b = s.lines
    assert (a.date, a.amount, a.direction, a.uncertain) == (
        date(2026, 10, 3), Decimal("82.17"), "out", False)
    assert a.ref.startswith("img:") and a.ref != b.ref, "identical lines stay distinct"
    assert lines_from_screenshots([_shot(), _shot()], TODAY).lines[0].ref == a.ref


def test_a_line_the_model_flagged_or_could_not_direct_is_uncertain():
    flagged, undirected = lines_from_screenshots(
        [_shot(legible=False), _shot(direction="sideways", amount="-5.00")], TODAY).lines
    assert flagged.uncertain
    assert undirected.uncertain and undirected.direction == "out", "a minus sign is a hint"


@pytest.mark.parametrize("bad", [{"amount": "12.3?"}, {"amount": ""}, {"amount": "NaN"},
                                 {"month": 2, "day": 30}, {"amount": "0.00"}])
def test_a_line_with_no_readable_date_or_amount_is_skipped_and_counted(bad):
    s = lines_from_screenshots([_shot(), _shot(**bad)], TODAY)
    assert len(s.lines) == 1 and s.skipped == 1


def test_no_readable_lines_is_refused():
    with pytest.raises(StatementError, match="could not be read"):
        lines_from_screenshots([_shot(amount="??")], TODAY)


# ── #454: merchant keys and categories from history ─────────────────────────

@pytest.mark.parametrize("a, b", [
    ("SQ *BLUE BOTTLE 0423", "SQ *BLUE BOTTLE 0611"),      # a processor prefix, a store no.
    ("KROGER #123", "KROGER #456"),
    ("COFFEE SHOP #123", "Coffee Shop"),                    # the bank's text vs mine
    ("TST* JOES PIZZA", "TST*JOE'S PIZZA 00123"),
    ("PAYPAL *NETFLIX", "NETFLIX"),
    ("AMZN Mktp US*2K4AB1CD3", "AMZN MKTP US*9ZZ1QQ8"),     # an order reference after `*`
    ("WAL-MART #5432", "WALMART 0042"),
])
def test_the_banks_variations_of_one_merchant_share_a_key(a, b):
    assert merchant_key(a) == merchant_key(b) != ""


@pytest.mark.parametrize("a, b", [
    ("KROGER #123", "TARGET #123"),
    ("SQ *BLUE BOTTLE", "SQ *RED BARN"),
    ("7-ELEVEN 1234", "ELEVEN 1234"),                       # 7eleven is a name, not a number
])
def test_different_merchants_do_not(a, b):
    assert merchant_key(a) != merchant_key(b)


def test_a_bare_processor_prefix_is_the_merchant():
    # `AMAZON*AB12CD`: nothing usable after the `*`, so the prefix names the shop.
    assert merchant_key("AMAZON*AB12CD") == "amazon"


@pytest.mark.parametrize("text", ["", None, "#1234", "0423 09/14", "*", "  "])
def test_a_description_with_no_name_has_no_key(text):
    assert merchant_key(text) == ""


HistRow = namedtuple("HistRow", "id transaction_date description category_id")
KINDS = {1: "expense", 2: "expense", 3: "income"}


def _review(i, description, status="missing", direction="out", match=None, transfer=False):
    return Review(index=i, line=_line(D, "5.00", direction, description), status=status,
                  match=match, transfer_like=transfer)


def test_an_earlier_row_of_the_same_merchant_gives_its_category():
    reviews = [_review(0, "KROGER #456")]
    rows = [HistRow(10, _on(1), "KROGER #123", 1)]
    assert history_categories(reviews, rows, KINDS) == {0: 1}


def test_a_recorded_match_teaches_by_the_lines_text_not_the_rows():
    # I typed "Coffee"; the bank says BLUE BOTTLE. The pairing is what links them.
    matched = HistRow(10, _on(1), "Coffee", 2)
    reviews = [_review(0, "SQ *BLUE BOTTLE 0423", status="recorded", match=matched),
               _review(1, "SQ *BLUE BOTTLE 0611")]
    assert history_categories(reviews, [], KINDS) == {1: 2}


def test_a_pending_match_teaches_too_and_a_possible_one_does_not():
    matched = HistRow(10, _on(1), "Coffee", 2)
    for status, expected in (("pending", {1: 2}), ("possible", {})):
        reviews = [_review(0, "SQ *BLUE BOTTLE 0423", status=status, match=matched),
                   _review(1, "SQ *BLUE BOTTLE 0611")]
        assert history_categories(reviews, [], KINDS) == expected, status


def test_the_most_recent_filing_wins_then_the_higher_id():
    reviews = [_review(0, "TARGET")]
    rows = [HistRow(30, _on(1), "TARGET", 1), HistRow(10, _on(5), "TARGET", 2),
            HistRow(20, _on(3), "TARGET", 1)]
    assert history_categories(reviews, rows, KINDS) == {0: 2}
    same_day = [HistRow(10, _on(5), "TARGET", 2), HistRow(11, _on(5), "TARGET", 1)]
    assert history_categories(reviews, same_day, KINDS) == {0: 1}
    assert history_categories(reviews, list(reversed(same_day)), KINDS) == {0: 1}


def test_a_category_of_the_wrong_kind_is_passed_over_for_one_of_the_right_kind():
    rows = [HistRow(10, _on(1), "AMAZON MKTP", 3),          # older, income
            HistRow(11, _on(5), "AMAZON MKTP", 1)]          # newer, expense
    refund = [_review(0, "AMAZON MKTP", direction="in")]
    assert history_categories(refund, rows, KINDS) == {0: 3}
    only_expense = rows[1:]
    assert history_categories(refund, only_expense, KINDS) == {}


def test_a_category_not_in_the_users_list_is_never_used():
    rows = [HistRow(10, _on(1), "KROGER", 99)]
    assert history_categories([_review(0, "KROGER #1")], rows, KINDS) == {}


def test_only_missing_non_transfer_lines_are_answered():
    rows = [HistRow(10, _on(1), "ACME PAYMENTS", 1)]
    reviews = [_review(0, "ACME PAYMENTS", status="recorded", match=HistRow(11, _on(2), "x", None)),
               _review(1, "ACME PAYMENTS", status="possible", match=HistRow(12, _on(2), "x", None)),
               _review(2, "ACME PAYMENTS", transfer=True),
               _review(3, "ACME PAYMENTS")]
    assert history_categories(reviews, rows, KINDS) == {3: 1}


def test_an_uncategorised_row_and_a_nameless_line_teach_nothing():
    rows = [HistRow(10, _on(1), "KROGER", None), HistRow(11, _on(1), "#1234", 1)]
    reviews = [_review(0, "KROGER #9"), _review(1, "#5678")]
    assert history_categories(reviews, rows, KINDS) == {}


# ── unlisted_rows (#457) ────────────────────────────────────────────────────
#
# The statement covers Sep 1 to Sep 30, so with MATCH_DAYS = 3 its last three
# days (28, 29, 30) are left out: a purchase made then usually posts next time.

START, END = _on(1), _on(30)


def _unlisted(lines, ledger):
    return unlisted_rows(match_lines(lines, ledger), ledger, START, END)


def test_a_row_no_line_matched_is_unlisted():
    gym = _row(1, _on(10), "30.00")
    assert _unlisted([_line(_on(5), "2000.00", "in")], [gym]) == ([gym], [])


def test_a_matched_row_is_not_unlisted_whatever_its_status():
    recorded = _row(1, _on(1), "1500.00")
    pending = _row(2, _on(8), "20.00", pending=True)
    possible = _row(3, _on(12), "40.00")
    lines = [_line(_on(1), "1500.00"), _line(_on(8), "20.00"), _line(_on(12), "42.00")]
    assert [r.status for r in match_lines(lines, [recorded, pending, possible])] == [
        "recorded", "pending", "possible"]
    assert _unlisted(lines, [recorded, pending, possible]) == ([], [])


def test_entered_twice_leaves_exactly_one_copy_unlisted():
    first, second = _row(1, _on(3), "4.50"), _row(2, _on(3), "4.50")
    unlisted, _pending = _unlisted([_line(_on(3), "4.50")], [first, second])
    assert unlisted == [second]


@pytest.mark.parametrize("day, shown", [(27, True), (28, False), (30, False)])
def test_the_last_match_days_of_the_period_are_left_out(day, shown):
    row = _row(1, _on(day), "50.00")
    assert _unlisted([_line(_on(5), "2000.00", "in")], [row])[0] == ([row] if shown else [])


@pytest.mark.parametrize("when, shown", [
    (date(2026, 8, 31), False), (_on(1), True), (date(2026, 10, 1), False)])
def test_rows_outside_the_period_are_left_out(when, shown):
    row = _row(1, when, "30.00")
    assert _unlisted([_line(_on(15), "2000.00", "in")], [row])[0] == ([row] if shown else [])


def test_a_balance_check_in_is_never_unlisted():
    adjustment = _row(1, _on(15), "25.00", adjustment=True)
    assert _unlisted([_line(_on(5), "2000.00", "in")], [adjustment]) == ([], [])


def test_an_unmatched_pending_row_is_still_pending_not_unlisted():
    early = _row(1, _on(10), "20.00", pending=True)
    late = _row(2, _on(29), "9.00", pending=True)
    outside = _row(3, date(2026, 10, 1), "9.00", pending=True)
    assert _unlisted([_line(_on(5), "2000.00", "in")], [early, late, outside]) == (
        [], [early, late])


# ── review_plan / apply_summary (#458) ──────────────────────────────────────

def _r(i, status="missing", transfer=False, uncertain=False):
    line = Line(_on(i + 1), Decimal("10.00"), "out", f"L{i}", None, uncertain)
    return Review(index=i, line=line, status=status, transfer_like=transfer)


def test_a_missing_line_with_a_category_is_added_and_needs_nobody():
    assert review_plan([_r(0)], suggested={0: 7}, pairings={}) == ({0: "add"}, set())


def test_a_pending_line_is_marked_posted():
    assert review_plan([_r(0, "pending")], {}, {}) == ({0: "posted"}, set())


def test_a_recorded_line_is_neither_ticked_nor_needed():
    assert review_plan([_r(0, "recorded")], {}, {}) == ({}, set())


def test_a_paired_transfer_line_is_recorded_as_a_transfer():
    assert review_plan([_r(0, transfer=True)], {}, {0: object()}) == ({0: "transfer"}, set())


@pytest.mark.parametrize("review, suggested, ticked", [
    (_r(0, "possible"), {0: 7}, False),           # a decision: update, add or skip
    (_r(0, transfer=True), {0: 7}, False),        # a transfer with no other leg found
    (_r(0, uncertain=True), {0: 7}, False),       # the model could not read it cleanly
    (_r(0, "pending", uncertain=True), {}, False),
    (_r(0), {}, True),                            # added, but with no category
])
def test_what_needs_a_decision(review, suggested, ticked):
    plan, needs = review_plan([review], suggested, {})
    assert needs == {0}
    assert (0 in plan) is ticked


@pytest.mark.parametrize("counts, text", [
    ({"add": 3, "posted": 1}, "Adding 3 transactions, marking 1 posted."),
    ({"add": 1, "update": 2, "posted": 4, "transfer": 1},
     "Adding 1 transaction, updating 2 entries, marking 4 posted, recording 1 transfer."),
    ({"update": 1, "transfer": 2}, "Updating 1 entry, recording 2 transfers."),
    ({}, "Nothing will be changed."),
    ({"add": 0}, "Nothing will be changed."),
])
def test_the_summary_says_what_applying_will_do(counts, text):
    assert apply_summary(counts) == text


# ── #455: clean descriptions ────────────────────────────────────────────────

@pytest.mark.parametrize("a, b", [
    ("SQ *BLUE BOTTLE 0423", "SQ *BLUE BOTTLE 0611 SAN FRANCISCO CA"),  # cut at the store no.
    ("BLUE BOTTLE SAN FRANCISCO", "BLUE BOTTLE SAN FRANCISCO CA"),     # a trailing state code
    ("TST* JOES PIZZA 00123 BROOKLYN NY", "Joes Pizza"),
])
def test_where_the_bank_says_it_was_is_not_part_of_the_merchant(a, b):
    assert merchant_key(a) == merchant_key(b) != ""


@pytest.mark.parametrize("text, key", [
    ("SHOP ME", "shop me"),          # one word before it: a name, not a place
    ("Blue Bottle ca", "blue bottle ca"),   # only an upper-case state code is one
    ("CHECK 1234", "check"),
    ("#1234 KROGER", "kroger"),      # a store number before the name is skipped, not a cut
    ("7-ELEVEN 12345 AUSTIN TX", "7eleven"),
])
def test_what_location_stripping_keeps(text, key):
    assert merchant_key(text) == key


@pytest.mark.parametrize("raw, name", [
    ("SQ *BLUE BOTTLE 0611 SAN FRANCISCO CA", "Blue Bottle"),
    ("TST* JOES PIZZA 00123 BROOKLYN NY", "Joes Pizza"),
    ("KROGER FUEL DALLAS TX", "Kroger Fuel Dallas"),
    ("AMAZON*AB12CD", "Amazon"),
    ("#1234", ""),
])
def test_an_unseen_merchant_is_cleaned(raw, name):
    assert clean_name(raw) == name


@pytest.mark.parametrize("raw", [
    "SQ *BLUE BOTTLE 0611 SAN FRANCISCO CA", "TST* JOES PIZZA 00123 BROOKLYN NY",
    "WAL-MART #5432", "KROGER FUEL DALLAS TX", "PAYPAL *NETFLIX"])
def test_a_cleaned_name_keys_back_to_the_same_merchant(raw):
    """No raw-text column (#455 Q2): history keys on the stored name, so the
    name stored for a line must find that line's merchant next time."""
    assert merchant_key(clean_name(raw)) == merchant_key(raw)


def _named(reviews, rows=()):
    return proposed_names(reviews, list(rows))


def test_my_own_name_from_this_upload_is_reused():
    mine = HistRow(10, _on(3), "Blue Bottle", None)
    reviews = [_review(0, "SQ *BLUE BOTTLE 0423", status="recorded", match=mine),
               _review(1, "SQ *BLUE BOTTLE 0611 SAN FRANCISCO CA")]
    assert _named(reviews) == {1: "Blue Bottle"}


def test_my_own_name_from_an_earlier_row_is_reused_categorised_or_not():
    rows = [HistRow(10, _on(1), "Pizza place", None), HistRow(11, _on(2), "Joes", 1)]
    assert _named([_review(0, "PIZZA PLACE 0042"), _review(1, "SQ *JOES")], rows) == {
        0: "Pizza place", 1: "Joes"}


def test_the_most_recent_name_wins_then_the_higher_id():
    rows = [HistRow(10, _on(1), "KROGER", 1),       # older
            HistRow(11, _on(5), "kroger", 1),       # latest
            HistRow(9, _on(5), "Kroger", 1)]        # same day, lower id
    assert _named([_review(0, "KROGER #123")], rows) == {0: "kroger"}


@pytest.mark.parametrize("stored", ["SQ *BLUE BOTTLE 0423", "BLUE BOTTLE", "Blue Bottle 0423",
                                    "Sq *Blue Bottle"])   # some banks print mixed case
def test_a_name_that_is_still_bank_text_is_cleaned_not_reused(stored):
    """Imports before #455 stored the bank's text as it came."""
    rows = [HistRow(10, _on(1), stored, None)]
    assert _named([_review(0, "SQ *BLUE BOTTLE 0611")], rows) == {0: "Blue Bottle"}


def test_names_are_proposed_for_lines_that_can_be_added_only():
    mine = HistRow(10, _on(3), "Coffee", None)
    reviews = [_review(0, "SQ *CAFE", status="recorded", match=mine),
               _review(1, "SQ *CAFE", status="pending", match=mine),
               _review(2, "SQ *CAFE", status="possible", match=mine),
               _review(3, "ONLINE TRANSFER TO SAV 4421", transfer=True),
               _review(4, "#0000")]
    assert _named(reviews) == {2: "Coffee", 3: "Online Transfer To Sav", 4: "#0000"}


def test_a_one_word_name_in_capitals_is_mine_not_the_banks():
    rows = [HistRow(10, _on(1), "IKEA", None)]
    assert _named([_review(0, "IKEA 0042 BROOKLYN NY")], rows) == {0: "IKEA"}


# ── #459: a closing balance for every kind of statement ─────────────────────

def _bal(balance_col=3):
    return CsvMapping(header_row=0, date_col=0, date_format="%Y-%m-%d",
                      description_col=1, amount_col=2, balance_col=balance_col)


def _csv_rows(*rows):
    return [["Date", "Description", "Amount", "Balance"], *[list(r) for r in rows]]


def test_an_oldest_first_csv_closes_on_its_last_row():
    st = parse_csv(_csv_rows(("2026-09-01", "A", "-10.00", "1,190.00"),
                             ("2026-09-02", "B", "-5.00", "1,185.00")), _bal())
    assert (st.closing_balance, st.closing_date) == (Decimal("1185.00"), _on(2))


def test_a_newest_first_csv_closes_on_its_first_row():
    st = parse_csv(_csv_rows(("2026-09-02", "B", "-5.00", "1200.00"),
                             ("2026-09-01", "A", "-10.00", "1205.00")), _bal())
    assert (st.closing_balance, st.closing_date) == (Decimal("1200.00"), _on(2))


def test_rows_on_one_day_take_the_last_row_of_an_oldest_first_file():
    st = parse_csv(_csv_rows(("2026-09-01", "A", "-10.00", "90.00"),
                             ("2026-09-02", "B", "-5.00", "85.00"),
                             ("2026-09-02", "C", "-1.00", "84.00")), _bal())
    assert st.closing_balance == Decimal("84.00")


@pytest.mark.parametrize("rows", [
    (("2026-09-01", "A", "-10.00", "90.00"), ("2026-09-01", "B", "-5.00", "85.00")),  # one day
    (("2026-09-01", "A", "-1.00", "9.00"), ("2026-09-03", "B", "-1.00", "8.00"),
     ("2026-09-02", "C", "-1.00", "7.00")),                                          # no order
    (("2026-09-01", "A", "-10.00", "90.00"), ("2026-09-02", "B", "-5.00", "")),       # unreadable
])
def test_no_closing_balance_when_the_last_row_cannot_be_told(rows):
    assert parse_csv(_csv_rows(*rows), _bal()).closing_balance is None


def test_no_balance_column_no_closing_balance():
    st = parse_csv(_csv_rows(("2026-09-01", "A", "-10.00", "90.00")), _bal(None))
    assert st.closing_balance is None


def _raw_mapping(**over):
    return {"header_row": 0, "date_col": 0, "date_format": "%Y-%m-%d", "description_col": 1,
            "amount_col": 2, "out_is_negative": True, "debit_col": None, "credit_col": None,
            "balance_col": 3, **over}


def test_a_balance_column_is_validated_like_the_others():
    rows = _csv_rows(("2026-09-01", "A", "-10.00", "90.00"))
    assert validate_mapping(_raw_mapping(), rows).balance_col == 3
    with pytest.raises(StatementError):
        validate_mapping(_raw_mapping(balance_col=4), rows)


@pytest.mark.parametrize("col", [0, 1, 2])
def test_a_balance_column_that_is_another_column_is_dropped(col):
    rows = _csv_rows(("2026-09-01", "A", "-10.00", "90.00"))
    assert validate_mapping(_raw_mapping(balance_col=col), rows).balance_col is None


def test_an_ofx_balance_applies_on_its_own_date():
    st = parse_ofx(OFX_SGML.replace("<DTASOF>20260930", "<DTASOF>20260928"))
    assert (st.closing_balance, st.closing_date) == (Decimal("2345.67"), _on(28))


def test_an_ofx_balance_with_no_date_applies_on_the_last_day():
    st = parse_ofx(OFX_SGML.replace("<DTASOF>20260930\n", ""))
    assert st.closing_date == _on(30)


def _shot_balance(**over):
    return {"amount": "950.00", "month": 10, "day": 2, "year": None, "kind": "statement", **over}


@pytest.mark.parametrize("kind", ["statement", "after_line"])
def test_a_screenshot_balance_tied_to_a_date_is_used(kind):
    st = lines_from_screenshots([_shot()], TODAY, _shot_balance(kind=kind))
    assert (st.closing_balance, st.closing_date) == (Decimal("950.00"), date(2026, 10, 2))


@pytest.mark.parametrize("balance", [
    None,
    _shot_balance(kind="available"),          # today's balance, holds included
    _shot_balance(kind="current"),
    _shot_balance(month=None, day=None),      # tied to no date
    _shot_balance(amount="NaN"),
    _shot_balance(month=2, day=30),           # no such day
])
def test_any_other_screenshot_balance_is_dropped(balance):
    st = lines_from_screenshots([_shot()], TODAY, balance)
    assert st.closing_balance is None and st.closing_date is None


@pytest.mark.parametrize("ledger, closing, card, gap", [
    ("100.00", "90.00", False, "10.00"),
    ("-1000.00", "1000.00", False, "-2000.00"),     # not a card: compared as-is
    ("-1042.80", "1042.80", True, "0.00"),          # owed shown positive
    ("-1042.80", "-1042.80", True, "0.00"),         # owed shown negative
    ("-1000.00", "1042.80", True, "42.80"),
    ("-1000.00", "-1042.80", True, "42.80"),
    ("20.00", "-20.00", True, "0.00"),              # a card in credit
])
def test_a_card_balance_is_read_in_the_nearer_sign(ledger, closing, card, gap):
    assert compare_balance(Decimal(ledger), Decimal(closing), card) == Decimal(gap)


Ledger = namedtuple("Ledger", "id transaction_date amount transaction_type description")


def test_a_gap_one_unticked_line_closes_is_named():
    trattoria = _line(_on(5), "42.80", "out", "TRATTORIA")
    others = [_line(_on(6), "12.00", "out", "BAKERY"), _line(_on(7), "42.80", "in", "REFUND")]
    assert explain_gap(Decimal("42.80"), [trattoria, *others], []) == trattoria
    assert explain_gap(Decimal("-42.80"), [trattoria, *others], []) == others[1]


def test_a_gap_one_ledger_only_row_closes_is_named():
    gym = Ledger(1, _on(10), Decimal("30.00"), "expense", "Gym")
    assert explain_gap(Decimal("-30.00"), [], [gym]) == gym
    assert explain_gap(Decimal("30.00"), [], [gym]) is None


def test_two_explanations_name_neither():
    a, b = _line(_on(5), "42.80", "out", "A"), _line(_on(6), "42.80", "out", "B")
    assert explain_gap(Decimal("42.80"), [a, b], []) is None
    gym = Ledger(1, _on(10), Decimal("42.80"), "income", "Refund")
    assert explain_gap(Decimal("42.80"), [a], [gym]) is None


def test_no_gap_needs_no_explanation():
    assert explain_gap(Decimal("0.00"), [_line(_on(5), "0.00")], []) is None

