"""Ingest raw Scantron-style exam exports (CSV or XLSX) into per-round score rows."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

# The Key Version that identifies a row as the Group (Round 2, form C) exam.
# Any other Key Version (A, B, ...) is treated as an Individual (Round 1) row.
GROUP_KEY_VERSION = "C"

# CustomID is not required: the Exam Scanner app doesn't write it, and StudentID
# is the only ID any of the tools use. Older ZipGrade exports that do have it
# still fall back to it when StudentID is blank.
REQUIRED_COLUMNS = [
    "QuizName",
    "QuizClass",
    "FirstName",
    "LastName",
    "StudentID",
    "Earned Points",
    "Possible Points",
    "Key Version",
]


@dataclass
class StudentScoreRow:
    student_id: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    score: Optional[float]
    possible_points: Optional[float]
    key_version: Optional[str]
    round: str  # "individual" or "group"


@dataclass
class StudentIdProblem:
    """An ID the file states that doesn't look like a student ID.

    `row` is the row number as the file opens in Excel, with the header as row
    1, so the reported row can be found and corrected directly."""

    row: int
    student_id: Optional[str]
    problem: str


@dataclass
class ParsedFile:
    source_path: str
    exam_name: Optional[str]
    semester_code: Optional[str]
    individual_rows: list = field(default_factory=list)
    group_rows: list = field(default_factory=list)
    # round -> {student_id: [StudentScoreRow, ...]} for every id that appears more than once
    duplicate_student_ids: dict = field(default_factory=dict)
    # rows with no usable StudentID/CustomID, can't be matched until fixed
    missing_id_rows: list = field(default_factory=list)
    # rows whose ID doesn't look like a student ID -- reported, never corrected
    id_problem_rows: list = field(default_factory=list)

    @property
    def flagged_student_ids(self) -> set:
        return {p.student_id for p in self.id_problem_rows if p.student_id}


def load_raw_export(path) -> pd.DataFrame:
    path = Path(path)
    # .xlsx and .csv only. Reading a legacy .xls needs the xlrd package, which
    # isn't installed; accepting the suffix only moved the failure to a
    # "missing optional dependency" message. No export has ever been .xls.
    if path.suffix.lower() == ".xlsx":
        df = pd.read_excel(path)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError(f"{path.name}: expected a .csv or .xlsx file, not {path.suffix or 'no extension'}")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name} is missing required column(s): {', '.join(missing)}")
    return df


def parse_exam_file(path) -> ParsedFile:
    df = load_raw_export(path)
    path = Path(path)

    parsed = ParsedFile(
        source_path=str(path),
        exam_name=_first_nonblank(df.get("QuizName")),
        semester_code=_first_nonblank(df.get("QuizClass")),
    )

    for row_number, (_, row) in enumerate(df.iterrows(), start=2):  # header is row 1
        student_id, problem = clean_student_id(row.get("StudentID"))
        if student_id is None:
            student_id, problem = clean_student_id(row.get("CustomID"))
        key_version = _clean(row.get("Key Version"))
        score = row.get("Earned Points")
        possible_points = row.get("Possible Points")

        record = StudentScoreRow(
            student_id=student_id,
            first_name=_clean(row.get("FirstName")),
            last_name=_clean(row.get("LastName")),
            score=float(score) if pd.notna(score) else None,
            possible_points=float(possible_points) if pd.notna(possible_points) else None,
            key_version=key_version,
            round="group" if key_version == GROUP_KEY_VERSION else "individual",
        )

        bucket = parsed.group_rows if record.round == "group" else parsed.individual_rows
        bucket.append(record)

        if student_id is None:
            parsed.missing_id_rows.append(record)
        elif problem:
            parsed.id_problem_rows.append(StudentIdProblem(row_number, student_id, problem))

    parsed.duplicate_student_ids["individual"] = _find_duplicates(parsed.individual_rows)
    parsed.duplicate_student_ids["group"] = _find_duplicates(parsed.group_rows)

    return parsed


def _find_duplicates(rows: list) -> dict:
    by_id: dict = {}
    for record in rows:
        if record.student_id is None:
            continue
        by_id.setdefault(record.student_id, []).append(record)
    return {sid: recs for sid, recs in by_id.items() if len(recs) > 1}


STUDENT_ID_DIGITS = 7

# D2L writes the student ID with this in front ("#1234567") in its course-grade
# exports. Every historical sheet has it.
HASH_PREFIX = "#"


def has_hash_prefix(value) -> bool:
    """Whether an ID cell arrived carrying D2L's leading "#"."""
    return isinstance(value, str) and value.strip().startswith(HASH_PREFIX)


def clean_student_id(value) -> tuple:
    """Returns `(student_id, problem)` for one ID cell.

    Student IDs are always 7 digits. Exactly three things are corrected,
    because each is a spreadsheet or the LMS mangling an ID that was right to
    begin with:

    * **A leading "#".** D2L's course-grade exports write the ID as
      "#1234567". The "#" is the LMS's own marker, not part of the ID, so
      without removing it the same student becomes one record per file format.
    * **A decimal point.** pandas widens a whole ID column to floats as soon as
      one cell in it is blank -- which real exports do have -- so 1234567
      arrives as "1234567.0".
    * **A dropped leading zero on a 6-digit ID.** 0123456 arrives as 123456.

    Anything else -- an ID of any other length, or one that isn't all digits --
    is returned untouched alongside a description of the problem, for the
    caller to report. Correcting those would be guessing at what the right ID
    was; the file is fixed at the source instead. A blank ID is not a problem
    here: those rows are collected separately as missing IDs."""
    if value is None:
        return None, None
    if isinstance(value, float):
        if pd.isna(value):
            return None, None
        if not value.is_integer():
            return str(value), "not a whole number"
        text = str(int(value))
    else:
        text = str(value).strip()
        if text.startswith(HASH_PREFIX):
            text = text[len(HASH_PREFIX):].strip()
        if re.fullmatch(r"\d+\.0+", text):
            text = text.split(".", 1)[0]
    if not text:
        return None, None
    if not text.isdigit():
        return text, "not a number"
    if len(text) == STUDENT_ID_DIGITS:
        return text, None
    if len(text) == STUDENT_ID_DIGITS - 1:
        return text.zfill(STUDENT_ID_DIGITS), None
    return text, f"{len(text)} digits, expected {STUDENT_ID_DIGITS}"


def normalize_student_id(value) -> Optional[str]:
    """The corrected ID only. Use `clean_student_id` when the problem matters."""
    return clean_student_id(value)[0]


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _first_nonblank(series) -> Optional[str]:
    if series is None:
        return None
    for value in series:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return None
