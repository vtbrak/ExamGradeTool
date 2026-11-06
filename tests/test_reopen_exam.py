"""Reopening a saved exam to edit and export it (roadmap item 14).

Only exams the app graded itself can be reopened. Reopening rebuilds the final
grade from the Individual and Group scores, which would overwrite an imported
exam's real grade — the sheet's figure carries any curve, and the oldest sheets
have no component scores at all.
"""

from pathlib import Path

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from app.core.course_export import parse_course_export  # noqa: E402
from app.core.matching import match_rows  # noqa: E402
from app.core.parsers import parse_exam_file  # noqa: E402
from app.db import store  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
COMBINED = FIXTURES / "synthetic_combined_round.csv"
COURSE = FIXTURES / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    conn = store.connect(tmp_path / "grades.db")
    w = MainWindow(conn)
    return w


def _save_scanned_exam(window):
    """Run and save the combined fixture, as a normal semester's work."""
    window._handle_file_dropped(COMBINED)
    window._run()
    window.results_view._save_to_history()
    return window.current_exam_id


def _pick(monkeypatch, semester_index=0, exam_index=0, cancel=False):
    """Answer the two QInputDialog prompts, recording what was offered."""
    offered = {"semesters": None, "exams": None}
    calls = []

    def get_item(parent, title, label, items, current, editable):
        calls.append(label)
        if label.startswith("Semester"):
            offered["semesters"] = list(items)
            return ("" if cancel else items[semester_index]), not cancel
        offered["exams"] = list(items)
        return ("" if cancel else items[exam_index]), not cancel

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
    return offered


# --- the happy path ----------------------------------------------------------

def test_reopening_restores_the_saved_scores(window, monkeypatch):
    _save_scanned_exam(window)
    before = {m.student_id: m.grade.final_grade for m in window.results_view.matched_students}

    window.results_view.load_results("", [], {"individual": {}, "group": {}}, None)
    assert window.results_view.matched_students == []  # cleared

    _pick(monkeypatch)
    window._open_saved_exam()

    after = {m.student_id: m.grade.final_grade for m in window.results_view.matched_students}
    assert after == before
    assert window.results_view.exam_name == "Exam 1"
    assert window.current_possible_points == 50


def test_reopening_switches_to_results_and_says_what_is_open(window, monkeypatch):
    _save_scanned_exam(window)
    _pick(monkeypatch)
    window._open_saved_exam()

    assert window.tabs.currentWidget() is window.results_view
    assert "Opened saved exam: Exam 1" in window.status_label.text()
    assert "202610" in window.status_label.text()
    # Run is disabled: there's no dropped file to re-run.
    assert window.run_button.isEnabled() is False


def test_a_reopened_exam_can_be_edited_and_saved(window, monkeypatch):
    exam_id = _save_scanned_exam(window)
    _pick(monkeypatch)
    window._open_saved_exam()

    student = window.results_view.matched_students[0]
    student.individual_score = 50
    window.results_view._recompute_all_grades()
    window.results_view._save_to_history()

    saved = window.db_conn.execute(
        "SELECT individual_score FROM exam_scores WHERE exam_id = ? AND student_id = ?",
        (exam_id, student.student_id),
    ).fetchone()
    assert saved["individual_score"] == 50


def test_a_reopened_exam_can_be_exported(window, monkeypatch, tmp_path):
    """The paste-ready per-exam export only works on what's loaded, so
    reopening is what makes a saved exam exportable again."""
    import csv

    from PySide6.QtWidgets import QFileDialog

    _save_scanned_exam(window)
    _pick(monkeypatch)
    window._open_saved_exam()

    out = tmp_path / "reopened.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "CSV (*.csv)"))
    )
    window.results_view._export_csv()

    rows = list(csv.DictReader(out.open()))
    assert rows and "Final Score" in rows[0]


# --- the protection ----------------------------------------------------------

def _import_course_sheet(window, semester="2165"):
    offering = store.get_or_create_course_offering(window.db_conn, semester)
    parsed = parse_course_export(COURSE)
    store.save_historical_exam_scores(window.db_conn, offering, parsed)
    return offering


def test_imported_exams_are_not_offered_for_reopening(window, monkeypatch):
    _import_course_sheet(window)
    offering = store.get_or_create_course_offering(window.db_conn, "2165")
    assert store.list_reopenable_exams(window.db_conn, offering) == []


def test_reopening_a_semester_of_imported_exams_explains_why_it_cannot(window, monkeypatch):
    _import_course_sheet(window)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))
    _pick(monkeypatch)
    window._open_saved_exam()

    assert "no exams that can be reopened" in messages[-1]
    assert "including any curve" in messages[-1]
    assert window.results_view.matched_students == []  # nothing loaded


