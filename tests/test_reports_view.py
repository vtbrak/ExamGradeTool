"""The Reports tab: exports, charts and their statistics.

Everything here reads the class record. Exam averages and course averages are
different measures -- exams alone run higher, since the course total includes
the lab and quizzes -- so each view names which it uses.
"""

import csv
from pathlib import Path

import pytest

from app.core.grading import PASS_THRESHOLD_PERCENT
from app.core.stats import histogram, summarize
from app.db import store

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFileDialog,
    QInputDialog,
    QMessageBox,
)

from app.core.course_export import parse_course_export  # noqa: E402
from app.gui.reports_view import (  # noqa: E402
    STAT_COLUMNS,
    VIEW_COURSE_ALL,
    VIEW_COURSE_BY_SEMESTER,
    VIEW_EXAM_BY_EXAM,
    VIEW_EXAM_ONE_SEMESTER,
    VIEW_ONE_EXAM_ACROSS,
    VIEWS,
    ReportsView,
)

TRIPLE = Path(__file__).parent / "fixtures" / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


@pytest.fixture
def loaded(conn):
    """Two semesters of class record, so comparisons have something to compare."""
    for semester in ("202430", "202610"):
        offering = store.get_or_create_course_offering(conn, semester)
        parsed = parse_course_export(TRIPLE)
        store.save_course_grades(conn, offering, parsed.rows, f"{semester}.csv")
        store.save_historical_exam_scores(conn, offering, parsed)
    return conn


# --- the statistics themselves ----------------------------------------------

def test_summarize_reports_spread_and_range():
    stats = summarize([60, 70, 80, 90])
    assert stats["n"] == 4
    assert stats["mean"] == 75
    assert stats["median"] == 75
    assert (stats["min"], stats["max"]) == (60, 90)
    assert round(stats["sd"], 3) == 12.910  # sample SD, n-1
    assert round(stats["se"], 3) == 6.455  # SD / sqrt(n)


def test_a_single_score_has_no_spread():
    stats = summarize([82])
    assert stats["n"] == 1 and stats["mean"] == 82
    assert stats["sd"] is None and stats["se"] is None


def test_summarize_ignores_missing_values():
    assert summarize([80, None, 90])["n"] == 2
    assert summarize([])["n"] == 0


def test_histogram_bins_are_fixed_so_groups_can_be_compared():
    bins = histogram([0, 4.9, 5, 99.9, 100])
    assert bins[0] == (0.0, 5.0, 2)  # 0 and 4.9
    assert bins[1][2] == 1  # 5 lands in the next bin, not the first
    assert bins[-1] == (95.0, 100.0, 2)  # the top bin includes 100


# --- the datasets behind each view ------------------------------------------

def test_exam_and_course_pools_are_different_measures(loaded):
    course = store.get_course_grade_pool(loaded)
    exams = store.get_exam_average_pool(loaded)
    assert len(course) == 6  # 3 students x 2 semesters
    assert len(exams) == 6
    assert course != exams  # the course total includes lab and quizzes


def test_grouped_datasets_are_in_order(loaded):
    assert list(store.course_averages_by_semester(loaded)) == ["202430", "202610"]
    assert list(store.exam_averages_by_semester(loaded)) == ["202430", "202610"]
    assert list(store.exam_percents_by_exam(loaded)) == ["Exam 1", "Exam 2"]


def test_one_semester_dataset_is_scoped_to_it(loaded):
    scoped = store.exam_percents_for_semester(loaded, "202430")
    assert list(scoped) == ["Exam 1", "Exam 2"]
    assert all(len(v) == 3 for v in scoped.values())


# --- the view ---------------------------------------------------------------

