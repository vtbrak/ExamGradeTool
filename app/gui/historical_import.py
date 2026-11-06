"""The "Historical Input" workflow: bulk-import a D2L course-grade sheet.

Covers two roadmap items that read the same file:

* **Course grades** -- the semester-level Adjusted Final Grade numerator/
  denominator pair, saved to the `course_grades` table.
* **Exam backfill** -- the per-exam columns (Exam 1-4), saved into the same
  `exam_scores` table the normal Scantron workflow populates, but tagged as an
  import so they stay distinguishable.

The semester is always asked for explicitly rather than inferred: unlike the
Scantron exports, these sheets carry no QuizClass column, and a file sitting in
a folder is no evidence of which term it came from. What's typed must be a real
semester code (see app/core/semesters.py), and if that semester is already saved
under the other code format the existing code is used, so one term never ends
up split across two course offerings.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.core.course_export import (
    BASIS_CALCULATED,
    FORMAT_SINGLE_SCORE,
    parse_course_export,
    parse_item_header,
)
from app.core.semesters import (
    EXAMPLE_HELP,
    describe,
    parse_semester_code,
    related_summer_codes,
)
from app.db import store
from app.gui.errors import guarded
from app.gui import dialogs
from app.gui.widgets import with_hint

ACCEPTED_FILTER = "Course grade export (*.xlsx *.csv)"
ACCEPTED_SUFFIXES = (".xlsx", ".csv")


class SheetDropZone(QFrame):
    """Drop target for one D2L course-grade export.

    Deliberately not the exam DropZone from main_window: that one takes
    Scantron files and lists .xls, which this path refuses outright (see
    course_export.load_course_export), and one sheet is one semester, so there
    is never a reason to drop two."""

    def __init__(self, on_file_chosen, on_browse):
        super().__init__()
        self._on_file_chosen = on_file_chosen
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(90)

        layout = QVBoxLayout(self)
        label = QLabel("Drag one course-grade export here — .xlsx or .csv")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(label)
        browse = QPushButton("Choose file…")
        browse.clicked.connect(on_browse)
        layout.addWidget(browse, alignment=Qt.AlignmentFlag.AlignCenter)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in urls if p.suffix.lower() in ACCEPTED_SUFFIXES]
        if not paths:
            dialogs.warning(
                self,
                "Not a course-grade export",
                "Drop the D2L course-grade export for one semester, as .xlsx or "
                ".csv.\n\nAn .xls file has to be opened in Excel and saved as "
                ".xlsx first.",
            )
            return
        if len(paths) > 1:
            dialogs.warning(
                self,
                "One sheet at a time",
                "Each sheet is one semester, imported under the semester code "
                "below. Drop them one at a time.",
            )
            return
        self._on_file_chosen(paths[0])


class HistoricalImportDialog(QDialog):
    def __init__(self, db_conn, parent=None):
        super().__init__(parent)
        self.db_conn = db_conn
        self.parsed = None
        self.resolved = []
        self.unresolved = []

        self.setWindowTitle("Historical Input")
        self.resize(720, 560)
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "Import past course grades and exam scores from a D2L course-grade "
                "export.\nThe exam columns are identified by their headers, so the "
                "sheet's column order doesn't matter."
            )
        )

        self.drop_zone = SheetDropZone(self._load_file, self._choose_file)
        layout.addWidget(self.drop_zone)
        self.file_label = QLabel("No file chosen.")
        layout.addWidget(self.file_label)

        form = QFormLayout()
        self.semester_combo = QComboBox()
        self.semester_combo.setEditable(True)
        self.semester_combo.setToolTip(
            "Pick an existing semester code or type a new one. " + EXAMPLE_HELP
        )
        self._load_semesters()
        self.semester_combo.editTextChanged.connect(self._update_semester_status)
        form.addRow(
            "Semester code these grades are from:", with_hint(self.semester_combo)
        )
        self.semester_status = QLabel()
        self.semester_status.setWordWrap(True)
        form.addRow("", self.semester_status)
        layout.addLayout(form)

        self.import_course_grades = QCheckBox("Import overall course grades (adjusted final grade)")
        self.import_course_grades.setChecked(True)
        self.import_exam_scores = QCheckBox("Backfill exam scores (Exam 1–4)")
        self.import_exam_scores.setChecked(True)
        self.import_exam_scores.toggled.connect(self._update_semester_status)
        layout.addWidget(self.import_course_grades)
        layout.addWidget(self.import_exam_scores)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlainText("Choose a file to see what will be imported.")
        layout.addWidget(self.preview, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Import")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.buttons.accepted.connect(self._do_import)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_semester_status()

    def _load_semesters(self):
        self.semester_combo.addItems(store.list_semester_codes(self.db_conn))
        self.semester_combo.setCurrentText("")

    def _check_semester(self):
        """Returns `(code_to_save_under, message)`; the code is None when what's
        been typed can't be used."""
        text = self.semester_combo.currentText().strip()
        if not text:
            return None, "Enter the semester code these grades are from."
        semester = parse_semester_code(text)
        if semester is None:
            return None, f"“{text}” isn't a valid semester code. {EXAMPLE_HELP}"

        code = store.resolve_semester_code(self.db_conn, text)
        if code != text:
            return code, (
                f"{semester.label} is already saved under code {code}. This import "
                f"will be added to {code}, so the semester isn't split in two."
            )
        saved = store.list_semester_codes(self.db_conn)
        status = (
            f"{semester.label} — already saved; this import adds to it."
            if code in saved
            else f"{semester.label} — new semester."
        )
        # A warning only, never a merge: the older format's two summer sessions
        # have no stated match to the current single Summer term.
        # The sheet builds the class record; this semester's scanned scores are
        # separate working data and are left alone. Said plainly because the
        # obvious fear is that importing overwrites the grading work.
        offering = self.db_conn.execute(
            "SELECT id FROM course_offerings WHERE semester_code = ?", (code,)
        ).fetchone()
        if offering is not None:
            scanned = store.count_app_graded_scores(self.db_conn, offering["id"])
            if scanned:
                total = sum(scanned.values())
                status += (
                    f" This semester also has {total} scanned exam score(s); they stay "
                    "in Exam Grading and are not changed by this import."
                )

        related = related_summer_codes(code, saved)
        if related:
            status += (
                f" Note: {', '.join(describe(c) for c in related)} is also saved for "
                f"{semester.year}. Summer codes aren't combined across the two formats, "
                "so check this is the code you meant."
            )
        return code, status

    def _update_semester_status(self, *_):
        code, message = self._check_semester()
        self.semester_status.setText(message)
        self.semester_status.setStyleSheet("" if code else "color: #b3261e;")
        self._update_import_enabled()

    def _update_import_enabled(self):
        code, _ = self._check_semester()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.resolved) and code is not None)

    def _choose_file(self):
        from app.db.config import get_data_folder

        path, _ = QFileDialog.getOpenFileName(
            self, "Choose course-grade export", str(get_data_folder()), ACCEPTED_FILTER
        )
        if path:
            self._load_file(path)

    def _load_file(self, path):
        """Read one sheet, from the drop zone or the file picker alike."""
        # Reading a D2L .xlsx takes a few seconds; the cursor says so rather
        # than the window simply not responding.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.parsed = parse_course_export(path)
        except Exception as exc:  # noqa: BLE001 - shown to the instructor
            QApplication.restoreOverrideCursor()
            dialogs.critical(self, "Could not read file", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.file_label.setText(Path(path).name)
        self._refresh_preview()

    def _refresh_preview(self):
        parsed = self.parsed
        self.resolved, self.unresolved = store.resolve_student_ids(self.db_conn, parsed.rows)

        lines = []
        if not parsed.exam_columns:
            lines.append("No exam columns found in this sheet.")
        else:
            single = parsed.layout == FORMAT_SINGLE_SCORE
            lines.append(
                "Layout: "
                + (
                    "one combined score per exam (older sheet)"
                    if single
                    else "Individual / Partner / Final per exam"
                )
            )
            lines.append("")
            lines.append("Exams found:")
            for entry in parsed.exam_columns:
                lines.append(f"  • {entry.exam_name} — out of {entry.possible_points:g} points")
        if parsed.uncounted_columns:
            # Textbook-registration markers and placeholders: read, shown, and
            # kept out of the lab / extra credit / other breakdown.
            lines.append("")
            lines.append("Not counted in any component (textbook / platform items, placeholders):")
            for column in parsed.uncounted_columns:
                label, max_points, _ = parse_item_header(column)
                lines.append(f"  • {label} — {max_points:g} points")

        if parsed.hash_prefixed_ids:
            lines.append("")
            lines.append(
                f"Student IDs: {parsed.hash_prefixed_ids} arrived with a leading "
                "\u201c#\u201d (D2L's own marker) — removed, so they match the "
                "same students in other files."
            )

        with_course_grade = sum(1 for r in parsed.rows if r.numerator is not None)
        bases = {r.grade_basis for r in parsed.rows if r.grade_basis}
        lines.append("")
        lines.append(f"Students in file: {len(parsed.rows)}")
        lines.append(f"  with a final course grade: {with_course_grade}")
        if bases == {BASIS_CALCULATED}:
            lines.append(
                "  this sheet has no Adjusted Final Grade columns, so the "
                "Calculated Final Grade is used and recorded as such"
            )
        elif BASIS_CALCULATED in bases:
            lines.append(
                "  some students have only a Calculated Final Grade; "
                "the basis is recorded per student"
            )
        if not parsed.has_id_column:
            lines.append(
                "  (no ID column in this sheet — matching on name against saved students)"
            )
        if self.unresolved:
            lines.append(
                f"  could not be matched to a student: {len(self.unresolved)} — these will be skipped:"
            )
            for row in self.unresolved[:10]:
                lines.append(f"      {row.display_name}")
            if len(self.unresolved) > 10:
                lines.append(f"      … and {len(self.unresolved) - 10} more")
        if parsed.unidentified_rows:
            lines.append(
                f"  rows with no name or ID at all: {len(parsed.unidentified_rows)} (skipped)"
            )
        if parsed.id_problem_rows:
            # Reported with row numbers and never corrected: the file is fixed
            # at the source, so a bad ID can't become a second student.
            lines.append("")
            lines.append(
                f"Student ID problems ({len(parsed.id_problem_rows)}) — student IDs are "
                "7 digits. These rows will NOT be imported; correct them in the file "
                "and import again:"
            )
            for problem in parsed.id_problem_rows[:10]:
                lines.append(
                    f"  Row {problem.row}: ID {problem.student_id} — {problem.problem}"
                )
            if len(parsed.id_problem_rows) > 10:
                lines.append(f"  … and {len(parsed.id_problem_rows) - 10} more")

        lines.append("")
        lines.append(
            "Exam scores are taken from the sheet as-is rather than recalculated, "
            "so any curve or manual adjustment already applied is preserved."
        )

        self.preview.setPlainText("\n".join(lines))
        self._update_import_enabled()

    @guarded("Import failed")
    def _do_import(self):
        semester, message = self._check_semester()
        if semester is None:
            dialogs.warning(self, "Semester code needed", message)
            return
        if not (self.import_course_grades.isChecked() or self.import_exam_scores.isChecked()):
            dialogs.warning(
                self, "Nothing selected", "Choose at least one kind of data to import."
            )
            return

        offering_id = store.get_or_create_course_offering(self.db_conn, semester)

        messages = []
        if self.import_course_grades.isChecked():
            saved = store.save_course_grades(
                self.db_conn,
                offering_id,
                self.resolved,
                source_file=self.file_label.text(),
            )
            messages.append(f"Course grades saved for {saved} student(s).")
        if self.import_exam_scores.isChecked():
            counts = store.save_historical_exam_scores(
                self.db_conn, offering_id, self.parsed, self.resolved,
                source_file=self.file_label.text(),
            )
            detail = ", ".join(f"{name}: {n}" for name, n in sorted(counts["exams"].items()))
            messages.append(f"Exam scores saved: {counts['scores']} ({detail}).")
        if self.unresolved:
            messages.append(f"{len(self.unresolved)} unmatched student(s) were skipped.")

        mismatches = store.exam_point_mismatches(self.db_conn)
        if mismatches:
            messages.append(
                "Point value mismatch — each exam keeps its recorded value, so check "
                "which is right:"
            )
            for name, recorded, incoming in mismatches:
                messages.append(
                    f"  {name}: recorded as out of {recorded:g}, this sheet says {incoming:g}"
                )
            store.clear_exam_point_mismatches(self.db_conn)

        dialogs.information(
            self, "Import complete", f"{describe(semester)}\n\n" + "\n".join(messages)
        )

        # Closing is asked here because uploading a sheet is what finishes a
        # semester. It's a label, not a lock: a closed semester can still be
        # re-imported over later, so a correction is never blocked.
        if not store.is_semester_closed(self.db_conn, offering_id):
            # First line is the question, so it is the dialog's headline;
            # see app/gui/dialogs.py.
            answer = dialogs.question(
                self,
                "Close this semester?",
                f"Mark {describe(semester)} as complete?\n\n"
                "Closed semesters count as the class record. One still open is "
                "shown as “in progress” wherever semesters are listed.\n\n"
                "You can still re-import a sheet for a closed semester if "
                "something needs correcting.",
                default=QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                store.set_semester_closed(self.db_conn, offering_id, True)
        self.accept()
