from pathlib import Path

import pytest

from app.core.matching import exclude_duplicates, match_rows
from app.core.parsers import parse_exam_file
from app.db import store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


@pytest.fixture
def exam(conn):
    offering_id = store.get_or_create_course_offering(conn, "202610")
    exam_id = store.get_or_create_exam(conn, offering_id, "Exam 1", possible_points=50)
    return offering_id, exam_id


def _clean_individual_rows():
    parsed = parse_exam_file(FIXTURES / "synthetic_individual_round.csv")
    return exclude_duplicates(parsed.individual_rows, parsed.duplicate_student_ids["individual"])


def _group_rows():
    return parse_exam_file(FIXTURES / "synthetic_group_round.csv").group_rows


def test_single_round_save_flags_missing_other_round(conn, exam):
    _, exam_id = exam
    matched = match_rows(_clean_individual_rows(), [])
    store.save_matched_students(conn, exam_id, matched)

    assert store.round_already_saved(conn, exam_id, "individual") is not None
    assert store.round_already_saved(conn, exam_id, "group") is None

    row = conn.execute(
        "SELECT missing_flag, final_grade FROM exam_scores WHERE student_id = '1000001'"
    ).fetchone()
    assert row["missing_flag"] == "Missing Group"
    assert row["final_grade"] is None


def test_second_round_save_completes_the_record(conn, exam):
    _, exam_id = exam
    store.save_matched_students(conn, exam_id, match_rows(_clean_individual_rows(), []))

    existing_individual = store.get_existing_round_rows(conn, exam_id, "individual")
    matched = match_rows(existing_individual, _group_rows())
    store.save_matched_students(conn, exam_id, matched)

    row = conn.execute(
        "SELECT individual_score, raw_group_score, final_grade, missing_flag "
        "FROM exam_scores WHERE student_id = '1000001'"
    ).fetchone()
    assert row["individual_score"] == 40
    assert row["raw_group_score"] == 42
    assert row["final_grade"] == pytest.approx(0.6 * 40 + 0.4 * 42)
    assert row["missing_flag"] is None

    eve = conn.execute(
        "SELECT missing_flag, final_grade FROM exam_scores WHERE student_id = '1000005'"
    ).fetchone()
    assert eve["missing_flag"] == "Missing Individual"
    assert eve["final_grade"] is None


def test_reupload_overwrites_previous_round_data(conn, exam):
    _, exam_id = exam
    store.save_matched_students(conn, exam_id, match_rows(_clean_individual_rows(), []))
    store.save_matched_students(
        conn, exam_id, match_rows(store.get_existing_round_rows(conn, exam_id, "individual"), _group_rows())
    )

    before = conn.execute(
        "SELECT individual_score FROM exam_scores WHERE student_id = '1000001'"
    ).fetchone()["individual_score"]
    assert before == 40

    # Instructor re-uploads a corrected individual-round file (Alice's score fixed to 48).
    corrected_rows = _clean_individual_rows()
    for row in corrected_rows:
        if row.student_id == "1000001":
            row.score = 48

    existing_group = store.get_existing_round_rows(conn, exam_id, "group")
    store.save_matched_students(conn, exam_id, match_rows(corrected_rows, existing_group))

    after = conn.execute(
        "SELECT individual_score, raw_group_score FROM exam_scores WHERE student_id = '1000001'"
    ).fetchone()
    assert after["individual_score"] == 48
    assert after["raw_group_score"] == 42  # untouched, carried through from the prior save


def test_exam_pass_stats(conn, exam):
    _, exam_id = exam
    store.save_matched_students(
        conn, exam_id, match_rows(_clean_individual_rows(), _group_rows())
    )
    stats = store.get_exam_pass_stats(conn, exam_id)
    # Eve (missing individual) excluded; Alice/Bob/Dan all have final grades.
    assert stats["total"] == 3
    assert stats["passed"] + stats["failed"] == 3


def test_the_class_record_excludes_scanned_working_data(conn, exam):
    """Scanned exams are the current semester's working set, reachable through
    the Exam Grading section. The class record is what a course sheet stated,
    so a scan must not appear in it."""
    offering_id, exam_id = exam
    store.save_matched_students(
        conn, exam_id, match_rows(_clean_individual_rows(), _group_rows())
    )

    assert store.get_semester_summary(conn, offering_id) == []
    assert store.get_all_student_semester_scores(conn) == []


def test_the_class_record_shows_imported_scores(conn, exam):
    offering_id, exam_id = exam
    conn.execute("INSERT INTO students VALUES ('1000001', 'Alice', 'Anderson')")
    conn.execute(
        """INSERT INTO historical_exam_scores
             (exam_id, student_id, final_grade, possible_points, updated_at)
           VALUES (?, '1000001', 40.8, 50, '2026-01-01')""",
        (exam_id,),
    )
    conn.commit()

    summary = store.get_semester_summary(conn, offering_id)
    by_id = {row["student_id"]: row for row in summary}
    assert by_id["1000001"]["exams_counted"] == 1
    assert by_id["1000001"]["overall_percent"] == pytest.approx(40.8 / 50 * 100)

    all_scores = store.get_all_student_semester_scores(conn)
    assert any(r["student_id"] == "1000001" and r["semester_code"] == "202610" for r in all_scores)


def test_percentile_cutoffs_and_pass_rate_are_pure_functions():
    scores = [95, 90, 85, 80, 75, 70, 65, 60, 55, 50]  # 10 scores
    cutoffs = store.percentile_cutoffs(scores)
    assert cutoffs["top_10_pct"] == 95  # top 1 of 10
    assert cutoffs["top_5_pct"] == 95  # ceil(10*0.05)=1 -> still top 1
    assert cutoffs["top_1_pct"] == 95

    stats = store.pass_rate(scores)
    assert stats["passed"] == 6  # 95,90,85,80,75,70 >= 70
    assert stats["total"] == 10


def test_backup_database(tmp_path):
    db_path = tmp_path / "grades.db"
    conn = store.connect(db_path)
    conn.close()

    dest_dir = tmp_path / "backups"
    backup_path = store.backup_database(db_path, dest_dir)

    assert backup_path.exists()
    assert backup_path.parent == dest_dir
