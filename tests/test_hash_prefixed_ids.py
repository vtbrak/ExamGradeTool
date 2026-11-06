"""D2L writes student IDs as "#1234567" in its course-grade exports, and every
historical sheet has them. The "#" is the LMS's marker, not part of the ID, so
it is detected and removed -- otherwise the same student is a separate record
in every sheet.

Also covers the Historical Input drop zone, which is how those sheets arrive.
Requires QT_QPA_PLATFORM=offscreen.
"""

from pathlib import Path

import pytest

from app.core.course_export import parse_course_export
from app.core.parsers import clean_student_id, has_hash_prefix

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.db import store  # noqa: E402
from app.gui.historical_import import (  # noqa: E402
    HistoricalImportDialog,
    SheetDropZone,
)

HEADERS = (
    "OrgDefinedId,Last Name,First Name,"
    "Exam 1 Points Grade <Numeric MaxPoints:80 Category:Exams>,"
    "Lab Grade Points Grade <Numeric MaxPoints:260>,"
    "Adjusted Final Grade Numerator,Adjusted Final Grade Denominator,"
    "End-of-Line Indicator"
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


def _sheet(tmp_path, *ids, name="sheet.csv"):
    rows = [
        f"{student_id},Last{n},First{n},64,200,264,340,#"
        for n, student_id in enumerate(ids, start=1)
    ]
    path = tmp_path / name
    path.write_text("\n".join([HEADERS, *rows]) + "\n")
    return path


# --- the rule itself ---------------------------------------------------------

@pytest.mark.parametrize(
    "cell, expected_id, expected_problem",
    [
        ("#1234567", "1234567", None),
        ("#1234567.0", "1234567", None),       # the "#" and pandas' decimal
        ("#123456", "0123456", None),          # ...and the dropped leading zero
        (" #1234567 ", "1234567", None),
        ("#  1234567", "1234567", None),
        ("1234567", "1234567", None),          # no "#" is equally fine
        ("#", None, None),                     # nothing left: a blank, not a problem
        ("#12345", "12345", "5 digits, expected 7"),
        ("#X999999", "X999999", "not a number"),
    ],
)
def test_a_leading_hash_comes_off_without_hiding_a_real_problem(
    cell, expected_id, expected_problem
):
    assert clean_student_id(cell) == (expected_id, expected_problem)


def test_detection_is_separate_from_removal():
    """The dialog reports that it happened, so it has to be detectable on the
    raw cell rather than inferred afterwards."""
    assert has_hash_prefix("#1234567")
    assert has_hash_prefix(" #1234567")
    assert not has_hash_prefix("1234567")
    assert not has_hash_prefix(None)
    assert not has_hash_prefix(1234567.0)


# --- through the course-sheet parser ----------------------------------------

def test_hash_ids_are_stripped_and_counted(tmp_path):
    parsed = parse_course_export(_sheet(tmp_path, "#1234567", "#2345678", "3456789"))
    assert [r.student_id for r in parsed.rows] == ["1234567", "2345678", "3456789"]
    assert parsed.hash_prefixed_ids == 2  # the third had none


def test_a_hashed_and_an_unhashed_sheet_are_the_same_student(conn, tmp_path):
    """The whole point: 1234567 and #1234567 must not become two records."""
    first = parse_course_export(_sheet(tmp_path, "#1234567", name="with.csv"))
    second = parse_course_export(_sheet(tmp_path, "1234567", name="without.csv"))
    assert first.rows[0].student_id == second.rows[0].student_id

    offering = store.get_or_create_course_offering(conn, "202310")
    for sheet in (first, second):
        resolved, unresolved = store.resolve_student_ids(conn, sheet.rows)
        assert not unresolved
        store.save_course_grades(conn, offering, resolved, source_file="sheet.csv")
    assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1


def test_a_bad_id_is_still_reported_with_its_row_number(tmp_path):
    """Removing the "#" must not quietly rescue an ID that is wrong underneath."""
    parsed = parse_course_export(_sheet(tmp_path, "#1234567", "#12345"))
    assert [r.student_id for r in parsed.rows] == ["1234567"]
    assert [(p.row, p.student_id) for p in parsed.id_problem_rows] == [(3, "12345")]


# --- the dialog says so ------------------------------------------------------

def test_the_preview_says_the_hash_was_removed(qapp, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog = HistoricalImportDialog(conn)
    dialog._load_file(_sheet(tmp_path, "#1234567", "#2345678"))

    text = dialog.preview.toPlainText()
    assert "2 arrived with a leading" in text
    assert "removed" in text


def test_no_note_when_no_id_carried_a_hash(qapp, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog = HistoricalImportDialog(conn)
    dialog._load_file(_sheet(tmp_path, "1234567"))
    assert "leading" not in dialog.preview.toPlainText()


# --- the drop zone -----------------------------------------------------------

class _Drop:
    """The two methods SheetDropZone actually reads off a drop event."""

    def __init__(self, *paths):
        self.data = QMimeData()
        self.data.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        self.accepted = False

    def mimeData(self):
        return self.data

    def acceptProposedAction(self):
        self.accepted = True


def test_dropping_one_sheet_loads_it(qapp, tmp_path):
    loaded = []
    zone = SheetDropZone(loaded.append, lambda: None)
    sheet = _sheet(tmp_path, "#1234567")

    event = _Drop(sheet)
    zone.dragEnterEvent(event)
    assert event.accepted
    zone.dropEvent(event)
    assert loaded == [sheet]


def test_dropping_an_xls_is_refused_with_a_reason(qapp, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[1]))
    loaded = []
    old = tmp_path / "Fall 2016.xls"
    old.write_text("not really an xls")

    SheetDropZone(loaded.append, lambda: None).dropEvent(_Drop(old))
    assert loaded == []
    assert shown == ["Not a course-grade export"]


def test_dropping_two_sheets_is_refused(qapp, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[1]))
    loaded = []

    SheetDropZone(loaded.append, lambda: None).dropEvent(
        _Drop(_sheet(tmp_path, "#1234567", name="a.csv"),
              _sheet(tmp_path, "#2345678", name="b.csv"))
    )
    assert loaded == []
    assert shown == ["One sheet at a time"]
