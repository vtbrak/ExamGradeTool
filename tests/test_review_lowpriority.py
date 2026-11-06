"""The low-priority items from the October 2026 review.
Requires QT_QPA_PLATFORM=offscreen for the view tests."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.course_export import load_course_export, parse_course_export
from app.core.parsers import load_raw_export
from app.db import store
from app.gui.main_window import MainWindow
from app.gui.results_view import INDIVIDUAL_SCORE_COL, SORT_ROLE
from app.gui import dialogs  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
COMBINED = FIXTURES / "synthetic_combined_round.csv"
TRIPLE = FIXTURES / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "grades.db")
    yield c
    c.close()


# --- student search ---------------------------------------------------------------

def test_search_treats_wildcards_as_plain_text(conn):
    conn.execute("INSERT INTO students (student_id, first_name, last_name) VALUES ('1000001', 'Ann', 'Alpha')")
    conn.execute("INSERT INTO students (student_id, first_name, last_name) VALUES ('1000002', 'Ben', '100%')")
    conn.execute("INSERT INTO students (student_id, first_name, last_name) VALUES ('1000003', 'Cy', 'O_Neil')")
    conn.commit()

    assert [r["student_id"] for r in store.search_students(conn, "%")] == ["1000002"]
    assert [r["student_id"] for r in store.search_students(conn, "_")] == ["1000003"]
    assert [r["student_id"] for r in store.search_students(conn, "O_N")] == ["1000003"]
    assert len(store.search_students(conn, "1")) == 3  # ordinary text still matches anywhere


# --- indexes -------------------------------------------------------------------------

def test_student_id_indexes_exist(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"exam_scores_by_student", "historical_exam_scores_by_student", "course_grades_by_student"} <= names


# --- .xls is refused clearly ----------------------------------------------------------

def test_xls_is_refused_with_a_plain_message(tmp_path):
    bogus = tmp_path / "old.xls"
    bogus.write_bytes(b"\xd0\xcf\x11\xe0 not really")
    with pytest.raises(ValueError, match=r"\.csv or \.xlsx"):
        load_raw_export(bogus)
    with pytest.raises(ValueError, match=r"\.csv or \.xlsx"):
        load_course_export(bogus)


# --- point mismatches don't bleed between saves -------------------------------------

def test_a_mismatch_is_reported_once_and_not_by_the_next_save(conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    store.get_or_create_exam(conn, offering, "Exam 1", 50)
    parsed = parse_course_export(TRIPLE)
    for col in parsed.exam_columns:
        col.possible_points = 80  # every exam in the sheet disagrees with 50
    store.save_historical_exam_scores(conn, offering, parsed)
    first = store.exam_point_mismatches(conn)
    assert any(name == "Exam 1" and (rec, inc) == (50, 80) for name, rec, inc in first)

    # Unread, then a save that agrees: the old mismatch is gone, not re-reported.
    for col in parsed.exam_columns:
        col.possible_points = 50 if col.exam_name == "Exam 1" else col.possible_points
    store.save_historical_exam_scores(conn, offering, parsed)
    assert not any(name == "Exam 1" for name, _, _ in store.exam_point_mismatches(conn))


# --- views ------------------------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    conn = store.connect(tmp_path / "grades.db")
    win = MainWindow(conn)
    yield win
    conn.close()


def test_history_refresh_keeps_the_selected_semester(window):
    conn = window.db_conn
    parsed = parse_course_export(TRIPLE)
    for code in ("202430", "202510", "202530"):
        offering = store.get_or_create_course_offering(conn, code)
        store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")
    hv = window.history_view
    hv.refresh()
    assert hv.semester_combo.currentIndex() == 0  # newest first

    hv.semester_combo.setCurrentIndex(2)  # the oldest: 202430
    chosen = hv.current_offering_id
    hv.refresh()  # what a Save or an import triggers
    assert hv.current_offering_id == chosen
    assert hv.semester_combo.currentIndex() == 2


def test_history_refresh_falls_back_when_the_semester_is_gone(window):
    conn = window.db_conn
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, store.get_or_create_course_offering(conn, "202430"), parsed.rows, "s")
    hv = window.history_view
    hv.refresh()
    hv.current_offering_id = 9999  # no such offering
    hv.refresh()
    assert hv.semester_combo.currentIndex() == 0


def test_invalid_score_restores_the_cell_without_rebuilding(window, monkeypatch):
    from app.gui import results_view as rv_module

    # The warning is deferred with QTimer.singleShot(0, ...) so it runs after
    # the itemChanged signal returns; run it at once here and record it.
    warned = []
    monkeypatch.setattr(rv_module.QTimer, "singleShot", staticmethod(lambda _ms, fn: fn()))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))

    window._handle_file_dropped(COMBINED)
    window._run()
    table = window.results_view.table
    item = table.item(0, INDIVIDUAL_SCORE_COL)
    before = item.text()
    items_before = [table.item(r, 0) for r in range(table.rowCount())]

    item.setText("abc")  # fires itemChanged

    assert [dialogs.split_rendered(w)[0] for w in warned] == ["'abc' isn't a number."]
    assert item.text() == before
    # The same QTableWidgetItem objects are still in the table: no rebuild.
    assert [table.item(r, 0) for r in range(table.rowCount())] == items_before


def test_clearing_a_score_clears_its_sort_key(window):
    window._handle_file_dropped(COMBINED)
    window._run()
    table = window.results_view.table
    item = table.item(0, INDIVIDUAL_SCORE_COL)
    assert item.data(SORT_ROLE) is not None

    item.setText("")
    assert item.text() == ""
    assert item.data(SORT_ROLE) is None


def test_run_button_is_re_enabled_after_a_run(window):
    window._handle_file_dropped(COMBINED)
    window._run()
    assert window.run_button.isEnabled()
