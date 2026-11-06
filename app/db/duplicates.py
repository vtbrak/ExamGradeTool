"""Finding and merging students saved under more than one ID (roadmap item 8).

One person can sit in the database under two `student_id` values, which splits
their exam history and course grades and counts them twice in the class-rank
pool. Imports no longer create these, so what's in a database now is a fixed
set; see ROADMAP.md item 8 for how they got there.

Nothing here merges on its own. `find_duplicates` proposes pairs and says how
confident it is; `find_conflicts` reports what would clash; `merge_students`
applies one approved merge and returns a log of exactly what it changed. The
review, and the decision on every conflict, happen in the tool that calls this
(tools/duplicate_students.py).
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from app.core.parsers import clean_student_id
from app.core.semesters import chronological_sort_key, describe
SCANNED, RECORD = "exam_scores", "historical_exam_scores"

# How sure the pairing is, which decides whether the tool pre-fills "merge".
CERTAIN = "certain"
REVIEW = "review"

REASON_DECIMAL = "one ID is the other written as a decimal"
REASON_LEADING_ZERO = "one ID is missing its leading zero"
REASON_SAME_NAME = "same name, different IDs"


@dataclass
class DuplicateGroup:
    """A proposed pairing. `keep_id` is the better-formed of the two IDs."""

    keep_id: str
    other_id: str
    reason: str
    confidence: str


@dataclass
class Conflict:
    """Both records hold a value for the same thing, so one has to be chosen."""

    kind: str  # "exam" or "course grade"
    label: str  # e.g. "Exam 1 — 202610 (Spring 2026)"
    keep_value: str
    other_value: str
    default: str  # "keep" or "other" -- which side is pre-selected
    why_default: str
    exam_id: Optional[int] = None
    course_offering_id: Optional[int] = None
    # Which population an exam conflict is in, since the same exam can hold a
    # scanned score and a record score without those clashing.
    table: Optional[str] = None


@dataclass
class StudentSummary:
    student_id: str
    first_name: Optional[str]
    last_name: Optional[str]
    semesters: list = field(default_factory=list)
    exam_scores: int = 0
    course_grades: int = 0

    @property
    def name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p) or "(no name)"


def _name_key(row) -> Optional[tuple]:
    first, last = row["first_name"], row["last_name"]
    if not first or not last:
        return None
    return (last.strip().casefold(), first.strip().casefold())


def find_duplicates(conn: sqlite3.Connection) -> list:
    """Propose pairs of student records that look like the same person.

    Two kinds, reported with different confidence:

    * **certain** -- one ID normalises to the other ("1234567.0" or "123456"
      against "1234567"). These are provably the same ID mangled by a
      spreadsheet.
    * **review** -- same first and last name under two unrelated IDs. Could be
      a typo in a source file, or two genuinely different people. Never merged
      without someone saying so.
    """
    students = list(conn.execute("SELECT student_id, first_name, last_name FROM students"))
    ids = {row["student_id"] for row in students}
    groups = []
    paired = set()

    for row in students:
        raw = row["student_id"]
        normalized, _problem = clean_student_id(raw)
        if not normalized or normalized == raw or normalized not in ids:
            continue
        pair = tuple(sorted((raw, normalized)))
        if pair in paired:
            continue
        paired.add(pair)
        reason = REASON_DECIMAL if "." in raw else REASON_LEADING_ZERO
        groups.append(DuplicateGroup(normalized, raw, reason, CERTAIN))

    by_name: dict = {}
    for row in students:
        key = _name_key(row)
        if key:
            by_name.setdefault(key, []).append(row["student_id"])
    for student_ids in by_name.values():
        if len(student_ids) < 2:
            continue
        ordered = sorted(student_ids)
        for other in ordered[1:]:
            pair = (ordered[0], other)
            if pair in paired:
                continue
            paired.add(pair)
            groups.append(DuplicateGroup(ordered[0], other, REASON_SAME_NAME, REVIEW))

    return groups


def summarize_student(conn: sqlite3.Connection, student_id: str) -> StudentSummary:
    row = conn.execute(
        "SELECT student_id, first_name, last_name FROM students WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    summary = StudentSummary(
        student_id=student_id,
        first_name=row["first_name"] if row else None,
        last_name=row["last_name"] if row else None,
    )
    semesters = set()
    # Both populations: a student can hold scanned working scores, record
    # scores, or both, and a merge has to move all of them.
    for table in (SCANNED, RECORD):
        for record in conn.execute(
            f"""SELECT co.semester_code FROM {table} es
               JOIN exams e ON e.id = es.exam_id
               JOIN course_offerings co ON co.id = e.course_offering_id
               WHERE es.student_id = ?""",
            (student_id,),
        ):
            semesters.add(record["semester_code"])
            summary.exam_scores += 1
    for record in conn.execute(
        """SELECT co.semester_code FROM course_grades cg
           JOIN course_offerings co ON co.id = cg.course_offering_id
           WHERE cg.student_id = ?""",
        (student_id,),
    ):
        semesters.add(record["semester_code"])
        summary.course_grades += 1
    summary.semesters = sorted(semesters, key=chronological_sort_key)
    return summary


def _exam_rows(conn: sqlite3.Connection, student_id: str, table: str) -> dict:
    """One student's scores in one of the two populations, keyed by exam."""
    return {
        row["exam_id"]: row
        for row in conn.execute(
            f"""SELECT es.*, e.exam_name, co.semester_code
               FROM {table} es
               JOIN exams e ON e.id = es.exam_id
               JOIN course_offerings co ON co.id = e.course_offering_id
               WHERE es.student_id = ?""",
            (student_id,),
        )
    }


