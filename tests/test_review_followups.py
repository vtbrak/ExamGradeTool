"""Follow-ups from the October 2026 review: the live file on Dropbox, backups,
and exam names saved before normalisation."""

import sqlite3

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db import store


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# --- opening -------------------------------------------------------------------

def test_connect_can_refuse_to_create(tmp_path):
    with pytest.raises(FileNotFoundError):
        store.connect(tmp_path / "missing" / "grades.db", create=False)
    assert not (tmp_path / "missing").exists()  # not even the folder


def test_connect_sets_a_busy_timeout(tmp_path):
    """Two writers: the second waits instead of failing at once."""
    import threading
    import time

    path = tmp_path / "grades.db"
    b = store.connect(path)
    # `a` is committed from another thread, which sqlite3 only allows with
    # check_same_thread off; it stands in for Dropbox or a second copy of the app.
    a = sqlite3.connect(path, check_same_thread=False)
    a.execute("BEGIN IMMEDIATE")
    a.execute("INSERT INTO students (student_id) VALUES ('1000001')")

    def release():
        time.sleep(0.5)
        a.commit()

    threading.Thread(target=release).start()
    started = time.monotonic()
    with b:
        b.execute("INSERT INTO students (student_id) VALUES ('1000002')")  # waits for `a`
    assert time.monotonic() - started >= 0.4
    assert b.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 2


def test_open_database_asks_before_creating_a_missing_live_database(qapp, tmp_path, monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main, "is_development_data", lambda: False)
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.No,
    )
    path = tmp_path / "live" / "grades.db"
    assert app_main.open_database(path) is None
    assert asked == ["Grade database not found"]
    assert not path.exists()


def test_open_database_creates_when_told_to(qapp, tmp_path, monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main, "is_development_data", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    path = tmp_path / "live" / "grades.db"
    conn = app_main.open_database(path)
    assert conn is not None and path.exists()
    conn.close()


def test_open_database_creates_a_dev_copy_silently(qapp, tmp_path, monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main, "is_development_data", lambda: True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("should not ask"))
    conn = app_main.open_database(tmp_path / "dev-data" / "grades.db")
    assert conn is not None
    conn.close()


# --- backup ----------------------------------------------------------------------

def test_backup_is_a_consistent_copy_that_opens(tmp_path):
    path = tmp_path / "grades.db"
    conn = store.connect(path)
    conn.execute("INSERT INTO students (student_id, first_name) VALUES ('1000001', 'Ann')")
    conn.commit()
    # A transaction left open by another writer doesn't stop the backup, and
    # its uncommitted row isn't in it.
    conn.execute("BEGIN")
    conn.execute("INSERT INTO students (student_id) VALUES ('1000002')")

    dest = store.backup_database(path, tmp_path / "backups")
    conn.rollback()

    copy = sqlite3.connect(dest)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [r[0] for r in copy.execute("SELECT student_id FROM students")] == ["1000001"]
    copy.close()
    conn.close()


# --- exam names saved before normalisation ----------------------------------------

def _raw_db(tmp_path):
    """A database as an older version left it: exam names with the semester code."""
    path = tmp_path / "grades.db"
    conn = store.connect(path)
    conn.execute("INSERT INTO course_offerings (semester_code, course_number) VALUES ('202610', '211')")
    conn.execute("INSERT INTO students (student_id) VALUES ('1000001'), ('1000002')")
    conn.commit()
    return path, conn


def _score(conn, exam_id, student_id, final, updated_at):
    conn.execute(
        "INSERT INTO exam_scores (exam_id, student_id, final_grade, updated_at) VALUES (?, ?, ?, ?)",
        (exam_id, student_id, final, updated_at),
    )


def test_migration_renames_an_old_exam_name(tmp_path):
    path, conn = _raw_db(tmp_path)
    conn.execute("INSERT INTO exams (course_offering_id, exam_name, possible_points) VALUES (1, '202610 Exam 1', 50)")
    conn.commit()
    conn.close()

    conn = store.connect(path)  # reconnecting runs _migrate
    assert [r["exam_name"] for r in conn.execute("SELECT exam_name FROM exams")] == ["Exam 1"]
    # ...so the next import finds it instead of creating a second exam.
    assert store.get_or_create_exam(conn, 1, "202610 Exam 1", 50) == 1
    assert conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0] == 1


def test_migration_merges_into_an_existing_normalised_exam(tmp_path):
    path, conn = _raw_db(tmp_path)
    conn.execute("INSERT INTO exams (id, course_offering_id, exam_name, possible_points) VALUES (1, 1, '202610 Exam 1', NULL)")
    conn.execute("INSERT INTO exams (id, course_offering_id, exam_name, possible_points) VALUES (2, 1, 'Exam 1', 50)")
    _score(conn, 1, "1000001", 40, "2026-01-02")  # newer: survives the clash
    _score(conn, 2, "1000001", 30, "2026-01-01")
    _score(conn, 1, "1000002", 44, "2026-01-01")  # only under the old row: moves
    conn.commit()
    conn.close()

    conn = store.connect(path)
    exams = conn.execute("SELECT id, exam_name, possible_points FROM exams").fetchall()
    assert [(e["id"], e["exam_name"], e["possible_points"]) for e in exams] == [(2, "Exam 1", 50)]
    scores = {
        r["student_id"]: r["final_grade"]
        for r in conn.execute("SELECT student_id, final_grade FROM exam_scores WHERE exam_id = 2")
    }
    assert scores == {"1000001": 40, "1000002": 44}
    assert conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0] == 2


def test_migration_is_a_no_op_on_a_clean_database(tmp_path):
    path, conn = _raw_db(tmp_path)
    conn.execute("INSERT INTO exams (course_offering_id, exam_name) VALUES (1, 'Exam 1'), (1, 'Exam 2')")
    conn.commit()
    conn.close()
    conn = store.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0] == 2
    assert not conn.in_transaction
