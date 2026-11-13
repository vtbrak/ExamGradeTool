"""Adding a curve to an exam, without ever overwriting the original score.

The rules:

- **Final Grade is always the original score.** A curve never changes it.
- **Curved Grade is a separate column**: the final grade plus the curve,
  **capped at the exam's point total**. Blank on an exam with no curve, which
  is most of them.
- The percentage, the pass mark, the Dashboard and the D2L Final Score all
  follow the grade the student *receives* -- the curved one when there is one.
- In the D2L export, Final Score (column F) is the curved score on a curved
  exam, and two reference columns follow Group Score Replaced: Original Exam
  Score (always the uncurved grade) and Curved (Yes / No).

The curve is stored on the exam as well as in each saved score: reopening a
saved exam recomputes every grade from the Individual and Group scores, so the
curve has to come back with it.

Requires QT_QPA_PLATFORM=offscreen for the view tests.
"""

import csv
import sqlite3
from pathlib import Path

import pytest

from app.core.grading import compute_grade
from app.db import store

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from app.gui.main_window import MainWindow  # noqa: E402
from app.gui.results_view import (  # noqa: E402
    COLUMNS,
    CURVED_GRADE_COL,
    FINAL_GRADE_COL,
    FINAL_PERCENT_COL,
)

COMBINED = Path(__file__).parent / "fixtures" / "synthetic_combined_round.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


# --- the rule ----------------------------------------------------------------

def test_the_original_final_grade_is_never_changed_by_a_curve():
    plain = compute_grade(30, 40, 50)
    curved = compute_grade(30, 40, 50, curve_points=4)

    assert plain.final_grade == curved.final_grade == 34.0  # 0.6*30 + 0.4*40
    assert curved.curved_grade == 38.0


def test_no_curve_means_no_curved_grade():
    """The column is blank on an uncurved exam, not a copy of Final Grade."""
    plain = compute_grade(30, 40, 50)
    assert plain.curved_grade is None
    assert plain.curved is False
    assert plain.grade_received == 34.0


def test_the_curved_grade_never_goes_past_the_total():
    """The requirement, stated once: 48 + 5 is 50, not 53."""
    curved = compute_grade(48, 48, 50, curve_points=5)

    assert curved.final_grade == 48.0       # the original, intact
    assert curved.curved_grade == 50.0      # capped
    assert curved.percent == 100.0
    assert curved.curve_capped is True


def test_a_student_at_full_marks_stays_at_full_marks():
    curved = compute_grade(50, 50, 50, curve_points=10)
    assert curved.final_grade == 50.0
    assert curved.curved_grade == 50.0


def test_a_curve_that_fits_is_not_reported_as_capped():
    curved = compute_grade(30, 30, 50, curve_points=10)
    assert curved.curved_grade == 40.0
    assert curved.curve_capped is False


def test_the_received_grade_is_the_curved_one_when_there_is_a_curve():
    assert compute_grade(30, 40, 50, curve_points=4).grade_received == 38.0
    assert compute_grade(30, 40, 50).grade_received == 34.0


def test_the_percentage_and_pass_mark_follow_the_received_grade():
    """Everything downstream -- the percentage, the pass line, the Dashboard's
    letter grades -- sees what the student gets, not the raw score."""
    plain = compute_grade(33, 33, 50)           # 33/50 = 66%, a fail
    curved = compute_grade(33, 33, 50, curve_points=3)

    assert plain.percent == 66.0 and plain.passed is False
    assert curved.final_grade == 33.0
    assert curved.curved_grade == 36.0
    assert curved.percent == 72.0 and curved.passed is True


def test_a_missing_score_gets_neither_a_final_nor_a_curved_grade():
    """Inventing a curved grade would turn a missing score into a real one."""
    curved = compute_grade(40, None, 50, curve_points=5)

    assert curved.final_grade is None
    assert curved.curved_grade is None
    assert curved.missing_flag == "Missing Group"
    assert curved.curve_capped is False


def test_a_curve_keeps_half_points_rather_than_rounding_them_away():
    """0.6*31 + 0.4*44 is 36.2; +2 is 38.2, not 38."""
    curved = compute_grade(31, 44, 50, curve_points=2)
    assert curved.final_grade == 36.2
    assert curved.curved_grade == 38.2


# --- stored on the exam, and in each saved score -----------------------------

def test_an_exam_has_no_curve_until_one_is_set(conn):
    offering = store.get_or_create_course_offering(conn, "202630")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)
    assert store.get_exam_curve(conn, exam_id) == 0.0


