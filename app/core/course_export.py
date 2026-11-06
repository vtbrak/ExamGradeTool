"""Ingest D2L (Brightspace) course-grade exports.

These are a different shape from the Scantron per-exam exports handled by
`parsers.py`: one row per student, one column per graded item, with the item's
maximum baked into the header text, e.g.

    Exam 1 - Individual Points Grade <Numeric MaxPoints:50 Category:Exams>

Two layouts show up in practice, and both are supported here:

* **individual/partner/final** -- newer semesters, where each exam contributes
  three columns (the Individual round, the Partner/Group round, and the blended
  Final) at 50 points each.
* **single score** -- older semesters, where each exam is a single column at 80
  points and the Individual/Group breakdown simply wasn't recorded.

The same file also carries the course-level `Adjusted Final Grade
Numerator`/`Denominator` pair. "Adjusted" means the calculated numerator plus
extra credit, with the occasional manual instructor override on top; the
denominator is per-student because D2L drops items a student was exempted
from, which is why the pair has to be kept rather than collapsed to a percent.
"""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from app.core.parsers import StudentIdProblem, clean_student_id, has_hash_prefix

# "<label> Points Grade <Numeric MaxPoints:50 Category:Exams>"
_ITEM_HEADER = re.compile(
    r"^(?P<label>.*?)\s+Points Grade\s*<Numeric\s+MaxPoints:(?P<max>[\d.]+)"
    r"(?:\s*Category:(?P<category>[^>]*))?>$"
)

# "Exam 1 - Individual" / "Exam 1 - Partner" / "Exam 1 - Final"
_EXAM_ROUND = re.compile(r"^Exam\s*(?P<number>\d+)\s*-\s*(?P<round>Individual|Partner|Final)$", re.I)
# "Exam 1" on its own -- deliberately anchored so that sibling items such as
# "Student Questions - Exam 1" and "Lecture Summary 1" are not mistaken for exams.
_EXAM_SINGLE = re.compile(r"^Exam\s*(?P<number>\d+)$", re.I)

ADJUSTED_NUMERATOR = "Adjusted Final Grade Numerator"
ADJUSTED_DENOMINATOR = "Adjusted Final Grade Denominator"
CALCULATED_NUMERATOR = "Calculated Final Grade Numerator"
CALCULATED_DENOMINATOR = "Calculated Final Grade Denominator"

FORMAT_INDIVIDUAL_PARTNER_FINAL = "individual_partner_final"
FORMAT_SINGLE_SCORE = "single_score"

# Which final-grade pair a sheet carries: the oldest exports have only the
# calculated pair, with no adjusted columns at all.
BASIS_ADJUSTED = "adjusted"
BASIS_CALCULATED = "calculated"

# What a graded item counts towards, besides the exams. Taken from the header's
# Category where there is one, and the label otherwise -- real sheets put the
# lab in no category at all.
COMPONENT_LAB = "lab"
COMPONENT_EXTRA_CREDIT = "extra_credit"
COMPONENT_OTHER = "other"
# Read but kept out of every bin (see `is_uncounted`). D2L's own final-grade
# pair still includes whatever it included; these are only left out of the
# lab / extra credit / other breakdown.
COMPONENT_UNCOUNTED = "uncounted"

# Textbook / homework-platform registration items: 100-point markers that a
# publisher's tool writes into the gradebook, not coursework. Decided
# 2026-10-21 from the headers of every semester since Fall 2016.
_UNCOUNTED_LABEL_PREFIXES = ("essential cell biology",)
# Placeholder items (Fall 2025 has "Not Graded" worth 0.01).
_UNCOUNTED_LABELS = {"not graded"}
# Anything worth less than a point is a placeholder too.
_UNCOUNTED_BELOW_POINTS = 1.0


@dataclass
class ExamColumns:
    """Where a single exam's columns live in the sheet, and what they're worth."""

    exam_name: str
    individual: Optional[str] = None
    partner: Optional[str] = None
    final: Optional[str] = None
    single: Optional[str] = None
    possible_points: Optional[float] = None

    @property
    def single_score_only(self) -> bool:
        return self.single is not None


@dataclass
class CourseExamScore:
    exam_name: str
    individual_score: Optional[float]
    raw_group_score: Optional[float]
    final_score: Optional[float]
    possible_points: Optional[float]
    single_score_only: bool


@dataclass
class ComponentTotal:
    """What a student earned in one group of items, and out of how much.

    `possible` counts only the items they actually have a number for: an
    `Exempt` cell is excused and a blank states nothing, so neither is counted
    on either side rather than being guessed at as a zero."""

    earned: float = 0.0
    possible: float = 0.0
    items: int = 0

    @property
    def percent(self) -> Optional[float]:
        return self.earned / self.possible * 100.0 if self.possible else None


