"""Finding and merging students saved under two IDs (roadmap item 8)."""

import csv
from pathlib import Path

import pytest

from app.db import duplicates, store

NOW = "2026-01-01T00:00:00"
LATER = "2026-06-01T00:00:00"


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades_copy.db")


def _student(conn, student_id, first="Ann", last="Alpha"):
    conn.execute("INSERT INTO students VALUES (?, ?, ?)", (student_id, first, last))
    conn.commit()


def _exam(conn, semester, name, points=50):
    offering = store.get_or_create_course_offering(conn, semester)
    return offering, store.get_or_create_exam(conn, offering, name, points)


def _score(conn, exam_id, student_id, grade, record=False, updated_at=NOW):
    """A scanned score by default, or a class-record one with record=True.

    They are different tables now, so a student can hold both for one exam and
    neither clashes with the other."""
    if record:
        conn.execute(
            """INSERT INTO historical_exam_scores
                 (exam_id, student_id, final_grade, possible_points, updated_at)
               VALUES (?, ?, ?, 50, ?)""",
            (exam_id, student_id, grade, updated_at),
        )
    else:
        conn.execute(
            """INSERT INTO exam_scores (exam_id, student_id, final_grade, updated_at)
               VALUES (?, ?, ?, ?)""",
            (exam_id, student_id, grade, updated_at),
        )
    conn.commit()


def _course_grade(conn, offering_id, student_id, percent, updated_at=NOW):
    conn.execute(
        """INSERT INTO course_grades (course_offering_id, student_id, final_percent,
                                      grade_basis, updated_at)
           VALUES (?, ?, ?, 'adjusted', ?)""",
        (offering_id, student_id, percent, updated_at),
    )
    conn.commit()


# --- finding them -----------------------------------------------------------

