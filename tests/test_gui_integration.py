"""End-to-end drive of the real GUI widgets (offscreen), simulating the
instructor's workflow: drop a single exam file, run it, edit a score, save,
export, re-upload to overwrite, and check the History tab. Requires
QT_QPA_PLATFORM=offscreen.

Exams only ever arrive as one of two file shapes: an Individual-round-only
export (forms A/B), or a combined export with Individual and Group data
together (forms A/B/C). Group-round (form C) data is never its own standalone
file, so the drop zone only ever accepts one file at a time.
"""

import csv
from pathlib import Path

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QTableWidgetItem  # noqa: E402

from app.db import store  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
INDIVIDUAL_ONLY = FIXTURES / "synthetic_individual_round.csv"
COMBINED = FIXTURES / "synthetic_combined_round.csv"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def no_blocking_info_dialogs(monkeypatch):
    # QMessageBox.information/warning/critical call exec() and block on a real
    # (if invisible) modal event loop with no one to click them -- silence
    # those here; .question is patched per-test where its return value matters.
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


@pytest.fixture
def window(qapp, tmp_path):
    conn = store.connect(tmp_path / "grades.db")
    win = MainWindow(conn)
    yield win
    conn.close()


def test_individual_only_run_reports_missing_and_hides_duplicates(window):
    window._handle_file_dropped(INDIVIDUAL_ONLY)
    window._run()

    rv = window.results_view
    assert rv.summary_label.text().startswith("Exam 1")
    ids = {m.student_id for m in rv.matched_students}
    assert ids == {"1000001", "1000002", "1000004"}  # Cara (1003) held out as a duplicate
    assert all(m.grade.missing_flag == "Missing Group" for m in rv.matched_students)
    assert "1000003" in rv.duplicates_label.text()
    # Cara's two conflicting rows are both Form A (44.0 and 10.0) in the fixture.
    assert "Form A: 44.0" in rv.duplicates_label.text()
    assert "Form A: 10.0" in rv.duplicates_label.text()


def test_dropping_two_files_at_once_is_rejected(window, monkeypatch):
    warned = {"count": 0}
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warned.__setitem__("count", warned["count"] + 1)
    )

    class FakeUrl:
        def __init__(self, path):
            self._path = path

        def isLocalFile(self):
            return True

        def toLocalFile(self):
            return str(self._path)

    class FakeMimeData:
        def hasUrls(self):
            return True

        def urls(self):
            return [FakeUrl(INDIVIDUAL_ONLY), FakeUrl(COMBINED)]

    class FakeDropEvent:
        def mimeData(self):
            return FakeMimeData()

    window.drop_zone.dropEvent(FakeDropEvent())

    assert warned["count"] == 1
    assert window.loaded_file is None  # nothing was accepted


def test_combined_file_run_completes_records(window):
    # An Individual-only file first (typical Round 1 upload)...
    window._handle_file_dropped(INDIVIDUAL_ONLY)
    window._run()
    window.results_view._save_to_history()  # first save, no prior data -> no confirmation needed

    # ...then the combined Individual+Group export that arrives once Round 2
    # is scanned (Group data never comes as its own separate file).
    window._handle_file_dropped(COMBINED)
    window._run()

    rv = window.results_view
    by_id = {m.student_id: m for m in rv.matched_students}
    assert by_id["1000001"].grade.final_grade == pytest.approx(0.6 * 40 + 0.4 * 42)
    assert by_id["1000005"].grade.missing_flag == "Missing Individual"  # Eve, group-only in the file


def test_combined_file_alone_works_without_a_prior_upload(window):
    # The combined file is also a valid *first* upload for an exam -- nothing
    # requires the Individual-only file to have been seen before.
    window._handle_file_dropped(COMBINED)
    window._run()

    rv = window.results_view
    by_id = {m.student_id: m for m in rv.matched_students}
    assert by_id["1000001"].grade.final_grade == pytest.approx(0.6 * 40 + 0.4 * 42)
    assert by_id["1000005"].grade.missing_flag == "Missing Individual"


