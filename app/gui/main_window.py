from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QFrame,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.matching import exclude_duplicates, match_rows
from app.core.parsers import parse_exam_file
from app.db import store
from app.gui.errors import guarded
from app.gui.dashboard_view import DashboardView
from app.gui.history_view import HistoryView
from app.gui.historical_import import HistoricalImportDialog
from app.gui.rec_letter_view import RecLetterInfoView
from app.gui.reports_view import ReportsView
from app.gui.results_view import ResultsView
from app.gui import dialogs
from app.gui.widgets import divide_tab_bar
from app.version import TITLE

ACCEPTED_SUFFIXES = (".csv", ".xlsx", ".xls")


class DropZone(QFrame):
    """Accepts a single dragged exam file (or one picked via Browse) and
    forwards it to the main window for processing.

    Exams only ever arrive as one of two file shapes: an Individual-round-only
    export (forms A/B), or a combined export with Individual and Group data
    together (forms A/B/C) -- Group-round (form C) data is never provided as
    its own standalone file, so there's never a legitimate reason to drop two
    files at once.
    """

    def __init__(self, on_file_dropped):
        super().__init__()
        self._on_file_dropped = on_file_dropped
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(110)

        layout = QVBoxLayout(self)
        self.label = QLabel(
            "Drag one exam file here — either an Individual-round-only export "
            "(forms A/B), or a combined export with Individual and Group data "
            "together (forms A/B/C) — CSV or Excel"
        )
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        browse_btn = QPushButton("Browse for a file…")
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.suffix.lower() in ACCEPTED_SUFFIXES]
        if not paths:
            return
        if len(paths) > 1:
            dialogs.warning(
                self,
                "One file at a time",
                "Drop a single exam file — either an Individual-round-only "
                "export, or a combined Individual+Group export.",
            )
            return
        self._on_file_dropped(paths[0])

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select exam file", "", "Exam files (*.csv *.xlsx *.xls)"
        )
        if path:
            self._on_file_dropped(Path(path))