def test_decimal_id_is_paired_with_its_clean_form(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    [group] = duplicates.find_duplicates(conn)
    assert (group.keep_id, group.other_id) == ("1234567", "1234567.0")
    assert group.confidence == duplicates.CERTAIN


def test_missing_leading_zero_is_paired(conn):
    _student(conn, "0123456")
    _student(conn, "123456")
    [group] = duplicates.find_duplicates(conn)
    assert (group.keep_id, group.other_id) == ("0123456", "123456")
    assert group.confidence == duplicates.CERTAIN


def test_same_name_different_ids_is_only_a_suggestion(conn):
    _student(conn, "7000001", "Cara", "Charlie")
    _student(conn, "7000002", "Cara", "Charlie")
    [group] = duplicates.find_duplicates(conn)
    assert group.confidence == duplicates.REVIEW
    assert group.reason == duplicates.REASON_SAME_NAME


def test_unrelated_students_are_not_paired(conn):
    _student(conn, "1234567", "Ann", "Alpha")
    _student(conn, "7654321", "Ben", "Bravo")
    assert duplicates.find_duplicates(conn) == []


def test_a_student_with_no_counterpart_is_not_paired(conn):
    """A short ID on its own isn't a duplicate -- there's nothing to merge it with."""
    _student(conn, "123456")
    assert duplicates.find_duplicates(conn) == []


def test_each_pair_is_reported_once(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    assert len(duplicates.find_duplicates(conn)) == 1  # not once per direction


def test_summary_lists_semesters_and_counts(conn):
    _student(conn, "1234567")
    offering, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "1234567", 40)
    _course_grade(conn, offering, "1234567", 88.0)

    summary = duplicates.summarize_student(conn, "1234567")
    assert summary.semesters == ["202610"]
    assert (summary.exam_scores, summary.course_grades) == (1, 1)
    assert summary.name == "Ann Alpha"


# --- conflicts --------------------------------------------------------------

def test_no_conflict_when_records_cover_different_exams(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _, exam1 = _exam(conn, "202610", "Exam 1")
    _, exam2 = _exam(conn, "202610", "Exam 2")
    _score(conn, exam1, "1234567", 40)
    _score(conn, exam2, "1234567.0", 44)
    assert duplicates.find_conflicts(conn, "1234567", "1234567.0") == []


def test_a_scanned_score_and_a_record_score_do_not_clash(conn):
    """Different populations, so both survive a merge: one is this semester's
    working data, the other is the class record."""
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "1234567", 42, record=True)
    _score(conn, exam_id, "1234567.0", 40)

    assert duplicates.find_conflicts(conn, "1234567", "1234567.0") == []

    duplicates.merge_students(conn, "1234567", "1234567.0")
    assert conn.execute(
        "SELECT final_grade FROM historical_exam_scores"
    ).fetchone()["final_grade"] == 42
    assert conn.execute("SELECT final_grade FROM exam_scores").fetchone()["final_grade"] == 40


def test_a_clash_within_one_population_prefers_the_newer_row(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "1234567", 42, record=True, updated_at=NOW)
    _score(conn, exam_id, "1234567.0", 40, record=True, updated_at=LATER)

    [conflict] = duplicates.find_conflicts(conn, "1234567", "1234567.0")
    assert conflict.kind == "class record exam"
    assert conflict.default == "other"
    assert "class record" in conflict.keep_value


def test_conflict_between_two_scanned_rows_prefers_the_newer(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "1234567", 42, updated_at=NOW)
    _score(conn, exam_id, "1234567.0", 40, updated_at=LATER)
    [conflict] = duplicates.find_conflicts(conn, "1234567", "1234567.0")
    assert conflict.kind == "scanned exam"
    assert conflict.default == "other"
    assert "newer" in conflict.why_default


def test_course_grade_conflict_is_reported_too(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    offering, _ = _exam(conn, "202610", "Exam 1")
    _course_grade(conn, offering, "1234567", 88.0, updated_at=NOW)
    _course_grade(conn, offering, "1234567.0", 91.0, updated_at=LATER)
    [conflict] = duplicates.find_conflicts(conn, "1234567", "1234567.0")
    assert conflict.kind == "course grade"
    assert conflict.default == "other"


# --- merging ----------------------------------------------------------------

def test_merge_moves_records_and_removes_the_duplicate(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    offering, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "1234567.0", 40)
    _course_grade(conn, offering, "1234567.0", 88.0)

    log = duplicates.merge_students(conn, "1234567", "1234567.0")

    assert [r["student_id"] for r in conn.execute("SELECT student_id FROM students")] == [
        "1234567"
    ]
    assert conn.execute(
        "SELECT student_id FROM exam_scores"
    ).fetchone()["student_id"] == "1234567"
    assert conn.execute(
        "SELECT student_id FROM course_grades"
    ).fetchone()["student_id"] == "1234567"
    assert any("moved 1 exam score(s) and 1 course grade(s)" in line for line in log)


def test_merge_keeps_the_default_side_of_a_conflict(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "1234567", 42, updated_at=LATER)
    _score(conn, exam_id, "1234567.0", 40, updated_at=NOW)

    duplicates.merge_students(conn, "1234567", "1234567.0")
    rows = list(conn.execute("SELECT student_id, final_grade FROM exam_scores"))
    assert [(r["student_id"], r["final_grade"]) for r in rows] == [("1234567", 42)]


def test_merge_honours_an_overridden_conflict_choice(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "1234567", 42, updated_at=LATER)
    _score(conn, exam_id, "1234567.0", 40, updated_at=NOW)

    duplicates.merge_students(
        conn, "1234567", "1234567.0",
        exam_choices={(duplicates.SCANNED, exam_id): "other"},
    )
    rows = list(conn.execute("SELECT student_id, final_grade FROM exam_scores"))
    assert [(r["student_id"], r["final_grade"]) for r in rows] == [("1234567", 40)]


def test_merge_fills_in_a_name_the_surviving_record_lacked(conn):
    conn.execute("INSERT INTO students VALUES ('1234567', NULL, NULL)")
    _student(conn, "1234567.0", "Ann", "Alpha")
    duplicates.merge_students(conn, "1234567", "1234567.0")
    row = conn.execute("SELECT first_name, last_name FROM students").fetchone()
    assert (row["first_name"], row["last_name"]) == ("Ann", "Alpha")


def test_merge_leaves_other_students_alone(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _student(conn, "7654321", "Ben", "Bravo")
    _, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "7654321", 33)

    duplicates.merge_students(conn, "1234567", "1234567.0")
    assert conn.execute(
        "SELECT final_grade FROM exam_scores WHERE student_id = '7654321'"
    ).fetchone()["final_grade"] == 33


def test_merge_combines_records_from_different_semesters(conn):
    _student(conn, "0123456")
    _student(conn, "123456")
    _, recent = _exam(conn, "202610", "Exam 1", 50)
    _, older = _exam(conn, "2195", "Exam 1", 80)
    _score(conn, recent, "0123456", 40)
    _score(conn, older, "123456", 60, record=True)

    duplicates.merge_students(conn, "0123456", "123456")
    rows = list(
        conn.execute(
            """SELECT co.semester_code, es.final_grade FROM exam_scores es
               JOIN exams e ON e.id = es.exam_id
               JOIN course_offerings co ON co.id = e.course_offering_id
               WHERE es.student_id = '0123456'
               UNION ALL
               SELECT co.semester_code, es.final_grade FROM historical_exam_scores es
               JOIN exams e ON e.id = es.exam_id
               JOIN course_offerings co ON co.id = e.course_offering_id
               WHERE es.student_id = '0123456' ORDER BY co.semester_code"""
        )
    )
    assert [(r["semester_code"], r["final_grade"]) for r in rows] == [
        ("202610", 40),
        ("2195", 60),
    ]


# --- the review tool ---------------------------------------------------------

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import duplicate_students as tool  # noqa: E402


@pytest.fixture
def db(conn, tmp_path):
    """Two provable duplicates with one conflict, plus a name-only suggestion."""
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _student(conn, "0123456", "Ben", "Bravo")
    _student(conn, "123456", "Ben", "Bravo")
    _student(conn, "7000001", "Cara", "Charlie")
    _student(conn, "7000002", "Cara", "Charlie")
    _, exam1 = _exam(conn, "202610", "Exam 1")
    _, exam2 = _exam(conn, "202610", "Exam 2")
    _score(conn, exam1, "1234567", 42, updated_at=LATER)
    _score(conn, exam1, "1234567.0", 40, updated_at=NOW)
    _score(conn, exam2, "123456", 30)
    conn.close()
    return tmp_path / "grades_copy.db"


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _set(path, predicate, column, value):
    rows = _rows(path)
    for row in rows:
        if predicate(row):
            row[column] = value
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_report_changes_nothing_and_prefills_only_certain_pairs(db, tmp_path, capsys):
    tool.main(["report", "--db", str(db)])

    review = _rows(tmp_path / tool.REVIEW_FILE)
    assert {r["action"] for r in review} == {"merge", "review"}
    assert [r["action"] for r in review if r["confidence"] == "review"] == ["review"]

    conflicts = _rows(tmp_path / tool.CONFLICTS_FILE)
    assert len(conflicts) == 1
    assert conflicts[0]["use_id"] == "1234567"  # the imported side, pre-selected

    conn = store.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 6


def test_apply_merges_only_what_is_marked_merge(db, tmp_path):
    tool.main(["report", "--db", str(db)])
    tool.main(["apply", "--db", str(db)])

    conn = store.connect(db)
    remaining = [r["student_id"] for r in conn.execute("SELECT student_id FROM students")]
    # The two certain pairs merged; the name-only pair was left alone.
    assert sorted(remaining) == ["0123456", "1234567", "7000001", "7000002"]


def test_apply_respects_a_flipped_conflict_choice(db, tmp_path):
    tool.main(["report", "--db", str(db)])
    _set(tmp_path / tool.CONFLICTS_FILE, lambda r: r["group"] == "1", "use_id", "1234567.0")
    tool.main(["apply", "--db", str(db)])

    conn = store.connect(db)
    grade = conn.execute(
        "SELECT final_grade FROM exam_scores WHERE student_id = '1234567'"
    ).fetchone()["final_grade"]
    assert grade == 40  # the app-graded value, chosen over the default


def test_apply_can_be_told_to_skip_a_pair(db, tmp_path):
    tool.main(["report", "--db", str(db)])
    _set(tmp_path / tool.REVIEW_FILE, lambda r: r["group"] == "1", "action", "skip")
    tool.main(["apply", "--db", str(db)])

    conn = store.connect(db)
    remaining = [r["student_id"] for r in conn.execute("SELECT student_id FROM students")]
    assert "1234567.0" in remaining  # untouched


def test_apply_approving_a_name_match_merges_it(db, tmp_path):
    tool.main(["report", "--db", str(db)])
    _set(tmp_path / tool.REVIEW_FILE, lambda r: r["confidence"] == "review", "action", "merge")
    tool.main(["apply", "--db", str(db)])

    conn = store.connect(db)
    remaining = [r["student_id"] for r in conn.execute("SELECT student_id FROM students")]
    assert "7000002" not in remaining


def test_apply_writes_a_log_of_every_change(db, tmp_path):
    tool.main(["report", "--db", str(db)])
    tool.main(["apply", "--db", str(db)])

    log = _rows(tmp_path / tool.LOG_FILE)
    assert {r["merged_id"] for r in log} == {"1234567.0", "123456"}
    assert any("deleted student record" in r["change"] for r in log)
    assert any("discarded" in r["change"] for r in log)


def test_apply_with_nothing_approved_makes_no_changes(db, tmp_path, capsys):
    tool.main(["report", "--db", str(db)])
    _set(tmp_path / tool.REVIEW_FILE, lambda r: True, "action", "skip")
    tool.main(["apply", "--db", str(db)])

    assert "No changes made." in capsys.readouterr().out
    assert not (tmp_path / tool.LOG_FILE).exists()


def test_tool_refuses_to_touch_the_live_database(db, tmp_path, monkeypatch):
    """The cleanup runs on a copy, so the original is always recoverable."""
    monkeypatch.setattr(store, "default_db_path", lambda: db)
    monkeypatch.setattr(tool.store, "default_db_path", lambda: db)
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["report", "--db", str(db)])
    assert "live database" in str(excinfo.value)


def test_apply_without_a_review_file_explains_what_to_do(db, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["apply", "--db", str(db)])
    assert "Run 'report' first" in str(excinfo.value)


def test_a_student_repeating_the_course_is_not_a_duplicate(conn):
    """One person, one ID, two semesters. That's a repeat, not a duplicate, and
    both semesters are theirs to keep."""
    _student(conn, "1234567")
    _, first = _exam(conn, "202610", "Exam 1")
    _, second = _exam(conn, "202630", "Exam 1")
    _score(conn, first, "1234567", 40, record=True)
    _score(conn, second, "1234567", 44, record=True)

    assert duplicates.find_duplicates(conn) == []

    summary = duplicates.summarize_student(conn, "1234567")
    assert summary.semesters == ["202610", "202630"]  # oldest first
    assert summary.exam_scores == 2  # both kept


def test_a_repeat_under_two_ids_is_still_a_duplicate(conn):
    """The repeat itself isn't the problem -- two IDs for one person is."""
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _, first = _exam(conn, "202610", "Exam 1")
    _, second = _exam(conn, "202630", "Exam 1")
    _score(conn, first, "1234567", 40, record=True)
    _score(conn, second, "1234567.0", 44, record=True)

    [group] = duplicates.find_duplicates(conn)
    assert (group.keep_id, group.other_id) == ("1234567", "1234567.0")
    # Different semesters, so nothing clashes -- the merge just combines them.
    assert duplicates.find_conflicts(conn, group.keep_id, group.other_id) == []
    duplicates.merge_students(conn, group.keep_id, group.other_id)
    assert conn.execute(
        "SELECT COUNT(*) FROM historical_exam_scores WHERE student_id = '1234567'"
    ).fetchone()[0] == 2


def test_a_merge_moves_both_populations(conn):
    _student(conn, "1234567")
    _student(conn, "1234567.0")
    _, exam_id = _exam(conn, "202610", "Exam 1")
    _score(conn, exam_id, "1234567.0", 40)  # scanned
    _, other = _exam(conn, "2195", "Exam 1")
    _score(conn, other, "1234567.0", 60, record=True)  # class record

    log = duplicates.merge_students(conn, "1234567", "1234567.0")

    assert conn.execute(
        "SELECT student_id FROM exam_scores"
    ).fetchone()["student_id"] == "1234567"
    assert conn.execute(
        "SELECT student_id FROM historical_exam_scores"
    ).fetchone()["student_id"] == "1234567"
    assert any("moved 2 exam score(s)" in line for line in log)


# --- guards added after the stability review ---------------------------------

def test_tool_refuses_the_production_database_even_from_a_dev_copy(db, tmp_path, monkeypatch):
    """In a development copy default_db_path() points at dev-data/, so the real
    Dropbox file used to pass the guard."""
    live_folder = tmp_path / "live"
    live_folder.mkdir()
    live = live_folder / "grades.db"
    db.rename(live)
    monkeypatch.setattr(tool.config, "DEFAULT_DATA_FOLDER", live_folder)
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["report", "--db", str(live)])
    assert "live database" in str(excinfo.value)


