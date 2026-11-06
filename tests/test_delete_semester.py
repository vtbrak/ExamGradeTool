"""Deleting a semester outright (ROADMAP.md item 13), so a sheet filed under
the wrong semester can be removed and imported again.

An import adds and overwrites but never removes, so re-importing to the right
semester leaves the wrong one still holding the data. This is the way back.

Requires QT_QPA_PLATFORM=offscreen for the dialog tests.
"""

from pathlib import Path

import pytest

from app.core.course_export import parse_course_export
from app.db import store

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gui.main_window import MainWindow  # noqa: E402

SHEET = Path(__file__).parent / "fixtures" / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


def _import(conn, code, source="sheet.csv"):
    """File the sample sheet under a semester, the way the dialog does."""
    parsed = parse_course_export(SHEET)
    resolved, _ = store.resolve_student_ids(conn, parsed.rows)
    offering = store.get_or_create_course_offering(conn, code)
    store.save_course_grades(conn, offering, resolved, source_file=source)
    store.save_historical_exam_scores(conn, offering, parsed, resolved, source_file=source)
    return offering


def _counts(conn):
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("students", "course_offerings", "exams",
                      "course_grades", "historical_exam_scores", "exam_scores")
    }


# --- what it reports before deleting -----------------------------------------

def test_contents_describes_what_would_go(conn):
    offering = _import(conn, "202610", source="Fall 2025 final.csv")
    contents = store.semester_contents(conn, offering)

    assert contents["course_grades"] == 3
    assert contents["exams"] == 2
    assert contents["record_scores"] == 6
    assert contents["scanned_scores"] == 0
    assert contents["source_files"] == ["Fall 2025 final.csv"]
    assert len(contents["students_removed"]) == 3
    assert contents["students_kept"] == []


# --- the deletion -------------------------------------------------------------

def test_deleting_leaves_nothing_behind(conn):
    offering = _import(conn, "202610")
    store.delete_semester(conn, offering)

    assert _counts(conn) == {
        "students": 0, "course_offerings": 0, "exams": 0,
        "course_grades": 0, "historical_exam_scores": 0, "exam_scores": 0,
    }
    assert store.list_semester_codes(conn) == []


def test_the_semester_can_be_imported_again_afterwards(conn):
    """The point of deleting: a clean slate to re-import onto."""
    store.delete_semester(conn, _import(conn, "202610"))
    offering = _import(conn, "202610")

    assert store.count_course_grades(conn, offering) == 3
    assert conn.execute("SELECT COUNT(*) FROM course_offerings").fetchone()[0] == 1


def test_only_the_named_semester_is_touched(conn):
    wrong = _import(conn, "202610", source="Fall 2025 final.csv")
    _import(conn, "202410", source="Spring 2024 final.csv")

    store.delete_semester(conn, wrong)

    assert store.list_semester_codes(conn) == ["202410"]
    assert conn.execute("SELECT COUNT(*) FROM course_grades").fetchone()[0] == 3


# --- the repeating student ----------------------------------------------------

def test_a_repeating_student_keeps_their_other_semester(conn):
    """A student who took the course twice is in both semesters. Deleting one
    must not delete them, or the semester that stays loses a student."""
    _import(conn, "202410", source="Spring 2024 final.csv")
    wrong = _import(conn, "202610", source="Fall 2025 final.csv")

    contents = store.semester_contents(conn, wrong)
    assert contents["students_removed"] == []        # all three repeat
    assert len(contents["students_kept"]) == 3

    store.delete_semester(conn, wrong)

    assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 3
    assert store.count_course_grades(conn, store.get_or_create_course_offering(conn, "202410")) == 3


