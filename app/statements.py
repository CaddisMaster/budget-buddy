"""Statement import (#445): read a bank statement export and decide which of its
lines the ledger is missing.

Pure: no database, no Flask, no model calls. `blueprints/imports.py` loads the
ledger and the categories, `ai.py` maps a CSV's columns, and everything that
DECIDES something lives here, where it is directly testable.

⚠️ THE APP DECIDES WHAT IS MISSING, NEVER THE MODEL. The model's only jobs are
naming a CSV's columns (`ai.map_csv_columns`, validated by `validate_mapping`
below) and suggesting categories for merchants the user's own history does not
already answer (`history_categories`, #454). Matching a statement line to a ledger row is
`match_lines()`, plain arithmetic on dates and amounts, so a model mistake can
never hide a duplicate or invent a gap.

Amounts are `Decimal` throughout. A statement line's amount is always POSITIVE,
with the direction ('in' or 'out') carried separately, which is how the ledger
stores money too (`transactions.amount` plus `transaction_type`).
"""
import csv
import hashlib
import html
import io
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from pypdf import PdfReader

# Caps. A monthly statement is tens to a few hundred lines; these keep one
# upload's review form, and its round trip through apply, comfortably bounded.
MAX_FILE_BYTES = 1_000_000
MAX_LINES = 500
DESCRIPTION_MAX = 200
REF_MAX = 255            # transactions.import_ref is varchar(255) (#444)

# A ledger row matches a statement line within this many days either side: a
# card charge entered on the day of purchase posts a day or three later.
MATCH_DAYS = 3
# A near miss (same direction, a few days apart, the amount within this share of
# the larger one) is shown as a POSSIBLE match, never decided.
POSSIBLE_SHARE = Decimal("0.10")

# The date formats a CSV mapping may name. An allowlist rather than a free
# strptime string: the model chooses from these, and anything else is refused.
DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y",
    "%Y/%m/%d", "%m-%d-%Y", "%d-%m-%Y", "%b %d, %Y", "%d %b %Y", "%Y%m%d",
)

# Descriptions that usually mean money moving between the user's own accounts.
# Such a line is shown unchecked and labelled until #446 can record it as a
# transfer pair; importing it as spending would double-count it.
_TRANSFER_RE = re.compile(
    r"\b(transfer|xfer|payment\s*-?\s*thank|autopay|auto\s*pay|online\s+pmt|"
    r"e-?payment|epay|card\s+payment|credit\s+card\s+payment)\b",
    re.I,
)


class StatementError(Exception):
    """A statement that cannot be read. The message is shown to the user as is,
    so it never carries the file's contents. Each begins "This file could not
    be read", then says why."""


_NO_COLUMNS = "This file could not be read: its columns couldn't be identified."
# C0 control characters other than tab, CR and LF. No CSV or OFX export holds
# one, and a binary file (a PDF, a photo) almost always does, so it is refused
# here rather than sent to the model to have its "columns" named.
_BINARY = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def looks_binary(text):
    return bool(_BINARY.search(text[:65536]))


@dataclass(frozen=True)
class Line:
    date: date
    amount: Decimal          # always > 0
    direction: str           # 'in' | 'out'
    description: str
    ref: str | None          # OFX FITID, or a derived CSV/screenshot reference
    uncertain: bool = False  # #447: the model could not read it cleanly


@dataclass(frozen=True)
class Statement:
    lines: tuple
    start: date
    end: date
    closing_balance: Decimal | None = None   # #459: OFX, a CSV's running balance, a screenshot's
    skipped: int = 0                          # rows that could not be read
    closing_date: date | None = None         # the day closing_balance applies to


@dataclass(frozen=True)
class CsvMapping:
    header_row: int
    date_col: int
    date_format: str
    description_col: int
    amount_col: int | None = None     # one signed column ...
    out_is_negative: bool = True      # ... and which sign is money out
    debit_col: int | None = None      # ... or separate debit/credit columns
    credit_col: int | None = None
    balance_col: int | None = None    # #459: the running balance after each row


@dataclass(frozen=True)
class Review:
    """One statement line and what the ledger already holds for it."""
    index: int
    line: Line
    status: str              # 'recorded' | 'pending' | 'possible' | 'missing'
    match: object = None     # the ledger row for recorded/pending/possible
    transfer_like: bool = False


# ── Reading the file ─────────────────────────────────────────────────────────

