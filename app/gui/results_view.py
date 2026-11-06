import csv
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
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

from app.core.grading import compute_grade
from app.db import store
from app.gui.errors import guarded
from app.gui import dialogs
from app.gui.widgets import add_group_separator, with_hint

# A second, dedicated data role for a column's *numeric* sort key, kept
# separate from the displayed text -- e.g. "Group Score (Used)" displays
# "44.0 *" when overridden, but must still sort as the number 44.0.
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
STUDENT_ID_ROLE = Qt.ItemDataRole.UserRole + 2

COLUMNS = [
    "Last Name",
    "First Name",
    "Student ID",
    "Individual Score",
    "Individual Key Used",
    "Group Score (Original)",
    "Group Score (Used)",
    "Final Grade",
    "Final %",
    "Missing Score Flag",
]
INDIVIDUAL_SCORE_COL = 3
KEY_USED_COL = 4
GROUP_ORIGINAL_COL = 5
GROUP_USED_COL = 6
FINAL_GRADE_COL = 7
FINAL_PERCENT_COL = 8
MISSING_FLAG_COL = 9
EDITABLE_COLUMNS = {INDIVIDUAL_SCORE_COL, GROUP_ORIGINAL_COL}
# Columns whose values are always short, fixed-format numbers/codes -- size
# them to their content instead of stretching, so Missing Score Flag (the
# one column with real variable-length text) gets the freed-up space.
NARROW_COLUMNS = {2, 3, 4, 5, 6, 7, 8}  # Student ID .. Final %


def _out_of(possible_points) -> str:
    """" (out of 50 points)" -- states the denominator the raw scores are
    against, since it has varied by semester."""
    if not possible_points:
        return ""
    return f" (out of {possible_points:g} points)"