class MainWindow(QMainWindow):
    def __init__(self, db_conn):
        super().__init__()
        self.db_conn = db_conn
        # Say which data a window is looking at. A development copy must never
        # be mistaken for the live one.
        from app.db.config import is_development_data

        title = TITLE
        if is_development_data():
            title += f"  —  DEVELOPMENT DATA  ({store.default_db_path()})"
        self.setWindowTitle(title)
        self.resize(1150, 780)

        self.loaded_file: Path | None = None
        # What Results is showing. The exam/offering ids are None until the
        # exam exists in the database -- a Run doesn't create it, Save does
        # (ensure_current_exam) -- so a failed or abandoned Run leaves no row.
        self.current_exam_id = None
        self.current_offering_id = None
        self.current_possible_points = None
        self.current_exam_name = None
        self.current_semester_code = None

        central = QWidget()
        layout = QVBoxLayout(central)

        # Which of the app's two jobs you're in, in a line that is always there
        # and always the same height -- so the tab bar still sits in the same
        # place on every screen.
        self.area_label = QLabel()
        self.area_label.setStyleSheet("font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(self.area_label)

        # The app does two separate jobs, and mixing their controls is what made
        # them easy to confuse. Everything that scores this semester's exams
        # lives in this box; the class record is reached through its own tabs,
        # and importing a course sheet belongs there (see HistoryView).
        self.exam_grading_group = QGroupBox("Exam Grading — this semester's scanned exams")
        grading_layout = QVBoxLayout(self.exam_grading_group)

        self.drop_zone = DropZone(self._handle_file_dropped)
        grading_layout.addWidget(self.drop_zone)

        status_row = QHBoxLayout()
        self.status_label = QLabel("No files loaded.")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        # Open Saved Exam lives with the Results buttons, not here: this row is
        # for the file just dropped, and that one loads from the app's own
        # store. Two different sources shouldn't sit side by side.
        self.run_button = QPushButton("Run")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self._run)
        status_row.addWidget(self.run_button)
        grading_layout.addLayout(status_row)

        self.tabs = QTabWidget()
        self.results_view = ResultsView(self)
        # The controls live inside the Results tab, so the tab bar is the first
        # thing on the window whichever tab is open.
        self.results_view.set_header_widget(self.exam_grading_group)
        self.dashboard_view = DashboardView()
        self.history_view = HistoryView(
            self.db_conn,
            on_historical_input=self._historical_input,
            on_data_changed=self._record_changed,
        )
        self.rec_letter_view = RecLetterInfoView(self.db_conn)
        self.reports_view = ReportsView(
            self.db_conn, on_export_semester=self.history_view._export_csv
        )

        # The tabs belong to the app's two jobs, so the bar is split to match
        # the Exam Grading box above: scoring this semester's exams on the left,
        # the class record on the right. The divider is an inert tab -- Qt has
        # no separator for a tab bar -- styled down to a thin rule by
        # widgets.divide_tab_bar, the same division as the Results button row.
        self.tabs.addTab(self.results_view, "Results")
        self.tabs.addTab(self.dashboard_view, "Dashboard")
        self.tab_divider_index = self.tabs.addTab(QWidget(), "")
        # "Class History" rather than "History": this tab is the course record,
        # while Results is the current semester's working area.
        self.tabs.addTab(self.history_view, "Class History")
        self.tabs.addTab(self.reports_view, "Reports")
        self.tabs.addTab(self.rec_letter_view, "Rec Letter Info")
        # Styled after every tab is added, so nothing added later resets it.
        divide_tab_bar(self.tabs, self.tab_divider_index)
        self.exam_area_tabs = (self.results_view, self.dashboard_view)
        self.tabs.currentChanged.connect(self._sync_area_label)

        layout.addWidget(self.tabs)

        self.setCentralWidget(central)
        self._sync_area_label()
        self._build_menu()
        self.history_view.refresh()
        self.reports_view.refresh()
        self.rec_letter_view.refresh()

    def _sync_area_label(self, *_):
        """EXAM or COURSE GRADES, so the area you're working in is unmistakable."""
        in_exam_area = self.tabs.currentWidget() in self.exam_area_tabs
        self.area_label.setText("EXAM" if in_exam_area else "COURSE GRADES")

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("File")
        backup_action = QAction("Backup Database…", self)
        backup_action.triggered.connect(self._backup_database)
        file_menu.addAction(backup_action)
        choose_folder_action = QAction("Choose Data Folder…", self)
        choose_folder_action.triggered.connect(self._choose_data_folder)
        file_menu.addAction(choose_folder_action)

    @guarded("Backup failed")
    def _backup_database(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose backup destination")
        if not folder:
            return
        dest = store.backup_database(store.default_db_path(), folder)
        dialogs.information(self, "Backup complete", f"Database backed up to:\n{dest}")

    @guarded("Could not change the data folder")
    def _choose_data_folder(self):
        from app.db.config import get_data_folder, set_data_folder

        folder = QFileDialog.getExistingDirectory(
            self, "Choose data folder", str(get_data_folder())
        )
        if not folder:
            return
        set_data_folder(folder)
        dialogs.information(
            self,
            "Data folder updated",
            f"Grade history will be read from/saved to:\n{folder}\n\n"
            "Restart the app for this to take effect.",
        )

    @guarded("Could not open the saved exam")
    def _open_saved_exam(self):
        """Reload a saved exam into Results, so it can be edited and exported.

        Only exams the app graded itself are offered: reopening recomputes the
        final grade from the Individual and Group scores, which would overwrite
        an imported exam's real grade (the sheet's figure carries any curve, and
        the oldest sheets have no component scores at all). Imported exams are
        the class record and are corrected by re-importing a sheet."""
        offerings = store.list_offerings(self.db_conn)
        if not offerings:
            dialogs.information(
                self, "Nothing saved yet", "No exams have been saved to history yet."
            )
            return

        labels = [store.describe_semester(self.db_conn, code) for _, code in offerings]
        label, chosen = QInputDialog.getItem(
            self, "Open Saved Exam", "Semester:", labels, 0, False
        )
        if not chosen:
            return
        offering_id, semester_code = offerings[labels.index(label)]

        exams = store.list_reopenable_exams(self.db_conn, offering_id)
        if not exams:
            dialogs.information(
                self,
                "Nothing to reopen",
                f"{store.describe_semester(self.db_conn, semester_code)} has no exams "
                "that can be reopened.\n\nExams imported from a course sheet aren't "
                "offered: their grades are the sheet's, including any curve, and "
                "reopening would recalculate them. Correct those by importing a "
                "corrected sheet through Historical Input instead.",
            )
            return

        names = [f"{e['exam_name']} ({e['students']} students)" for e in exams]
        name, chosen = QInputDialog.getItem(self, "Open Saved Exam", "Exam:", names, 0, False)
        if not chosen:
            return
        exam = exams[names.index(name)]

        exam_name, possible_points, matched = store.load_saved_exam(self.db_conn, exam["id"])
        self.current_offering_id = offering_id
        self.current_exam_id = exam["id"]
        self.current_possible_points = possible_points
        self.current_exam_name = exam_name
        self.current_semester_code = semester_code
        self.loaded_file = None
        self.run_button.setEnabled(False)
        self.status_label.setText(
            f"Opened saved exam: {exam_name} — "
            f"{store.describe_semester(self.db_conn, semester_code)}"
        )
        self.results_view.load_results(
            exam_name=exam_name,
            matched_students=matched,
            duplicates={"individual": {}, "group": {}},
            possible_points=possible_points,
        )
        self.dashboard_view.load_results(matched, possible_points, exam_name)
        self.tabs.setCurrentWidget(self.results_view)

    def has_current_exam(self) -> bool:
        return self.current_exam_id is not None or self.current_exam_name is not None

    def ensure_current_exam(self) -> int:
        """The database id of the exam Results is showing, creating the exam
        (and its semester) now if a Run hasn't saved it yet. Called by Save, so
        the row appears only when the instructor commits to it."""
        if self.current_exam_id is None:
            self.current_offering_id = store.get_or_create_course_offering(
                self.db_conn, self.current_semester_code
            )
            self.current_exam_id = store.get_or_create_exam(
                self.db_conn,
                self.current_offering_id,
                self.current_exam_name,
                self.current_possible_points,
            )
        return self.current_exam_id

    def _record_changed(self):
        """Re-read the class record, and let go of an exam that no longer exists.

        Deleting a semester takes its exam rows with it, so an exam loaded from
        it would otherwise still be the save target -- and saving would write it
        back under a semester the instructor had just removed."""
        if self.current_exam_id is not None and not self.db_conn.execute(
            "SELECT 1 FROM exams WHERE id = ?", (self.current_exam_id,)
        ).fetchone():
            self.current_exam_id = None
        self.reports_view.refresh()
        self.rec_letter_view.refresh()

    @guarded("Historical input failed")
    def _historical_input(self):
        dialog = HistoricalImportDialog(self.db_conn, self)
        dialog.exec()
        # Refresh whether or not the dialog accepted: an import that failed
        # part-way may still have written something, and the views must show
        # the database as it is, not as it was before the dialog opened.
        self.history_view.refresh()
        self.reports_view.refresh()
        self.rec_letter_view.refresh()

    def _handle_file_dropped(self, path: Path):
        self.loaded_file = path
        self.status_label.setText(f"Loaded: {path.name}")
        self.run_button.setEnabled(True)

    def _run(self):
        # Parsing an .xlsx export can take a few seconds on the GUI thread; the
        # wait cursor and a disabled Run button say so, and stop a second click
        # queuing a second run.
        self.run_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._process_loaded_file()
        except Exception as exc:  # noqa: BLE001 - surfaced to the instructor, not swallowed
            QApplication.restoreOverrideCursor()
            dialogs.critical(self, "Error processing file", str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.run_button.setEnabled(self.loaded_file is not None)

    def _process_loaded_file(self):
        parsed = parse_exam_file(self.loaded_file)

        if not parsed.exam_name or not parsed.semester_code:
            raise ValueError(
                "Could not determine the exam name/semester from the uploaded file "
                "(expected the QuizName/QuizClass columns to be populated)."
            )
        exam_name, semester_code = parsed.exam_name, parsed.semester_code

        possible_points = None
        for row in parsed.individual_rows + parsed.group_rows:
            if row.possible_points:
                possible_points = row.possible_points
                break

        # Look the exam up without creating it. Everything below works on
        # locals; the window's current_* state changes only once Results has
        # been updated, so a failure part-way can't leave Results showing the
        # previous exam while Save writes under this one.
        exam_id = store.find_exam(self.db_conn, semester_code, exam_name)

        individual_rows = parsed.individual_rows
        group_rows = parsed.group_rows
        duplicate_individual = parsed.duplicate_student_ids.get("individual", {})
        duplicate_group = parsed.duplicate_student_ids.get("group", {})

        # Group-round (form C) data is never uploaded on its own -- an
        # Individual-only file (forms A/B) is the other legitimate shape, so
        # a file with nothing on one side falls back to whatever was already
        # saved to history for this exam, letting a single-file run still
        # report who's missing the other round.
        if exam_id is not None:
            if not individual_rows:
                individual_rows = store.get_existing_round_rows(self.db_conn, exam_id, "individual")
            if not group_rows:
                group_rows = store.get_existing_round_rows(self.db_conn, exam_id, "group")

        clean_individual = exclude_duplicates(individual_rows, duplicate_individual.keys())
        clean_group = exclude_duplicates(group_rows, duplicate_group.keys())

        # A row whose ID doesn't look like a student ID is held out too, rather
        # than saved under that ID and quietly becoming a second student.
        flagged_ids = parsed.flagged_student_ids
        if flagged_ids:
            clean_individual = exclude_duplicates(clean_individual, flagged_ids)
            clean_group = exclude_duplicates(clean_group, flagged_ids)

        matched = match_rows(clean_individual, clean_group)

        self.results_view.load_results(
            exam_name=exam_name,
            matched_students=matched,
            duplicates={"individual": duplicate_individual, "group": duplicate_group},
            possible_points=possible_points,
            id_problems=parsed.id_problem_rows,
        )
        self.current_exam_id = exam_id
        self.current_offering_id = None if exam_id is None else store.get_or_create_course_offering(
            self.db_conn, semester_code
        )
        self.current_possible_points = possible_points
        self.current_exam_name = exam_name
        self.current_semester_code = semester_code
        self.dashboard_view.load_results(matched, possible_points, exam_name)
        self.tabs.setCurrentWidget(self.results_view)
