"""Backup and restore from the Class History tab.

Restore replaces the contents of the database the app already has open, using
SQLite's backup API in reverse rather than copying a file over grades.db: the
connection every view holds stays valid, so nothing is left reading a file that
was swapped out underneath it.

Requires QT_QPA_PLATFORM=offscreen for the button tests.
"""

import sqlite3
from pathlib import Path

import pytest

from app.core.course_export import parse_course_export
from app.db import store

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from app.gui.main_window import MainWindow  # noqa: E402

SHEET = Path(__file__).parent / "fixtures" / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A live database at a real path, with default_db_path pointing at it."""
    path = tmp_path / "grades.db"
    monkeypatch.setattr(store, "default_db_path", lambda: path)
    monkeypatch.setattr("app.db.config.get_data_folder", lambda: tmp_path)
    return path


def _import(conn, code, source="sheet.csv"):
    parsed = parse_course_export(SHEET)
    resolved, _ = store.resolve_student_ids(conn, parsed.rows)
    offering = store.get_or_create_course_offering(conn, code)
    store.save_course_grades(conn, offering, resolved, source_file=source)
    store.save_historical_exam_scores(conn, offering, parsed, resolved, source_file=source)
    return offering


# --- describing a file before trusting it ------------------------------------

def test_describe_reports_what_a_database_holds(live):
    conn = store.connect(live)
    _import(conn, "202610")
    _import(conn, "202410")

    info = store.describe_database(live)
    assert info["semesters"] == 2
    assert info["semester_codes"] == ["202610", "202410"]  # newest first
    assert info["students"] == 3
    assert info["course_grades"] == 6


def test_a_file_that_is_not_ours_is_refused(tmp_path):
    """Picking the wrong .db must be reported, not restored over the grades."""
    other = tmp_path / "something_else.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE notes (id INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="not an exam-grader database"):
        store.describe_database(other)


def test_a_file_that_is_not_a_database_is_refused(tmp_path):
    junk = tmp_path / "notes_backup_20260101.db"
    junk.write_text("this is not a database at all")

    with pytest.raises(ValueError):
        store.describe_database(junk)


# --- backup ------------------------------------------------------------------

def test_backup_lands_beside_grades_db_with_a_date(live):
    conn = store.connect(live)
    _import(conn, "202610")

    dest = store.backup_database(live, live.parent)

    assert dest.parent == live.parent
    assert dest.name.startswith("grades_backup_")
    assert dest.name.endswith(".db")
    # The date is in the name, to the second, so two backups never collide.
    stamp = dest.stem.split(store.BACKUP_SUFFIX)[1]
    assert len(stamp) == 15 and stamp[8] == "_"  # YYYYmmdd_HHMMSS
    assert store.describe_database(dest)["semester_codes"] == ["202610"]


def test_list_backups_is_newest_first_and_skips_strangers(live):
    conn = store.connect(live)
    _import(conn, "202610")
    first = store.backup_database(live, live.parent)
    _import(conn, "202410")
    second = store.backup_database(live, live.parent)
    # Something else in the same folder, matching the glob but not ours.
    stranger = live.parent / "notes_backup_20200101_000000.db"
    stranger.write_text("not a database")

    found = store.list_backups(live.parent)
    assert {f["path"] for f in found} == {first, second}
    assert stranger not in {f["path"] for f in found}
    # Newest first: the second backup has the extra semester in it.
    assert found[0]["semesters"] >= found[-1]["semesters"]


# --- restore -----------------------------------------------------------------

def test_restore_replaces_the_contents_of_the_open_connection(live):
    conn = store.connect(live)
    _import(conn, "202610", source="Fall 2025 final.csv")
    backup = store.backup_database(live, live.parent)

    # Carry on working: a second semester, and the first one deleted.
    _import(conn, "202410")
    store.delete_semester(conn, store.get_or_create_course_offering(conn, "202610"))
    assert store.list_semester_codes(conn) == ["202410"]

    store.restore_database(conn, backup)

    # Same connection object, different contents -- no reconnect needed.
    assert store.list_semester_codes(conn) == ["202610"]
    assert conn.execute("SELECT COUNT(*) FROM course_grades").fetchone()[0] == 3
    # And it really is the file on disk that changed, not just this session.
    assert store.describe_database(live)["semester_codes"] == ["202610"]


def test_restore_can_still_be_written_to_afterwards(live):
    """A restored database has to be usable, not just readable."""
    conn = store.connect(live)
    _import(conn, "202610")
    backup = store.backup_database(live, live.parent)
    store.delete_semester(conn, store.get_or_create_course_offering(conn, "202610"))

    store.restore_database(conn, backup)
    _import(conn, "202430")

    assert set(store.list_semester_codes(conn)) == {"202610", "202430"}


def test_restoring_an_older_schema_adds_the_missing_columns(live, tmp_path):
    """A backup taken before a column existed must still open."""
    old = tmp_path / "grades_backup_20200101_000000.db"
    legacy = sqlite3.connect(old)
    legacy.executescript(
        """CREATE TABLE students (student_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT);
           CREATE TABLE course_offerings (id INTEGER PRIMARY KEY AUTOINCREMENT,
               semester_code TEXT NOT NULL, course_number TEXT NOT NULL,
               UNIQUE (semester_code, course_number));
           CREATE TABLE exams (id INTEGER PRIMARY KEY AUTOINCREMENT,
               course_offering_id INTEGER NOT NULL, exam_name TEXT NOT NULL,
               possible_points REAL, UNIQUE (course_offering_id, exam_name));
           CREATE TABLE course_grades (course_offering_id INTEGER NOT NULL,
               student_id TEXT NOT NULL, adjusted_numerator REAL,
               adjusted_denominator REAL, calculated_numerator REAL,
               calculated_denominator REAL, letter_grade TEXT, source_file TEXT,
               updated_at TEXT NOT NULL, PRIMARY KEY (course_offering_id, student_id));
           INSERT INTO course_offerings (semester_code, course_number) VALUES ('2201', '211');"""
    )
    legacy.commit()
    legacy.close()

    conn = store.connect(live)
    store.restore_database(conn, old)

    # closed_at and the component columns were added by the migration.
    assert store.list_semester_codes(conn) == ["2201"]
    assert store.in_progress_semester_codes(conn) == ["2201"]
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(course_grades)")}
    assert {"final_percent", "lab_earned", "other_possible"} <= columns


# --- through the buttons -----------------------------------------------------

def _window(conn, monkeypatch, answer=None):
    seen = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: seen.append((a[1], a[2])))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: seen.append((a[1], a[2])))
    if answer is not None:
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer)
    window = MainWindow(conn)
    window.seen = seen
    return window


def test_the_backup_button_needs_no_folder_prompt(qapp, live, monkeypatch):
    conn = store.connect(live)
    _import(conn, "202610")

    def no_prompt(*a, **k):
        raise AssertionError("the backup button must not ask for a folder")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", no_prompt)
    window = _window(conn, monkeypatch)
    window.history_view._backup()

    backups = list(live.parent.glob("grades_backup_*.db"))
    assert len(backups) == 1
    assert window.seen[-1][0] == "Backup complete"


def test_the_restore_button_backs_up_first_then_restores(qapp, live, monkeypatch):
    conn = store.connect(live)
    _import(conn, "202610", source="Fall 2025 final.csv")
    backup = store.backup_database(live, live.parent)
    _import(conn, "202410")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(backup), ""))
    window = _window(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    window.history_view._restore()

    assert store.list_semester_codes(conn) == ["202610"]
    # Two backups now: the one restored from, and the safety one taken first.
    safety = [p for p in live.parent.glob("grades_backup_*.db") if p != backup]
    assert len(safety) == 1
    assert store.describe_database(safety[0])["semesters"] == 2  # what was there before
    assert window.seen[-1][0] == "Restore complete"


def test_answering_no_restores_nothing(qapp, live, monkeypatch):
    conn = store.connect(live)
    _import(conn, "202610")
    backup = store.backup_database(live, live.parent)
    _import(conn, "202410")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(backup), ""))
    window = _window(conn, monkeypatch, QMessageBox.StandardButton.No)
    window.history_view._restore()

    assert set(store.list_semester_codes(conn)) == {"202610", "202410"}


def test_choosing_grades_db_itself_is_refused(qapp, live, monkeypatch):
    conn = store.connect(live)
    _import(conn, "202610")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(live), ""))
    window = _window(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    window.history_view._restore()

    assert window.seen[-1][0] == "That is the live database"
    assert store.list_semester_codes(conn) == ["202610"]


def test_a_failed_safety_backup_cancels_the_restore(qapp, live, monkeypatch):
    conn = store.connect(live)
    _import(conn, "202610")
    backup = store.backup_database(live, live.parent)
    _import(conn, "202410")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(backup), ""))
    window = _window(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    critical = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: critical.append(a[1]))
    monkeypatch.setattr(store, "backup_database", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    window.history_view._restore()

    assert set(store.list_semester_codes(conn)) == {"202610", "202410"}
    assert critical == ["Restore failed"]


def test_restoring_drops_an_exam_that_is_no_longer_there(qapp, live, monkeypatch):
    conn = store.connect(live)
    backup = store.backup_database(live, live.parent)   # empty database
    offering = _import(conn, "202610")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(backup), ""))
    window = _window(conn, monkeypatch, QMessageBox.StandardButton.Yes)
    window.current_exam_id = store.get_or_create_exam(conn, offering, "Exam 1", 50)
    window.history_view._restore()

    assert store.list_semester_codes(conn) == []
    assert window.current_exam_id is None