class SortableTableWidgetItem(QTableWidgetItem):
    """Sorts by the numeric value stashed in SORT_ROLE when both sides have
    one, otherwise falls back to normal text comparison (so name/flag/key
    columns, which never set SORT_ROLE, sort alphabetically as usual)."""

    def __lt__(self, other):
        self_key = self.data(SORT_ROLE)
        other_key = other.data(SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if self_key is not None and other_key is not None:
            try:
                return float(self_key) < float(other_key)
            except (TypeError, ValueError):
                pass
        # Compare text directly rather than via super().__lt__(): PySide6 can
        # recurse back into this same override when delegating to the base
        # implementation of a Python-subclassed comparison method.
        other_text = other.text() if isinstance(other, QTableWidgetItem) else str(other)
        return self.text() < other_text


class ResultsView(QWidget):
    """Editable results table for the currently-loaded exam, plus Save/Export."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.matched_students = []
        self.exam_name = None
        self.possible_points = None
        self.duplicates = {"individual": {}, "group": {}}
        self.id_problems = []
        self._students_by_id = {}
        self._suspend_item_signal = False

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("No exam loaded yet. Drop a file above and click Run.")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        header = self.table.horizontalHeader()
        # Qt's header defaults its sort indicator to (column 0, Descending)
        # even when sorting has never been explicitly requested -- without
        # setting this explicitly, the very first setSortingEnabled(True)
        # call below would silently resort every render into that default
        # instead of leaving rows in a predictable order. Last Name ascending
        # is a sensible default for a gradebook.
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)
        for col in range(len(COLUMNS)):
            mode = (
                QHeaderView.ResizeMode.ResizeToContents
                if col in NARROW_COLUMNS
                else QHeaderView.ResizeMode.Stretch
            )
            header.setSectionResizeMode(col, mode)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        legend = QLabel(
            "Click a column header to sort. \"Group Score (Used)\" is what actually counts "
            "toward Final Grade — it only differs from \"Group Score (Original)\" (marked with "
            "*) when the Individual score was higher and the score-protection rule substituted "
            "it in."
        )
        legend.setWordWrap(True)
        layout.addWidget(legend)

        self.duplicates_label = QLabel("")
        self.duplicates_label.setWordWrap(True)
        layout.addWidget(self.duplicates_label)

        # Two groups on one line, divided by a rule. The first acts on the exam
        # just imported -- the one Results and Dashboard are showing. The second
        # reaches for an exam already on record, which is a different job and
        # was easy to hit by mistake while working through a scan.
        button_row = QHBoxLayout()

        self.recalculate_button = QPushButton("Recalculate")
        self.recalculate_button.clicked.connect(self._recalculate)
        button_row.addWidget(self.recalculate_button)
        # Not "Save to History": after the split, history means the class
        # record, which comes from a course sheet. This saves the scanned scores
        # for this semester's exam -- working data, kept in Exam Grading.
        self.save_button = QPushButton("Save Exam Scores")
        # This used to be a dialog shown after every save. The wording never
        # changed from one save to the next, so it belongs on the button rather
        # than in a box to dismiss.
        self.save_button.setToolTip(
            "Save the scanned scores for this exam. They stay in Exam Grading — "
            "you can reopen this exam from here later. The class record is built "
            "separately, by importing a course sheet."
        )
        self.save_button.clicked.connect(self._save_to_history)
        button_row.addWidget(with_hint(self.save_button))
        # Named for what it's for: the CSV whose contiguous Individual / Group /
        # Final block is pasted into the D2L gradebook.
        self.export_button = QPushButton("Export Exam CSV for D2L…")
        self.export_button.clicked.connect(self._export_csv)
        button_row.addWidget(self.export_button)

        self.button_separator = add_group_separator(button_row)

        self.open_saved_button = QPushButton("Open Saved Exam…")
        self.open_saved_button.setToolTip(
            "Load an exam already saved in this app, to edit or export it. Exams "
            "imported from a course sheet are the class record and aren't listed."
        )
        self.open_saved_button.clicked.connect(
            lambda: self.main_window._open_saved_exam()
        )
        button_row.addWidget(with_hint(self.open_saved_button))
        button_row.addStretch()
        layout.addLayout(button_row)

        # Confirms a save without a dialog in the way. Cleared whenever new
        # results load, so it never vouches for the exam now on screen.
        self.save_status_label = QLabel("")
        layout.addWidget(self.save_status_label)

    def set_header_widget(self, widget):
        """Put the exam-grading controls at the top of this tab.

        They used to sit above the tab bar, which pushed the tabs down the
        window on some screens and left them at the top on others. Inside the
        tab, the tab bar is always the first thing on every screen."""
        self.layout().insertWidget(0, widget)

    def load_results(
        self, exam_name, matched_students, duplicates, possible_points, id_problems=()
    ):
        self.exam_name = exam_name
        self.matched_students = matched_students
        self.duplicates = duplicates
        self.id_problems = list(id_problems)
        self.possible_points = possible_points
        self._students_by_id = {m.student_id: m for m in matched_students}

        missing_count = sum(1 for m in matched_students if m.grade.missing_flag)
        self.summary_label.setText(
            f"{exam_name}{_out_of(possible_points)} — {len(matched_students)} "
            f"students matched, {missing_count} missing a score."
        )
        self.save_status_label.setText("")
        self._render_table()
        self._render_duplicates()

    def _render_table(self):
        self._suspend_item_signal = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.matched_students))
        for row_idx, m in enumerate(self.matched_students):
            g = m.grade

            group_used_display = ""
            if g.effective_group_score is not None:
                group_used_display = str(g.effective_group_score)
                if g.override_original_group_score is not None:
                    group_used_display += " *"

            # (display text, numeric sort key or None)
            cells = [
                (m.last_name or "", None),
                (m.first_name or "", None),
                (m.student_id, None),
                ("" if m.individual_score is None else str(m.individual_score), m.individual_score),
                (m.individual_key_used or "", None),
                ("" if m.raw_group_score is None else str(m.raw_group_score), m.raw_group_score),
                (group_used_display, g.effective_group_score),
                ("" if g.final_grade is None else str(round(g.final_grade, 2)), g.final_grade),
                # Reported alongside the raw score, and the one to read across
                # semesters: the same exam has been out of 50, 72, and 80
                # points in different terms, so raw scores aren't comparable.
                ("" if g.percent is None else f"{g.percent:.1f}%", g.percent),
                (g.missing_flag or "", None),
            ]
            for col_idx, (text, sort_key) in enumerate(cells):
                item = SortableTableWidgetItem(text)
                item.setData(STUDENT_ID_ROLE, m.student_id)
                if sort_key is not None:
                    item.setData(SORT_ROLE, sort_key)
                if col_idx not in EDITABLE_COLUMNS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_idx, col_idx, item)
        self.table.setSortingEnabled(True)
        self._suspend_item_signal = False

    def _render_duplicates(self):
        lines = []
        for round_name, dup_map in self.duplicates.items():
            for student_id, rows in dup_map.items():
                forms = ", ".join(f"Form {r.key_version or '?'}: {r.score}" for r in rows)
                lines.append(
                    f"[{round_name}] Student {student_id}: {len(rows)} forms found — {forms}. "
                    "Held out of this save until resolved (re-upload a corrected file)."
                )
        text = (
            "Duplicate Forms:\n" + "\n".join(lines) if lines else "No duplicate forms detected."
        )

        # IDs that don't look like student IDs are reported with their row
        # number, never corrected -- the file is fixed at the source.
        if self.id_problems:
            problems = "\n".join(
                f"Row {p.row}: ID {p.student_id} — {p.problem}." for p in self.id_problems
            )
            text += (
                f"\n\nStudent ID problems ({len(self.id_problems)}) — student IDs are "
                "7 digits. Held out of this save; correct these rows in the file and "
                f"re-upload:\n{problems}"
            )
        self.duplicates_label.setText(text)

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._suspend_item_signal:
            return
        col = item.column()
        if col not in EDITABLE_COLUMNS:
            return

        student_id = item.data(STUDENT_ID_ROLE)
        m = self._students_by_id.get(student_id)
        if m is None:
            return

        text = item.text().strip()
        try:
            new_value = float(text) if text else None
        except ValueError:
            # Put the old value back in this one cell, then warn. Rebuilding the
            # whole table here would delete the item whose itemChanged signal is
            # still being delivered, and opening a modal dialog inside the
            # signal runs an event loop while the cell editor is still closing;
            # both are classic sources of sporadic QTableWidget crashes.
            old = m.individual_score if col == INDIVIDUAL_SCORE_COL else m.raw_group_score
            self._suspend_item_signal = True
            item.setText("" if old is None else str(old))
            self._suspend_item_signal = False
            QTimer.singleShot(
                0, lambda: dialogs.warning(self, "Invalid score", f"'{text}' isn't a number.")
            )
            return

        if col == INDIVIDUAL_SCORE_COL:
            m.individual_score = new_value
        else:
            m.raw_group_score = new_value

        # Give immediate visual confirmation the edit registered -- typing a
        # whole number like "40" is normalized to "40.0" right away, rather
        # than silently sitting there unformatted until Recalculate is
        # clicked. Final Grade / Missing Score Flag / Group Score (Used)
        # otherwise stay stale until Recalculate, so editing several cells in
        # a row doesn't re-render the whole table after every single one.
        self._suspend_item_signal = True
        display_text = "" if new_value is None else str(new_value)
        item.setText(display_text)
        # A cleared cell drops its numeric sort key too; otherwise it kept
        # sorting as its old score.
        item.setData(SORT_ROLE, new_value)
        self._suspend_item_signal = False

    def _recompute_all_grades(self):
        for m in self.matched_students:
            m.grade = compute_grade(m.individual_score, m.raw_group_score, self.possible_points)

    def _recalculate(self):
        if not self.matched_students:
            dialogs.information(self, "Nothing to recalculate", "Run an exam file first.")
            return
        self._recompute_all_grades()
        missing_count = sum(1 for m in self.matched_students if m.grade.missing_flag)
        self.summary_label.setText(
            f"{self.exam_name}{_out_of(self.possible_points)} — "
            f"{len(self.matched_students)} students matched, "
            f"{missing_count} missing a score."
        )
        self._render_table()
        self.main_window.dashboard_view.load_results(
            self.matched_students, self.possible_points, self.exam_name
        )

    @guarded("Save failed")
    def _save_to_history(self):
        if not self.matched_students or not self.main_window.has_current_exam():
            dialogs.information(self, "Nothing to save", "Run an exam file first.")
            return

        exam_id = self.main_window.ensure_current_exam()
        conn = self.main_window.db_conn

        # Guard against saving stale figures if the instructor edited a score
        # but didn't click Recalculate first.
        self._recompute_all_grades()

        warnings = []
        for round_name, score_attr in (
            ("individual", "individual_score"),
            ("group", "raw_group_score"),
        ):
            timestamp = store.round_already_saved(conn, exam_id, round_name)
            has_fresh_data = any(
                getattr(m, score_attr) is not None for m in self.matched_students
            )
            if timestamp and has_fresh_data:
                warnings.append(
                    f"{round_name.capitalize()} round was already saved on {timestamp} "
                    "— this will overwrite it."
                )

        # Saving over the class record is a different matter from overwriting a
        # round, so it's listed first and spelled out.
        record_warnings = store.scan_save_warnings(conn, exam_id)
        warnings = record_warnings + warnings

        if warnings:
            reply = dialogs.question(
                self,
                "Overwrite existing data?",
                "\n\n".join(warnings) + "\n\nContinue and overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
                if record_warnings
                else QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        store.save_matched_students(conn, exam_id, self.matched_students)

        count = len(self.matched_students)
        self.save_status_label.setText(
            f"Exam scores saved — {count} student{'' if count == 1 else 's'} "
            f"at {datetime.now().strftime('%I:%M %p').lstrip('0')}."
        )

        # A point mismatch is about this save in particular and needs a
        # decision, so it still interrupts; the routine confirmation doesn't.
        mismatches = store.exam_point_mismatches(conn)
        if mismatches:
            detail = "\n".join(
                f"  {name}: recorded as out of {recorded:g}, this file says {incoming:g}"
                for name, recorded, incoming in mismatches
            )
            store.clear_exam_point_mismatches(conn)
            dialogs.warning(
                self,
                "Point value mismatch",
                "The exam keeps its recorded value, so check which is right:\n"
                + detail,
            )
        self.main_window.history_view.refresh()
        self.main_window.dashboard_view.load_results(
            self.matched_students, self.possible_points, self.exam_name
        )

    @guarded("Export failed")
    def _export_csv(self):
        if not self.matched_students:
            dialogs.information(self, "Nothing to export", "Run an exam file first.")
            return

        # Guard against exporting stale figures if the instructor edited a
        # score but didn't click Recalculate first.
        self._recompute_all_grades()

        from app.db.config import get_data_folder

        default_name = f"{(self.exam_name or 'exam').replace(' ', '_')}_scores.csv"
        default_path = str(get_data_folder() / default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Exam CSV", default_path, "CSV files (*.csv)"
        )
        if not path:
            return

        # Individual / Group (Used) / Final are grouped first, contiguous, so
        # that block alone can be selected and pasted straight into the CMS.
        # A blank spacer column separates it from the reference-only columns
        # (original group score, whether it was replaced, and the form
        # letter) that shouldn't get swept into that paste. Final % sits just
        # past the spacer rather than beside Final Score for that reason --
        # the CMS gradebook column takes raw points, not a percentage.
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
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
            )
            for m in self.matched_students:
                g = m.grade
                writer.writerow(
                    [
                        m.first_name or "",
                        m.last_name or "",
                        m.student_id,
                        m.individual_score if m.individual_score is not None else "",
                        g.effective_group_score if g.effective_group_score is not None else "",
                        round(g.final_grade, 2) if g.final_grade is not None else "",
                        "",
                        round(g.percent, 1) if g.percent is not None else "",
                        m.raw_group_score if m.raw_group_score is not None else "",
                        "Yes" if g.override_original_group_score is not None else "",
                        m.individual_key_used or "",
                    ]
                )
        dialogs.information(self, "Exported", f"Saved to {path}")
