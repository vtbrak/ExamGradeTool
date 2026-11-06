"""Dashboard histogram now buckets by letter grade (A/B/C/D/F) instead of
raw score bins. Requires QT_QPA_PLATFORM=offscreen.
"""

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.grading import compute_grade  # noqa: E402
from app.core.matching import MatchedStudent  # noqa: E402
from app.gui.dashboard_view import LETTER_ORDER, DashboardView  # noqa: E402
from app.gui import dialogs  # noqa: E402

POSSIBLE_POINTS = 100


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _student(student_id, individual, group):
    grade = compute_grade(individual, group, POSSIBLE_POINTS)
    return MatchedStudent(
        student_id=student_id,
        first_name="First",
        last_name="Last",
        individual_score=individual,
        raw_group_score=group,
        possible_points=POSSIBLE_POINTS,
        individual_key_used="A",
        grade=grade,
    )


def _bar_heights(view: DashboardView) -> dict:
    ax = view.figure.axes[0]
    return {letter: patch.get_height() for letter, patch in zip(LETTER_ORDER, ax.patches)}


def test_bars_grouped_by_base_letter_ignoring_plus_minus(qapp):
    view = DashboardView()
    students = [
        _student("1", 95, 95),  # 95% -> A
        _student("2", 91, 91),  # 91% -> A- -> bucket A
        _student("3", 85, 85),  # 85% -> B  -> bucket B
        _student("4", 65, 65),  # 65% -> D  -> bucket D
        _student("5", 50, 50),  # 50% -> F
    ]

    view.load_results(students, POSSIBLE_POINTS)

    heights = _bar_heights(view)
    assert heights == {"A": 2, "B": 1, "C": 0, "D": 1, "F": 1}


def test_missing_score_students_excluded_from_chart(qapp):
    view = DashboardView()
    students = [
        _student("1", 95, 95),
        _student("2", None, 90),  # missing individual -> no percent, excluded
    ]

    view.load_results(students, POSSIBLE_POINTS)

    heights = _bar_heights(view)
    assert sum(heights.values()) == 1
    assert "Missing a score: 1" in view.stats_label.text()


def test_empty_results_render_without_error(qapp):
    view = DashboardView()
    view.load_results([], POSSIBLE_POINTS)
    assert view.stats_label.text() == "No graded students yet."


# --- the numbers behind the bars, and exporting both ------------------------

def _loaded_dashboard(qapp):
    view = DashboardView()
    # The fixture exam is out of 100, so the score is the percentage.
    view.load_results(
        [
            _student("1000001", 95, 95),  # A
            _student("1000002", 85, 85),  # B
            _student("1000003", 75, 75),  # C
            _student("1000004", 30, 30),  # F
        ],
        POSSIBLE_POINTS,
        exam_name="Exam 1",
    )
    return view


def test_the_counts_behind_the_chart_are_on_screen(qapp):
    """Reading a count off a bar is guesswork, so the figures are shown."""
    view = _loaded_dashboard(qapp)
    rows = {
        view.data_table.item(r, 0).text(): (
            view.data_table.item(r, 1).text(),
            view.data_table.item(r, 2).text(),
        )
        for r in range(view.data_table.rowCount())
    }
    assert list(rows) == LETTER_ORDER  # every letter, including the empty ones
    assert rows["A"] == ("1", "25.0%")
    assert rows["F"] == ("1", "25.0%")
    assert rows["D"] == ("0", "0.0%")


def test_the_chart_data_can_be_exported(qapp, monkeypatch, tmp_path):
    import csv

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    out = tmp_path / "dist.csv"
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "CSV"))
    )
    view = _loaded_dashboard(qapp)
    view._export_data()

    rows = list(csv.reader(out.open()))
    assert rows[0] == ["Exam", "Exam 1"]
    assert rows[2] == ["Letter", "Students", "Share of graded"]
    assert rows[-1] == ["Total", "4", ""]


def test_the_chart_image_can_be_exported(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    out = tmp_path / "dist.png"
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "PNG"))
    )
    view = _loaded_dashboard(qapp)
    view._export_chart()

    assert out.stat().st_size > 0


def test_exporting_with_no_exam_loaded_says_so(qapp, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))

    def fail(*a, **k):
        raise AssertionError("should not open a file dialog with nothing to export")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fail))
    view = DashboardView()
    view._export_data()
    view._export_chart()
    # Dialog text is formatted rich text (app/gui/dialogs.py), so it is read
    # back rather than compared as a string.
    assert [dialogs.split_rendered(m)[0] for m in messages] == [
        "No exam is loaded.",
        "No exam is loaded.",
    ]