def _course_rows(conn: sqlite3.Connection, student_id: str) -> dict:
    return {
        row["course_offering_id"]: row
        for row in conn.execute(
            """SELECT cg.*, co.semester_code FROM course_grades cg
               JOIN course_offerings co ON co.id = cg.course_offering_id
               WHERE cg.student_id = ?""",
            (student_id,),
        )
    }


def _describe_exam(row, table: str) -> str:
    score = "no score" if row["final_grade"] is None else f"{row['final_grade']:g}"
    points = row["possible_points"] if "possible_points" in row.keys() else None
    if points:
        score += f"/{points:g}"
    origin = "class record" if table == RECORD else "scanned"
    return f"{score} ({origin}, saved {row['updated_at'][:10]})"


def _describe_course_grade(row) -> str:
    percent = "no percent" if row["final_percent"] is None else f"{row['final_percent']:.2f}%"
    return f"{percent} ({row['grade_basis'] or 'unknown basis'}, saved {row['updated_at'][:10]})"


def find_conflicts(conn: sqlite3.Connection, keep_id: str, other_id: str) -> list:
    """What would clash if these two records were merged.

    A conflict is only when both records hold a value for the *same* exam in the
    *same* population, or the same semester's course grade. Everything else
    simply combines -- including a scanned score and a record score for one
    exam, which are different things living in different tables and both
    survive.

    Note what is deliberately not a conflict: the same student appearing in two
    semesters. That is someone repeating the course, and both semesters are
    theirs to keep.
    """
    conflicts = []

    for table, kind in ((SCANNED, "scanned exam"), (RECORD, "class record exam")):
        keep_exams = _exam_rows(conn, keep_id, table)
        other_exams = _exam_rows(conn, other_id, table)
        for exam_id in sorted(set(keep_exams) & set(other_exams)):
            keep_row, other_row = keep_exams[exam_id], other_exams[exam_id]
            newer_is_other = (other_row["updated_at"] or "") > (keep_row["updated_at"] or "")
            conflicts.append(
                Conflict(
                    kind=kind,
                    label=f"{keep_row['exam_name']} — "
                    f"{describe(keep_row['semester_code'])} ({kind})",
                    keep_value=_describe_exam(keep_row, table),
                    other_value=_describe_exam(other_row, table),
                    default="other" if newer_is_other else "keep",
                    why_default="both rows are the same kind, so the newer one wins",
                    exam_id=exam_id,
                    table=table,
                )
            )

    keep_courses, other_courses = _course_rows(conn, keep_id), _course_rows(conn, other_id)
    for offering_id in sorted(set(keep_courses) & set(other_courses)):
        keep_row, other_row = keep_courses[offering_id], other_courses[offering_id]
        newer_is_other = (other_row["updated_at"] or "") > (keep_row["updated_at"] or "")
        conflicts.append(
            Conflict(
                kind="course grade",
                label=f"Course grade — {describe(keep_row['semester_code'])}",
                keep_value=_describe_course_grade(keep_row),
                other_value=_describe_course_grade(other_row),
                default="other" if newer_is_other else "keep",
                why_default="course grades are all imported, so the newer one wins",
                course_offering_id=offering_id,
            )
        )

    return conflicts


