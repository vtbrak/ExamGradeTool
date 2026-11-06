"""Semester codes: parsing, validation, and matching across the two formats.

The university has used two term-code formats over time:

* **Current, 6 digits -- ``YYYYTT``.** Four-digit year, then a two-digit term:
  ``10`` = Spring, ``20`` = Summer, ``30`` = Fall. ``202610`` is Spring 2026.
* **Older, 4 digits -- ``CYYT``.** A three-digit year (century digit plus
  two-digit year, so ``220`` is 2020), then a one-digit term: ``1`` = Spring,
  ``2`` = Summer 1, ``3`` = Summer 2, ``5`` = Fall. ``2201`` is Spring 2020.

Only the term codes above are accepted. Any other term digit is rejected
instead of guessed at.

Both formats can legitimately sit in the same database: older semesters were
issued the 4-digit code, newer ones the 6-digit code. The danger is the *same*
semester being saved under both (``2201`` and ``202010``), which would create two
unrelated course offerings for one term. `same_semester` is what lets callers
catch that.

Spring and Fall map one-to-one between the formats. Summer does not: the older
format had two sessions and the current one has a single Summer term, with no
stated correspondence between them. So a current-format Summer is never treated
as the same semester as either older session. `related_summer_codes` reports
the possible overlap for a person to judge instead.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

SPRING = "Spring"
SUMMER = "Summer"
SUMMER_1 = "Summer 1"
SUMMER_2 = "Summer 2"
FALL = "Fall"

SUMMER_TERMS = frozenset({SUMMER, SUMMER_1, SUMMER_2})

# Calendar order within a year, for sorting. Summer and the two older summer
# sessions never share a year in practice (they belong to different code
# eras), so their relative order only matters for keeping sorting stable.
_TERM_ORDER = {SPRING: 1, SUMMER_1: 2, SUMMER: 3, SUMMER_2: 4, FALL: 5}

_CURRENT_TERMS = {"10": SPRING, "20": SUMMER, "30": FALL}
_LEGACY_TERMS = {"1": SPRING, "2": SUMMER_1, "3": SUMMER_2, "5": FALL}

FORMAT_CURRENT = "current"
FORMAT_LEGACY = "legacy"

EXAMPLE_HELP = (
    "Use the 6-digit code, year then term — 10 Spring, 20 Summer, 30 Fall "
    "(202610 = Spring 2026) — or the older 4-digit code, whose last digit is "
    "1 Spring, 2 Summer 1, 3 Summer 2, 5 Fall (2201 = Spring 2020)."
)


@dataclass(frozen=True)
class Semester:
    code: str
    year: int
    term: str
    format: str

    @property
    def label(self) -> str:
        # "Summer 1, 2020" rather than "Summer 1 2020", which reads as one number.
        separator = ", " if self.term[-1].isdigit() else " "
        return f"{self.term}{separator}{self.year}"

    @property
    def key(self) -> tuple:
        """Identity of the term itself, independent of which format named it."""
        return (self.year, self.term)

    @property
    def sort_key(self) -> tuple:
        return (self.year, _TERM_ORDER[self.term])


def parse_semester_code(code) -> Optional[Semester]:
    """Interpret a semester code, or return None if it isn't a valid one."""
    if code is None:
        return None
    text = str(code).strip()
    if not text.isdigit():
        return None

    if len(text) == 6:
        term = _CURRENT_TERMS.get(text[4:])
        if term is None:
            return None
        return Semester(text, int(text[:4]), term, FORMAT_CURRENT)

    if len(text) == 4:
        term = _LEGACY_TERMS.get(text[3])
        if term is None:
            return None
        # The three-digit year is a century digit plus a two-digit year:
        # 220 -> 2020, and by the same rule 199 -> 1999.
        return Semester(text, 1800 + int(text[:3]), term, FORMAT_LEGACY)

    return None


def describe(code) -> str:
    """ "202610 (Spring 2026)", or the bare code if it can't be interpreted."""
    semester = parse_semester_code(code)
    return f"{code} ({semester.label})" if semester else str(code)


def same_semester(code_a, code_b) -> bool:
    a, b = parse_semester_code(code_a), parse_semester_code(code_b)
    return a is not None and b is not None and a.key == b.key


def find_existing_code(code, existing_codes: Iterable[str]) -> Optional[str]:
    """The code already in use for the same semester as `code`, if any.

    An exact match wins; otherwise a code naming the same term in the other
    format (``2201`` for ``202010``) is returned."""
    existing_codes = list(existing_codes)
    if code in existing_codes:
        return code
    for existing in existing_codes:
        if same_semester(code, existing):
            return existing
    return None


def related_summer_codes(code, existing_codes: Iterable[str]) -> list:
    """Summer codes from the *other* format in the same year as `code`.

    These aren't treated as the same semester (see the module docstring), but
    typing 202020 while 2202 is already saved may still be a mistake, so callers
    surface them as a warning. Two older-format sessions in one year (2202 and
    2203) are normal and are not reported."""
    semester = parse_semester_code(code)
    if semester is None or semester.term not in SUMMER_TERMS:
        return []
    related = []
    for existing in existing_codes:
        other = parse_semester_code(existing)
        if (
            other is not None
            and other.format != semester.format
            and other.year == semester.year
            and other.term in SUMMER_TERMS
        ):
            related.append(existing)
    return related


def chronological_sort_key(code) -> tuple:
    """Sort key putting semesters in calendar order across both formats.

    Plain text sorting gets mixed formats wrong ("2201" sorts after "202610"
    even though Spring 2020 came first). Codes that can't be parsed sort
    before every real semester, so reversing the sort still leaves them at
    the bottom of a newest-first list."""
    semester = parse_semester_code(code)
    if semester is None:
        return (0, 0, 0, str(code))
    return (1, *semester.sort_key, "")


def newest_first(codes: Iterable) -> list:
    return sorted(codes, key=chronological_sort_key, reverse=True)
