"""Conflicts between the two import paths (scanned exams vs course sheets).

The scanned path writes app-graded scores from a Scantron file; the course-sheet
path writes the class record. Three ways they used to collide, each now fixed
and pinned here.
"""

from pathlib import Path

import pandas as pd
import pytest

from app.core.course_export import parse_course_export
from app.core.matching import match_rows
from app.core.parsers import parse_exam_file
from app.db import store


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "grades.db")
    yield c
    store.clear_exam_point_mismatches(c)


def _scan(conn, tmp_path, quiz_name="202630 Exam 1", points=50, individual=40, group=44):
    path = tmp_path / "scan.csv"
    pd.DataFrame(
        [
            {"QuizName": quiz_name, "QuizClass": "202630", "FirstName": "Ann",
             "LastName": "Alpha", "StudentID": "1234567", "Earned Points": individual,
             "Possible Points": points, "Key Version": "A"},
            {"QuizName": quiz_name, "QuizClass": "202630", "FirstName": "Ann",
             "LastName": "Alpha", "StudentID": "1234567", "Earned Points": group,
             "Possible Points": points, "Key Version": "C"},
        ]
    ).to_csv(path, index=False)
    parsed = parse_exam_file(path)
    offering = store.get_or_create_course_offering(conn, parsed.semester_code)
    exam_id = store.get_or_create_exam(conn, offering, parsed.exam_name, points)
    store.save_matched_students(
        conn, exam_id, match_rows(parsed.individual_rows, parsed.group_rows)
    )
    return offering, exam_id


def _sheet(conn, tmp_path, offering, max_points=50, final=35.2):
    def header(part):
        return f"Exam 1 - {part} Points Grade <Numeric MaxPoints:{max_points} Category:Exams>"

    path = tmp_path / "course.csv"
    pd.DataFrame(
        [{"OrgDefinedId": "1234567", "Last Name": "Alpha", "First Name": "Ann",
          header("Individual"): 26, header("Partner"): 44, header("Final"): final}]
    ).to_csv(path, index=False)
    store.save_historical_exam_scores(conn, offering, parse_course_export(path))


# --- 1. the two paths name the same exam differently -------------------------

def test_exam_names_are_normalised_so_one_exam_stays_one_exam(conn, tmp_path):
    """A Scantron QuizName of "202630 Exam 1" and a sheet column of "Exam 1"
    are the same exam. Unnormalised they became two rows, and the semester
    roster counted the student's one exam twice."""
    offering, _ = _scan(conn, tmp_path)
    _sheet(conn, tmp_path, offering)

    exams = [r["exam_name"] for r in conn.execute("SELECT exam_name FROM exams")]
    assert exams == ["Exam 1"]

    roster = store.get_semester_summary(conn, offering)
    assert [r["exams_counted"] for r in roster] == [1]
    # The sheet's value won, as the record should.
    assert round(roster[0]["overall_percent"], 1) == 70.4


def test_get_or_create_exam_normalises_the_name(conn):
    offering = store.get_or_create_course_offering(conn, "202630")
    first = store.get_or_create_exam(conn, offering, "202630 Exam 1", 50)
    again = store.get_or_create_exam(conn, offering, "Exam 1", 50)
    assert first == again


# --- 2. provenance has to stay truthful --------------------------------------

