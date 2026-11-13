"""Adding a curve to an exam.

The rule that matters: a curve lifts every student's final grade by the same
number of points, but **no final grade may exceed the exam's point total**.
A student already near the top therefore receives less of the curve than
everyone else, which the screen says out loud rather than leaving to be
discovered.

The curve is stored on the exam, not baked into each saved score, because
reopening a saved exam recomputes every final grade from the Individual and
Group scores -- a curve living only in the saved final would vanish the first
time the exam was reopened.

Requires QT_QPA_PLATFORM=offscreen for the view tests.
"""

from pathlib import Path

import pytest

from app.core.grading import compute_grade
from app.db import store

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gui.main_window import MainWindow  # noqa: E402

COMBINED = Path(__file__).parent / "fixtures" / "synthetic_combined_round.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


# --- the rule ----------------------------------------------------------------

def test_a_curve_adds_points_to_the_final_grade():
    plain = compute_grade(30, 40, 50)
    curved = compute_grade(30, 40, 50, curve_points=4)

    assert plain.final_grade == 34.0           # 0.6*30 + 0.4*40
    assert curved.final_grade == 38.0
    assert curved.curve_points == 4
    assert curved.uncurved_final_grade == 34.0


def test_the_curve_never_pushes_a_score_past_the_total():
    """The requirement, stated once: 48 + 5 is 50, not 53."""
    curved = compute_grade(48, 48, 50, curve_points=5)

    assert curved.final_grade == 50.0
    assert curved.percent == 100.0
    assert curved.curve_capped is True


def test_a_student_at_full_marks_stays_at_full_marks():
    curved = compute_grade(50, 50, 50, curve_points=10)
    assert curved.final_grade == 50.0


def test_a_curve_that_fits_is_not_reported_as_capped():
    curved = compute_grade(30, 30, 50, curve_points=10)
    assert curved.final_grade == 40.0
    assert curved.curve_capped is False


def test_the_percentage_and_pass_mark_follow_the_curved_score():
    """Everything downstream -- the percentage, the pass line, the letter grade
    on the Dashboard -- has to see the curved number, not the raw one."""
    plain = compute_grade(33, 33, 50)          # 33/50 = 66%, a fail
    curved = compute_grade(33, 33, 50, curve_points=3)

    assert plain.percent == 66.0 and plain.passed is False
    assert curved.final_grade == 36.0
    assert curved.percent == 72.0 and curved.passed is True


def test_a_missing_score_is_not_given_a_curve():
    """A student with no Group score has no final grade to lift; inventing one
    would turn a missing score into a real one."""
    curved = compute_grade(40, None, 50, curve_points=5)

    assert curved.final_grade is None
    assert curved.missing_flag == "Missing Group"
    assert curved.curve_capped is False


def test_no_curve_leaves_the_grade_exactly_as_before():
    assert compute_grade(30, 40, 50).final_grade == compute_grade(
        30, 40, 50, curve_points=0
    ).final_grade


def test_a_curve_keeps_half_points_rather_than_rounding_them_away():
    """0.6*31 + 0.4*44 is 36.2; +2 is 38.2, not 38."""
    curved = compute_grade(31, 44, 50, curve_points=2)
    assert curved.uncurved_final_grade == 36.2
    assert curved.final_grade == 38.2


# --- it is stored on the exam ------------------------------------------------

def test_an_exam_has_no_curve_until_one_is_set(conn):
    offering = store.get_or_create_course_offering(conn, "202630")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)

    assert store.get_exam_curve(conn, exam_id) == 0.0


def test_the_curve_round_trips(conn):
    offering = store.get_or_create_course_offering(conn, "202630")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)

    store.set_exam_curve(conn, exam_id, 3.5)
    assert store.get_exam_curve(conn, exam_id) == 3.5

    store.set_exam_curve(conn, exam_id, 0)
    assert store.get_exam_curve(conn, exam_id) == 0.0


def test_a_database_predating_the_column_reads_as_no_curve(tmp_path):
    """An exam saved before the column existed gets it by migration, and
    ALTER TABLE cannot backfill, so that row holds NULL rather than 0.

    A fresh database can't reach this state -- schema.sql declares the column
    NOT NULL DEFAULT 0 -- so the old schema is built by hand here."""
    import sqlite3

    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """CREATE TABLE course_offerings (id INTEGER PRIMARY KEY AUTOINCREMENT,
               semester_code TEXT NOT NULL, course_number TEXT NOT NULL,
               UNIQUE (semester_code, course_number));
           CREATE TABLE exams (id INTEGER PRIMARY KEY AUTOINCREMENT,
               course_offering_id INTEGER NOT NULL, exam_name TEXT NOT NULL,
               possible_points REAL, UNIQUE (course_offering_id, exam_name));
           INSERT INTO course_offerings (semester_code, course_number)
               VALUES ('202630', '211');
           INSERT INTO exams (course_offering_id, exam_name, possible_points)
               VALUES (1, 'Exam 1', 50);"""
    )
    legacy.commit()
    legacy.close()

    conn = store.connect(path)  # migrates: adds curve_points as NULL
    exam_id = conn.execute("SELECT id FROM exams").fetchone()["id"]
    assert conn.execute(
        "SELECT curve_points FROM exams WHERE id = ?", (exam_id,)
    ).fetchone()["curve_points"] is None

    assert store.get_exam_curve(conn, exam_id) == 0.0
    # And it is writable from there on.
    store.set_exam_curve(conn, exam_id, 2)
    assert store.get_exam_curve(conn, exam_id) == 2.0