def test_the_exam_curve_round_trips(conn):
    offering = store.get_or_create_course_offering(conn, "202630")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)

    store.set_exam_curve(conn, exam_id, 3.5)
    assert store.get_exam_curve(conn, exam_id) == 3.5
    store.set_exam_curve(conn, exam_id, 0)
    assert store.get_exam_curve(conn, exam_id) == 0.0


def test_a_database_predating_the_curve_columns_is_migrated(tmp_path):
    """Exams and scores saved before the curve existed get the new columns by
    migration. ALTER TABLE can't backfill, so they hold NULL, which reads as
    "no curve". A fresh database can't reach that state, so the old schema is
    built by hand."""
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """CREATE TABLE students (student_id TEXT PRIMARY KEY,
               first_name TEXT, last_name TEXT);
           CREATE TABLE course_offerings (id INTEGER PRIMARY KEY AUTOINCREMENT,
               semester_code TEXT NOT NULL, course_number TEXT NOT NULL,
               UNIQUE (semester_code, course_number));
           CREATE TABLE exams (id INTEGER PRIMARY KEY AUTOINCREMENT,
               course_offering_id INTEGER NOT NULL, exam_name TEXT NOT NULL,
               possible_points REAL, UNIQUE (course_offering_id, exam_name));
           CREATE TABLE exam_scores (exam_id INTEGER NOT NULL,
               student_id TEXT NOT NULL, individual_score REAL,
               raw_group_score REAL, effective_group_score REAL,
               final_grade REAL, individual_key_used TEXT, missing_flag TEXT,
               updated_at TEXT NOT NULL, PRIMARY KEY (exam_id, student_id));
           INSERT INTO course_offerings (semester_code, course_number)
               VALUES ('202630', '211');
           INSERT INTO exams (course_offering_id, exam_name, possible_points)
               VALUES (1, 'Exam 1', 50);
           INSERT INTO students VALUES ('1234567', 'Ann', 'Alpha');
           INSERT INTO exam_scores (exam_id, student_id, final_grade, updated_at)
               VALUES (1, '1234567', 40.8, 'then');"""
    )
    legacy.commit()
    legacy.close()

    conn = store.connect(path)

    assert store.get_exam_curve(conn, 1) == 0.0
    row = conn.execute("SELECT final_grade, curved_grade FROM exam_scores").fetchone()
    assert row["final_grade"] == 40.8, "the original survived the migration"
    assert row["curved_grade"] is None


def test_the_curve_comes_back_when_the_exam_is_reopened(conn):
    offering = store.get_or_create_course_offering(conn, "202630")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)
    store.set_exam_curve(conn, exam_id, 2)

    *_rest, curve = store.load_saved_exam(conn, exam_id)
    assert curve == 2.0


# --- through the window ------------------------------------------------------