def test_every_view_renders_with_data(qapp, loaded, monkeypatch):
    # Answer whichever picker a view opens with its first option, so both the
    # semester and the exam prompts get something valid.
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        staticmethod(lambda parent, title, label, items, *a, **k: (items[0], True)),
    )
    view = ReportsView(loaded)
    view.refresh()
    for name in VIEWS:
        view.view_combo.setCurrentText(name)
        assert view.stats_table.rowCount() > 0, name
        assert view.caption.text()


def test_comparisons_add_a_pooled_row(qapp, loaded):
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_COURSE_BY_SEMESTER)
    labels = [
        view.stats_table.item(r, 0).text() for r in range(view.stats_table.rowCount())
    ]
    assert labels[-1] == "All groups"
    assert len(labels) == 3  # two semesters plus the pooled row


def test_a_single_group_is_a_distribution_with_no_pooled_row(qapp, loaded):
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_COURSE_ALL)
    assert view.stats_table.rowCount() == 1
    assert view.stats_table.item(0, 0).text() == "All semesters"


def test_each_view_says_which_measure_it_uses(qapp, loaded):
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_EXAM_BY_EXAM)
    assert "semester" in view.caption.text().lower()
    view.view_combo.setCurrentText(VIEW_COURSE_ALL)
    assert "lab" in view.caption.text()  # names what a course average includes


def test_an_empty_record_explains_itself_rather_than_charting_nothing(qapp, conn):
    view = ReportsView(conn)
    view.refresh()
    assert view.stats_table.rowCount() == 0
    assert "class record" in view.caption.text()


def test_cancelling_the_semester_prompt_leaves_nothing_charted(qapp, loaded, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_EXAM_ONE_SEMESTER)
    assert view.stats_table.rowCount() == 0


def test_the_bundle_exports_data_statistics_and_the_chart(qapp, loaded, monkeypatch, tmp_path):
    """A mean is a claim; the scores behind it are the evidence. So an export
    is three files, not one."""
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    out = tmp_path / "export"
    out.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out))
    )
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_COURSE_BY_SEMESTER)
    view._export_bundle()

    written = sorted(p.name for p in out.iterdir())
    assert written == [
        "Course averages - chart.png",
        "Course averages - data.csv",
        "Course averages - statistics.csv",
    ]

    raw = list(csv.reader((out / "Course averages - data.csv").open()))
    assert raw[0][:3] == ["Semester", "StudentID", "First Name"]
    assert len(raw) == 7  # header plus 3 students x 2 semesters

    stats = list(csv.reader((out / "Course averages - statistics.csv").open()))
    assert stats[0] == ["Chart", VIEW_COURSE_BY_SEMESTER]
    assert stats[-1][0] == "All groups"
    assert (out / "Course averages - chart.png").stat().st_size > 0


def test_one_exam_across_semesters_can_be_charted_and_exported(
    qapp, loaded, monkeypatch, tmp_path
):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Exam 2", True))
    )
    out = tmp_path / "export"
    out.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out))
    )
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_ONE_EXAM_ACROSS)
    view.export_bundle_button.click()

    assert view.exam_for_view == "Exam 2"
    # Two semesters of that one exam, so the trend is the thing charted.
    labels = [view.stats_table.item(r, 0).text() for r in range(view.stats_table.rowCount())]
    assert labels == ["202430 (Fall 2024)", "202610 (Spring 2026)", "All groups"]

    raw = list(csv.reader((out / "Exam 2 across semesters - data.csv").open()))
    assert raw[0][1] == "Exam"
    assert {row[1] for row in raw[1:]} == {"Exam 2"}  # only the chosen exam
    assert len(raw) == 7


def test_cancelling_the_exam_picker_leaves_nothing_to_export(
    qapp, loaded, monkeypatch, tmp_path
):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    out = tmp_path / "export"
    out.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out))
    )
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_ONE_EXAM_ACROSS)
    view.export_bundle_button.click()
    assert list(out.iterdir()) == []


