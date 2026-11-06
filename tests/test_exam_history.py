"""One exam across every semester: name grouping, the query, and the export."""

from pathlib import Path

import pytest

from app.core.exams import exam_group_key, exam_sort_key, normalize_exam_name
from app.db import store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


def _score(conn, semester, exam_name, points, student_id, final_grade):
    """Writes record data: this export is a class-record view, and scanned
    working data lives in its own table entirely."""
    offering = store.get_or_create_course_offering(conn, semester)
    exam_id = store.get_or_create_exam(conn, offering, exam_name, points)
    conn.execute(
        "INSERT OR IGNORE INTO students (student_id, first_name, last_name) VALUES (?, ?, ?)",
        (student_id, "F" + student_id[-1], "L" + student_id[-1]),
    )
    conn.execute(
        """INSERT INTO historical_exam_scores
             (exam_id, student_id, final_grade, possible_points, updated_at)
           VALUES (?, ?, ?, ?, '2026-01-01')""",
        (exam_id, student_id, final_grade, points),
    )
    conn.commit()


# --- exam names -------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("202610 Exam 4", "Exam 4"),  # Scantron QuizName carries the semester code
        ("2165 Exam 1", "Exam 1"),  # older 4-digit code
        ("Exam 4", "Exam 4"),  # historical import names it plainly
        ("  Exam   2  ", "Exam 2"),
        ("2026 Exam 1", "2026 Exam 1"),  # 2026 isn't a semester code: left alone
        ("Final Exam", "Final Exam"),
        ("202610", "202610"),  # only a code: stripping leaves nothing
        (None, None),
    ],
)
def test_normalize_exam_name(raw, expected):
    assert normalize_exam_name(raw) == expected


def test_same_exam_across_naming_styles_groups_together():
    assert exam_group_key("202610 Exam 4") == exam_group_key("Exam 4") == exam_group_key("exam 4")
    assert exam_group_key("Exam 4") != exam_group_key("Exam 1")


def test_exams_sort_numerically():
    names = ["Exam 10", "Exam 2", "202610 Exam 1", "Final Exam"]
    assert sorted(names, key=exam_sort_key) == [
        "202610 Exam 1", "Exam 2", "Exam 10", "Final Exam",
    ]


# --- the picker and the query ----------------------------------------------

def test_list_exam_names_merges_naming_styles(conn):
    _score(conn, "202610", "202610 Exam 1", 50, "1000001", 40)
    _score(conn, "202430", "Exam 1", 50, "1000002", 45)
    _score(conn, "202430", "Exam 10", 50, "1000003", 30)
    assert store.list_exam_names(conn) == ["Exam 1", "Exam 10"]


def test_list_exam_names_is_empty_before_anything_is_saved(conn):
    assert store.list_exam_names(conn) == []


def test_get_exam_history_spans_semesters_and_naming_styles(conn):
    _score(conn, "202610", "202610 Exam 1", 50, "1000001", 40)
    _score(conn, "2165", "Exam 1", 72, "1000002", 36)
    _score(conn, "202430", "Exam 2", 50, "1000003", 25)  # a different exam

    rows = store.get_exam_history(conn, "Exam 1")
    assert [r["semester_code"] for r in rows] == ["2165", "202610"]  # oldest first
    assert [r["possible_points"] for r in rows] == [72, 50]
    assert [round(r["percent"], 1) for r in rows] == [50.0, 80.0]


def test_get_exam_history_ignores_scores_with_no_final_grade(conn):
    _score(conn, "202610", "Exam 1", 50, "1000001", 40)
    _score(conn, "202610", "Exam 1", 50, "1000002", None)
    assert [r["student_id"] for r in store.get_exam_history(conn, "Exam 1")] == ["1000001"]


def test_get_exam_history_for_an_unknown_exam_is_empty(conn):
    _score(conn, "202610", "Exam 1", 50, "1000001", 40)
    assert store.get_exam_history(conn, "Exam 9") == []


def test_percent_is_comparable_where_raw_points_are_not(conn):
    """The same exam has been out of 50, 72 and 80 points -- equal percentages
    must come back equal even though the raw scores differ."""
    _score(conn, "2165", "Exam 1", 72, "1000001", 36)
    _score(conn, "2195", "Exam 1", 80, "1000002", 40)
    _score(conn, "202430", "Exam 1", 50, "1000003", 25)
    rows = store.get_exam_history(conn, "Exam 1")
    assert [r["final_grade"] for r in rows] == [36, 40, 25]
    assert {round(r["percent"], 1) for r in rows} == {50.0}


# --- the store query behind the exam reports --------------------------------

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.gui.history_view import HistoryView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_the_export_buttons_live_on_the_reports_tab(qapp, conn):
    """Class History is for looking at student data. Exporting is the Reports
    tab's job, where one exam exports as data + statistics + chart together --
    the single-CSV version it replaced is `get_exam_history` plus a writer."""
    from app.gui.reports_view import ReportsView

    history = HistoryView(conn)
    assert not hasattr(history, "export_button")
    assert not hasattr(history, "export_exam_button")

    reports = ReportsView(conn, on_export_semester=history._export_csv)
    assert reports.export_semester_button.text() == "Export Semester CSV…"
    assert "Data + Stats + Chart" in reports.export_bundle_button.text()