def test_a_scan_and_the_record_live_in_separate_tables(conn, tmp_path):
    """The split in one assertion: a scan writes working data, a sheet writes
    the class record, and neither is in the other's table. No flag to go stale,
    and no filter to forget."""
    offering, _ = _scan(conn, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM historical_exam_scores").fetchone()[0] == 0

    _sheet(conn, tmp_path, offering)
    assert conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM historical_exam_scores").fetchone()[0] == 1


def test_importing_a_sheet_leaves_the_scanned_scores_alone(conn, tmp_path):
    """What used to overwrite the grading work now cannot reach it."""
    offering, _ = _scan(conn, tmp_path)
    before = conn.execute("SELECT final_grade FROM exam_scores").fetchone()["final_grade"]

    _sheet(conn, tmp_path, offering, final=35.2)

    assert conn.execute(
        "SELECT final_grade FROM exam_scores"
    ).fetchone()["final_grade"] == before == 41.6
    assert conn.execute(
        "SELECT final_grade FROM historical_exam_scores"
    ).fetchone()["final_grade"] == 35.2


def test_rescanning_cannot_disturb_the_record(conn, tmp_path):
    offering, _ = _scan(conn, tmp_path)
    _sheet(conn, tmp_path, offering, final=35.2)

    _scan(conn, tmp_path, individual=48, group=48)

    assert conn.execute(
        "SELECT final_grade FROM historical_exam_scores"
    ).fetchone()["final_grade"] == 35.2  # untouched
    assert conn.execute("SELECT final_grade FROM exam_scores").fetchone()["final_grade"] == 48


def test_a_closed_semester_still_warns_before_a_scan_is_saved(conn, tmp_path):
    """Saving can't change the record any more, so the only thing left worth
    saying is that this semester's grades were marked complete."""
    offering, exam_id = _scan(conn, tmp_path)
    assert store.scan_save_warnings(conn, exam_id) == []

    _sheet(conn, tmp_path, offering)
    assert store.scan_save_warnings(conn, exam_id) == []  # the record isn't at risk

    store.set_semester_closed(conn, offering, True)
    [warning] = store.scan_save_warnings(conn, exam_id)
    assert "is closed" in warning
    assert "won't change the class record" in warning


# --- 3. a differing point value is reported, not silently kept ---------------

def test_a_differing_point_value_is_reported(conn, tmp_path):
    offering, _ = _scan(conn, tmp_path, points=50)
    _sheet(conn, tmp_path, offering, max_points=80)

    assert store.exam_point_mismatches(conn) == [("Exam 1", 50.0, 80.0)]
    # The exam keeps its recorded value: changing it would rescale every score
    # already saved against it.
    assert conn.execute("SELECT possible_points FROM exams").fetchone()[0] == 50


def test_matching_point_values_report_nothing(conn, tmp_path):
    offering, _ = _scan(conn, tmp_path, points=50)
    _sheet(conn, tmp_path, offering, max_points=50)
    assert store.exam_point_mismatches(conn) == []


def test_a_missing_point_value_is_filled_in_rather_than_flagged(conn):
    offering = store.get_or_create_course_offering(conn, "202630")
    exam_id = store.get_or_create_exam(conn, offering, "Exam 1", None)
    store.get_or_create_exam(conn, offering, "Exam 1", 50)
    assert conn.execute(
        "SELECT possible_points FROM exams WHERE id = ?", (exam_id,)
    ).fetchone()[0] == 50
    assert store.exam_point_mismatches(conn) == []


def test_mismatches_are_per_connection_and_clearable(conn, tmp_path):
    offering, _ = _scan(conn, tmp_path, points=50)
    _sheet(conn, tmp_path, offering, max_points=80)
    assert store.exam_point_mismatches(conn)

    store.clear_exam_point_mismatches(conn)
    assert store.exam_point_mismatches(conn) == []


# --- the warnings as the instructor meets them -------------------------------

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gui.main_window import MainWindow  # noqa: E402

COMBINED = Path(__file__).parent / "fixtures" / "synthetic_combined_round.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_saving_into_a_closed_semester_defaults_to_no(qapp, conn, tmp_path, monkeypatch):
    """A closed semester's prompt defaults to No, unlike the ordinary
    round-overwrite prompt which defaults to Yes."""
    offering, exam_id = _scan(conn, tmp_path)
    _sheet(conn, tmp_path, offering)
    store.set_semester_closed(conn, offering, True)

    asked = {}

    def question(parent, title, text, buttons=None, default=None):
        asked["text"] = text
        asked["default"] = default
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    window = MainWindow(conn)
    window._handle_file_dropped(COMBINED)
    window._run()
    window.current_exam_id = exam_id  # pretend this run is for the imported exam
    window.results_view._save_to_history()

    assert "is closed" in asked["text"]
    assert asked["default"] == QMessageBox.StandardButton.No
    # Declining changed nothing, and the record was never at risk either way.
    assert conn.execute(
        "SELECT final_grade FROM exam_scores WHERE student_id = '1234567'"
    ).fetchone()["final_grade"] == 41.6
    assert conn.execute(
        "SELECT final_grade FROM historical_exam_scores"
    ).fetchone()["final_grade"] == 35.2


def test_a_point_mismatch_still_interrupts_the_save(qapp, conn, tmp_path, monkeypatch):
    """A routine save only writes a status line, but a mismatch needs a
    decision, so it keeps its dialog."""
    offering, exam_id = _scan(conn, tmp_path, points=50)
    _sheet(conn, tmp_path, offering, max_points=80)

    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append((a[1], a[2])))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window = MainWindow(conn)
    window._handle_file_dropped(COMBINED)
    window._run()
    window.current_exam_id = exam_id
    window.results_view._save_to_history()

    title, body = shown[-1]
    assert title == "Point value mismatch"
    assert "recorded as out of 50, this file says 80" in body
    # The save itself is confirmed on screen rather than in a second dialog.
    assert "saved" in window.results_view.save_status_label.text().lower()
    # Reported once, then cleared.
    assert store.exam_point_mismatches(conn) == []
