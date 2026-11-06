"""A save that fails part-way leaves nothing behind.

Each save function used to commit once at the end with no rollback, so rows
written before the failure sat in an open transaction and were committed by the
next unrelated commit anywhere in the app. Now each save is one transaction."""

import pytest

from app.core.course_export import (
    BASIS_CALCULATED,
    CourseExamScore,
    CourseGradeRow,
    ExamColumns,
    ParsedCourseExport,
)
from app.core.grading import compute_grade
from app.core.matching import MatchedStudent
from app.db import store


class Boom(Exception):
    pass


def _row(student_id, numerator=500.0, denominator=600.0, exams=None):
    return CourseGradeRow(
        student_id=student_id, username=None, first_name="A", last_name=student_id,
        adjusted_numerator=numerator, adjusted_denominator=denominator,
        calculated_numerator=numerator, calculated_denominator=denominator,
        exams=exams or {},
    )


class _ExplodingRow:
    """Looks like a CourseGradeRow until its percent is read."""

    def __init__(self, student_id, exams=None):
        self.student_id, self.first_name, self.last_name = student_id, "A", student_id
        self.adjusted_numerator = self.adjusted_denominator = 1.0
        self.calculated_numerator = self.calculated_denominator = 1.0
        self.grade_basis, self.components, self.exams = "adjusted", {}, exams or {}

    @property
    def percent(self):
        raise Boom("row 2 is bad")


def _score(final=41.6):
    return CourseExamScore(exam_name="Exam 1", individual_score=40.0, raw_group_score=44.0,
                           final_score=final, possible_points=50.0, single_score_only=False)


def _matched(student_id):
    m = MatchedStudent(student_id=student_id, first_name="A", last_name="B", individual_score=40.0,
                       raw_group_score=44.0, possible_points=50.0, individual_key_used="A",
                       grade=compute_grade(40.0, 44.0, 50.0))
    return m


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "grades.db")
    yield c
    c.close()


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_course_grades_roll_back_as_a_unit(conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    rows = [_row("1000001"), _ExplodingRow("1000002"), _row("1000003")]

    with pytest.raises(Boom):
        store.save_course_grades(conn, offering, rows)

    assert _count(conn, "course_grades") == 0
    assert _count(conn, "students") == 0
    assert not conn.in_transaction  # nothing left open for a later commit to pick up


def test_historical_exam_scores_roll_back_including_new_exam_rows(conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    good = _row("1000001", exams={"Exam 1": _score()})
    bad = _row("1000002", exams={"Exam 1": None})  # `.final_score` on None raises
    parsed = ParsedCourseExport(
        source_path="x.xlsx", layout="three",
        rows=[good, bad], exam_columns=[ExamColumns(exam_name="Exam 1", possible_points=50.0)],
    )

    with pytest.raises(AttributeError):
        store.save_historical_exam_scores(conn, offering, parsed)

    assert _count(conn, "historical_exam_scores") == 0
    assert _count(conn, "exams") == 0, "the exam row created before the scores rolled back too"
    assert not conn.in_transaction


def test_matched_students_roll_back_as_a_unit(conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50.0)
    bad = _matched(None)  # student_id NOT NULL

    with pytest.raises(Exception):
        store.save_matched_students(conn, exam_id, [_matched("1000001"), bad])

    assert _count(conn, "exam_scores") == 0
    assert _count(conn, "students") == 0
    assert not conn.in_transaction


def test_successful_saves_still_commit(conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    assert store.save_course_grades(conn, offering, [_row("1000001")]) == 1
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50.0)
    store.save_matched_students(conn, exam_id, [_matched("1000001")])
    assert _count(conn, "course_grades") == 1
    assert _count(conn, "exam_scores") == 1
    assert not conn.in_transaction


# --- the blank-numerator crash that would have triggered the rollback ----------

def test_denominator_without_numerator_is_no_grade():
    row = _row("1000001", numerator=None, denominator=587.0)
    assert row.grade_basis is None
    assert row.percent is None
    assert row.adjusted_percent is None


def test_blank_adjusted_numerator_falls_back_to_calculated_pair():
    row = CourseGradeRow(
        student_id="1000001", username=None, first_name="A", last_name="B",
        adjusted_numerator=None, adjusted_denominator=587.0,
        calculated_numerator=500.0, calculated_denominator=587.0,
    )
    assert row.grade_basis == BASIS_CALCULATED
    assert row.percent == pytest.approx(500 / 587 * 100)
    assert row.adjusted_percent is None


def test_exempt_row_imports_without_aborting_the_sheet(conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    rows = [_row("1000001"), _row("1000002", numerator=None, denominator=587.0), _row("1000003")]
    assert store.save_course_grades(conn, offering, rows) == 3
    saved = {r["student_id"]: r["final_percent"] for r in store.get_course_grades(conn, offering)}
    assert saved["1000002"] is None and saved["1000001"] is not None