def test_tool_refuses_the_chosen_data_folder(db, tmp_path, monkeypatch):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    live = chosen / "grades.db"
    db.rename(live)
    monkeypatch.setattr(tool.config, "get_data_folder", lambda: chosen)
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["report", "--db", str(live)])
    assert "live database" in str(excinfo.value)


def test_tool_refuses_a_copy_inside_dropbox_unless_told(db, tmp_path):
    synced = tmp_path / "Dropbox" / "work"
    synced.mkdir(parents=True)
    copy = synced / "grades_copy.db"
    db.rename(copy)
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["report", "--db", str(copy)])
    assert "inside Dropbox" in str(excinfo.value)
    tool.main(["report", "--db", str(copy), "--allow-synced-folder"])  # explicit opt-in works
    assert (synced / tool.REVIEW_FILE).exists()


def test_apply_stops_on_an_id_excel_turned_into_a_stranger(db, tmp_path):
    tool.main(["report", "--db", str(db)])
    _set(tmp_path / tool.REVIEW_FILE, lambda r: r["merge_id"] == "123456", "merge_id", "9999999")
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["apply", "--db", str(db)])
    assert "9999999" in str(excinfo.value) and "not a student" in str(excinfo.value)
    # Checked before merging anything: the other approved pair is untouched too.
    c = store.connect(db)
    assert c.execute("SELECT COUNT(*) FROM students WHERE student_id = '1234567.0'").fetchone()[0] == 1
    assert not (tmp_path / tool.LOG_FILE).exists()


