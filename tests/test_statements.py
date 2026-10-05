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
    StatementError,
    adjustments_within,
    csv_ref,
    csv_rows,
    decode,
    find_counterpart,
    image_type,
    lines_from_screenshots,
    looks_binary,
    looks_like_ofx,
    match_lines,
    money,
    parse_csv,
    parse_ofx,
    resolve_date,
    sample_rows,
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
