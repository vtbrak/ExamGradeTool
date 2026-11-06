"""Semester-code guard in the Historical Input dialog.
Requires QT_QPA_PLATFORM=offscreen.
"""

from pathlib import Path

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox  # noqa: E402

from app.core.course_export import parse_course_export  # noqa: E402
from app.db import store  # noqa: E402
from app.gui.historical_import import HistoricalImportDialog  # noqa: E402

TRIPLE = Path(__file__).parent / "fixtures" / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


@pytest.fixture
def dialog(qapp, conn, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a))
    # _do_import asks whether to close the semester. Unpatched this opens a
    # real modal and hangs the suite, so answer No by default; the tests that
    # care about closing override it.
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    d = HistoricalImportDialog(conn)
    d.parsed = parse_course_export(TRIPLE)
    d.file_label.setText(TRIPLE.name)
    d._refresh_preview()
    d.shown = shown
    return d


def _import_enabled(d):
    return d.buttons.button(QDialogButtonBox.Ok).isEnabled()


def test_blank_semester_blocks_import(dialog):
    assert dialog.semester_combo.currentText() == ""
    assert not _import_enabled(dialog)


@pytest.mark.parametrize("text", ["Fall 2019", "20261", "202640", "2204"])
def test_invalid_code_blocks_import_and_explains(dialog, text):
    dialog.semester_combo.setCurrentText(text)
    assert not _import_enabled(dialog)
    assert "isn't a valid semester code" in dialog.semester_status.text()
    assert "202610 = Spring 2026" in dialog.semester_status.text()


@pytest.mark.parametrize("text, label", [("202610", "Spring 2026"), ("2205", "Fall 2020")])
def test_valid_code_is_interpreted_back_to_the_user(dialog, text, label):
    dialog.semester_combo.setCurrentText(text)
    assert _import_enabled(dialog)
    assert label in dialog.semester_status.text()
    assert "new semester" in dialog.semester_status.text()


def test_equivalent_code_is_flagged_and_existing_code_is_used(dialog, conn):
    store.get_or_create_course_offering(conn, "2201")  # Spring 2020, older format
    dialog.semester_combo.setCurrentText("202010")  # Spring 2020, current format

    status = dialog.semester_status.text()
    assert "already saved under code 2201" in status
    assert _import_enabled(dialog)

    dialog._do_import()
    assert store.list_semester_codes(conn) == ["2201"]
    saved = conn.execute(
        """SELECT co.semester_code FROM course_grades cg
           JOIN course_offerings co ON co.id = cg.course_offering_id"""
    ).fetchall()
    assert {r["semester_code"] for r in saved} == {"2201"}


def test_do_import_refuses_an_invalid_code_even_if_called_directly(dialog, conn):
    dialog.semester_combo.setCurrentText("Fall 2019")
    dialog._do_import()
    assert store.list_semester_codes(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM course_grades").fetchone()[0] == 0


def test_existing_semesters_are_offered_newest_first(qapp, conn):
    for code in ("2165", "202610", "2201"):
        store.get_or_create_course_offering(conn, code)
    d = HistoricalImportDialog(conn)
    items = [d.semester_combo.itemText(i) for i in range(d.semester_combo.count())]
    assert items == ["202610", "2201", "2165"]


def test_summer_across_formats_warns_but_does_not_merge(dialog, conn):
    store.get_or_create_course_offering(conn, "2202")  # Summer 1, 2020
    dialog.semester_combo.setCurrentText("202020")  # Summer 2020, current format

    status = dialog.semester_status.text()
    assert "Summer 2020 — new semester." in status
    assert "2202 (Summer 1, 2020) is also saved" in status
    assert _import_enabled(dialog)

    dialog._do_import()
    assert store.list_semester_codes(conn) == ["202020", "2202"]


def test_two_older_summer_sessions_in_one_year_do_not_warn(dialog, conn):
    store.get_or_create_course_offering(conn, "2202")
    dialog.semester_combo.setCurrentText("2203")
    assert dialog.semester_status.text() == "Summer 2, 2020 — new semester."