def test_apply_accepts_an_id_excel_only_reformatted(db, tmp_path):
    """"0123456" saved by Excel as 123456 -- but here 123456 is itself a stored
    duplicate, so it's taken verbatim; the correction applies only to an ID that
    doesn't exist as written."""
    tool.main(["report", "--db", str(db)])
    _set(tmp_path / tool.REVIEW_FILE, lambda r: r["keep_id"] == "1234567", "keep_id", "1234567.0")
    _set(tmp_path / tool.REVIEW_FILE, lambda r: r["merge_id"] == "1234567.0", "merge_id", "1234567")
    tool.main(["apply", "--db", str(db)])  # both exist verbatim: merge runs, just flipped
    c = store.connect(db)
    assert c.execute("SELECT COUNT(*) FROM students WHERE student_id = '1234567'").fetchone()[0] == 0


def test_apply_rejects_a_use_id_that_is_neither_side(db, tmp_path):
    tool.main(["report", "--db", str(db)])
    _set(tmp_path / tool.CONFLICTS_FILE, lambda r: True, "use_id", "7000001")
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["apply", "--db", str(db)])
    assert "neither" in str(excinfo.value)


def test_log_records_pairs_merged_before_a_failure(db, tmp_path, monkeypatch):
    tool.main(["report", "--db", str(db)])
    real = tool.duplicates.merge_students
    calls = []

    def flaky(conn, keep_id, other_id, *a, **k):
        calls.append(other_id)
        if len(calls) == 2:
            raise RuntimeError("database is locked")
        return real(conn, keep_id, other_id, *a, **k)

    monkeypatch.setattr(tool.duplicates, "merge_students", flaky)
    with pytest.raises(RuntimeError):
        tool.main(["apply", "--db", str(db)])

    log = _rows(tmp_path / tool.LOG_FILE)
    assert {r["merged_id"] for r in log} == {calls[0]}  # the first pair, and only the first
