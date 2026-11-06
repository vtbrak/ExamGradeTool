from pathlib import Path

import pytest

from app.core.course_export import parse_course_export
from app.db import store

FIXTURES = Path(__file__).parent / "fixtures"
TRIPLE = FIXTURES / "synthetic_course_export.csv"
SINGLE = FIXTURES / "synthetic_course_export_single.csv"


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


@pytest.fixture
def offering(conn):
    return store.get_or_create_course_offering(conn, "2195")


def test_save_course_grades_keeps_numerator_denominator_pair(conn, offering):
    parsed = parse_course_export(TRIPLE)
    assert store.save_course_grades(conn, offering, parsed.rows, "sheet.csv") == 3

    rows = store.get_course_grades(conn, offering)
    by_id = {r["student_id"]: r for r in rows}
    assert by_id["0900001"]["adjusted_numerator"] == 474.5
    assert by_id["0900001"]["adjusted_denominator"] == 587
    assert by_id["0900003"]["adjusted_denominator"] == 543
    assert by_id["0900001"]["final_percent"] == pytest.approx(474.5 / 587 * 100)
    assert by_id["0900001"]["grade_basis"] == "adjusted"


def test_course_grades_do_not_invent_a_letter_grade(conn, offering):
    """The sheet is authoritative; the app does not assign grades of its own."""
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")
    assert all(r["letter_grade"] is None for r in store.get_course_grades(conn, offering))


def test_save_course_grades_is_idempotent(conn, offering):
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")
    store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")
    assert len(store.get_course_grades(conn, offering)) == 3


def test_course_grades_are_separate_from_exam_scores(conn, offering):
    """Roadmap item 1 requires course-level grades stay out of exam_scores."""
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")
    assert conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0] == 0


def test_backfill_uses_the_sheets_final_score_verbatim(conn, offering):
    """Exam 1 for Ann is 30/40 -- the 60/40 blend would be 34.0 and happens to
    agree, but Cara's 20/44 blends to 29.6 while a curved sheet could say
    otherwise. What matters is that the stored value is the sheet's own."""
    parsed = parse_course_export(TRIPLE)
    store.save_historical_exam_scores(conn, offering, parsed)

    row = conn.execute(
        """SELECT es.final_grade FROM historical_exam_scores es
           JOIN exams e ON e.id = es.exam_id
           WHERE e.exam_name = 'Exam 2' AND es.student_id = '0900001'"""
    ).fetchone()
    assert row["final_grade"] == 34.4


def test_backfill_does_not_infer_effective_group_score(conn, offering):
    parsed = parse_course_export(TRIPLE)
    store.save_historical_exam_scores(conn, offering, parsed)
    # The record has no effective_group_score column at all: which score the
    # blend used isn't stated in the sheet, so there is nowhere to infer it.
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(historical_exam_scores)")}
    assert "effective_group_score" not in columns


def test_backfill_writes_to_the_record_not_the_scanned_table(conn, offering):
    """The table is the provenance now: a scan cannot reach the record, and the
    record cannot be mistaken for scanned working data."""
    parsed = parse_course_export(TRIPLE)
    store.save_historical_exam_scores(conn, offering, parsed)

    assert conn.execute("SELECT COUNT(*) FROM historical_exam_scores").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0] == 0


def test_backfill_single_score_sheet_leaves_rounds_null(conn, offering):
    """Older sheets record one score per exam and no Individual/Group split;
    those columns must stay empty rather than be back-filled with a guess."""
    parsed = parse_course_export(SINGLE)
    counts = store.save_historical_exam_scores(conn, offering, parsed)
    assert counts["scores"] == 4

    row = conn.execute(
        """SELECT es.individual_score, es.raw_group_score, es.final_grade,
                  es.possible_points
           FROM historical_exam_scores es JOIN exams e ON e.id = es.exam_id
           WHERE e.exam_name = 'Exam 1' AND es.student_id = '0800001'"""
    ).fetchone()
    assert row["individual_score"] is None
    assert row["raw_group_score"] is None
    assert row["final_grade"] == 55
    assert row["possible_points"] == 80


def test_possible_points_may_differ_between_semesters(conn):
    """The same exam name is worth 50 in one term and 80 in another."""
    new_term = store.get_or_create_course_offering(conn, "202430")
    old_term = store.get_or_create_course_offering(conn, "2195")
    store.save_historical_exam_scores(conn, new_term, parse_course_export(TRIPLE))
    store.save_historical_exam_scores(conn, old_term, parse_course_export(SINGLE))

    points = dict(
        conn.execute(
            """SELECT co.semester_code, e.possible_points FROM exams e
               JOIN course_offerings co ON co.id = e.course_offering_id
               WHERE e.exam_name = 'Exam 1'"""
        ).fetchall()
    )
    assert points == {"202430": 50, "2195": 80}