def test_filenames_name_the_specific_choice_not_the_chart_kind(
    qapp, loaded, monkeypatch, tmp_path
):
    """Three exams exported into one folder have to be tellable apart, so the
    exam chosen goes in the filename rather than just the chart's name."""
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    out = tmp_path / "export"
    out.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out))
    )
    view = ReportsView(loaded)
    view.refresh()

    for exam in ("Exam 1", "Exam 2"):
        monkeypatch.setattr(
            QInputDialog, "getItem", staticmethod(lambda *a, exam=exam, **k: (exam, True))
        )
        view.view_combo.setCurrentText(VIEW_COURSE_ALL)  # leave and come back
        view.view_combo.setCurrentText(VIEW_ONE_EXAM_ACROSS)
        view.export_bundle_button.click()

    assert sorted(f.name for f in out.iterdir()) == [
        "Exam 1 across semesters - chart.png",
        "Exam 1 across semesters - data.csv",
        "Exam 1 across semesters - statistics.csv",
        "Exam 2 across semesters - chart.png",
        "Exam 2 across semesters - data.csv",
        "Exam 2 across semesters - statistics.csv",
    ]


def test_the_semester_export_still_delegates_to_the_history_view(qapp, loaded):
    calls = []
    view = ReportsView(loaded, on_export_semester=lambda: calls.append("semester"))
    view.export_semester_button.click()
    assert calls == ["semester"]


# --- the pass mark ----------------------------------------------------------

def test_the_pass_rate_counts_scores_at_or_above_seventy():
    """70 is a C- and passes; 69.9 is a D+ and doesn't."""
    stats = summarize([69.9, 70, 70.1, 95])
    assert stats["passing"] == 3
    assert stats["pass_rate"] == 75.0
    assert stats["pass_threshold"] == 70


def test_an_empty_group_has_no_pass_rate():
    stats = summarize([])
    assert stats["passing"] == 0 and stats["pass_rate"] is None


def test_the_table_reports_the_pass_rate_per_group(qapp, loaded):
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_COURSE_BY_SEMESTER)

    headers = [
        view.stats_table.horizontalHeaderItem(c).text()
        for c in range(view.stats_table.columnCount())
    ]
    assert headers[-1] == "Passing (≥70%)"
    last_column = view.stats_table.columnCount() - 1
    for row in range(view.stats_table.rowCount()):
        cell = view.stats_table.item(row, last_column).text()
        assert "%" in cell and "/" in cell  # e.g. "66.7%  (2/3)"


def test_the_pass_mark_is_drawn_on_a_distribution_as_a_vertical_line(qapp, loaded):
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_COURSE_ALL)

    axes = view.figure.axes[0]
    verticals = [
        line for line in axes.get_lines() if line.get_xdata()[0] == PASS_THRESHOLD_PERCENT
    ]
    assert verticals, "no pass-mark line on the histogram"
    assert verticals[0].get_color() == "#D62728"  # red


def test_the_pass_mark_is_horizontal_where_percentages_run_up_the_y_axis(qapp, loaded):
    """Comparison charts plot percentages on the y axis, so the same threshold
    has to be drawn the other way round."""
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_COURSE_BY_SEMESTER)

    axes = view.figure.axes[0]
    horizontals = [
        line for line in axes.get_lines() if line.get_ydata()[0] == PASS_THRESHOLD_PERCENT
    ]
    assert horizontals, "no pass-mark line on the comparison chart"


def test_the_exported_statistics_name_the_pass_mark(qapp, loaded, monkeypatch, tmp_path):
    out = tmp_path / "export"
    out.mkdir()
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out))
    )
    view = ReportsView(loaded)
    view.refresh()
    view.view_combo.setCurrentText(VIEW_COURSE_ALL)
    view._export_bundle()

    rows = list(csv.reader((out / "Course averages - statistics.csv").open()))
    assert ["Pass mark", "70%"] in rows
    assert rows[rows.index(STAT_COLUMNS) + 1][-1]  # the pass rate is filled in
