"""Join Individual-round and Group-round rows into per-student grade records.

This module is deliberately DB-agnostic: `match_rows` just joins two lists of
`StudentScoreRow` on `student_id`. Callers decide where those rows come from --
either both freshly parsed from files just dropped on the app, or one list
freshly parsed and the other reconstructed from a previously-saved round in
the database (see app/db/store.py), so that dropping a single round file
still reports who's missing the other round.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

from app.core.grading import GradeResult, compute_grade
from app.core.parsers import StudentScoreRow


@dataclass
class MatchedStudent:
    student_id: str
    first_name: Optional[str]
    last_name: Optional[str]
    individual_score: Optional[float]
    raw_group_score: Optional[float]
    possible_points: Optional[float]
    individual_key_used: Optional[str]
    grade: GradeResult


def exclude_duplicates(rows: Iterable[StudentScoreRow], duplicate_ids) -> list:
    """Drop every row for a student whose forms haven't been manually resolved yet."""
    duplicate_ids = set(duplicate_ids)
    return [row for row in rows if row.student_id not in duplicate_ids]


def match_rows(
    individual_rows: Iterable[StudentScoreRow],
    group_rows: Iterable[StudentScoreRow],
) -> list:
    by_id: dict = {}

    for row in individual_rows:
        if row.student_id is None:
            continue
        by_id.setdefault(row.student_id, {})["individual"] = row

    for row in group_rows:
        if row.student_id is None:
            continue
        by_id.setdefault(row.student_id, {})["group"] = row

    matched = []
    for student_id in sorted(by_id):
        entry = by_id[student_id]
        individual = entry.get("individual")
        group = entry.get("group")
        source = individual or group

        possible_points = None
        if individual is not None:
            possible_points = individual.possible_points
        elif group is not None:
            possible_points = group.possible_points

        grade = compute_grade(
            individual_score=individual.score if individual else None,
            raw_group_score=group.score if group else None,
            possible_points=possible_points,
        )

        matched.append(
            MatchedStudent(
                student_id=student_id,
                first_name=source.first_name,
                last_name=source.last_name,
                individual_score=individual.score if individual else None,
                raw_group_score=group.score if group else None,
                possible_points=possible_points,
                individual_key_used=individual.key_version if individual else None,
                grade=grade,
            )
        )

    return matched
