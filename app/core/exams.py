"""Exam names: the same exam recognised across semesters.

`exams` rows are scoped to one course offering, so "the same exam" in two
semesters is two unrelated rows -- and the names don't even match. A Scantron
export names its quiz with the semester code in front (`QuizName` of
"202610 Exam 4"), while the historical importer names exams from the
course-sheet column headers ("Exam 4"). Grouping one exam across semesters
therefore needs a normalised name rather than the stored one.

Only a leading token that is a real semester code is stripped, so an exam
genuinely named with a number keeps it.
"""

import re
from typing import Optional

from app.core.semesters import parse_semester_code

_LEADING_TOKEN = re.compile(r"^(?P<token>\S+)[\s\-:]+(?P<rest>.+)$")
_TRAILING_NUMBER = re.compile(r"^(?P<label>.*?)(?P<number>\d+)\s*$")


def normalize_exam_name(raw_name) -> Optional[str]:
    """The exam's name without a leading semester code: "202610 Exam 4" -> "Exam 4".

    Whitespace is collapsed. A name that is *only* a semester code is left
    alone, since stripping it would leave nothing."""
    if raw_name is None:
        return None
    name = " ".join(str(raw_name).split())
    if not name:
        return None
    match = _LEADING_TOKEN.match(name)
    if match and parse_semester_code(match.group("token")) is not None:
        name = match.group("rest").strip()
    return name or None


def exam_group_key(raw_name) -> Optional[str]:
    """Key for deciding two stored exam names are the same exam."""
    name = normalize_exam_name(raw_name)
    return name.casefold() if name else None


def exam_sort_key(name) -> tuple:
    """Natural order: Exam 1, Exam 2, Exam 10 -- not Exam 1, Exam 10, Exam 2."""
    text = normalize_exam_name(name) or ""
    match = _TRAILING_NUMBER.match(text)
    if match:
        return (match.group("label").strip().casefold(), 0, int(match.group("number")))
    return (text.casefold(), 1, 0)