def test_the_curve_comes_back_when_the_exam_is_reopened(conn):
    """The reason it is stored on the exam at all."""
    offering = store.get_or_create_course_offering(conn, "202630")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)
    store.set_exam_curve(conn, exam_id, 2)

    _name, _points, _matched, curve = store.load_saved_exam(conn, exam_id)
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


def test_typing_a_curve_lifts_every_final_grade(qapp, conn, monkeypatch):
    window = _window(conn, monkeypatch)
    results = window.results_view
    before = [m.grade.final_grade for m in results.matched_students]

    results.curve_box.setValue(3)

    after = [m.grade.final_grade for m in results.matched_students]
    total = results.possible_points
    assert any(x is not None for x in before), "sanity: some grades to lift"
    for was, now in zip(before, after):
        if was is None:  # a missing score stays missing
            assert now is None
            continue
        assert now == min(was + 3, total)
    assert all(
        n <= total for n in after if n is not None
    ), "no score may exceed the exam total"


def test_the_curve_box_starts_at_zero(qapp, conn, monkeypatch):
    results = _window(conn, monkeypatch).results_view
    assert results.curve_box.value() == 0
    assert results.curve_points == 0
    assert results.curve_note.text() == ""


def test_setting_the_curve_back_to_zero_restores_the_original_grades(qapp, conn, monkeypatch):
    results = _window(conn, monkeypatch).results_view
    before = [m.grade.final_grade for m in results.matched_students]

    results.curve_box.setValue(5)
    results.curve_box.setValue(0)

    assert [m.grade.final_grade for m in results.matched_students] == before


def test_the_screen_says_how_many_students_the_cap_bit(qapp, conn, monkeypatch):
    results = _window(conn, monkeypatch).results_view
    results.curve_box.setValue(40)  # far more than anyone needs

    capped = [m for m in results.matched_students if m.grade.curve_capped]
    assert capped, "a 40-point curve on a 50-point exam must cap somebody"
    assert f"{len(capped)} student(s) capped" in results.curve_note.text()


def test_a_curve_that_fits_everyone_says_nothing(qapp, conn, monkeypatch):
    results = _window(conn, monkeypatch).results_view
    results.curve_box.setValue(0.5)

    if not any(m.grade.curve_capped for m in results.matched_students):
        assert results.curve_note.text() == ""


def test_saving_records_the_curve_and_reopening_restores_it(qapp, conn, monkeypatch):
    window = _window(conn, monkeypatch)
    results = window.results_view
    results.curve_box.setValue(2.5)
    exam_id = window.ensure_current_exam()
    results._save_to_history()

    assert store.get_exam_curve(conn, exam_id) == 2.5

    saved = [(m.student_id, m.grade.final_grade) for m in results.matched_students]

    # Reopen it the way the window does.
    name, points, matched, curve = store.load_saved_exam(conn, exam_id)
    fresh = MainWindow(conn)
    fresh.results_view.load_results(
        exam_name=name,
        matched_students=matched,
        duplicates={"individual": {}, "group": {}},
        possible_points=points,
        curve_points=curve,
    )

    assert fresh.results_view.curve_box.value() == 2.5
    reopened = {
        m.student_id: m.grade.final_grade for m in fresh.results_view.matched_students
    }
    for student_id, grade in saved:
        assert reopened[student_id] == grade, "reopening changed a final grade"


def test_reopening_does_not_apply_the_curve_twice(qapp, conn, monkeypatch):
    """The failure this design exists to prevent: scores already carry the
    curve when they are handed to the view, so applying it again on load would
    double it."""
    window = _window(conn, monkeypatch)
    window.results_view.curve_box.setValue(4)
    exam_id = window.ensure_current_exam()
    window.results_view._save_to_history()

    name, points, matched, curve = store.load_saved_exam(conn, exam_id)
    fresh = MainWindow(conn)
    fresh.results_view.load_results(
        exam_name=name,
        matched_students=matched,
        duplicates={"individual": {}, "group": {}},
        possible_points=points,
        curve_points=curve,
    )

    for m in fresh.results_view.matched_students:
        if m.grade.final_grade is None:
            continue
        expected = min(m.grade.uncurved_final_grade + 4, points)
        assert m.grade.final_grade == expected


def test_the_exported_csv_carries_the_curved_score(qapp, conn, monkeypatch, tmp_path):
    """The CSV is pasted into D2L, so it must hold what the student gets."""
    from PySide6.QtWidgets import QFileDialog

    window = _window(conn, monkeypatch)
    results = window.results_view
    results.curve_box.setValue(3)
    window.ensure_current_exam()

    out = tmp_path / "export.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    results._export_csv()

    text = out.read_text()
    for m in results.matched_students:
        if m.grade.final_grade is not None:
            assert str(m.grade.final_grade) in text