def merge_students(
    conn: sqlite3.Connection,
    keep_id: str,
    other_id: str,
    exam_choices: Optional[dict] = None,
    course_choices: Optional[dict] = None,
) -> list:
    """Merge `other_id` into `keep_id`, returning a log of every change.

    `exam_choices` / `course_choices` map the conflicting exam id / course
    offering id to "keep" or "other" -- the side whose value survives. Anything
    not named defaults as `find_conflicts` reported it. The merge runs in one
    transaction: either all of it lands or none of it does.
    """
    exam_choices = dict(exam_choices or {})
    course_choices = dict(course_choices or {})
    for conflict in find_conflicts(conn, keep_id, other_id):
        if conflict.table:
            exam_choices.setdefault((conflict.table, conflict.exam_id), conflict.default)
        else:
            course_choices.setdefault(conflict.course_offering_id, conflict.default)

    log = []
    with conn:  # one transaction; rolls back if anything raises
        for (table, exam_id), choice in exam_choices.items():
            losing_id = other_id if choice == "keep" else keep_id
            conn.execute(
                f"DELETE FROM {table} WHERE exam_id = ? AND student_id = ?",
                (exam_id, losing_id),
            )
            log.append(
                f"exam {exam_id} ({table}): kept the "
                f"{'surviving' if choice == 'keep' else 'merged'} record's score, "
                f"discarded {losing_id}'s"
            )
        for offering_id, choice in course_choices.items():
            losing_id = other_id if choice == "keep" else keep_id
            conn.execute(
                "DELETE FROM course_grades WHERE course_offering_id = ? AND student_id = ?",
                (offering_id, losing_id),
            )
            log.append(
                f"course grade for offering {offering_id}: kept the "
                f"{'surviving' if choice == 'keep' else 'merged'} record's value, "
                f"discarded {losing_id}'s"
            )

        # Both populations move: a student can hold scanned and record scores.
        moved_exams = 0
        for table in (SCANNED, RECORD):
            moved_exams += conn.execute(
                f"UPDATE {table} SET student_id = ? WHERE student_id = ?",
                (keep_id, other_id),
            ).rowcount
        moved_grades = conn.execute(
            "UPDATE course_grades SET student_id = ? WHERE student_id = ?", (keep_id, other_id)
        ).rowcount

        # Keep any name the surviving record was missing.
        conn.execute(
            """UPDATE students SET
                   first_name = COALESCE(first_name, (SELECT first_name FROM students WHERE student_id = ?)),
                   last_name = COALESCE(last_name, (SELECT last_name FROM students WHERE student_id = ?))
               WHERE student_id = ?""",
            (other_id, other_id, keep_id),
        )
        conn.execute("DELETE FROM students WHERE student_id = ?", (other_id,))

    log.append(f"moved {moved_exams} exam score(s) and {moved_grades} course grade(s)")
    log.append(f"deleted student record {other_id}; {keep_id} survives")
    return log
