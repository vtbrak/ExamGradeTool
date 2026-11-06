"""The Reports tab: exports, and charts of the class record.

Two jobs in one place. Exports produce the CSVs that used to live on the Class
History tab, leaving that tab to be the student list it is. Charts answer the
questions the numbers alone don't: how a semester compares with the ones before
it, and whether one exam runs harder than the others.

Everything here reads the **class record** -- the course sheets' figures -- not
the current semester's scanned work. Exam averages and course averages are
different measures and are always labelled as such: exams alone run higher than
the whole course, which includes the lab and quizzes.
"""

import csv
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from app.core.grading import PASS_THRESHOLD_PERCENT
from app.core.semesters import describe
from app.core.stats import histogram, summarize
from app.db import store
from app.gui.errors import guarded
from app.gui import dialogs
from app.gui.widgets import with_hint

STAT_COLUMNS = [
    "Group", "n", "Mean", "SD", "SE", "Median", "Lowest", "Highest",
    f"Passing (≥{PASS_THRESHOLD_PERCENT:g}%)",
]

# Each view names the measure it uses, because exam averages and course
# averages are not interchangeable.
VIEW_COURSE_ALL = "Course averages — all semesters pooled"
VIEW_COURSE_BY_SEMESTER = "Course averages — compare semesters"
VIEW_EXAM_ALL = "Exam averages — whole course, all semesters pooled"
VIEW_EXAM_BY_SEMESTER = "Exam averages — compare semesters"
VIEW_EXAM_BY_EXAM = "Exam scores — compare exams (all semesters)"
VIEW_EXAM_ONE_SEMESTER = "Exam scores — compare exams within one semester…"
VIEW_ONE_EXAM_ACROSS = "Exam scores — one exam across semesters…"

VIEWS = [
    VIEW_COURSE_ALL,
    VIEW_COURSE_BY_SEMESTER,
    VIEW_EXAM_ALL,
    VIEW_EXAM_BY_SEMESTER,
    VIEW_EXAM_BY_EXAM,
    VIEW_ONE_EXAM_ACROSS,
    VIEW_EXAM_ONE_SEMESTER,
]


def _slug(text: str) -> str:
    """A filename-safe stem from a chart's name."""
    text = text.split("—")[0].strip() if "—" in text else text.strip()
    return re.sub(r"[^\w .-]", "", text).strip(" .") or "report"


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _cell(value, suffix="%") -> str:
    return "" if value is None else f"{value:.2f}{suffix}"