def test_a_student_only_in_the_deleted_semester_goes(conn):
    """The mixed case: some students repeat, some don't."""
    first = _import(conn, "202410")
    # One of the three also appears in the semester being deleted.
    repeater = sorted(store.semester_students(conn, first))[0]
    second = store.get_or_create_course_offering(conn, "202610")
    exam = store.get_or_create_exam(conn, second, "Exam 1", 50)
    conn.execute(
        """INSERT INTO historical_exam_scores
             (exam_id, student_id, final_grade, possible_points, updated_at)
           VALUES (?, ?, 40, 50, 'now')""",
        (exam, repeater),
    )
    conn.execute(
        """INSERT INTO students (student_id, first_name, last_name)
           VALUES ('9999999', 'Solo', 'Student')"""
    )
    conn.execute(
        """INSERT INTO historical_exam_scores
             (exam_id, student_id, final_grade, possible_points, updated_at)
           VALUES (?, '9999999', 30, 50, 'now')""",
        (exam,),
    )
    conn.commit()

    contents = store.semester_contents(conn, second)
    assert contents["students_removed"] == ["9999999"]
    assert contents["students_kept"] == [repeater]

    store.delete_semester(conn, second)

    remaining = {r["student_id"] for r in conn.execute("SELECT student_id FROM students")}
    assert "9999999" not in remaining
    assert repeater in remaining


# --- scanned working data goes too -------------------------------------------

def test_scanned_scores_in_that_semester_go_as_well(conn):
    offering = _import(conn, "202610")
    exam = store.get_or_create_exam(conn, offering, "Exam 3", 50)
    conn.execute(
        """INSERT INTO exam_scores
             (exam_id, student_id, final_grade, updated_at)
           SELECT ?, student_id, 44, 'now' FROM students LIMIT 1""",
        (exam,),
    )
    conn.commit()

    assert store.semester_contents(conn, offering)["scanned_scores"] == 1
    store.delete_semester(conn, offering)
    assert conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0] == 0


# --- through the button -------------------------------------------------------

def _window(conn, qapp, monkeypatch, answer):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[1]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[1]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer)
    window = MainWindow(conn)
    window.shown = shown
    return window


def test_the_button_backs_up_before_deleting(qapp, conn, tmp_path, monkeypatch):
    _import(conn, "202610")
    monkeypatch.setattr("app.db.config.get_data_folder", lambda: tmp_path)
    monkeypatch.setattr(store, "default_db_path", lambda: tmp_path / "grades.db")

    window = _window(conn, qapp, monkeypatch, QMessageBox.StandardButton.Yes)
    window.history_view._delete_semester()

    backups = list(tmp_path.glob("grades_backup_*.db"))
    assert len(backups) == 1
    # The backup still holds the semester that was just deleted.
    restored = store.connect(backups[0])
    assert store.list_semester_codes(restored) == ["202610"]
    assert store.list_semester_codes(conn) == []


def test_answering_no_deletes_nothing(qapp, conn, monkeypatch):
    _import(conn, "202610")
    window = _window(conn, qapp, monkeypatch, QMessageBox.StandardButton.No)
    window.history_view._delete_semester()

    assert store.list_semester_codes(conn) == ["202610"]


def test_a_failed_backup_cancels_the_deletion(qapp, conn, monkeypatch):
    """The backup is the undo, so without one the data stays put."""
    _import(conn, "202610")

    def boom(*a, **k):
        raise OSError("No space left on device")

    window = _window(conn, qapp, monkeypatch, QMessageBox.StandardButton.Yes)
    dialogs = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: dialogs.append(a[1]))
    monkeypatch.setattr(store, "backup_database", boom)
    window.history_view._delete_semester()

    assert store.list_semester_codes(conn) == ["202610"]
    assert dialogs == ["Could not delete the semester"]


def test_deleting_drops_an_exam_loaded_from_that_semester(qapp, conn, tmp_path, monkeypatch):
    """Otherwise Save would write the exam back under a deleted semester."""
    offering = _import(conn, "202610")
    monkeypatch.setattr("app.db.config.get_data_folder", lambda: tmp_path)
    monkeypatch.setattr(store, "default_db_path", lambda: tmp_path / "grades.db")

    window = _window(conn, qapp, monkeypatch, QMessageBox.StandardButton.Yes)
    window.current_exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)
    window.history_view._delete_semester()

    assert window.current_exam_id is None