@dataclass
class CourseGradeRow:
    student_id: Optional[str]
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    adjusted_numerator: Optional[float]
    adjusted_denominator: Optional[float]
    calculated_numerator: Optional[float]
    calculated_denominator: Optional[float]
    exams: dict = field(default_factory=dict)  # exam_name -> CourseExamScore
    # component -> ComponentTotal, for lab / extra credit / everything else
    components: dict = field(default_factory=dict)

    @property
    def grade_basis(self) -> Optional[str]:
        """Which final-grade pair this sheet actually carries.

        Newer exports have both `Calculated Final Grade` and `Adjusted Final
        Grade`; the oldest ones have only the calculated pair. The basis is
        recorded rather than papered over, so a course grade is never silently
        presented as adjusted when the file never adjusted it."""
        # A pair needs both halves: a sheet can carry the denominator for a
        # student whose numerator is blank or "Exempt", and that is no grade.
        if self.adjusted_denominator and self.adjusted_numerator is not None:
            return BASIS_ADJUSTED
        if self.calculated_denominator and self.calculated_numerator is not None:
            return BASIS_CALCULATED
        return None

    @property
    def numerator(self) -> Optional[float]:
        return (
            self.adjusted_numerator
            if self.grade_basis == BASIS_ADJUSTED
            else self.calculated_numerator
        )

    @property
    def denominator(self) -> Optional[float]:
        return (
            self.adjusted_denominator
            if self.grade_basis == BASIS_ADJUSTED
            else self.calculated_denominator
        )

    @property
    def percent(self) -> Optional[float]:
        """The sheet's own final percentage, from whichever pair it provides."""
        if not self.denominator or self.numerator is None:
            return None
        return self.numerator / self.denominator * 100.0

    @property
    def adjusted_percent(self) -> Optional[float]:
        if not self.adjusted_denominator or self.adjusted_numerator is None:
            return None
        return self.adjusted_numerator / self.adjusted_denominator * 100.0

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or (self.username or self.student_id or "(unknown)")


@dataclass
class ParsedCourseExport:
    source_path: str
    layout: str
    exam_columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    # Rows carrying neither a student id nor a usable name -- nothing to match on.
    unidentified_rows: list = field(default_factory=list)
    # Rows whose ID doesn't look like a student ID: reported, never corrected,
    # and kept out of `rows` so a bad ID can't create a second student record.
    id_problem_rows: list = field(default_factory=list)
    has_id_column: bool = False
    # Graded items read but left out of every component bin (textbook
    # registration markers, placeholders): listed so the preview can show them.
    uncounted_columns: list = field(default_factory=list)
    # How many IDs arrived with D2L's leading "#", which is stripped. Counted
    # so the preview can say it happened rather than silently rewriting IDs.
    hash_prefixed_ids: int = 0

    @property
    def exam_names(self) -> list:
        return [c.exam_name for c in self.exam_columns]


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def _number(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_item_header(header: str):
    """Split a D2L grade-item header into (label, max_points, category).

    Returns None for columns that aren't graded items (identity columns,
    subtotals, the End-of-Line Indicator, and so on)."""
    match = _ITEM_HEADER.match(str(header).strip())
    if not match:
        return None
    return (
        match.group("label").strip(),
        _number(match.group("max")),
        (match.group("category") or "").strip() or None,
    )


def find_exam_columns(columns) -> tuple:
    """Locate the exam columns in a course export by reading the headers.

    Returns `(exam_columns, layout)`. Exams are ordered by their number, so
    "Exam 10" sorts after "Exam 2" rather than between "Exam 1" and "Exam 2"."""
    by_number: dict = {}

    for column in columns:
        parsed = parse_item_header(column)
        if parsed is None:
            continue
        label, max_points, _category = parsed

        round_match = _EXAM_ROUND.match(label)
        if round_match:
            number = int(round_match.group("number"))
            entry = by_number.setdefault(number, ExamColumns(exam_name=f"Exam {number}"))
            round_name = round_match.group("round").lower()
            setattr(entry, round_name, column)
            entry.possible_points = max_points
            continue

        single_match = _EXAM_SINGLE.match(label)
        if single_match:
            number = int(single_match.group("number"))
            entry = by_number.setdefault(number, ExamColumns(exam_name=f"Exam {number}"))
            entry.single = column
            entry.possible_points = max_points

    exam_columns = [by_number[n] for n in sorted(by_number)]
    layout = (
        FORMAT_SINGLE_SCORE
        if exam_columns and all(c.single_score_only for c in exam_columns)
        else FORMAT_INDIVIDUAL_PARTNER_FINAL
    )
    return exam_columns, layout


def is_uncounted(label: str, max_points: Optional[float]) -> bool:
    """A graded item that is deliberately left out of every component bin."""
    text = " ".join(label.split()).casefold()
    if text in _UNCOUNTED_LABELS:
        return True
    if text.startswith(_UNCOUNTED_LABEL_PREFIXES):
        return True
    return max_points is not None and max_points < _UNCOUNTED_BELOW_POINTS


def classify_component(
    label: str, category: Optional[str], max_points: Optional[float] = None
) -> Optional[str]:
    """Which bin a non-exam item belongs to, or None if it's an exam item.

    Exams are handled by `find_exam_columns`; the lab carries no category in
    real sheets, so it is recognised by its label. COMPONENT_UNCOUNTED marks an
    item read but left out of every bin (`is_uncounted`)."""
    category = (category or "").strip()
    if category.casefold() == "exams" or _EXAM_ROUND.match(label) or _EXAM_SINGLE.match(label):
        return None
    if is_uncounted(label, max_points):
        return COMPONENT_UNCOUNTED
    if label.casefold().startswith("lab grade") or category.casefold() == "lab":
        return COMPONENT_LAB
    if "extra credit" in label.casefold() or "extra credit" in category.casefold():
        return COMPONENT_EXTRA_CREDIT
    return COMPONENT_OTHER


def find_component_columns(columns) -> dict:
    """`component -> [(column, max_points)]` for every non-exam graded item."""
    found: dict = {}
    for column in columns:
        parsed = parse_item_header(column)
        if parsed is None:
            continue
        label, max_points, category = parsed
        component = classify_component(label, category, max_points)
        if component is not None:
            found.setdefault(component, []).append((column, max_points))
    return found


def _find_identity_columns(columns) -> dict:
    """Match D2L's identity headers case/space-insensitively.

    Real exports lead with OrgDefinedId and Username; the anonymized samples
    strip both, leaving only the name columns, so every one of these is
    treated as optional."""
    wanted = {
        "orgdefinedid": "student_id",
        "studentid": "student_id",
        "username": "username",
        "lastname": "last_name",
        "firstname": "first_name",
    }
    found: dict = {}
    for column in columns:
        key = re.sub(r"[^a-z]", "", str(column).lower())
        field_name = wanted.get(key)
        if field_name and field_name not in found:
            found[field_name] = column
    return found


def load_course_export(path) -> pd.DataFrame:
    path = Path(path)
    # .xlsx and .csv only; see parsers.load_raw_export for why .xls isn't.
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"{path.name}: expected a .csv or .xlsx file, not {path.suffix or 'no extension'}")