def test_resolve_student_ids_falls_back_to_names(conn, offering):
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")

    for row in parsed.rows:
        row.student_id = None
    resolved, unresolved = store.resolve_student_ids(conn, parsed.rows)
    assert not unresolved
    assert {r.student_id for r in resolved} == {"0900001", "0900002", "0900003"}


def test_resolve_student_ids_reports_unknown_students(conn, offering):
    parsed = parse_course_export(TRIPLE)
    for row in parsed.rows:
        row.student_id = None
    resolved, unresolved = store.resolve_student_ids(conn, parsed.rows)
    assert not resolved
    assert len(unresolved) == 3


def test_migration_adds_source_column_to_existing_database(tmp_path):
    """A grades.db created before this feature must gain the column, not break."""
    import sqlite3

    db = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        """
        CREATE TABLE students (student_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT);
        CREATE TABLE course_offerings (id INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_code TEXT NOT NULL, course_number TEXT NOT NULL,
            UNIQUE (semester_code, course_number));
        CREATE TABLE exams (id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_offering_id INTEGER NOT NULL REFERENCES course_offerings (id),
            exam_name TEXT NOT NULL, possible_points REAL,
            UNIQUE (course_offering_id, exam_name));
        CREATE TABLE exam_scores (exam_id INTEGER NOT NULL, student_id TEXT NOT NULL,
            individual_score REAL, raw_group_score REAL, effective_group_score REAL,
            final_grade REAL, individual_key_used TEXT, missing_flag TEXT,
            updated_at TEXT NOT NULL, PRIMARY KEY (exam_id, student_id));
        """
    )
    legacy.commit()
    legacy.close()

    conn = store.connect(db)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(exam_scores)")}
    assert "source" in columns


CALCULATED_ONLY = FIXTURES / "synthetic_course_export_calculated_only.csv"


def test_calculated_only_sheet_records_its_basis(conn, offering):
    parsed = parse_course_export(CALCULATED_ONLY)
    store.save_course_grades(conn, offering, parsed.rows, "oldest.csv")

    row = {r["student_id"]: r for r in store.get_course_grades(conn, offering)}["0700001"]
    assert row["grade_basis"] == "calculated"
    assert row["adjusted_numerator"] is None
    assert row["calculated_numerator"] == 427
    assert row["final_percent"] == pytest.approx(94.88888888888889)


def test_exempt_exam_is_not_stored_as_zero(conn, offering):
    parsed = parse_course_export(CALCULATED_ONLY)
    store.save_historical_exam_scores(conn, offering, parsed)

    rows = conn.execute(
        """SELECT e.exam_name FROM historical_exam_scores es
           JOIN exams e ON e.id = es.exam_id WHERE es.student_id = '0700002'"""
    ).fetchall()
    assert [r["exam_name"] for r in rows] == ["Exam 1"]


def test_all_course_grades_are_in_calendar_order(conn):
    parsed = parse_course_export(TRIPLE)
    for code in ("202610", "2201"):
        store.save_course_grades(conn, store.get_or_create_course_offering(conn, code), parsed.rows)
    codes = [r["semester_code"] for r in store.get_all_course_grades(conn)]
    assert codes == ["2201"] * 3 + ["202610"] * 3


def test_rows_with_problem_ids_never_reach_the_database(conn, offering, tmp_path):
    """A bad ID must not be saved and quietly become a second student."""
    import pandas as pd

    header = "Exam 1 Points Grade <Numeric MaxPoints:80 Category:>"
    path = tmp_path / "course.csv"
    pd.DataFrame(
        [
            {"OrgDefinedId": "1234567", "Last Name": "Good", "First Name": "G", header: 60,
             "Adjusted Final Grade Numerator": 500, "Adjusted Final Grade Denominator": 600},
            {"OrgDefinedId": "12345", "Last Name": "Short", "First Name": "S", header: 70,
             "Adjusted Final Grade Numerator": 520, "Adjusted Final Grade Denominator": 600},
        ]
    ).to_csv(path, index=False)

    parsed = parse_course_export(path)
    store.save_course_grades(conn, offering, parsed.rows, "course.csv")
    store.save_historical_exam_scores(conn, offering, parsed)

    saved = [r["student_id"] for r in conn.execute("SELECT student_id FROM students")]
    assert saved == ["1234567"]
    assert conn.execute("SELECT COUNT(*) FROM historical_exam_scores").fetchone()[0] == 1
