"""Running a file creates nothing in the database; Save does.

Before, Run created the semester and exam rows and pointed the window at them
*before* Results was updated. A failure in between left Results on the previous
exam while Save wrote under the new exam's id, and every failed Run left an
empty exam row behind (a typo'd QuizClass became a permanent stray semester)."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db import store
from app.gui import main_window as mw
from app.gui.main_window import MainWindow

FIXTURES = Path(__file__).parent / "fixtures"
COMBINED = FIXTURES / "synthetic_combined_round.csv"
INDIVIDUAL_ONLY = FIXTURES / "synthetic_individual_round.csv"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    conn = store.connect(tmp_path / "grades.db")
    win = MainWindow(conn)
    yield win
    conn.close()


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_run_creates_no_rows_until_save(window):
    window._handle_file_dropped(COMBINED)
    window._run()

    assert window.current_exam_id is None
    assert _count(window.db_conn, "exams") == 0
    assert _count(window.db_conn, "course_offerings") == 0
    assert window.current_exam_name == "Exam 1"
    assert window.current_semester_code == "202610"

    window.results_view._save_to_history()

    assert window.current_exam_id is not None
    assert _count(window.db_conn, "exams") == 1
    assert _count(window.db_conn, "exam_scores") == len(window.results_view.matched_students)


def test_rerunning_a_saved_exam_finds_its_existing_row(window):
    window._handle_file_dropped(COMBINED)
    window._run()
    window.results_view._save_to_history()
    exam_id = window.current_exam_id

    window._handle_file_dropped(INDIVIDUAL_ONLY)
    window._run()

    # Same semester and exam name -> the saved row, and the group round saved
    # earlier is pulled in for the students this file lacks a C score for.
    assert window.current_exam_id == exam_id
    assert _count(window.db_conn, "exams") == 1


def test_failed_run_leaves_the_previous_exam_in_place(window, monkeypatch):
    window._handle_file_dropped(COMBINED)
    window._run()
    window.results_view._save_to_history()
    before = (window.current_exam_id, [m.student_id for m in window.results_view.matched_students])

    def boom(*a, **k):
        raise RuntimeError("matching blew up")

    monkeypatch.setattr(mw, "match_rows", boom)
    monkeypatch.setattr(mw, "parse_exam_file", lambda p: _parsed_for_other_exam())
    window._handle_file_dropped(COMBINED)
    window._run()  # error dialog, no exception

    after = (window.current_exam_id, [m.student_id for m in window.results_view.matched_students])
    assert after == before
    assert _count(window.db_conn, "exams") == 1  # the failed run's exam was never created


def _parsed_for_other_exam():
    from app.core.parsers import parse_exam_file

    parsed = parse_exam_file(COMBINED)
    parsed.exam_name = "202610 Exam 2"
    return parsed