def test_manual_edit_recomputes_final_grade(window):
    # Individual+Group data both present so a real final_grade exists to
    # compare against before/after the edit.
    window._handle_file_dropped(COMBINED)
    window._run()

    rv = window.results_view
    row = next(i for i, m in enumerate(rv.matched_students) if m.student_id == "1000001")
    original_final_grade = rv.matched_students[row].grade.final_grade

    rv._suspend_item_signal = False
    item = rv.table.item(row, 3)  # Individual Score column
    item.setText("50")  # triggers itemChanged -> _on_item_changed

    # Editing alone updates the raw score but must NOT recompute the derived
    # grade -- that only happens when Recalculate is explicitly clicked, so
    # editing several cells in a row doesn't re-render after every keystroke.
    assert rv.matched_students[row].individual_score == 50
    assert rv.matched_students[row].grade.final_grade == original_final_grade

    rv._recalculate()

    # Individual (50) now exceeds the raw Group score (42), so the
    # score-protection override applies: effective_group becomes 50 too.
    assert rv.matched_students[row].grade.final_grade == pytest.approx(50.0)
    assert rv.matched_students[row].grade.final_grade != original_final_grade


def test_save_prompts_overwrite_and_export_writes_expected_csv(window, monkeypatch, tmp_path):
    # First save (no prior data for this exam -> no confirmation dialog should appear).
    window._handle_file_dropped(INDIVIDUAL_ONLY)
    window._run()

    def fail_if_asked(*args, **kwargs):
        raise AssertionError("Should not prompt to overwrite on the very first save")

    monkeypatch.setattr(QMessageBox, "question", fail_if_asked)
    window.results_view._save_to_history()

    # Re-running + re-saving the same Individual-only file now SHOULD trigger
    # the overwrite prompt (this is the instructor's preferred way to correct
    # a whole file: just re-upload and re-save).
    asked = {"count": 0}

    def confirm_yes(*args, **kwargs):
        asked["count"] += 1
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm_yes)
    window._handle_file_dropped(INDIVIDUAL_ONLY)
    window._run()
    window.results_view._save_to_history()
    assert asked["count"] == 1

    # Now bring in the combined file (adds Group data, re-supplies Individual
    # too -> should prompt again since Individual was already saved) and
    # export the CSV the instructor uploads to the CMS.
    monkeypatch.setattr(QMessageBox, "question", confirm_yes)
    window._handle_file_dropped(COMBINED)
    window._run()
    window.results_view._save_to_history()

    export_path = tmp_path / "exam1_export.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(export_path), "CSV files (*.csv)")
    )
    window.results_view._export_csv()

    assert export_path.exists()
    with open(export_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        data_rows = list(reader)

    # Individual / Group (Used) / Final grouped contiguous for a CMS paste,
    # then a blank spacer column, then the reference-only columns.
    assert header == [
        "First Name",
        "Last Name",
        "StudentID",
        "Individual Score",
        "Group Score (Used)",
        "Final Score",
        "",
        "Final %",
        "Group Score (Original)",
        "Group Score Replaced",
        "Form Letter",
    ]
    by_id = {row[2]: dict(zip(header, row)) for row in data_rows}
    assert by_id["1000001"]["Individual Score"] == "40.0"
    assert by_id["1000001"]["Group Score (Used)"] == "42.0"
    assert by_id["1000001"]["Final Score"] == "40.8"
    # Raw score and percentage are both reported; the percentage is what stays
    # comparable when an exam's possible points change between semesters.
    assert by_id["1000001"]["Final %"] == "81.6"
    assert by_id["1000001"]["Group Score (Original)"] == "42.0"
    assert by_id["1000001"]["Group Score Replaced"] == ""
    assert by_id["1000001"]["Form Letter"] == "A"


def test_history_tab_reflects_saved_semester_data(window, monkeypatch):
    window._handle_file_dropped(INDIVIDUAL_ONLY)
    window._run()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.results_view._save_to_history()

    window._handle_file_dropped(COMBINED)
    window._run()
    window.results_view._save_to_history()

    hv = window.history_view
    assert hv.semester_combo.count() == 1
    # Not closed yet, so the picker says so.
    assert hv.semester_combo.currentText() == "202610 (Spring 2026) — in progress"
    # Scanned exams are working data, not the class record: the semester is
    # listed, but its roster is empty until a course sheet is imported, and the
    # note says so rather than leaving it looking broken.
    assert hv.current_summary == []
    assert "No course grades imported yet" in hv.stats_label.text()


def test_real_raw_sample_without_identity_does_not_crash(window):
    # The real sample file has FirstName/LastName/StudentID stripped for
    # privacy; the app should still run without crashing, just matching zero
    # students (nothing to join on).
    window._handle_file_dropped(FIXTURES / "round1_round2_raw_sample.csv")
    window._run()
    assert window.results_view.matched_students == []


def test_column_sort_is_numeric_not_alphabetic(window):
    # Final grades: Alice 40.8, Bob 36.8, Dan 35.6, Eve blank (missing
    # Individual -> no final grade, sorts last via text-fallback). Numeric
    # ascending order (35.6, 36.8, 40.8) disagrees with lexicographic order
    # of the *rounded display strings* in general, so this exercises the
    # SORT_ROLE numeric key rather than a coincidental string-sort match.
    window._handle_file_dropped(COMBINED)
    window._run()

    rv = window.results_view
    from app.gui.results_view import FINAL_GRADE_COL, STUDENT_ID_ROLE

    rv.table.sortItems(FINAL_GRADE_COL, Qt.SortOrder.DescendingOrder)
    ordered_ids = [rv.table.item(r, 2).data(STUDENT_ID_ROLE) for r in range(rv.table.rowCount())]
    assert ordered_ids == ["1000001", "1000002", "1000004", "1000005"]  # 40.8, 36.8, 35.6, then blank


def test_editing_after_sort_updates_the_correct_student(window):
    # Regression guard: editing used to map a table row to a student by
    # position (self.matched_students[row.row()]), which breaks the instant
    # the table is sorted into a different order than it was populated in.
    window._handle_file_dropped(COMBINED)
    window._run()

    rv = window.results_view
    from app.gui.results_view import INDIVIDUAL_SCORE_COL, STUDENT_ID_ROLE

    rv.table.sortItems(INDIVIDUAL_SCORE_COL, Qt.SortOrder.DescendingOrder)
    # Whichever student the sort puts in row 0 (highest Individual Score),
    # editing that row's cell must update *that* student, not whichever
    # student originally occupied row 0 before sorting.
    first_row_student_id = rv.table.item(0, 2).data(STUDENT_ID_ROLE)
    assert first_row_student_id == "1000001"  # Alice, highest individual score (40)

    other_ids = [m for m in rv._students_by_id if m != first_row_student_id]
    other_scores_before = {sid: rv._students_by_id[sid].individual_score for sid in other_ids}

    rv._suspend_item_signal = False
    item = rv.table.item(0, INDIVIDUAL_SCORE_COL)
    item.setText("99")

    assert rv._students_by_id[first_row_student_id].individual_score == 99
    for sid in other_ids:
        assert rv._students_by_id[sid].individual_score == other_scores_before[sid]


def test_whole_number_edit_is_normalized_to_decimal(window):
    from app.gui.results_view import INDIVIDUAL_SCORE_COL

    window._handle_file_dropped(INDIVIDUAL_ONLY)
    window._run()

    rv = window.results_view
    row = next(i for i, m in enumerate(rv.matched_students) if m.student_id == "1000001")

    rv._suspend_item_signal = False
    item = rv.table.item(row, INDIVIDUAL_SCORE_COL)
    item.setText("40")  # no decimal point

    assert rv.matched_students[row].individual_score == 40.0
    assert item.text() == "40.0"  # visually normalized immediately, no Recalculate needed
