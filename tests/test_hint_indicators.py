"""A button whose explanation is in a tooltip shows a greyed information mark,
so the explanation isn't hidden from anyone who doesn't happen to hover.

The sweep at the bottom is the point of this file: it walks every real window
and dialog and fails if any tooltipped button lacks a mark, so the next button
added with a tooltip can't quietly ship without one.

Requires QT_QPA_PLATFORM=offscreen.
"""

from pathlib import Path

import pytest

from app.core.course_export import parse_course_export
from app.db import store

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from app.gui import widgets  # noqa: E402
from app.gui.historical_import import HistoricalImportDialog  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402

SHEET = Path(__file__).parent / "fixtures" / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    conn = store.connect(tmp_path / "grades.db")
    parsed = parse_course_export(SHEET)
    resolved, _ = store.resolve_student_ids(conn, parsed.rows)
    offering = store.get_or_create_course_offering(conn, "202610")
    store.save_course_grades(conn, offering, resolved, source_file="sheet.csv")
    store.save_historical_exam_scores(conn, offering, parsed, resolved, source_file="sheet.csv")
    return conn


def _hints(root):
    return [
        label
        for label in root.findChildren(QLabel)
        if label.objectName() == widgets.HINT_OBJECT_NAME
    ]


def _tooltipped(root):
    """Every widget in `root` carrying a tooltip, marks themselves excluded."""
    return [
        w
        for w in root.findChildren(QWidget)
        if w.toolTip() and w.objectName() != widgets.HINT_OBJECT_NAME
    ]


def _describe(widget):
    return f"{type(widget).__name__}({widget.objectName() or widget.toolTip()[:40]!r})"


# --- the widget itself --------------------------------------------------------

def test_with_hint_puts_the_mark_outside_the_button(qapp):
    button = QPushButton("Do the thing")
    button.setToolTip("What the thing does.")

    holder = widgets.with_hint(button)

    assert holder is not button, "the mark is not inside the button"
    marks = _hints(holder)
    assert len(marks) == 1
    assert marks[0].text() == widgets.INFO_CHARACTER
    # Not a button, so it can't be clicked or tabbed to as if it were one.
    assert not isinstance(marks[0], QPushButton)
    assert marks[0].parent() is not button


def test_the_mark_carries_the_same_explanation(qapp):
    button = QPushButton("Back Up Now")
    button.setToolTip("Save a dated copy beside grades.db.")

    holder = widgets.with_hint(button)  # held: Qt deletes an unreferenced parent
    mark = _hints(holder)[0]

    assert mark.toolTip() == button.toolTip()
    # Repeated where a screen reader can reach it: it cannot hover.
    assert mark.accessibleDescription() == button.toolTip()


def test_the_mark_is_greyed(qapp):
    button = QPushButton("x")
    button.setToolTip("y")
    holder = widgets.with_hint(button)
    assert widgets.HINT_COLOR in _hints(holder)[0].styleSheet()


def test_no_tooltip_means_no_visible_mark(qapp):
    """Nothing to advertise, nothing shown."""
    button = QPushButton("Recalculate")
    holder = widgets.with_hint(button)
    holder.show()

    mark = _hints(holder)[0]
    assert not mark.isVisible()
    assert mark.toolTip() == ""


def test_a_tooltip_set_later_gets_its_mark(qapp):
    """Several tooltips are built as data loads, so the mark can't be handed a
    fixed copy of the text at construction."""
    label = QLabel("Pool: 0 course grades")
    holder = widgets.with_hint(label)
    holder.show()
    mark = _hints(holder)[0]
    assert not mark.isVisible()

    label.setToolTip("Ties are not split, so a tier can hold more students.")

    assert mark.isVisible()
    assert mark.toolTip() == label.toolTip()


def test_a_changed_tooltip_is_followed(qapp):
    """The rank-pool caveats are rebuilt as semesters are imported."""
    label = QLabel("Pool")
    label.setToolTip("First wording.")
    holder = widgets.with_hint(label)
    mark = _hints(holder)[0]

    label.setToolTip("Second wording, after an import.")
    assert mark.toolTip() == "Second wording, after an import."

    label.setToolTip("")
    holder.show()
    assert not mark.isVisible(), "a mark with nothing to say hides itself"


def test_the_button_still_works_through_the_wrapper(qapp):
    clicked = []
    button = QPushButton("Go")
    button.setToolTip("Goes.")
    button.clicked.connect(lambda: clicked.append(True))

    holder = widgets.with_hint(button)  # noqa: F841 - keeps the button alive
    button.click()

    assert clicked == [True]


# --- every button in the app --------------------------------------------------

def test_every_tooltip_in_the_main_window_has_a_mark(qapp, conn):
    """The convention, enforced: any widget with a tooltip, not just buttons."""
    window = MainWindow(conn)
    tooltipped = _tooltipped(window)
    assert tooltipped, "sanity: the window has tooltips to check"

    marks = {label.toolTip() for label in _hints(window)}
    missing = [_describe(w) for w in tooltipped if w.toolTip() not in marks]
    assert missing == [], (
        "tooltips with no information mark -- add the widget to its layout "
        f"through widgets.with_hint(): {missing}"
    )


def test_every_tooltip_in_the_import_dialog_has_a_mark(qapp, conn, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog = HistoricalImportDialog(conn)

    marks = {label.toolTip() for label in _hints(dialog)}
    missing = [_describe(w) for w in _tooltipped(dialog) if w.toolTip() not in marks]
    assert missing == [], (
        "tooltips with no information mark -- add the widget to its layout "
        f"through widgets.with_hint(): {missing}"
    )


def test_the_rank_pool_tooltips_are_marked_once_data_loads(qapp, conn):
    """These are built in a render method, not at construction."""
    window = MainWindow(conn)
    rec = window.rec_letter_view
    rec.refresh()

    for label in (rec.pool_label, rec.exam_pool_label):
        assert label.toolTip(), "sanity: the pool labels explain themselves"
        marks = [m for m in _hints(rec) if m.toolTip() == label.toolTip()]
        assert marks, f"no mark for {label.text()!r}"
        assert marks[0].isVisible() or not rec.isVisible()


def test_there_are_no_marks_without_a_button_to_explain(qapp, conn):
    """A mark with no tooltip would be decoration, and one left behind after a
    button lost its tooltip would point at nothing."""
    window = MainWindow(conn)
    for label in _hints(window):
        assert label.toolTip(), "an information mark with nothing to say"