def _window(conn, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window = MainWindow(conn)
    window._handle_file_dropped(COMBINED)
    window._run()
    return window


def _column(results, col):
    return [
        results.table.item(row, col).text() for row in range(results.table.rowCount())
    ]


def test_curved_grade_is_its_own_column_after_final_grade():
    assert COLUMNS[FINAL_GRADE_COL] == "Final Grade"
    assert COLUMNS[CURVED_GRADE_COL] == "Curved Grade"
    assert CURVED_GRADE_COL == FINAL_GRADE_COL + 1
    assert COLUMNS[FINAL_PERCENT_COL] == "Final %"


def test_the_curved_column_is_blank_without_a_curve(qapp, conn, monkeypatch):
    results = _window(conn, monkeypatch).results_view
    assert set(_column(results, CURVED_GRADE_COL)) == {""}


def test_typing_a_curve_fills_the_curved_column_and_leaves_final_grade_alone(
    qapp, conn, monkeypatch
):
    results = _window(conn, monkeypatch).results_view
    finals_before = _column(results, FINAL_GRADE_COL)

    results.curve_box.setValue(3)

    assert _column(results, FINAL_GRADE_COL) == finals_before, "original overwritten"
    total = results.possible_points
    for m in results.matched_students:
        g = m.grade
        if g.final_grade is None:  # a missing score stays missing
            assert g.curved_grade is None
            continue
        assert g.curved_grade == min(round(g.final_grade + 3, 2), total)
        assert g.curved_grade <= total, "no score may exceed the exam total"


def test_setting_the_curve_back_to_zero_blanks_the_column_again(qapp, conn, monkeypatch):
    results = _window(conn, monkeypatch).results_view
    results.curve_box.setValue(5)
    results.curve_box.setValue(0)

    assert set(_column(results, CURVED_GRADE_COL)) == {""}
    assert all(m.grade.curved_grade is None for m in results.matched_students)


def test_the_screen_says_how_many_students_the_cap_bit(qapp, conn, monkeypatch):
    results = _window(conn, monkeypatch).results_view
    results.curve_box.setValue(40)  # far more than anyone needs

    capped = [m for m in results.matched_students if m.grade.curve_capped]
    assert capped, "a 40-point curve on a 50-point exam must cap somebody"
    assert f"{len(capped)} student(s) capped" in results.curve_note.text()


def test_saving_keeps_the_original_and_the_curved_grade_apart(qapp, conn, monkeypatch):
    """Original scores kept intact in the database too, not only on screen."""
    window = _window(conn, monkeypatch)
    results = window.results_view
    results.curve_box.setValue(2.5)
    exam_id = window.ensure_current_exam()
    results._save_to_history()

    assert store.get_exam_curve(conn, exam_id) == 2.5
    saved = {
        r["student_id"]: (r["final_grade"], r["curved_grade"])
        for r in conn.execute(
            "SELECT student_id, final_grade, curved_grade FROM exam_scores WHERE exam_id = ?",
            (exam_id,),
        )
    }
    for m in results.matched_students:
        final, curved = saved[m.student_id]
        assert final == m.grade.final_grade
        assert curved == m.grade.curved_grade


def test_an_uncurved_save_leaves_the_curved_grade_empty(qapp, conn, monkeypatch):
    window = _window(conn, monkeypatch)
    exam_id = window.ensure_current_exam()
    window.results_view._save_to_history()

    values = {
        r["curved_grade"]
        for r in conn.execute("SELECT curved_grade FROM exam_scores WHERE exam_id = ?", (exam_id,))
    }
    assert values == {None}


def test_reopening_restores_the_curve_without_applying_it_twice(qapp, conn, monkeypatch):
    window = _window(conn, monkeypatch)
    window.results_view.curve_box.setValue(4)
    exam_id = window.ensure_current_exam()
    window.results_view._save_to_history()
    before = {m.student_id: m.grade for m in window.results_view.matched_students}

    name, points, matched, curve = store.load_saved_exam(conn, exam_id)
    fresh = MainWindow(conn)
    fresh.results_view.load_results(
        exam_name=name,
        matched_students=matched,
        duplicates={"individual": {}, "group": {}},
        possible_points=points,
        curve_points=curve,
    )

    assert fresh.results_view.curve_box.value() == 4
    for m in fresh.results_view.matched_students:
        assert m.grade.final_grade == before[m.student_id].final_grade
        assert m.grade.curved_grade == before[m.student_id].curved_grade


# --- the D2L export ----------------------------------------------------------

def _export(results, monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    results._export_csv()
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        return header, [dict(zip(header, row)) for row in reader]


def test_the_export_columns_follow_group_score_replaced(qapp, conn, monkeypatch, tmp_path):
    results = _window(conn, monkeypatch).results_view
    header, _rows = _export(results, monkeypatch, tmp_path / "e.csv")

    replaced = header.index("Group Score Replaced")
    assert header[replaced + 1] == "Original Exam Score"
    assert header[replaced + 2] == "Curved"
    # The D2L paste block is untouched: Final Score is still column F.
    assert header.index("Final Score") == 5


def test_an_uncurved_export_fills_the_original_and_says_no(qapp, conn, monkeypatch, tmp_path):
    """Original Exam Score is filled in on every export, curved or not."""
    results = _window(conn, monkeypatch).results_view
    _header, rows = _export(results, monkeypatch, tmp_path / "e.csv")

    by_id = {r["StudentID"]: r for r in rows}
    for m in results.matched_students:
        row = by_id[m.student_id]
        assert row["Curved"] == "No"
        if m.grade.final_grade is not None:
            assert float(row["Original Exam Score"]) == m.grade.final_grade
            assert row["Final Score"] == row["Original Exam Score"]


def test_a_curved_export_puts_the_curved_score_in_column_f(qapp, conn, monkeypatch, tmp_path):
    """Column F is pasted into D2L, so it must hold what the student receives;
    the uncurved score is kept beside it rather than lost."""
    results = _window(conn, monkeypatch).results_view
    results.curve_box.setValue(3)
    header, rows = _export(results, monkeypatch, tmp_path / "e.csv")

    assert header[5] == "Final Score"
    by_id = {r["StudentID"]: r for r in rows}
    for m in results.matched_students:
        row = by_id[m.student_id]
        assert row["Curved"] == "Yes"
        g = m.grade
        if g.final_grade is None:
            assert row["Final Score"] == "" and row["Original Exam Score"] == ""
            continue
        assert float(row["Final Score"]) == g.curved_grade
        assert float(row["Original Exam Score"]) == g.final_grade
        assert float(row["Final Score"]) <= results.possible_points