class ReportsView(QWidget):
    def __init__(self, db_conn, on_export_semester=None, on_export_exam=None):
        super().__init__()
        self.db_conn = db_conn
        self.semester_for_view = None
        self.exam_for_view = None

        layout = QVBoxLayout(self)

        # --- exports ------------------------------------------------------
        exports = QGroupBox("Export")
        export_row = QHBoxLayout(exports)
        self.export_semester_button = QPushButton("Export Semester CSV…")
        self.export_semester_button.setToolTip(
            "One semester's roster, as a single CSV."
        )
        if on_export_semester:
            self.export_semester_button.clicked.connect(on_export_semester)
        export_row.addWidget(with_hint(self.export_semester_button))

        # One export, not two. A separate "export one exam" button was the same
        # code with the chart pre-selected, which only bought a click and cost a
        # second near-identical label.
        self.export_bundle_button = QPushButton("Export This Chart: Data + Stats + Chart…")
        self.export_bundle_button.setToolTip(
            "Save whatever is charted below: the raw scores, the statistics worked out "
            "from them, and the chart as an image. A mean on its own is a claim without "
            "its evidence, so all three go together."
        )
        self.export_bundle_button.clicked.connect(self._export_bundle)
        export_row.addWidget(with_hint(self.export_bundle_button))
        export_row.addStretch()
        layout.addWidget(exports)

        # --- chart --------------------------------------------------------
        chooser = QHBoxLayout()
        chooser.addWidget(QLabel("Chart:"))
        self.view_combo = QComboBox()
        self.view_combo.addItems(VIEWS)
        self.view_combo.currentTextChanged.connect(self._on_view_changed)
        chooser.addWidget(self.view_combo, 1)
        layout.addLayout(chooser)

        self.figure = Figure(figsize=(7, 3.6), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)

        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)

        self.stats_table = QTableWidget(0, len(STAT_COLUMNS))
        self.stats_table.setHorizontalHeaderLabels(STAT_COLUMNS)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stats_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.stats_table.setMaximumHeight(220)
        layout.addWidget(self.stats_table)

    # ------------------------------------------------------------------
    def refresh(self):
        self._render()

    def _on_view_changed(self, view):
        # The two views that need a choice ask for it when picked.
        if view == VIEW_EXAM_ONE_SEMESTER:
            codes = store.list_semester_codes(self.db_conn)
            if not codes:
                self.semester_for_view = None
            else:
                labels = [store.describe_semester(self.db_conn, c) for c in codes]
                label, chosen = QInputDialog.getItem(
                    self, "Which semester?", "Semester:", labels, 0, False
                )
                self.semester_for_view = codes[labels.index(label)] if chosen else None
        elif view == VIEW_ONE_EXAM_ACROSS:
            self.exam_for_view = self._ask_which_exam()
        self._render()

    def _ask_which_exam(self):
        names = store.list_exam_names(self.db_conn)
        if not names:
            return None
        name, chosen = QInputDialog.getItem(self, "Which exam?", "Exam:", names, 0, False)
        return name if chosen else None

    def _raw_rows(self) -> tuple:
        """`(headers, rows)` of the figures behind the chart, for export.

        The raw data as well as the summary, because a mean is a claim and the
        scores behind it are the evidence."""
        view = self.view_combo.currentText()
        # One lookup table for the whole export, not one query per row.
        labels = store.describe_semesters(self.db_conn)

        def semester(code):
            return labels.get(code) or store.describe_semester(self.db_conn, code)

        if view in (VIEW_COURSE_ALL, VIEW_COURSE_BY_SEMESTER):
            headers = [
                "Semester", "StudentID", "First Name", "Last Name",
                "Course Numerator", "Course Denominator", "Course %",
            ]
            rows = [
                [
                    semester(r["semester_code"]), r["student_id"], r["first_name"] or "",
                    r["last_name"] or "", r["adjusted_numerator"],
                    r["adjusted_denominator"],
                    "" if r["final_percent"] is None else round(r["final_percent"], 2),
                ]
                for r in store.get_all_course_grades(self.db_conn)
            ]
            return headers, rows

        if view in (VIEW_EXAM_ALL, VIEW_EXAM_BY_SEMESTER):
            headers = ["Semester", "StudentID", "First Name", "Last Name",
                       "Exams Counted", "Exam Average %"]
            rows = [
                [
                    semester(r["semester_code"]), r["student_id"], r["first_name"] or "",
                    r["last_name"] or "", r.get("exams_counted", ""),
                    round(r["overall_percent"], 2),
                ]
                for r in store.get_all_student_semester_scores(self.db_conn)
            ]
            return headers, rows

        # the per-exam views: one row per student per exam sitting
        headers = ["Semester", "Exam", "StudentID", "First Name", "Last Name",
                   "Score", "Possible Points", "Percent"]
        names = (
            [self.exam_for_view]
            if view == VIEW_ONE_EXAM_ACROSS and self.exam_for_view
            else store.list_exam_names(self.db_conn)
        )
        rows = []
        for name in names:
            for r in store.get_exam_history(self.db_conn, name):
                if view == VIEW_EXAM_ONE_SEMESTER and r["semester_code"] != self.semester_for_view:
                    continue
                rows.append(
                    [
                        semester(r["semester_code"]), r["exam_name"], r["student_id"],
                        r["first_name"] or "", r["last_name"] or "", r["final_grade"],
                        r["possible_points"],
                        "" if r["percent"] is None else round(r["percent"], 2),
                    ]
                )
        return headers, rows

    def _dataset(self) -> tuple:
        """`(groups, measure, note)` for the chosen view.

        `groups` is an ordered mapping of label -> percentages. One group means
        a distribution worth a histogram; several mean a comparison, drawn as
        means with error bars."""
        view = self.view_combo.currentText()
        if view == VIEW_COURSE_ALL:
            return (
                {"All semesters": store.get_course_grade_pool(self.db_conn)},
                "course average",
                "The whole course: lab, quizzes and exams, as the course sheet stated it.",
            )
        if view == VIEW_COURSE_BY_SEMESTER:
            return (
                {
                    describe(code): values
                    for code, values in store.course_averages_by_semester(self.db_conn).items()
                },
                "course average",
                "Each bar is a semester's mean course grade; the line is one standard error.",
            )
        if view == VIEW_EXAM_ALL:
            return (
                {"All semesters": store.get_exam_average_pool(self.db_conn)},
                "exam average",
                "Exams only — these run higher than course averages, which include "
                "the lab and quizzes.",
            )
        if view == VIEW_EXAM_BY_SEMESTER:
            return (
                {
                    describe(code): values
                    for code, values in store.exam_averages_by_semester(self.db_conn).items()
                },
                "exam average",
                "Each bar is a semester's mean exam average; the line is one standard error.",
            )
        if view == VIEW_EXAM_BY_EXAM:
            return (
                store.exam_percents_by_exam(self.db_conn),
                "exam score",
                "Every semester pooled, so a consistently harder exam shows up as a "
                "lower bar.",
            )
        if view == VIEW_ONE_EXAM_ACROSS:
            if not self.exam_for_view:
                return ({}, "exam score", "No exam chosen.")
            grouped = store.exam_percents_by_semester_for_exam(
                self.db_conn, self.exam_for_view
            )
            return (
                {describe(code): values for code, values in grouped.items()},
                "exam score",
                f"{self.exam_for_view} term by term — whether this one exam has got "
                "harder or easier over time.",
            )
        if self.semester_for_view:
            return (
                store.exam_percents_for_semester(self.db_conn, self.semester_for_view),
                "exam score",
                f"Exams within {describe(self.semester_for_view)}.",
            )
        return ({}, "exam score", "No semester chosen.")

    def _render(self):
        groups, measure, note = self._dataset()
        groups = {label: [v for v in values if v is not None] for label, values in groups.items()}
        groups = {label: values for label, values in groups.items() if values}
        self.figure.clear()
        axes = self.figure.add_subplot(111)

        if not groups:
            axes.text(0.5, 0.5, "No class-record data yet.", ha="center", va="center")
            axes.set_axis_off()
            self.caption.setText(
                "Charts read the class record, which is built by importing a course "
                "sheet. A semester that has only scanned exams isn't here yet."
            )
            self.stats_table.setRowCount(0)
            # Clear the figures too, not just the table: left behind, an export
            # would happily write the *previous* chart's numbers under this
            # chart's name.
            self.current_stats = []
            self.canvas.draw_idle()
            return

        if len(groups) == 1:
            label, values = next(iter(groups.items()))
            bins = histogram(values)
            centres = [(low + high) / 2 for low, high, _ in bins]
            counts = [count for _, _, count in bins]
            axes.bar(centres, counts, width=4.4, color="#4C78A8", edgecolor="white")
            axes.set_xlabel(f"{measure.capitalize()} (%)")
            axes.set_ylabel("Students")
            axes.set_title(f"{label} — distribution of {measure}s")
            axes.set_xlim(0, 100)
            # The pass mark, on the axis the scores are plotted against: here
            # that's the x axis, so the line is vertical.
            axes.axvline(
                PASS_THRESHOLD_PERCENT, color="#D62728", linewidth=1.6,
                label=f"Pass mark {PASS_THRESHOLD_PERCENT:g}%",
            )
            axes.legend(fontsize=8, loc="upper left")
        else:
            labels = list(groups)
            means = [summarize(groups[label])["mean"] for label in labels]
            errors = [summarize(groups[label])["se"] or 0 for label in labels]
            axes.bar(
                range(len(labels)), means, yerr=errors, capsize=4,
                color="#4C78A8", edgecolor="white",
            )
            axes.set_xticks(range(len(labels)))
            axes.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
            axes.set_ylabel(f"Mean {measure} (%)")
            axes.set_title(f"Mean {measure} by group (error bars: 1 SE)")
            axes.set_ylim(0, 100)
            # Same pass mark, but percentages run up the y axis here, so the
            # line is horizontal. A mean above it doesn't mean everyone passed
            # -- the Passing column in the table is the figure to read.
            axes.axhline(
                PASS_THRESHOLD_PERCENT, color="#D62728", linewidth=1.6,
                label=f"Pass mark {PASS_THRESHOLD_PERCENT:g}%",
            )
            axes.legend(fontsize=8, loc="lower right")
        axes.grid(axis="y", alpha=0.3)
        self.canvas.draw_idle()

        self.caption.setText(note)
        self._render_stats(groups)

    def _render_stats(self, groups):
        self.current_stats = []
        for label, values in groups.items():
            stats = summarize(values)
            self.current_stats.append((label, stats))
        # A pooled row when there's more than one group, so the overall figure
        # is there without doing the arithmetic by hand.
        if len(groups) > 1:
            pooled = [v for values in groups.values() for v in values]
            self.current_stats.append(("All groups", summarize(pooled)))

        self.stats_table.setRowCount(len(self.current_stats))
        for row, (label, stats) in enumerate(self.current_stats):
            cells = [
                label,
                str(stats["n"]),
                _cell(stats["mean"]),
                _cell(stats["sd"]),
                _cell(stats["se"]),
                _cell(stats["median"]),
                _cell(stats["min"]),
                _cell(stats["max"]),
                ""
                if stats["pass_rate"] is None
                else f"{stats['pass_rate']:.1f}%  ({stats['passing']}/{stats['n']})",
            ]
            for col, value in enumerate(cells):
                self.stats_table.setItem(row, col, _readonly(value))

    def _export_name(self) -> str:
        """What to call the files: the specific choice, not just the chart kind.

        Three exams exported into one folder have to be tellable apart, so the
        exam or semester chosen goes in the stem."""
        view = self.view_combo.currentText()
        if view == VIEW_ONE_EXAM_ACROSS and self.exam_for_view:
            return f"{self.exam_for_view} across semesters"
        if view == VIEW_EXAM_ONE_SEMESTER and self.semester_for_view:
            return f"{describe(self.semester_for_view)} exams"
        return view

    @guarded("Export failed")
    def _export_bundle(self, default_name=None):
        """Raw scores, the statistics, and the chart image, into one folder."""
        if not getattr(self, "current_stats", None):
            dialogs.information(
                self, "Nothing to export", "No data is charted, so there's nothing to save."
            )
            return

        from app.db.config import get_data_folder

        folder = QFileDialog.getExistingDirectory(
            self, "Choose a folder for the export", str(get_data_folder())
        )
        if not folder:
            return

        stem = _slug(default_name or self._export_name())
        folder = Path(folder)
        raw_path = folder / f"{stem} - data.csv"
        stats_path = folder / f"{stem} - statistics.csv"
        chart_path = folder / f"{stem} - chart.png"

        headers, rows = self._raw_rows()
        with open(raw_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)

        with open(stats_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Chart", self.view_combo.currentText()])
            if self.exam_for_view and self.view_combo.currentText() == VIEW_ONE_EXAM_ACROSS:
                writer.writerow(["Exam", self.exam_for_view])
            writer.writerow(["Measure", "percent"])
            writer.writerow(["Pass mark", f"{PASS_THRESHOLD_PERCENT:g}%"])
            writer.writerow([])
            writer.writerow(STAT_COLUMNS)
            for label, stats in self.current_stats:
                writer.writerow(
                    [
                        label, stats["n"],
                        *(
                            "" if stats[key] is None else round(stats[key], 2)
                            for key in ("mean", "sd", "se", "median", "min", "max")
                        ),
                        "" if stats["pass_rate"] is None else round(stats["pass_rate"], 1),
                    ]
                )

        self.figure.savefig(chart_path, dpi=150)

        dialogs.information(
            self,
            "Exported",
            f"Saved three files to {folder}:\n\n"
            f"  {raw_path.name} — {len(rows)} row(s) of scores\n"
            f"  {stats_path.name} — means, SD, SE per group\n"
            f"  {chart_path.name} — the chart as shown",
        )