def decode(raw):
    """Bytes to text. UTF-8 (with or without a BOM) first, then Windows-1252,
    which is what most US bank exports that are not UTF-8 actually are."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def looks_like_ofx(text):
    head = text[:4096].upper()
    return "OFXHEADER" in head or "<OFX>" in head or "<OFX " in head


def _clean_description(text):
    text = html.unescape(re.sub(r"\s+", " ", text or "")).strip()
    return text[:DESCRIPTION_MAX]


def money(text):
    """A statement amount string to a signed Decimal, or None if it is not one.
    Accepts `$1,234.56`, `-12.00`, `12.00-` and `(12.00)`. Rejects NaN and
    infinities by construction: only digits reach Decimal()."""
    s = (text or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.endswith("-"):
        negative, s = True, s[:-1]
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("-"):
        negative, s = not negative, s[1:]
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    try:
        value = Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return -value if negative else value


def _bounded_ref(ref):
    ref = (ref or "").strip()
    if not ref:
        return None
    if len(ref) > REF_MAX:
        return "sha:" + hashlib.sha256(ref.encode()).hexdigest()
    return ref


def _period(lines, start=None, end=None):
    dates = [ln.date for ln in lines]
    return (start or min(dates), end or max(dates))


# ── OFX / QFX ────────────────────────────────────────────────────────────────

def _ofx_field(block, name):
    m = re.search(rf"<{name}>\s*([^<\r\n]*)", block, re.I)
    return m.group(1).strip() if m else ""


def _ofx_date(value):
    m = re.match(r"(\d{4})(\d{2})(\d{2})", value or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_ofx(text):
    """OFX 1.x (SGML: leaf elements unclosed) and 2.x (XML) alike: every
    aggregate is closed in both, and leaf values are read up to the next tag or
    line end. No XML parser, so no entity expansion to worry about."""
    statements = len(re.findall(r"<(?:CC)?STMTRS>", text, re.I))
    if statements > 1:
        raise StatementError("This file holds more than one account. "
                             "Export one account at a time.")

    lines, skipped = [], 0
    for block in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.I | re.S):
        when = _ofx_date(_ofx_field(block, "DTPOSTED"))
        amount = money(_ofx_field(block, "TRNAMT"))
        if when is None or amount is None:
            skipped += 1
            continue
        if amount == 0:
            continue
        name = _ofx_field(block, "NAME") or _ofx_field(block, "MEMO")
        lines.append(Line(
            date=when,
            amount=abs(amount),
            direction="out" if amount < 0 else "in",
            description=_clean_description(name),
            ref=_bounded_ref(_ofx_field(block, "FITID")),
        ))

    if not lines:
        raise StatementError("This file could not be read: no transactions were found in it.")
    if len(lines) > MAX_LINES:
        raise StatementError(f"This statement has more than {MAX_LINES} lines. "
                             "Export a shorter date range.")

    closing = as_of = None
    ledger = re.search(r"<LEDGERBAL>(.*?)</LEDGERBAL>", text, re.I | re.S)
    if ledger:
        closing = money(_ofx_field(ledger.group(1), "BALAMT"))
        as_of = _ofx_date(_ofx_field(ledger.group(1), "DTASOF"))

    tranlist = re.search(r"<BANKTRANLIST>(.*?)<STMTTRN>", text, re.I | re.S)
    start = end = None
    if tranlist:
        start = _ofx_date(_ofx_field(tranlist.group(1), "DTSTART"))
        end = _ofx_date(_ofx_field(tranlist.group(1), "DTEND"))
    start, end = _period(lines, start, end)
    closing_date = (as_of or end) if closing is not None else None
    return Statement(tuple(lines), start, end, closing, skipped, closing_date)


# ── CSV ──────────────────────────────────────────────────────────────────────

def csv_rows(text):
    """Every row of a CSV, with the delimiter sniffed from a fixed set."""
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def sample_rows(rows, count=10):
    """The first rows of the file, which is ALL the model sees: any preamble,
    the header and a few transactions. Cells are trimmed and capped so a hostile
    file cannot inflate the call."""
    return [[cell.strip()[:80] for cell in row[:20]] for row in rows[:count]]


def validate_mapping(raw, rows):
    """The model's proposed mapping, checked against the file itself. Anything
    out of range, or a date format outside the allowlist, is refused rather
    than guessed at. Returns a CsvMapping."""
    def index(name):
        value = raw.get(name)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise StatementError(_NO_COLUMNS)
        return value

    header_row = index("header_row")
    if header_row is None or not 0 <= header_row < len(rows):
        raise StatementError(_NO_COLUMNS)
    width = len(rows[header_row])

    cols = {name: index(name) for name in
            ("date_col", "description_col", "amount_col", "debit_col", "credit_col",
             "balance_col")}
    for value in cols.values():
        if value is not None and not 0 <= value < width:
            raise StatementError(_NO_COLUMNS)
    if cols["date_col"] is None or cols["description_col"] is None:
        raise StatementError(_NO_COLUMNS)
    signed = cols["amount_col"] is not None
    split = cols["debit_col"] is not None and cols["credit_col"] is not None
    if signed == split:   # exactly one way of reading amounts
        raise StatementError(_NO_COLUMNS)

    # #459: a balance column the model pointed at another column is dropped,
    # not refused: the file still imports, just with no balance check.
    balance_col = cols["balance_col"]
    if balance_col in [v for k, v in cols.items() if k != "balance_col"]:
        balance_col = None

    date_format = raw.get("date_format")
    if date_format not in DATE_FORMATS:
        raise StatementError("This file could not be read: its dates are in a format "
                             "that isn't supported.")

    return CsvMapping(
        header_row=header_row,
        date_col=cols["date_col"],
        date_format=date_format,
        description_col=cols["description_col"],
        amount_col=cols["amount_col"],
        out_is_negative=bool(raw.get("out_is_negative", True)),
        debit_col=cols["debit_col"] if split else None,
        credit_col=cols["credit_col"] if split else None,
        balance_col=balance_col,
    )


def csv_ref(when, amount, direction, description, occurrence, prefix="csv"):
    """A stable reference for a line with no bank-issued id (a CSV row, or a
    line read from a screenshot). The occurrence count separates identical
    lines on the same day (two $4.50 coffees), so re-importing the same file
    gives every line the same ref."""
    key = f"{when.isoformat()}|{amount}|{direction}|{description.lower()}|{occurrence}"
    return f"{prefix}:" + hashlib.sha256(key.encode()).hexdigest()[:40]


def parse_csv(rows, mapping):
    def cell(row, col):
        return row[col] if col is not None and col < len(row) else ""

    lines, skipped, seen, balances = [], 0, {}, []
    for row in rows[mapping.header_row + 1:]:
        if not any(c.strip() for c in row):
            continue                      # blank line: not a transaction at all
        try:
            when = datetime.strptime(cell(row, mapping.date_col).strip(),
                                     mapping.date_format).date()
        except ValueError:
            skipped += 1                  # a footer, a total, an unreadable date
            continue

        if mapping.amount_col is not None:
            amount = money(cell(row, mapping.amount_col))
            if amount is None:
                skipped += 1
                continue
            if amount == 0:
                continue                  # a zero line moves no money
            out = (amount < 0) if mapping.out_is_negative else (amount > 0)
        else:
            debit = money(cell(row, mapping.debit_col)) or Decimal(0)
            credit = money(cell(row, mapping.credit_col)) or Decimal(0)
            if (debit == 0) == (credit == 0):  # neither, or both: ambiguous
                skipped += 1
                continue
            amount, out = (debit, True) if debit else (credit, False)

        amount = abs(amount)
        direction = "out" if out else "in"
        description = _clean_description(cell(row, mapping.description_col))
        key = (when, amount, direction, description.lower())
        seen[key] = seen.get(key, 0) + 1
        lines.append(Line(when, amount, direction, description,
                          csv_ref(when, amount, direction, description, seen[key])))
        balances.append((when, cell(row, mapping.balance_col)))

    if not lines:
        raise StatementError("This file could not be read: no transactions were found in it.")
    if len(lines) > MAX_LINES:
        raise StatementError(f"This statement has more than {MAX_LINES} lines. "
                             "Export a shorter date range.")
    start, end = _period(lines)
    closing, closing_date = (_csv_closing(balances) if mapping.balance_col is not None
                             else (None, None))
    return Statement(tuple(lines), start, end, closing, skipped, closing_date)


def _csv_closing(balances):
    """(balance, date) from a CSV's running balance (#459): the balance on the
    file's CHRONOLOGICALLY last row, never just its last physical one. A file
    whose dates rise is oldest-first and closes on its last row; one whose
    dates fall is newest-first and closes on its first. A file all on one day,
    or out of order, cannot say which row is last, so it gets no check."""
    dates = [when for when, _cell in balances]
    if dates[0] < dates[-1] and all(a <= b for a, b in pairwise(dates)):
        when, cell = balances[-1]
    elif dates[0] > dates[-1] and all(a >= b for a, b in pairwise(dates)):
        when, cell = balances[0]
    else:
        return None, None
    closing = money(cell)
    return (closing, when) if closing is not None else (None, None)


# ── Matching ─────────────────────────────────────────────────────────────────

def _direction(row):
    return "in" if row.transaction_type == "income" else "out"


def _days(line, row):
    return abs((line.date - row.transaction_date).days)


def _share(line, row):
    amount = Decimal(row.amount)
    return abs(amount - line.amount) / max(amount, line.amount)


def is_possible(line, row):
    """Whether a ledger row is near enough to a statement line to be shown as
    its possible match: the same direction, within ±MATCH_DAYS, the amounts
    within POSSIBLE_SHARE of the larger. The ONE place this rule lives:
    `match_lines()` offers a possible match by it, and apply (#456) re-checks
    it against the row before updating that row to the line, so a hand-edited
    review cannot rewrite an entry the page never offered."""
    return (_direction(row) == line.direction
            and _days(line, row) <= MATCH_DAYS
            and _share(line, row) <= POSSIBLE_SHARE)


def match_lines(lines, ledger):
    """Classify each statement line against the account's ledger rows.

    `ledger` rows need: id, transaction_date, amount, transaction_type,
    is_pending, is_adjustment, import_ref. Adjustments never match: a check-in's
    "Balance check-in" row is not a transaction the bank lists (the blueprint
    warns about them separately).

    Every ledger row matches AT MOST ONE line, so two identical coffees need two
    ledger rows to both count as recorded. Within each pass, pairs are assigned
    closest-first, then by line order and row id, so the result never depends
    on the order the rows came back in:

      1. the same import reference         -> recorded
      2. same direction and amount, ±3 days -> recorded (or pending, if that
                                               ledger row is still pending)
      3. same direction, ±3 days, amount
         within 10% of the larger           -> possible
      4. anything else                      -> missing
    """
    rows = [r for r in ledger if not r.is_adjustment]
    status, match, used = {}, {}, set()

    by_ref = {r.import_ref: r for r in rows if r.import_ref}
    for i, line in enumerate(lines):
        row = by_ref.get(line.ref) if line.ref else None
        if row is not None and row.id not in used:
            status[i], match[i] = "recorded", row
            used.add(row.id)

    # The ONE place a row is held to a single line: candidate lists below
    # include rows already taken, and are filtered here as they are assigned.
    def assign(pairs, label):
        for _key, i, row in sorted(pairs, key=lambda p: (p[0], p[1], p[2].id)):
            if i in status or row.id in used:
                continue
            status[i], match[i] = label(row), row
            used.add(row.id)

    exact = [(_days(line, row), i, row)
             for i, line in enumerate(lines) if i not in status
             for row in rows
             if _direction(row) == line.direction
             and Decimal(row.amount) == line.amount
             and _days(line, row) <= MATCH_DAYS]
    assign(exact, lambda row: "pending" if row.is_pending else "recorded")

    near = [((_share(line, row), _days(line, row)), i, row)
            for i, line in enumerate(lines) if i not in status
            for row in rows if is_possible(line, row)]
    assign(near, lambda row: "possible")

    return [Review(index=i, line=line, status=status.get(i, "missing"),
                   match=match.get(i), transfer_like=bool(_TRANSFER_RE.search(line.description)))
            for i, line in enumerate(lines)]


# ── Categories from the user's own history (#454) ────────────────────────────
#
# Before the model is asked, a missing line takes the category the user last
# gave the same merchant. "The same merchant" is `merchant_key()`, plain string
# handling: the bank prints one shop a dozen ways (`SQ *BLUE BOTTLE 0423`,
# `SQ *BLUE BOTTLE 0611`), and the key throws away what varies.

# A short card-processor token glued to the merchant with `*`: `SQ *`, `TST*`,
# `PAYPAL *`, `DOORDASH*`. No space allowed inside it, so `AMZN Mktp US*2K4AB1`
# is NOT read as a prefix: its `*` is followed by an order reference instead.
_PROCESSOR = re.compile(r"^\s*([a-z0-9]{1,8})\s*\*\s*(.*)$", re.I | re.S)


def _is_reference(word):
    """A store number, date or order reference rather than part of a name:
    all digits (`0423`), or a code at least a third digits (`2k4ab1`). `7eleven`
    and `1800flowerscom` stay."""
    digits = sum(c.isdigit() for c in word)
    return digits == len(word) or (digits >= 2 and digits * 3 >= len(word))


# #455: the bank often ends a line with where it happened. An upper-case state
# code after at least two other words is a place, not a name ("SHOP ME" keeps
# its "ME"; a hand-typed "Blue Bottle ca" keeps its "ca").
_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE "
    "NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())


def _key_words(text):
    tokens = text.split()
    if len(tokens) >= 3 and tokens[-1] in _STATES:
        tokens = tokens[:-1]
    text = re.sub(r"[-.']", "", " ".join(tokens).lower())   # wal-mart, amazon.com, joe's
    words = []
    for w in re.split(r"[^a-z0-9&]+", text):
        if not w:
            continue
        if _is_reference(w):
            # A store number ends the name: what follows it is where the shop
            # is (`BLUE BOTTLE 0611 SAN FRANCISCO CA`). One BEFORE any name
            # word (`#1234 KROGER`) is just skipped.
            if words:
                break
            continue
        words.append(w)
    return words


def merchant_key(description):
    """The part of a description that names the merchant, normalised so the
    bank's variations of one shop agree: lowercase, a processor prefix dropped,
    anything after a `*` dropped, a store number and everything after it
    dropped, and a trailing state code dropped (#455). An empty string means
    nothing usable was left, and is never looked up."""
    text = description or ""
    m = _PROCESSOR.match(text)
    if m:
        words = _key_words(m.group(2).split("*")[0])
        if words:
            return " ".join(words)
        text = m.group(1)            # `AMAZON*AB12CD`: the prefix IS the merchant
    return " ".join(_key_words(text.split("*")[0]))



# ── Clean descriptions (#455) ────────────────────────────────────────────────
#
# A line that can be added is proposed under the name the user already gives
# that merchant, else a cleaned version of the bank's text. Both are keyed by
# merchant_key(), and a cleaned name is built FROM the key, so it keys back to
# the same merchant when the next statement arrives. That is what lets history
# work with no column for the bank's raw text (Sean, 2026-10-07).

def clean_name(description):
    """The bank's text reduced to the merchant: `TST* JOES PIZZA 00123 BROOKLYN
    NY` -> `Joes Pizza`. Empty when nothing usable is left."""
    return " ".join(w.capitalize() for w in merchant_key(description).split())


def _looks_like_bank_text(description):
    """A stored description that still reads as the bank printed it, as every
    import before #455 stored it: a `*`, a store number or reference, or
    several words in capitals. Reusing one would propose the raw text back."""
    words = re.split(r"[^A-Za-z0-9&]+", re.sub(r"[-.']", "", description))
    words = [w for w in words if w]
    return ("*" in description
            or any(_is_reference(w.lower()) for w in words)
            or (len(words) >= 2 and description == description.upper()
                and any(c.isalpha() for c in description)))


def proposed_names(reviews, rows):
    """The description proposed for each line that can be added (missing or
    possible), keyed by line index. The review shows it in an editable field,
    with the bank's text beside it.

    The user's own name for the merchant comes first, from the same two sources
    as history_categories(): this upload's recorded and pending matches, then
    `rows`, earlier rows (categorised or not), most recently dated first, then
    the higher id. A name that is itself still bank text is cleaned. A merchant
    with no history gets clean_name(), and a line with nothing usable keeps the
    bank's text.

    ⚠️ The line's import_ref is untouched: it was derived from the bank's text
    when the statement was parsed, so a re-import still recognises the line."""
    seen = {}

    def note(key, row):
        if key:
            seen.setdefault(key, []).append(row)

    for r in reviews:
        if r.status in ("recorded", "pending") and r.match is not None:
            note(merchant_key(r.line.description), r.match)
    for row in rows:
        note(merchant_key(row.description), row)

    names = {}
    for r in reviews:
        if r.status not in ("missing", "possible"):
            continue
        mine = seen.get(merchant_key(r.line.description))
        if mine:
            name = max(mine, key=lambda row: (row.transaction_date, row.id)).description or ""
            if _looks_like_bank_text(name):
                name = clean_name(name)
        else:
            name = clean_name(r.line.description)
        names[r.index] = (name or r.line.description)[:DESCRIPTION_MAX]
    return names

def history_categories(reviews, rows, kinds):
    """Category ids from the user's own history for the lines that would
    otherwise go to the model (missing, not transfer-like), keyed by line index.

    Two sources, both the user's own rows:
      1. this upload's recorded and pending matches: the LINE's merchant key is
         paired with the matched row's category. That is what teaches the bank's
         `SQ *BLUE BOTTLE` the category of a row typed by hand as "Coffee";
      2. `rows`, earlier categorised rows, each under its own description's key.

    `rows` (and the matched rows) need: id, transaction_date, description,
    category_id. `kinds` maps the user's category ids to 'expense'/'income'; a
    category of the wrong kind for the line (an expense category on a refund),
    or one not in `kinds`, is never used. When a merchant has been filed under
    more than one category, the most recently dated row wins (then the higher
    id), so a recategorisation takes effect at once.
    """
    seen = {}

    def note(key, row):
        if key and row.category_id is not None:
            seen.setdefault(key, []).append(row)

    for r in reviews:
        if r.status in ("recorded", "pending") and r.match is not None:
            note(merchant_key(r.line.description), r.match)
    for row in rows:
        note(merchant_key(row.description), row)

    found = {}
    for r in reviews:
        if r.status != "missing" or r.transfer_like:
            continue
        kind = "income" if r.line.direction == "in" else "expense"
        usable = [row for row in seen.get(merchant_key(r.line.description), ())
                  if kinds.get(row.category_id) == kind]
        if usable:
            latest = max(usable, key=lambda row: (row.transaction_date, row.id))
            found[r.index] = latest.category_id
    return found


def adjustments_within(ledger, start, end):
    """Balance check-in adjustments dated inside the statement period. Each one
    may already stand in for money the statement now lists, so adding those
    lines would count it twice. Shown as a warning, never acted on."""
    return [r for r in ledger
            if r.is_adjustment and start <= r.transaction_date <= end]



def unlisted_rows(reviews, ledger, start, end):
    """The account's ledger rows that the statement does not list (#457), as
    (unlisted, still_pending). Matching only runs from line to row, so without
    this a row entered twice, typo'd or cancelled at the bank is never seen.

    A row is "used" when `match_lines()` gave it to a line, recorded, pending
    or possible alike: the reviews carry exactly that set, so the two cannot
    disagree. Of the rest, inside the statement period:
      - adjustments are left out (the blueprint already warns about them);
      - pending rows are still_pending, not a problem;
      - rows in the period's last MATCH_DAYS days are left out, since a
        purchase made then usually posts on the next statement.
    Only lists rows; nothing here (or in the review) changes one."""
    used = {r.match.id for r in reviews if r.match is not None}
    settled = end - timedelta(days=MATCH_DAYS)
    unlisted, still_pending = [], []
    for row in ledger:
        if row.id in used or row.is_adjustment or not start <= row.transaction_date <= end:
            continue
        if row.is_pending:
            still_pending.append(row)
        elif row.transaction_date <= settled:
            unlisted.append(row)
    return unlisted, still_pending



def compare_balance(ledger, closing, card):
    """The gap between the ledger and a statement's closing balance (#459),
    ledger minus statement. The ledger holds a card that owes money as a
    NEGATIVE balance, but banks and formats differ on the sign they print the
    amount owed in, and a CSV column or a screenshot carries no convention at
    all. So for a card the statement's figure is read as +x or -x, whichever
    is nearer the ledger (Sean, 2026-10-07): right under either convention.
    Every other account is compared as printed."""
    gap = ledger - closing
    if card:
        gap = min(gap, ledger + closing, key=abs)
    return gap.quantize(Decimal("0.01"))


def explain_gap(gap, unticked, unlisted):
    """The ONE thing whose amount equals the gap exactly (#459), or None: an
    unticked line that adding would close it (money out lowers the ledger, so
    it closes a ledger that is above), or a ledger-only row (#457) that the
    ledger would agree without. Two candidates explain nothing, so neither is
    named. `unticked` are Lines; `unlisted` are ledger rows."""
    if not gap:
        return None
    found = [ln for ln in unticked
             if (ln.amount if ln.direction == "in" else -ln.amount) == -gap]
    found += [row for row in unlisted
              if (row.amount if row.transaction_type == "income" else -row.amount) == gap]
    return found[0] if len(found) == 1 else None

def review_plan(reviews, suggested, pairings):
    """How the review opens (#458), as (plan, needs).

    `plan` maps each line ticked by default to what applying it will do:
    'add', 'posted' (a pending entry the bank has now posted) or 'transfer'
    (a transfer line whose other leg was found, #446). The template reads it
    for every checkbox, and the summary counts it, so what the page shows ticked
    and what it says applying will do come from one rule.

    `needs` is the lines that need a decision: a possible match (update, add or
    skip), a transfer-looking line with no other leg found, a line the model
    found hard to read, and a missing line with no category. The last stays
    ticked, since it can be added uncategorised; the others start unticked."""
    plan, needs = {}, set()
    for r in reviews:
        i = r.index
        if r.status == "recorded":
            continue
        if r.line.uncertain or r.status == "possible":
            needs.add(i)
        elif r.status == "pending":
            plan[i] = "posted"
        elif i in pairings:
            plan[i] = "transfer"
        elif r.transfer_like:
            needs.add(i)
        else:
            plan[i] = "add"
            if suggested.get(i) is None:
                needs.add(i)
    return plan, needs


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def apply_summary(counts):
    """What applying will do, in words: "Adding 3 transactions, marking 1
    posted." `counts` maps 'add' / 'update' / 'posted' / 'transfer' to how many
    ticked lines will do each. ⚠️ base.html's review-summary listener phrases
    the same counts the same way as boxes are ticked; change both together."""
    parts = []
    if counts.get("add"):
        parts.append("adding " + _plural(counts["add"], "transaction", "transactions"))
    if counts.get("update"):
        parts.append("updating " + _plural(counts["update"], "entry", "entries"))
    if counts.get("posted"):
        parts.append(f"marking {counts['posted']} posted")
    if counts.get("transfer"):
        parts.append("recording " + _plural(counts["transfer"], "transfer", "transfers"))
    if not parts:
        return "Nothing will be changed."
    text = ", ".join(parts) + "."
    return text[0].upper() + text[1:]

def find_counterpart(line, rows, used=()):
    """The other account's existing plain row that a transfer line should pair
    with (#446), or None. Same amount, OPPOSITE direction (money into the card
    is money out of checking), within ±MATCH_DAYS, closest date first; never a
    row that is already a transfer leg or an adjustment, nor one in `used`.

    The ONE place this rule lives: the review calls it to show the pairing, and
    apply calls it again inside its write transaction to perform it, so what
    is shown and what is done cannot drift apart."""
    opposite = "in" if line.direction == "out" else "out"
    candidates = [r for r in rows
                  if not r.is_transfer and not r.is_adjustment and r.id not in used
                  and _direction(r) == opposite
                  and Decimal(r.amount) == line.amount
                  and _days(line, r) <= MATCH_DAYS]
    candidates.sort(key=lambda r: (_days(line, r), r.id))
    return candidates[0] if candidates else None


# ── Screenshots (#447) ───────────────────────────────────────────────────────
#
# A screenshot of a banking app is read by the model (ai.read_screenshots), and
# everything it returns is untrusted: lines_from_screenshots() re-checks every
# field, resolves a missing year, and marks anything doubtful as uncertain, so
# the review shows it unticked rather than guessing.

MAX_IMAGES = 5
MAX_IMAGE_BYTES = 5_000_000
MAX_UPLOAD_BYTES = 15_000_000    # the whole request; see RUNBOOK §3 for Nginx

# #460: a monthly statement is a handful of pages. Both caps are checked before
# the model is asked: every page is billed as its text AND a rendered image.
MAX_PDF_BYTES = 10_000_000
MAX_PDF_PAGES = 12

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def image_type(raw):
    """The media type of an image the model can read, from its first bytes
    (never the filename or the browser's claim), or None."""
    for magic, media_type in _IMAGE_MAGIC:
        if raw.startswith(magic):
            return media_type
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def resolve_date(month, day, year, today):
    """A screenshot line's date. Most banking apps show "Dec 30" with no year;
    then the year is the one that puts the date on or before today, never in
    the future (on Jan 5, "Dec 30" is last year). Returns None for an
    impossible date."""
    if year is not None:
        try:
            return date(year, month, day)
        except (TypeError, ValueError):
            return None
    # The most recent year in which that day exists and is not after today.
    # Four years back covers Feb 29: on Jan 1 2029, "Feb 29" is 2028's.
    for candidate in range(today.year, today.year - 5, -1):
        try:
            when = date(candidate, month, day)
        except (TypeError, ValueError):
            continue
        if when <= today:
            return when
    return None


def screenshot_balance(raw, today):
    """(balance, date) from a screenshot (#459), or (None, None). Only a
    balance the model ties to a date, a statement balance or the running
    balance beside a line, is used. An "available" balance is today's and
    includes holds, so it is not the balance after any line the review shows."""
    if not raw or raw.get("kind") not in ("statement", "after_line"):
        return None, None
    when = resolve_date(raw.get("month"), raw.get("day"), raw.get("year"), today)
    amount = money(str(raw.get("amount") or ""))
    if when is None or amount is None:
        return None, None
    return amount, when


def lines_from_screenshots(raw_lines, today, balance=None, noun="These screenshots"):
    """The model's lines, re-checked. A line with no readable date or amount
    cannot be shown at all and is counted as skipped; one the model flagged as
    hard to read, or with a direction it could not name, is kept but marked
    uncertain."""
    lines, skipped, seen = [], 0, {}
    for raw in raw_lines:
        when = resolve_date(raw.get("month"), raw.get("day"), raw.get("year"), today)
        amount = money(str(raw.get("amount") or ""))
        if when is None or amount is None or amount == 0:
            skipped += 1
            continue
        direction = raw.get("direction")
        uncertain = not raw.get("legible", False) or direction not in ("in", "out")
        if direction not in ("in", "out"):
            direction = "out" if amount < 0 else "in"
        amount = abs(amount)
        description = _clean_description(str(raw.get("description") or ""))
        key = (when, amount, direction, description.lower())
        seen[key] = seen.get(key, 0) + 1
        lines.append(Line(when, amount, direction, description,
                          csv_ref(when, amount, direction, description, seen[key], "img"),
                          uncertain))
    if not lines:
        raise StatementError(f"{noun} could not be read: no transactions were found.")
    if len(lines) > MAX_LINES:
        raise StatementError(f"{noun} list more than {MAX_LINES} transactions.")
    start, end = _period(lines)
    closing, closing_date = screenshot_balance(balance, today)
    return Statement(tuple(lines), start, end, closing, skipped, closing_date)


# ── PDF statements (#460) ───────────────────────────────────────────────────
#
# The same untrusted-lines path as screenshots: the model reads the PDF, and
# everything it returns is re-checked here. pypdf only ever opens the file to
# count its pages and see whether it is locked, BEFORE the model is asked.

_PDF_UNREADABLE = ("This file could not be read: the PDF is damaged or "
                   "password-protected. Download the statement again, or export "
                   "OFX or CSV instead.")


def is_pdf(raw):
    """A PDF, from its first bytes alone, never the filename."""
    return raw.startswith(b"%PDF-")


def check_pdf(raw):
    """The page count of a PDF the model may be sent, or a StatementError.
    Many banks lock a statement with only an OWNER password (no printing, no
    copying); pypdf opens that with an empty password by itself, and it is
    fine. One that needs a password to open makes pypdf raise
    FileNotDecryptedError when its pages are counted, and is refused below,
    as is one pypdf cannot parse at all."""
    try:
        pages = len(PdfReader(io.BytesIO(raw)).pages)
    except Exception as e:   # pypdf raises many types on a locked or malformed file
        raise StatementError(_PDF_UNREADABLE) from e
    if pages > MAX_PDF_PAGES:
        raise StatementError(f"This PDF has more than {MAX_PDF_PAGES} pages. "
                             "Upload one month's statement at a time.")
    return pages


def _pdf_period(raw, today):
    """(start, end) of the statement period the model read, or None."""
    if not raw:
        return None
    start = resolve_date(raw.get("start_month"), raw.get("start_day"),
                         raw.get("start_year"), today)
    end = resolve_date(raw.get("end_month"), raw.get("end_day"), raw.get("end_year"), today)
    if start is None or end is None:
        return None
    return start, end   # a backwards period holds no line, so lines_from_pdf drops it


def lines_from_pdf(read, today):
    """The model's reading of a PDF statement, re-checked. Its lines and its
    balance go through the screenshot rules; its period is the bank's, used
    only when it holds every line read (else the lines' own range, as for a
    CSV). A PDF that lists more than one account is refused, as a
    multi-account OFX is."""
    if (read.get("account_count") or 1) > 1:
        raise StatementError("This PDF holds more than one account. "
                             "Upload one account's statement at a time.")
    statement = lines_from_screenshots(read.get("lines") or [], today,
                                       read.get("balance"), noun="This PDF")
    period = _pdf_period(read.get("period"), today)
    if period and period[0] <= statement.start and statement.end <= period[1]:
        statement = replace(statement, start=period[0], end=period[1])
    return statement