def parse_course_export(path) -> ParsedCourseExport:
    df = load_course_export(path)
    path = Path(path)

    exam_columns, layout = find_exam_columns(df.columns)
    component_columns = find_component_columns(df.columns)
    uncounted = component_columns.pop(COMPONENT_UNCOUNTED, [])
    identity = _find_identity_columns(df.columns)

    parsed = ParsedCourseExport(
        source_path=str(path),
        layout=layout,
        exam_columns=exam_columns,
        has_id_column="student_id" in identity,
        uncounted_columns=[column for column, _ in uncounted],
    )

    def cell(row, column):
        return row.get(column) if column and column in df.columns else None

    for row_number, (_, raw) in enumerate(df.iterrows(), start=2):  # header is row 1
        # D2L closes every student row with a "#" sentinel; trailing sheet
        # furniture (the letter-grade cutoff table lives in spare columns of
        # the same sheet) has no identity at all and is skipped below.
        raw_id = cell(raw, identity.get("student_id"))
        if has_hash_prefix(raw_id):
            parsed.hash_prefixed_ids += 1
        student_id, id_problem = clean_student_id(raw_id)
        record = CourseGradeRow(
            student_id=student_id,
            username=_clean(cell(raw, identity.get("username"))),
            first_name=_clean(cell(raw, identity.get("first_name"))),
            last_name=_clean(cell(raw, identity.get("last_name"))),
            adjusted_numerator=_number(cell(raw, ADJUSTED_NUMERATOR)),
            adjusted_denominator=_number(cell(raw, ADJUSTED_DENOMINATOR)),
            calculated_numerator=_number(cell(raw, CALCULATED_NUMERATOR)),
            calculated_denominator=_number(cell(raw, CALCULATED_DENOMINATOR)),
        )

        for component, items in component_columns.items():
            total = ComponentTotal()
            for column, max_points in items:
                value = _number(cell(raw, column))
                if value is None:  # blank, or "Exempt": states nothing either way
                    continue
                total.earned += value
                total.possible += max_points or 0
                total.items += 1
            if total.items:
                record.components[component] = total

        for entry in exam_columns:
            if entry.single_score_only:
                final_score = _number(cell(raw, entry.single))
                individual = group = None
            else:
                individual = _number(cell(raw, entry.individual))
                group = _number(cell(raw, entry.partner))
                final_score = _number(cell(raw, entry.final))
            if individual is None and group is None and final_score is None:
                continue
            record.exams[entry.exam_name] = CourseExamScore(
                exam_name=entry.exam_name,
                individual_score=individual,
                raw_group_score=group,
                final_score=final_score,
                possible_points=entry.possible_points,
                single_score_only=entry.single_score_only,
            )

        identified = record.student_id or (record.last_name and record.first_name)
        has_data = record.exams or record.numerator is not None
        if not identified:
            if has_data:
                parsed.unidentified_rows.append(record)
            continue
        if id_problem:
            parsed.id_problem_rows.append(
                StudentIdProblem(row_number, record.student_id, id_problem)
            )
            continue
        parsed.rows.append(record)

    return parsed
