"""Letter-grade distribution for the exam currently in Results.

Shows the chart, the numbers behind it, and exports either -- the figures as
well as the picture, since a bar you can't read off is hard to quote.
"""

import csv
from collections import Counter

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.grading import PASS_THRESHOLD_PERCENT, letter_grade_bucket
from app.gui.errors import guarded
from app.gui import dialogs
from app.gui.widgets import with_hint

# F on the left, A on the right: the axis then runs low to high, the same
# direction as the percentage axis on the Reports histograms. Used for the bars,
# the table beneath them and the CSV export, so all three read the same way.
LETTER_ORDER = ["F", "D", "C", "B", "A"]
DATA_COLUMNS = ["Letter", "Students", "Share of graded"]


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class DashboardView(QWidget):
    """Letter-grade distribution for the current exam, plus pass/fail stats."""

    def __init__(self):
        super().__init__()
        self.exam_name = ""
        self.rows = []  # (letter, count, share) for the table and the export

        layout = QVBoxLayout(self)

        self.stats_label = QLabel("No exam loaded yet.")
        layout.addWidget(self.stats_label)

        self.figure = Figure(figsize=(6, 3.4), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)

        # The figures behind the bars, on screen rather than only in an export:
        # reading a count off a bar is guesswork.
        self.data_table = QTableWidget(0, len(DATA_COLUMNS))
        self.data_table.setHorizontalHeaderLabels(DATA_COLUMNS)
        self.data_table.verticalHeader().setVisible(False)
        self.data_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.data_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.data_table.setMaximumHeight(200)
        layout.addWidget(self.data_table)

        button_row = QHBoxLayout()
        self.export_chart_button = QPushButton("Export Chart Image…")
        self.export_chart_button.setToolTip("Save the chart above as a PNG image.")
        self.export_chart_button.clicked.connect(self._export_chart)
        button_row.addWidget(with_hint(self.export_chart_button))
        self.export_data_button = QPushButton("Export Chart Data…")
        self.export_data_button.setToolTip(
            "Save the counts behind the chart as a CSV."
        )
        self.export_data_button.clicked.connect(self._export_data)
        button_row.addWidget(with_hint(self.export_data_button))
        button_row.addStretch()
        layout.addLayout(button_row)

    def load_results(self, matched_students, possible_points, exam_name=""):
        self.exam_name = exam_name or self.exam_name
        percents = [m.grade.percent for m in matched_students if m.grade.percent is not None]
        missing = sum(1 for m in matched_students if m.grade.missing_flag)

        if percents:
            passed = sum(1 for p in percents if p >= PASS_THRESHOLD_PERCENT)
            failed = len(percents) - passed
            pass_rate = passed / len(percents) * 100
            text = f"Passed: {passed}   Failed: {failed}   Pass rate: {pass_rate:.1f}%"
        else:
            text = "No graded students yet."
        if missing:
            text += f"   Missing a score: {missing}"
        self.stats_label.setText(text)

        counts = Counter(letter_grade_bucket(p) for p in percents)
        total = len(percents)
        self.rows = [
            (
                letter,
                counts.get(letter, 0),
                counts.get(letter, 0) / total * 100 if total else None,
            )
            for letter in LETTER_ORDER
        ]

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        if percents:
            ax.bar(LETTER_ORDER, [row[1] for row in self.rows], edgecolor="white")
        ax.set_title(f"Grade distribution{f' — {self.exam_name}' if self.exam_name else ''}")
        ax.set_xlabel("Letter Grade")
        ax.set_ylabel("Number of Students")
        ax.grid(axis="y", alpha=0.3)
        self.canvas.draw()

        self.data_table.setRowCount(len(self.rows))
        for index, (letter, count, share) in enumerate(self.rows):
            self.data_table.setItem(index, 0, _readonly(letter))
            self.data_table.setItem(index, 1, _readonly(str(count)))
            self.data_table.setItem(
                index, 2, _readonly("" if share is None else f"{share:.1f}%")
            )

    # ------------------------------------------------------------------
    def _default_stem(self) -> str:
        name = self.exam_name or "exam"
        return f"{name} grade distribution".replace("/", "-")

    @guarded("Export failed")
    def _export_chart(self):
        if not any(count for _, count, _ in self.rows):
            dialogs.information(self, "Nothing to export", "No exam is loaded.")
            return

        from app.db.config import get_data_folder

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Chart Image",
            str(get_data_folder() / f"{self._default_stem()}.png"),
            "PNG image (*.png)",
        )
        if not path:
            return
        self.figure.savefig(path, dpi=150)
        dialogs.information(self, "Exported", f"Saved to {path}")

    @guarded("Export failed")
    def _export_data(self):
        if not any(count for _, count, _ in self.rows):
            dialogs.information(self, "Nothing to export", "No exam is loaded.")
            return

        from app.db.config import get_data_folder

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Chart Data",
            str(get_data_folder() / f"{self._default_stem()}.csv"),
            "CSV files (*.csv)",
        )
        if not path:
            return
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle)
            if self.exam_name:
                writer.writerow(["Exam", self.exam_name])
            writer.writerow([])
            writer.writerow(DATA_COLUMNS)
            for letter, count, share in self.rows:
                writer.writerow(
                    [letter, count, "" if share is None else round(share, 1)]
                )
            writer.writerow(["Total", sum(row[1] for row in self.rows), ""])
        dialogs.information(self, "Exported", f"Saved to {path}")
