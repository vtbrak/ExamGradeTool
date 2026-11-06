"""Semester state: in progress vs closed (roadmap item 15).

Closing is explicit, asked when a course sheet is imported, and is a label on
the record rather than a lock.
"""

from pathlib import Path

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.core.course_export import parse_course_export  # noqa: E402
from app.db import store  # noqa: E402
from app.gui import dialogs  # noqa: E402
from app.gui.historical_import import HistoricalImportDialog  # noqa: E402

TRIPLE = Path(__file__).parent / "fixtures" / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


# --- the state itself --------------------------------------------------------

def test_a_new_semester_starts_in_progress(conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    assert store.is_semester_closed(conn, offering) is False
    assert store.in_progress_semester_codes(conn) == ["202610"]
    assert store.closed_semester_codes(conn) == set()


def test_closing_and_reopening_a_semester(conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    store.set_semester_closed(conn, offering, True)
    assert store.is_semester_closed(conn, offering) is True
    assert store.in_progress_semester_codes(conn) == []

    store.set_semester_closed(conn, offering, False)
    assert store.is_semester_closed(conn, offering) is False


def test_closing_does_not_lock_a_semester(conn):
    """A late correction must never be blocked: re-importing over a closed
    semester still works, and it stays closed."""
    offering = store.get_or_create_course_offering(conn, "202610")
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "first.csv")
    store.set_semester_closed(conn, offering, True)

    store.save_course_grades(conn, offering, parsed.rows, "corrected.csv")
    sources = {r["student_id"] for r in store.get_course_grades(conn, offering)}
    assert len(sources) == 3
    assert store.is_semester_closed(conn, offering) is True


def test_describe_semester_labels_only_open_semesters(conn):
    open_offering = store.get_or_create_course_offering(conn, "202610")
    closed = store.get_or_create_course_offering(conn, "2165")
    store.set_semester_closed(conn, closed, True)

    assert store.describe_semester(conn, "202610") == "202610 (Spring 2026) — in progress"
    assert store.describe_semester(conn, "2165") == "2165 (Fall 2016)"
    assert open_offering != closed


def test_in_progress_codes_are_newest_first(conn):
    for code in ("2165", "202610", "2201"):
        store.get_or_create_course_offering(conn, code)
    assert store.in_progress_semester_codes(conn) == ["202610", "2201", "2165"]


def test_semesters_missing_course_grades(conn):
    with_grades = store.get_or_create_course_offering(conn, "202610")
    store.get_or_create_course_offering(conn, "2165")
    store.save_course_grades(conn, with_grades, parse_course_export(TRIPLE).rows, "s.csv")

    assert store.semesters_missing_course_grades(conn) == ["2165"]


def test_migration_adds_closed_at_to_an_existing_database(tmp_path):
    """A grades.db from before this feature must gain the column, not break."""
    import sqlite3

    db = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        """
        CREATE TABLE course_offerings (id INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_code TEXT NOT NULL, course_number TEXT NOT NULL,
            UNIQUE (semester_code, course_number));
        INSERT INTO course_offerings (semester_code, course_number) VALUES ('202610', '211');
        """
    )
    legacy.commit()
    legacy.close()

    conn = store.connect(db)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(course_offerings)")}
    assert "closed_at" in columns
    # The pre-existing semester reads as in progress rather than erroring.
    assert store.in_progress_semester_codes(conn) == ["202610"]


# --- the prompt at import ----------------------------------------------------

def _dialog(conn, monkeypatch, answer):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    asked = []

    def question(*args, **kwargs):
        asked.append(args[2] if len(args) > 2 else "")
        return answer

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    d = HistoricalImportDialog(conn)
    d.parsed = parse_course_export(TRIPLE)
    d.file_label.setText(TRIPLE.name)
    d._refresh_preview()
    d.semester_combo.setCurrentText("202610")
    d.asked = asked
    return d


def test_import_asks_whether_to_close_and_yes_closes_it(qapp, conn, monkeypatch):
    d = _dialog(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    d._do_import()

    assert len(d.asked) == 1
    assert "Spring 2026" in d.asked[0]
    assert store.closed_semester_codes(conn) == {"202610"}


def test_answering_no_leaves_the_semester_in_progress(qapp, conn, monkeypatch):
    d = _dialog(conn, monkeypatch, QMessageBox.StandardButton.No)
    d._do_import()

    assert len(d.asked) == 1
    assert store.in_progress_semester_codes(conn) == ["202610"]


def test_an_already_closed_semester_is_not_asked_again(qapp, conn, monkeypatch):
    offering = store.get_or_create_course_offering(conn, "202610")
    store.set_semester_closed(conn, offering, True)

    d = _dialog(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    d._do_import()

    assert d.asked == []  # no second prompt
    assert store.is_semester_closed(conn, offering) is True


# --- phase 3: the two areas are named, and figures say where they came from ---

def test_the_class_record_tab_is_named_for_what_it_is(qapp, conn):
    """Results is the working area; this tab is the course record."""
    from app.gui.main_window import MainWindow

    window = MainWindow(conn)
    # The divider splits the exam-grading tabs from the class-record ones.
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        # The divider is an inert tab with no label; see tests/test_tab_divider.py.
        "Results", "Dashboard", "", "Class History", "Reports", "Rec Letter Info",
    ]
    assert window.tabs.isTabEnabled(window.tab_divider_index) is False


def _history_note(conn) -> str:
    """The History tab's provenance note. Returns the text rather than the
    view: a HistoryView that goes out of scope is garbage-collected and its
    labels deleted, so reading them afterwards raises."""
    from app.gui.history_view import HistoryView

    view = HistoryView(conn)
    view.refresh()
    return view.stats_label.text()


def test_a_semester_with_no_course_grades_says_so(qapp, conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)
    conn.execute("INSERT INTO students VALUES ('1000001', 'Ann', 'Alpha')")
    conn.execute(
        """INSERT INTO exam_scores (exam_id, student_id, final_grade, updated_at)
           VALUES (?, '1000001', 40, '2026-01-01')""",
        (exam_id,),
    )
    conn.commit()

    note = _history_note(conn)
    assert "No course grades imported yet" in note
    assert "exam averages, not course grades" in note


def test_a_semester_with_course_grades_distinguishes_the_two_figures(qapp, conn):
    offering = store.get_or_create_course_offering(conn, "202610")
    store.save_course_grades(conn, offering, parse_course_export(TRIPLE).rows, "s.csv")

    note = _history_note(conn)
    assert "Course grades on record for 3 student(s)" in note
    assert "still open" in note  # not closed yet
    assert "what class rank uses" in note

    store.set_semester_closed(conn, offering, True)
    assert "semester complete" in _history_note(conn)


# --- phase 4: replacing scanned scores is stated, not silent ------------------

def _with_scanned_scores(conn, semester="202610"):
    offering = store.get_or_create_course_offering(conn, semester)
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)
    # 0900001, not 900001: the sheet's 6-digit OrgDefinedId is padded to seven
    # digits on import, so this is the same student.
    conn.execute("INSERT OR IGNORE INTO students VALUES ('0900001', 'Ann', 'Alpha')")
    conn.execute(
        """INSERT INTO exam_scores (exam_id, student_id, final_grade, updated_at, source)
           VALUES (?, '0900001', 41.6, '2026-01-01', NULL)""",
        (exam_id,),
    )
    conn.commit()
    return offering


