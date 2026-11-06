"""The "Rec Letter Info" tab: class-rank cutoffs plus a student lookup.

Built for writing recommendation letters, which needs two things at once: the
score that puts a student in the top 0.1/1/5/10/20% of the course historically,
and a way to pull up a particular student's grades and see where they landed.

Ranking is on **percentage only**. A course grade's raw figure is a
numerator/denominator pair whose denominator varies per student (D2L drops
items a student was exempted from), so raw numerators aren't comparable even
within a single semester, let alone across the pooled history. Per-exam scores
elsewhere in the app do show raw and percent together; a rank cutoff never does.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.semesters import describe
from app.db import store
from app.gui.widgets import with_hint

RANK_COLUMNS = ["Tier", "Percentage needed", "Students in tier"]
RESULT_COLUMNS = ["Last Name", "First Name", "Student ID", "Semesters"]
DETAIL_COLUMNS = ["Semester", "Item", "Score", "Percent"]
# How long after the last keystroke the search runs.
SEARCH_DELAY_MS = 200


def _note(text: str) -> QLabel:
    """A wrapped label at the window's own font size.

    The panel used to mix bold headings, <small> captions and default-size
    notes, which read as three different fonts. Size is left alone everywhere
    and the group-box titles do the work instead."""
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _ordinal(percentile: float) -> str:
    """1st, 2nd, 3rd, 11th, 21st — for a percentile rounded to whole numbers."""
    value = max(1, min(100, round(percentile)))
    if value % 100 in (11, 12, 13):
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _pair(earned, possible) -> str:
    """ "256.7 / 293", or just the score when nothing says what it's out of."""
    if earned is None:
        return ""
    return f"{earned:g} / {possible:g}" if possible else f"{earned:g}"


