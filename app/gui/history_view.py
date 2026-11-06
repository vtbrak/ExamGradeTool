import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
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

from app.core.grading import PASS_THRESHOLD_PERCENT
from app.core.semesters import chronological_sort_key, parse_semester_code
from app.db import store
from app.gui.errors import guarded
from app.gui import dialogs
from app.gui.widgets import with_hint

# "Overall Course %" is an average of each exam's Final Grade as a percentage,
# not a raw-point total: exams have been out of 50, 72, and 80 points in
# different semesters, so only the percentage is comparable across them.
COLUMNS = ["Last Name", "First Name", "Student ID", "Overall Course %", "Exams Counted", "Pass/Fail"]


class HistoryView(QWidget):
    """Per-semester roster with percentile cutoffs, pass-rate, and a semester export."""

    def __init__(self, db_conn, on_historical_input=None, on_data_changed=None):
        super().__init__()
        self.db_conn = db_conn
        # Called after a deletion so the views built on the class record --
        # Reports, Rec Letter Info -- and any exam loaded from the deleted
        # semester don't go on showing data that no longer exists.
        self._on_data_changed = on_data_changed
        self.offerings: list[tuple[int, str]] = []
        self.current_offering_id = None
        self.current_summary: list[dict] = []

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Semester:"))
        self.semester_combo = QComboBox()
        self.semester_combo.currentIndexChanged.connect(self._on_semester_changed)
        top_row.addWidget(self.semester_combo)
        top_row.addStretch()
        layout.addLayout(top_row)

        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        # This tab is for looking at and maintaining student data. Exporting and
        # charting moved to the Reports tab, which is what those are for; the
        # methods stay here because they work on the selected semester, and
        # Reports calls them.
        button_row = QHBoxLayout()
        # Importing a course sheet is a class-record action, so its button sits
        # here rather than beside the exam-grading controls.
        if on_historical_input is not None:
            self.import_sheet_button = QPushButton("Import Course Sheet…")
            self.import_sheet_button.setToolTip(
                "Import a semester's course grades and exam scores from a D2L "
                "course-grade export. This is what the class record is built from."
            )
            self.import_sheet_button.clicked.connect(on_historical_input)
            button_row.addWidget(with_hint(self.import_sheet_button))
        # Maintenance, and the only destructive action in the app: a sheet
        # filed under the wrong semester can't be unpicked by re-importing,
        # because an import adds and overwrites but never removes.
        self.delete_semester_button = QPushButton("Delete Semester…")
        self.delete_semester_button.setToolTip(
            "Remove the selected semester completely — its course grades, exams "
            "and scores — so it can be imported again from scratch. Backs the "
            "database up first. Students who also appear in another semester "
            "keep that record."
        )
        self.delete_semester_button.clicked.connect(self._delete_semester)
        button_row.addWidget(with_hint(self.delete_semester_button))

        self.backup_button = QPushButton("Back Up Now")
        self.backup_button.setToolTip(
            "Save a dated copy of the database next to grades.db itself, so a "
            "backup is never somewhere else to go looking for."
        )
        self.backup_button.clicked.connect(self._backup)
        button_row.addWidget(with_hint(self.backup_button))

        self.restore_button = QPushButton("Restore from Backup…")
        self.restore_button.setToolTip(
            "Replace the current data with a backup's. The database as it is "
            "now is backed up first, so a restore can itself be undone."
        )
        self.restore_button.clicked.connect(self._restore)
        button_row.addWidget(with_hint(self.restore_button))
        button_row.addStretch()
        layout.addLayout(button_row)

    def refresh(self):
        rows = self.db_conn.execute("SELECT id, semester_code FROM course_offerings").fetchall()
        # Newest first in calendar order. Text ordering would put an older
        # 4-digit code ("2201", Spring 2020) above "202610" (Spring 2026).
        self.offerings = sorted(
            ((r["id"], r["semester_code"]) for r in rows),
            key=lambda o: chronological_sort_key(o[1]),
            reverse=True,
        )

        labels = store.describe_semesters(self.db_conn)
        self.semester_combo.blockSignals(True)
        self.semester_combo.clear()
        for _, code in self.offerings:
            self.semester_combo.addItem(labels.get(code) or store.describe_semester(self.db_conn, code))
        self.semester_combo.blockSignals(False)

        if self.offerings:
            # Keep the semester the instructor was looking at. refresh() runs
            # after every save and import; bouncing to the newest semester each
            # time also made "Export Semester CSV" export a different semester
            # from the one on screen.
            ids = [offering_id for offering_id, _ in self.offerings]
            index = ids.index(self.current_offering_id) if self.current_offering_id in ids else 0
            self.semester_combo.setCurrentIndex(index)
            self._load_semester(self.offerings[index][0])
        else:
            self.current_summary = []
            self.stats_label.setText("No saved exam data yet.")
            self.table.setRowCount(0)

    def _on_semester_changed(self, index: int):
        if 0 <= index < len(self.offerings):
            self._load_semester(self.offerings[index][0])

    def _load_semester(self, offering_id: int):
        self.current_offering_id = offering_id
        self.current_summary = store.get_semester_summary(self.db_conn, offering_id)
        scores = [r["overall_percent"] for r in self.current_summary]

        if scores:
            cutoffs = store.percentile_cutoffs(scores)
            pass_stats = store.pass_rate(scores)
            pass_pct = pass_stats["passed"] / pass_stats["total"] * 100
            self.stats_label.setText(
                f"{len(scores)} students. Pass rate: {pass_stats['passed']}/{pass_stats['total']} "
                f"({pass_pct:.1f}%).  Top 10%: {cutoffs['top_10_pct']:.1f}%  "
                f"Top 5%: {cutoffs['top_5_pct']:.1f}%  Top 1%: {cutoffs['top_1_pct']:.1f}%"
                f"\n{self._provenance_note(offering_id)}"
            )
        else:
            self.stats_label.setText(
                "No saved exam data for this semester yet."
                f"\n{self._provenance_note(offering_id)}"
            )

        self.table.setRowCount(len(self.current_summary))
        for row_idx, r in enumerate(self.current_summary):
            passed = r["overall_percent"] >= PASS_THRESHOLD_PERCENT
            values = [
                r["last_name"] or "",
                r["first_name"] or "",
                r["student_id"],
                f"{r['overall_percent']:.1f}%",
                r["exams_counted"],
                "Pass" if passed else "Fail",
            ]
            for col_idx, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_idx, col_idx, item)

    def _provenance_note(self, offering_id: int) -> str:
        """Where this semester's figures come from.

        The two kinds of data are easy to confuse: the roster column is an
        average of exam scores, while the course grade is the whole course as
        stated by the end-of-semester sheet. Saying so here avoids renaming the
        column, which is pasted elsewhere."""
        course_grades = store.count_course_grades(self.db_conn, offering_id)
        closed = store.is_semester_closed(self.db_conn, offering_id)
        if course_grades:
            state = "complete" if closed else "still open"
            return (
                f"Course grades on record for {course_grades} student(s) — semester {state}. "
                "Roster figures below are exam averages; course grades come from the "
                "imported sheet and are what class rank uses."
            )
        return (
            "No course grades imported yet, so this semester is in progress and not in "
            "class rank. Roster figures below are exam averages, not course grades."
        )

    @guarded("Export failed")
    @guarded("Backup failed")
    def _backup(self):
        """Dated copy, in the folder grades.db lives in.

        No folder prompt: a backup is wanted beside the database, and being
        asked where to put it every time is how backups end up scattered."""
        live = store.default_db_path()
        dest = store.backup_database(live, live.parent)
        dialogs.information(
            self,
            "Backup complete",
            f"The database is backed up to:\n{dest}\n\n"
            "It sits beside grades.db, so Restore from Backup… will open on it.",
        )

    @guarded("Restore failed")
    def _restore(self):
        live = store.default_db_path()
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a backup to restore", str(live.parent), "Databases (*.db)"
        )
        if not path:
            return
        if Path(path).resolve() == Path(live).resolve():
            dialogs.warning(
                self,
                "That is the live database",
                "That file is grades.db itself, not a backup of it. Pick a "
                f"{store.BACKUP_SUFFIX.strip('_')} file.",
            )
            return

        # Refuses a file that isn't one of this app's databases, before
        # anything is overwritten.
        incoming = store.describe_database(path)
        current = store.describe_database(live)

        def summarise(info):
            semesters = ", ".join(info["semester_codes"]) or "none"
            return (
                f"  {info['semesters']} semester(s): {semesters}\n"
                f"  {info['students']} student(s), {info['course_grades']} course grade(s)\n"
                f"  dated {info['modified']:%d %B %Y, %I:%M %p}".replace(" 0", " ")
            )

        answer = dialogs.question(
            self,
            "Restore this backup?",
            f"Replace all current data with {Path(path).name}?\n\n"
            "The backup being restored holds:\n"
            + summarise(incoming)
            + "\n\nIt will replace what is in grades.db now:\n"
            + summarise(current)
            + "\n\nThe current data is backed up first, so this can be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # As with deleting a semester, the safety backup is the undo, so a
        # failed one cancels the restore rather than proceeding without it.
        safety = store.backup_database(live, live.parent)
        store.restore_database(self.db_conn, path)

        self.current_offering_id = None
        self.refresh()
        if self._on_data_changed is not None:
            self._on_data_changed()
        dialogs.information(
            self,
            "Restore complete",
            f"grades.db now holds the data from {Path(path).name}.\n\n"
            f"What it held before this restore is saved at:\n{safety}",
        )

    @guarded("Could not delete the semester")
    def _delete_semester(self):
        index = self.semester_combo.currentIndex()
        if not (0 <= index < len(self.offerings)):
            dialogs.information(
                self, "No semester selected", "There is no semester to delete."
            )
            return
        offering_id, semester_code = self.offerings[index]
        label = store.describe_semester(self.db_conn, semester_code)
        contents = store.semester_contents(self.db_conn, offering_id)

        detail = [
            f"  {contents['course_grades']} course grade(s)",
            f"  {contents['record_scores']} exam score(s) in the class record",
            f"  {contents['exams']} exam(s)",
        ]
        if contents["scanned_scores"]:
            detail.append(
                f"  {contents['scanned_scores']} scanned exam score(s) from Exam Grading"
            )
        if contents["source_files"]:
            detail.append("")
            detail.append("Imported from: " + ", ".join(contents["source_files"]))
        removed, kept = contents["students_removed"], contents["students_kept"]
        detail.append("")
        detail.append(f"  {len(removed)} student(s) will be removed entirely")
        if kept:
            # The repeating-student case. Deleting these would take data from a
            # semester the instructor isn't deleting.
            detail.append(
                f"  {len(kept)} student(s) will be kept, because they also "
                "appear in another semester"
            )

        answer = dialogs.question(
            self,
            "Delete this semester?",
            f"Delete everything in {label}?\n\n"
            "This cannot be undone from inside the app, so the database is "
            "backed up first.\n\nThis will delete:\n"
            + "\n".join(detail),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # The backup is the undo, so a failed backup cancels the deletion
        # rather than going ahead without one.
        from app.db.config import get_data_folder

        backup = store.backup_database(store.default_db_path(), get_data_folder())
        store.delete_semester(self.db_conn, offering_id)

        self.current_offering_id = None
        self.refresh()
        if self._on_data_changed is not None:
            self._on_data_changed()
        dialogs.information(
            self,
            "Semester deleted",
            f"{label} has been deleted. You can import its sheet again.\n\n"
            f"The database as it was is saved at:\n{backup}",
        )

    def _export_csv(self):
        if not self.current_summary:
            dialogs.information(self, "Nothing to export", "No semester data loaded.")
            return

        from app.db.config import get_data_folder

        semester_code = self.offerings[self.semester_combo.currentIndex()][1]
        default_name = f"{semester_code}_semester_scores.csv"
        default_path = str(get_data_folder() / default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Semester CSV", default_path, "CSV files (*.csv)"
        )
        if not path:
            return

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["First Name", "Last Name", "StudentID", "Overall Course %", "Pass/Fail"]
            )
            for r in self.current_summary:
                writer.writerow(
                    [
                        r["first_name"] or "",
                        r["last_name"] or "",
                        r["student_id"],
                        round(r["overall_percent"], 2),
                        "Pass" if r["overall_percent"] >= PASS_THRESHOLD_PERCENT else "Fail",
                    ]
                )
        dialogs.information(self, "Exported", f"Saved to {path}")