def test_a_record_exam_is_not_reopenable_because_it_is_elsewhere(window):
    """The protection is structural now: the class record lives in its own
    table, and reopening reads the scanned one. Nothing to exclude."""
    offering = _import_course_sheet(window)
    exam = window.db_conn.execute(
        "SELECT id FROM exams WHERE course_offering_id = ? AND exam_name = 'Exam 1'",
        (offering,),
    ).fetchone()

    assert window.db_conn.execute(
        "SELECT COUNT(*) FROM historical_exam_scores WHERE exam_id = ?", (exam["id"],)
    ).fetchone()[0] > 0
    assert window.db_conn.execute(
        "SELECT COUNT(*) FROM exam_scores WHERE exam_id = ?", (exam["id"],)
    ).fetchone()[0] == 0
    assert store.list_reopenable_exams(window.db_conn, offering) == []


def test_an_exam_with_both_populations_reopens_only_the_scanned_side(window, monkeypatch):
    """A semester can hold both: the scan is reopenable, the record is not
    reachable from here and is left exactly as it is."""
    exam_id = _save_scanned_exam(window)
    window.db_conn.execute(
        """INSERT INTO historical_exam_scores
             (exam_id, student_id, final_grade, possible_points, updated_at)
           SELECT ?, student_id, 36, 50, '2026-01-01' FROM exam_scores WHERE exam_id = ?
           LIMIT 1""",
        (exam_id, exam_id),
    )
    window.db_conn.commit()

    offering = window.current_offering_id
    assert [e["exam_name"] for e in store.list_reopenable_exams(window.db_conn, offering)] == [
        "Exam 1"
    ]

    _pick(monkeypatch)
    window._open_saved_exam()
    reopened = {m.student_id: m.grade.final_grade for m in window.results_view.matched_students}
    assert 36 not in reopened.values()  # the record's figure was not loaded
    assert window.db_conn.execute(
        "SELECT final_grade FROM historical_exam_scores"
    ).fetchone()["final_grade"] == 36


# --- cancelling --------------------------------------------------------------

def test_cancelling_the_picker_loads_nothing(window, monkeypatch):
    _save_scanned_exam(window)
    window.results_view.load_results("", [], {"individual": {}, "group": {}}, None)
    _pick(monkeypatch, cancel=True)
    window._open_saved_exam()
    assert window.results_view.matched_students == []


def test_nothing_saved_yet_is_explained(window, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))
    _pick(monkeypatch)
    window._open_saved_exam()
    assert "No exams have been saved to history yet." in messages[-1]


# --- where the buttons live and what they claim to do -----------------------

def _button_row(view):
    """The QHBoxLayout holding the Results tab's own buttons."""
    top = view.layout()
    for i in range(top.count()):
        row = top.itemAt(i).layout()
        if row is None:
            continue
        for j in range(row.count()):
            if row.itemAt(j).widget() is view.button_separator:
                return row
    raise AssertionError("the button row with the separator was not found")


def _row_order(view):
    """What the row reads as, left to right: button labels and the divider."""
    from PySide6.QtWidgets import QPushButton

    row = _button_row(view)
    order = []
    for i in range(row.count()):
        widget = row.itemAt(i).widget()
        if widget is None:  # spacing or the trailing stretch
            continue
        if widget is view.button_separator:
            order.append("|")
        elif isinstance(widget, QPushButton):
            order.append(widget.text())
        else:  # a button wrapped with its information mark
            buttons = widget.findChildren(QPushButton)
            if buttons:
                order.append(buttons[0].text())
    return order


def test_the_results_buttons_are_two_groups_on_one_line(window):
    """Acting on the exam just imported is one job; reaching for one already on
    record is another. One line, but divided, so Open Saved Exam isn't hit by
    mistake in the middle of working through a scan."""
    assert _row_order(window.results_view) == [
        "Recalculate",
        "Save Exam Scores",
        "Export Exam CSV for D2L…",
        "|",
        "Open Saved Exam…",
    ]


def test_open_saved_exam_is_not_in_the_file_drop_row(window):
    """It loads from the app's own store, not from a file being dropped."""
    from PySide6.QtWidgets import QPushButton

    assert not hasattr(window, "open_saved_button")
    grading = window.exam_grading_group
    assert [b.text() for b in grading.findChildren(QPushButton)] == [
        "Browse for a file…", "Run"
    ]
    assert not grading.isAncestorOf(window.results_view.open_saved_button)


def test_the_save_button_does_not_claim_to_write_the_class_record(window, monkeypatch):
    """It was called "Save to History", which after the split is the one thing
    it doesn't do: history means the class record, built from a course sheet."""
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))

    results = window.results_view
    assert results.save_button.text() == "Save Exam Scores"
    # The explanation is on the button, where it doesn't have to be dismissed.
    tooltip = results.save_button.toolTip()
    assert "stay in Exam Grading" in tooltip
    assert "class record is built" in tooltip

    _save_scanned_exam(window)

    # Confirmed on screen, with no dialog to clear.
    assert "Exam scores saved" in results.save_status_label.text()
    assert messages == []
    # And it really did write the working table, not the record.
    assert window.db_conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0] > 0
    assert window.db_conn.execute(
        "SELECT COUNT(*) FROM historical_exam_scores"
    ).fetchone()[0] == 0