def _percent(earned, possible) -> str:
    if earned is None or not possible:
        return ""
    return f"{earned / possible * 100:.1f}%"


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class RecLetterInfoView(QWidget):
    def __init__(self, db_conn):
        super().__init__()
        self.db_conn = db_conn
        self.pool = []
        self.exam_pool = []
        self.pool_cutoffs = []
        self.exam_pool_cutoffs = []
        self._results = []

        layout = QHBoxLayout(self)

        # The cutoffs are reference material, so they sit in a narrow column on
        # the left and the student's record gets the full height beside them.
        rank_panel = QWidget()
        rank_panel.setMaximumWidth(260)
        rank_layout = QVBoxLayout(rank_panel)
        rank_layout.setContentsMargins(0, 0, 0, 0)
        # One box per scale. The box title carries the hierarchy, so every
        # label inside can share the window's font rather than mixing bold,
        # <small> and default sizes as this panel used to.
        course_box = QGroupBox("Class rank — course grades")
        course_layout = QVBoxLayout(course_box)
        course_layout.addWidget(_note("The whole course: lab, quizzes and exams."))
        self.rank_table = self._make_rank_table()
        course_layout.addWidget(self.rank_table)
        self.pool_label = _note("")
        course_layout.addWidget(with_hint(self.pool_label))
        rank_layout.addWidget(course_box)

        # The same five tiers on exam averages. Exams alone run higher than
        # course grades, so the two give different standings and must never be
        # read as one.
        exam_box = QGroupBox("Exam rank — exam averages")
        exam_layout = QVBoxLayout(exam_box)
        exam_layout.addWidget(_note("Exams only: excludes the lab and quizzes."))
        self.exam_rank_table = self._make_rank_table()
        exam_layout.addWidget(self.exam_rank_table)
        self.exam_pool_label = _note("")
        exam_layout.addWidget(with_hint(self.exam_pool_label))
        rank_layout.addWidget(exam_box)
        rank_layout.addStretch()
        layout.addWidget(rank_panel)

        # --- student lookup, given the rest of the window -------------------
        lookup_box = QGroupBox("Look up a student")
        lookup_layout = QVBoxLayout(lookup_box)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by name or student ID…")
        self.search_box.setClearButtonEnabled(True)
        # Search a moment after typing stops, not on every keystroke: each
        # search is a LIKE over four columns plus the detail render below.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(lambda: self._on_search(self.search_box.text()))
        self.search_box.textChanged.connect(self._schedule_search)
        self.search_box.returnPressed.connect(self.flush_search)
        lookup_layout.addWidget(self.search_box)
        lookup_layout.addWidget(
            _note(
                "Select a student and press ⌘C to copy their ID. In the record "
                "below, the ID can be selected with the mouse and copied."
            )
        )

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.results_table = QTableWidget(0, len(RESULT_COLUMNS))
        self.results_table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.itemSelectionChanged.connect(self._on_student_selected)
        self.results_table.setMaximumHeight(170)
        # The ID is what gets typed into D2L or a letter, so it has to be
        # copyable. A table cell's text can't be selected with the mouse
        # without making the cell editable -- which would let a stray
        # double-click rewrite a student ID -- so Copy is bound here instead.
        copy_id = QShortcut(QKeySequence.StandardKey.Copy, self.results_table)
        copy_id.setContext(Qt.ShortcutContext.WidgetShortcut)
        copy_id.activated.connect(self._copy_student_id)
        splitter.addWidget(self.results_table)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_label = QLabel("Search for a student to see their grades.")
        self.detail_label.setWordWrap(True)
        # So the ID written out here can be dragged over and copied.
        self.detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        detail_layout.addWidget(self.detail_label)
        self.detail_table = QTableWidget(0, len(DETAIL_COLUMNS))
        self.detail_table.setHorizontalHeaderLabels(DETAIL_COLUMNS)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        detail_layout.addWidget(self.detail_table)
        splitter.addWidget(detail_panel)

        # The record gets the lion's share of the height; the match list only
        # needs to show a few names.
        splitter.setSizes([150, 650])
        splitter.setStretchFactor(1, 1)
        lookup_layout.addWidget(splitter, 1)
        layout.addWidget(lookup_box, 1)

    def _make_rank_table(self) -> QTableWidget:
        table = QTableWidget(0, len(RANK_COLUMNS))
        table.setHorizontalHeaderLabels(RANK_COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        return table

    # ------------------------------------------------------------------
    def refresh(self):
        """Re-read both pools. Called after an import adds course grades."""
        self.pool = store.get_course_grade_pool(self.db_conn)
        self.exam_pool = store.get_exam_average_pool(self.db_conn)
        # Tier cutoffs once per refresh; _render_detail ranks against them.
        self.pool_cutoffs = store.class_rank_cutoffs(self.pool)
        self.exam_pool_cutoffs = store.class_rank_cutoffs(self.exam_pool)
        self._render_rank_table()
        self._render_exam_rank_table()
        self._on_search(self.search_box.text())

    def _render_exam_rank_table(self):
        self._fill_rank_table(self.exam_rank_table, self.exam_pool)
        n = len(self.exam_pool)
        self.exam_pool_label.setText(
            f"Pool: {n} exam averages" if n else "No exam scores yet."
        )
        self.exam_pool_label.setToolTip(
            "One average per student per semester, from the exams in the class "
            "record. Exams alone run higher than course grades, which include the "
            "lab and quizzes, so the two scales give different standings."
        )

    def _render_rank_table(self):
        """The visible note is the pool size; the caveats live in its tooltip.

        In a column this narrow the caveats wrapped to five lines and buried the
        one number worth seeing at a glance. They still matter -- a tie can put
        more than N% in a tier, and a semester with no imported sheet isn't
        counted at all -- so they're a hover away rather than gone."""
        n = len(self.pool)
        if not n:
            self.pool_label.setText("No course grades yet.")
            self.pool_label.setToolTip(
                "Class rank is drawn from imported course grades. Import a "
                "course-grade sheet through Class History to build the pool."
            )
        else:
            self.pool_label.setText(f"Pool: {n} course grades")
            caveats = [
                "Ties are not split, so a tier can hold slightly more students "
                "than its percentage.",
            ]
            missing = store.semesters_missing_course_grades(self.db_conn)
            if missing:
                names = ", ".join(
                    store.describe_semester(self.db_conn, c) for c in missing
                )
                caveats.append(
                    f"Not included: {names}. Class rank uses imported course "
                    "grades, so a semester counts once its sheet is imported."
                )
            self.pool_label.setToolTip("\n\n".join(caveats))

        self._fill_rank_table(self.rank_table, self.pool)

    def _fill_rank_table(self, table: QTableWidget, pool: list):
        tiers = store.class_rank_cutoffs(pool)
        table.setRowCount(len(tiers))
        for row, tier in enumerate(tiers):
            if tier["cutoff"] is None:
                cutoff_text, count_text = "—", "—"
            elif not tier["supported"]:
                cutoff_text = f"{tier['cutoff']:.2f}%  (provisional)"
                count_text = (
                    f"{tier['students']} — pool too small; needs "
                    f"~{store.MIN_POOL_FOR_TOP_TENTH_PERCENT} grades"
                )
            else:
                cutoff_text = f"{tier['cutoff']:.2f}%"
                count_text = str(tier["students"])
            table.setItem(row, 0, _readonly(tier["label"]))
            table.setItem(row, 1, _readonly(cutoff_text))
            table.setItem(row, 2, _readonly(count_text))

    # ------------------------------------------------------------------
    def _schedule_search(self, _text: str):
        """(Re)start the typing delay; the search runs when it expires."""
        self._search_timer.start()

    def flush_search(self):
        """Run the pending search now (Return key, and tests) instead of after
        the typing delay."""
        self._search_timer.stop()
        self._on_search(self.search_box.text())

    def _on_search(self, text: str):
        self._results = store.search_students(self.db_conn, text)
        self.results_table.clearSelection()
        self.results_table.setRowCount(len(self._results))
        for row, r in enumerate(self._results):
            values = [
                r["last_name"] or "",
                r["first_name"] or "",
                r["student_id"],
                str(r["semesters"]),
            ]
            for col, value in enumerate(values):
                self.results_table.setItem(row, col, _readonly(value))

        if self._results:
            # Select row 0 with the selection signal blocked, then render once
            # explicitly. Leaning on the signal alone fails when row 0 was
            # already selected from the previous search (selectRow() then fires
            # nothing); letting it fire *and* rendering explicitly did every
            # search's detail queries twice.
            self.results_table.blockSignals(True)
            try:
                self.results_table.selectRow(0)
            finally:
                self.results_table.blockSignals(False)
            self._render_detail(self._results[0])
            return

        self.detail_table.setRowCount(0)
        self.detail_label.setText(
            "Search for a student to see their grades."
            if not text.strip()
            else f"No student matches “{text.strip()}”."
        )

    def _copy_student_id(self):
        """Put the selected student's ID on the clipboard."""
        rows = self.results_table.selectionModel().selectedRows()
        if not rows:
            return
        QGuiApplication.clipboard().setText(self._results[rows[0].row()]["student_id"])

    def _on_student_selected(self):
        rows = self.results_table.selectionModel().selectedRows()
        if not rows:
            return
        record = self._results[rows[0].row()]
        self._render_detail(record)

    def _render_detail(self, record):
        student_id = record["student_id"]
        name = " ".join(p for p in (record["first_name"], record["last_name"]) if p)
        course_grades = store.get_student_course_grades(self.db_conn, student_id)
        exam_scores = store.get_student_exam_scores(self.db_conn, student_id)

        # One three-line block per semester: who they are and when, then the
        # course standing, then the exam-only standing. A student who repeated
        # the course gets a block each, so the two records can't be read as one.
        #
        # The standing is a tier -- "Top 10%", or "below 20%" for anyone outside
        # the widest one. The rank number itself isn't shown: the tier is the
        # fact a letter needs.
        identity = f"<b>{name or student_id}</b> — ID {student_id}"
        exam_averages = {
            row["semester_code"]: row["overall_percent"]
            for row in store.get_student_exam_averages(self.db_conn, student_id)
        }
        headline = []
        for grade in course_grades:
            semester = grade["semester_code"]
            if headline:
                headline.append("")  # a blank line between semesters
            headline.append(f"{identity} — {describe(semester)}")

            rank = store.rank_for_percent(self.pool, grade["final_percent"], self.pool_cutoffs)
            course_line = f"<b>Course:</b> {grade['final_percent']:.2f}%"
            if rank:
                course_line += f" — {rank['label']}"
            headline.append(course_line)

            # The exam-only standing on its own line, with the percentile, so a
            # letter can cite either measure without them being confused.
            exam_percent = exam_averages.get(semester)
            if exam_percent is not None:
                exam_rank = store.rank_for_percent(
                    self.exam_pool, exam_percent, self.exam_pool_cutoffs
                )
                exam_line = f"<b>Exam:</b> {exam_percent:.2f}%"
                if exam_rank:
                    # A percentile rather than "top N%": for a weak student
                    # "top 99%" is true and useless, while "1st percentile"
                    # reads correctly at both ends of the scale.
                    exam_line += (
                        f" — {exam_rank['label']}, {_ordinal(exam_rank['percentile'])}"
                        f" percentile of {exam_rank['pool']}"
                    )
                headline.append(exam_line)
        if not course_grades:
            headline.append(identity)
            headline.append("No overall course grade saved for this student.")
        self.detail_label.setText("<br>".join(headline))

        rows = []
        for grade in course_grades:
            semester = describe(grade["semester_code"])
            percent = grade["final_percent"]
            if grade["grade_basis"] == "adjusted":
                numerator, denominator = (
                    grade["adjusted_numerator"],
                    grade["adjusted_denominator"],
                )
            else:
                numerator, denominator = (
                    grade["calculated_numerator"],
                    grade["calculated_denominator"],
                )
            basis = "" if grade["grade_basis"] == "adjusted" else " (calculated)"
            rows.append(
                (
                    semester,
                    f"Overall course grade{basis}",
                    _pair(numerator, denominator),
                    "" if percent is None else f"{percent:.2f}%",
                )
            )

            # The parts behind that total, each with score and percentage the
            # way the exams are shown.
            for label, earned, possible, show_percent in (
                ("Lab grade", grade["lab_earned"], grade["lab_possible"], True),
                # Extra credit is a bonus, so it shows the points earned and no
                # percentage -- "50% of extra credit" doesn't mean anything.
                (
                    "Extra credit",
                    grade["extra_credit_earned"],
                    grade["extra_credit_possible"],
                    False,
                ),
                (
                    "Other grades (quizzes, etc.)",
                    grade["other_earned"],
                    grade["other_possible"],
                    True,
                ),
            ):
                if earned is None and not possible:
                    continue
                rows.append(
                    (
                        semester,
                        label,
                        _pair(earned, possible),
                        _percent(earned, possible) if show_percent else "",
                    )
                )

        # Per-exam scores: raw and percentage together, since an exam has one
        # shared denominator and the raw score means something beside it.
        for score in exam_scores:
            rows.append(
                (
                    describe(score["semester_code"]),
                    score["exam_name"],
                    _pair(score["final_grade"], score["possible_points"]),
                    "" if score["percent"] is None else f"{score['percent']:.1f}%",
                )
            )

        self.detail_table.setRowCount(len(rows))
        for row_idx, values in enumerate(rows):
            for col, value in enumerate(values):
                self.detail_table.setItem(row_idx, col, _readonly(str(value)))
        self.detail_table.resizeColumnsToContents()