def test_the_status_line_says_scanned_scores_are_left_alone(qapp, conn, monkeypatch):
    """The obvious fear is that importing overwrites the grading work, so the
    dialog says plainly that it doesn't."""
    _with_scanned_scores(conn)
    d = _dialog(conn, monkeypatch, QMessageBox.StandardButton.No)
    d.semester_combo.setCurrentText("202610")

    status = d.semester_status.text()
    assert "1 scanned exam score(s)" in status
    assert "not changed by this import" in status


def test_importing_leaves_the_scanned_scores_untouched(qapp, conn, monkeypatch):
    offering = _with_scanned_scores(conn)
    d = _dialog(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    d.semester_combo.setCurrentText("202610")
    d._do_import()

    assert conn.execute(
        "SELECT final_grade FROM exam_scores"
    ).fetchone()["final_grade"] == 41.6
    assert store.count_course_grades(conn, offering) == 3
    assert conn.execute("SELECT COUNT(*) FROM historical_exam_scores").fetchone()[0] > 0


def test_the_record_gets_the_sheets_figure(qapp, conn, monkeypatch):
    _with_scanned_scores(conn)
    d = _dialog(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    d.semester_combo.setCurrentText("202610")
    d._do_import()

    assert conn.execute(
        "SELECT final_grade FROM historical_exam_scores WHERE student_id = '0900001'"
    ).fetchone()["final_grade"] == 34  # the sheet's Exam 1 Final


def test_only_the_close_out_question_is_asked(qapp, conn, monkeypatch):
    """Importing no longer replaces anything, so there is nothing else to ask."""
    d = _dialog(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    d.semester_combo.setCurrentText("202610")
    d._do_import()

    # Only the close-out question was asked.
    assert len(d.asked) == 1
    assert "as complete?" in d.asked[0]


def test_the_tab_bar_is_the_first_thing_on_every_screen(qapp, conn):
    """The exam-grading controls live inside the Results tab, not above the tab
    bar: there they pushed the tabs down the window on some screens and left
    them at the top on others."""
    from app.gui.main_window import MainWindow

    window = MainWindow(conn)
    central = window.centralWidget().layout()
    # Only the area label is above the tab bar: one line, always present and
    # always the same height, so the tabs don't move between screens.
    assert central.itemAt(0).widget() is window.area_label
    assert central.itemAt(1).widget() is window.tabs

    # And the controls are inside Results, so they can't hang over the record.
    assert window.exam_grading_group.parent() is window.results_view
    assert window.results_view.layout().itemAt(0).widget() is window.exam_grading_group


def test_the_area_label_says_which_job_you_are_in(qapp, conn):
    """EXAM or COURSE GRADES, in caps, because the two were easy to confuse."""
    from app.gui.main_window import MainWindow

    window = MainWindow(conn)
    for widget, expected in (
        (window.results_view, "EXAM"),
        (window.dashboard_view, "EXAM"),
        (window.history_view, "COURSE GRADES"),
        (window.reports_view, "COURSE GRADES"),
        (window.rec_letter_view, "COURSE GRADES"),
    ):
        window.tabs.setCurrentWidget(widget)
        assert window.area_label.text() == expected, window.tabs.tabText(
            window.tabs.currentIndex()
        )


def test_the_close_out_prompts_first_line_is_the_question(qapp, conn, monkeypatch):
    """The dialog's headline is its first line, so the question has to be it --
    the explanation belongs underneath. Weight and size are app/gui/dialogs.py's
    job and are tested in tests/test_dialogs.py."""
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No,
    )
    d = HistoricalImportDialog(conn)
    d.parsed = parse_course_export(TRIPLE)
    d.file_label.setText(TRIPLE.name)
    d._refresh_preview()
    d.semester_combo.setCurrentText("202610")
    d._do_import()

    headline, body = dialogs.split_rendered(asked[-1])
    assert headline == "Mark 202610 (Spring 2026) as complete?"
    assert body.startswith("Closed semesters count as")
    assert "re-import a sheet for a closed semester" in body
