"""SQLite persistence layer: students, course offerings, exams, and per-exam scores."""

import bisect
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from app.core.grading import PASS_THRESHOLD_PERCENT
from app.core.matching import MatchedStudent
from app.core.exams import exam_group_key, exam_sort_key, normalize_exam_name
from app.core.parsers import GROUP_KEY_VERSION, StudentScoreRow
from app.core.semesters import (
    chronological_sort_key,
    describe,
    find_existing_code,
    newest_first,
)
from app.db.config import get_data_folder

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_COURSE_NUMBER = "211"


def default_db_path() -> Path:
    # Lives inside the Dropbox-synced course folder (see app/db/config.py for
    # why this is computed independently of where the app itself is running
    # from), so it travels with the app to a new Mac.
    return get_data_folder() / "grades.db"


# How long a write waits for another writer (Dropbox syncing the file, a second
# copy of the app) before "database is locked" is raised. SQLite's default is
# 5 s; the sync client typically holds the file for well under that, but a long
# wait beats a failed save.
BUSY_TIMEOUT_SECONDS = 15.0


def connect(db_path, create: bool = True) -> sqlite3.Connection:
    """Open the database, creating it (and its folder) when `create` is set.

    `create=False` raises FileNotFoundError for a missing file. The app uses
    that for the live database: on a Mac where Dropbox hasn't finished syncing,
    silently creating an empty grades.db would split the record in two."""
    db_path = Path(db_path)
    if not create and not db_path.exists():
        raise FileNotFoundError(f"No database at {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an already-existing database up to the current schema.

    schema.sql is all CREATE TABLE IF NOT EXISTS, so brand-new databases are
    handled there; this only covers columns added to tables that already exist
    in a user's live grades.db."""
    _add_missing_columns(conn, "exam_scores", {"source": "TEXT"})
    _add_missing_columns(conn, "course_offerings", {"closed_at": "TEXT"})
    # Scores an older database kept in exam_scores under the imported flag are
    # the class record and belong in its table. Moved rather than left behind,
    # where no record view would ever find them again.
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(exam_scores)")}
    if "source" in columns:
        conn.execute(
            """INSERT OR IGNORE INTO historical_exam_scores
                 (exam_id, student_id, individual_score, raw_group_score,
                  final_grade, possible_points, updated_at)
               SELECT es.exam_id, es.student_id, es.individual_score,
                      es.raw_group_score, es.final_grade, e.possible_points,
                      es.updated_at
               FROM exam_scores es JOIN exams e ON e.id = es.exam_id
               WHERE es.source = ?""",
            (HISTORICAL_SOURCE,),
        )
        conn.execute("DELETE FROM exam_scores WHERE source = ?", (HISTORICAL_SOURCE,))
        conn.commit()
    _add_missing_columns(
        conn,
        "course_grades",
        {
            "grade_basis": "TEXT", "final_percent": "REAL",
            "lab_earned": "REAL", "lab_possible": "REAL",
            "extra_credit_earned": "REAL", "extra_credit_possible": "REAL",
            "other_earned": "REAL", "other_possible": "REAL",
        },
    )
    _normalize_exam_names(conn)


def _normalize_exam_names(conn: sqlite3.Connection) -> None:
    """Rename exams saved before names were normalised ("202610 Exam 1" ->
    "Exam 1"), merging into an existing row where the normalised name is taken.

    get_or_create_exam looks exams up by normalised name, so an old row under
    the raw name was never found again: the next import created a second
    "Exam 1" for the semester and the student was counted twice. When both rows
    exist, the scores move to the surviving row; a student with a score under
    both keeps the more recently updated one (the rule duplicates.py applies to
    the same clash between two student IDs)."""
    stale = [
        (r["id"], r["course_offering_id"], r["exam_name"], normalize_exam_name(r["exam_name"]))
        for r in conn.execute("SELECT id, course_offering_id, exam_name FROM exams")
    ]
    stale = [s for s in stale if s[3] and s[3] != s[2]]
    if not stale:
        return
    with conn:
        for exam_id, offering_id, raw, name in stale:
            survivor = conn.execute(
                "SELECT id FROM exams WHERE course_offering_id = ? AND exam_name = ? AND id != ?",
                (offering_id, name, exam_id),
            ).fetchone()
            if survivor is None:
                conn.execute("UPDATE exams SET exam_name = ? WHERE id = ?", (name, exam_id))
                continue
            keep = survivor["id"]
            for table in ("exam_scores", "historical_exam_scores"):
                # Where the same student has a row under both, the older one goes.
                conn.execute(
                    f"""DELETE FROM {table} WHERE exam_id = ? AND student_id IN (
                            SELECT a.student_id FROM {table} a JOIN {table} b
                              ON a.student_id = b.student_id
                            WHERE a.exam_id = ? AND b.exam_id = ? AND a.updated_at <= b.updated_at)""",
                    (exam_id, exam_id, keep),
                )
                conn.execute(
                    f"""DELETE FROM {table} WHERE exam_id = ? AND student_id IN (
                            SELECT student_id FROM {table} WHERE exam_id = ?)""",
                    (keep, exam_id),
                )
                conn.execute(f"UPDATE {table} SET exam_id = ? WHERE exam_id = ?", (keep, exam_id))
            conn.execute(
                "UPDATE exams SET possible_points = COALESCE(possible_points, "
                "(SELECT possible_points FROM exams WHERE id = ?)) WHERE id = ?",
                (exam_id, keep),
            )
            conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if not existing:  # table doesn't exist yet; schema.sql will have created it
        return
    added = False
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")
            added = True
    if added:
        conn.commit()


def backup_database(db_path, dest_folder) -> Path:
    db_path = Path(db_path)
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = dest_folder / f"{db_path.stem}{BACKUP_SUFFIX}{timestamp}{db_path.suffix}"
    # Never overwrite an existing backup. The name is only dated to the second,
    # and two backups within one second do happen: a restore takes a safety
    # backup immediately, which could otherwise land on the very file being
    # restored from and leave the restore reading back what it was replacing.
    attempt = 2
    while dest.exists():
        dest = dest_folder / f"{db_path.stem}{BACKUP_SUFFIX}{timestamp}-{attempt}{db_path.suffix}"
        attempt += 1
    # SQLite's online backup, not a file copy: it reads a consistent snapshot
    # even if a transaction is open on the file (ours, or Dropbox mid-sync),
    # and includes anything still in the rollback journal.
    source = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dest


BACKUP_SUFFIX = "_backup_"
# Tables a file must have to be one of this app's databases. Checked before a
# restore so picking the wrong .db reports a clear refusal instead of wiping
# the grades and leaving something unusable in their place.
REQUIRED_TABLES = ("students", "course_offerings", "exams", "course_grades")


def describe_database(db_path) -> dict:
    """What a database file holds, for naming it in a restore confirmation.

    Raises ValueError if the file isn't one of this app's databases."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"No database at {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ValueError(f"{db_path.name} could not be opened as a database: {exc}")
    try:
        # SQLite opens lazily, so a file that isn't a database only says so on
        # the first read -- which is here, not at connect.
        conn.row_factory = sqlite3.Row
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        if missing:
            raise ValueError(
                f"{db_path.name} is not an exam-grader database "
                f"(no {', '.join(missing)} table)."
            )
        counts = {
            label: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for label, table in (
                ("semesters", "course_offerings"),
                ("students", "students"),
                ("course_grades", "course_grades"),
            )
        }
        counts["semester_codes"] = newest_first(
            [r["semester_code"] for r in conn.execute("SELECT semester_code FROM course_offerings")]
        )
    except sqlite3.Error as exc:
        raise ValueError(f"{db_path.name} is not a readable database: {exc}")
    finally:
        conn.close()
    counts["path"] = db_path
    counts["modified"] = datetime.fromtimestamp(db_path.stat().st_mtime)
    return counts


def restore_database(conn: sqlite3.Connection, backup_path) -> dict:
    """Replace the open database's contents with a backup's, in place.

    Deliberately SQLite's backup API in reverse rather than copying the file
    over grades.db: the connection the whole app is holding stays valid, so no
    view is left reading a database that was swapped out from under it, and
    SQLite does the replacement as one atomic operation instead of leaving a
    half-written file if it fails.

    The schema is re-applied afterwards, so restoring a backup taken before a
    column was added gives a database the current code can read."""
    summary = describe_database(backup_path)  # refuses a file that isn't ours
    source = sqlite3.connect(f"file:{Path(backup_path)}?mode=ro", uri=True)
    try:
        source.backup(conn)
    finally:
        source.close()
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    conn.commit()
    return summary


def list_backups(folder) -> list:
    """Backups in a folder, newest first, as `describe_database` dicts.

    Files that aren't this app's databases are skipped rather than reported:
    the folder is the instructor's, and whatever else is in it is not an
    error."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob(f"*{BACKUP_SUFFIX}*.db"):
        try:
            found.append(describe_database(path))
        except (ValueError, FileNotFoundError, sqlite3.Error):
            continue
    return sorted(found, key=lambda d: d["modified"], reverse=True)


# ---------------------------------------------------------------------------
# Course offerings / exams
# ---------------------------------------------------------------------------

def resolve_semester_code(
    conn: sqlite3.Connection, semester_code: str, course_number: str = DEFAULT_COURSE_NUMBER
) -> str:
    """The code a semester is (or will be) saved under.

    If this semester is already saved under the other code format (``2201``
    vs ``202010``, both Spring 2020), the existing code is returned so the
    two don't become separate course offerings. Otherwise the code is
    returned unchanged."""
    existing = [
        r["semester_code"]
        for r in conn.execute(
            "SELECT semester_code FROM course_offerings WHERE course_number = ?",
            (course_number,),
        )
    ]
    return find_existing_code(semester_code, existing) or semester_code


def get_or_create_course_offering(
    conn: sqlite3.Connection,
    semester_code: str,
    course_number: str = DEFAULT_COURSE_NUMBER,
    commit: bool = True,
) -> int:
    """`commit=False` when called inside a `with conn:` block, so the new row is
    part of that transaction and rolls back with it."""
    semester_code = resolve_semester_code(conn, semester_code, course_number)
    row = conn.execute(
        "SELECT id FROM course_offerings WHERE semester_code = ? AND course_number = ?",
        (semester_code, course_number),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO course_offerings (semester_code, course_number) VALUES (?, ?)",
        (semester_code, course_number),
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def find_exam(
    conn: sqlite3.Connection,
    semester_code: str,
    exam_name: str,
    course_number: str = DEFAULT_COURSE_NUMBER,
) -> Optional[int]:
    """The id of an exam already saved under this semester and name, or None.

    A lookup only: nothing is created. Running a file must not leave an exam
    row behind until the instructor chooses to save it (see
    MainWindow.ensure_current_exam)."""
    semester_code = resolve_semester_code(conn, semester_code, course_number)
    row = conn.execute(
        """
        SELECT e.id FROM exams e
        JOIN course_offerings co ON co.id = e.course_offering_id
        WHERE co.semester_code = ? AND co.course_number = ? AND e.exam_name = ?
        """,
        (semester_code, course_number, normalize_exam_name(exam_name) or exam_name),
    ).fetchone()
    return row["id"] if row else None


def get_or_create_exam(
    conn: sqlite3.Connection,
    course_offering_id: int,
    exam_name: str,
    possible_points: Optional[float] = None,
    commit: bool = True,
) -> int:
    """Find or create an exam, under its normalised name.

    Both import paths come through here, and they name the same exam
    differently: a Scantron file's QuizName is "202630 Exam 1" while a course
    sheet's column is "Exam 1". Stored unnormalised, those are two rows for one
    exam -- which double-counts the student in that semester's roster. The name
    is normalised here, at the single point where exams are created, so the two
    paths land on the same row and a course sheet supersedes the scan as
    intended.

    A differing `possible_points` is recorded as a mismatch rather than
    silently ignored: keeping the old denominator with a new score yields a
    wrong percentage (35.2 stored against 50 reads as 70.4% when the sheet
    meant 44%). See `exam_point_mismatches`.

    `commit=False` when called inside a `with conn:` block (the save functions
    do this), so a failed save rolls the exam row back too."""
    exam_name = normalize_exam_name(exam_name) or exam_name
    row = conn.execute(
        "SELECT id, possible_points FROM exams WHERE course_offering_id = ? AND exam_name = ?",
        (course_offering_id, exam_name),
    ).fetchone()
    if row:
        if possible_points and not row["possible_points"]:
            conn.execute(
                "UPDATE exams SET possible_points = ? WHERE id = ?", (possible_points, row["id"])
            )
            if commit:
                conn.commit()
        elif (
            possible_points
            and row["possible_points"]
            and abs(row["possible_points"] - possible_points) > 1e-9
        ):
            _record_point_mismatch(conn, row["id"], row["possible_points"], possible_points)
        return row["id"]
    cur = conn.execute(
        "INSERT INTO exams (course_offering_id, exam_name, possible_points) VALUES (?, ?, ?)",
        (course_offering_id, exam_name, possible_points),
    )
    if commit:
        conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Round save / reload (drives the "one file at a time" + overwrite workflow)
# ---------------------------------------------------------------------------

def round_already_saved(conn: sqlite3.Connection, exam_id: int, round_name: str) -> Optional[str]:
    """Timestamp of the last save for this round ("individual"/"group") on this
    exam, or None if it's never been saved -- used to decide whether the
    "this will overwrite existing data" confirmation dialog should appear."""
    column = "individual_score" if round_name == "individual" else "raw_group_score"
    row = conn.execute(
        f"SELECT MAX(updated_at) AS ts FROM exam_scores "
        f"WHERE exam_id = ? AND {column} IS NOT NULL",
        (exam_id,),
    ).fetchone()
    return row["ts"] if row and row["ts"] else None


def get_existing_round_rows(conn: sqlite3.Connection, exam_id: int, round_name: str) -> list:
    """Reconstruct StudentScoreRow objects for a round already saved to
    history, so matching.match_rows can join a freshly-uploaded file for the
    *other* round against data that isn't in that file at all."""
    if round_name == "individual":
        query = """
            SELECT es.student_id, es.individual_score AS score,
                   es.individual_key_used AS key_version,
                   e.possible_points, s.first_name, s.last_name
            FROM exam_scores es
            JOIN students s ON s.student_id = es.student_id
            JOIN exams e ON e.id = es.exam_id
            WHERE es.exam_id = ? AND es.individual_score IS NOT NULL
        """
    else:
        query = """
            SELECT es.student_id, es.raw_group_score AS score,
                   NULL AS key_version,
                   e.possible_points, s.first_name, s.last_name
            FROM exam_scores es
            JOIN students s ON s.student_id = es.student_id
            JOIN exams e ON e.id = es.exam_id
            WHERE es.exam_id = ? AND es.raw_group_score IS NOT NULL
        """
    rows = conn.execute(query, (exam_id,)).fetchall()
    return [
        StudentScoreRow(
            student_id=r["student_id"],
            first_name=r["first_name"],
            last_name=r["last_name"],
            score=r["score"],
            possible_points=r["possible_points"],
            key_version=r["key_version"] if round_name == "individual" else GROUP_KEY_VERSION,
            round=round_name,
        )
        for r in rows
    ]


def save_matched_students(
    conn: sqlite3.Connection, exam_id: int, matched_students: Iterable[MatchedStudent]
) -> None:
    """Upsert a full set of already-joined/graded student records for an exam.

    Because matching.match_rows already merges the freshly-uploaded round
    with whatever the other round already had (from a file dropped in the
    same run, or from a previous save), this is always a full-row upsert --
    re-uploading a file for a round simply overwrites its column(s) here.

    The whole set is one transaction: either every student is saved or none
    is, so a failure part-way can't leave half an exam on disk.
    """
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for m in matched_students:
            _upsert_student(conn, m.student_id, m.first_name, m.last_name)
            g = m.grade
            conn.execute(
                """
                INSERT INTO exam_scores (
                    exam_id, student_id, individual_score, raw_group_score,
                    effective_group_score, final_grade, individual_key_used,
                    missing_flag, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exam_id, student_id) DO UPDATE SET
                    individual_score = excluded.individual_score,
                    raw_group_score = excluded.raw_group_score,
                    effective_group_score = excluded.effective_group_score,
                    final_grade = excluded.final_grade,
                    individual_key_used = excluded.individual_key_used,
                    missing_flag = excluded.missing_flag,
                    updated_at = excluded.updated_at
                """,
                (
                    exam_id,
                    m.student_id,
                    m.individual_score,
                    m.raw_group_score,
                    g.effective_group_score,
                    g.final_grade,
                    m.individual_key_used,
                    g.missing_flag,
                    now,
                ),
            )


# ---------------------------------------------------------------------------
# Per-exam stats
# ---------------------------------------------------------------------------

def get_exam_pass_stats(conn: sqlite3.Connection, exam_id: int) -> dict:
    rows = conn.execute(
        """
        SELECT es.final_grade / e.possible_points * 100 AS percent
        FROM exam_scores es
        JOIN exams e ON e.id = es.exam_id
        WHERE es.exam_id = ? AND es.final_grade IS NOT NULL AND e.possible_points > 0
        """,
        (exam_id,),
    ).fetchall()
    percents = [r["percent"] for r in rows]
    passed = sum(1 for p in percents if p >= PASS_THRESHOLD_PERCENT)
    return {"passed": passed, "failed": len(percents) - passed, "total": len(percents), "percents": percents}


# ---------------------------------------------------------------------------
# Semester / historical summaries
# ---------------------------------------------------------------------------

def get_semester_summary(conn: sqlite3.Connection, course_offering_id: int) -> list:
    """Per-student overall course score for one semester: the simple
    (unweighted) average, across that student's exams in this course
    offering, of each exam's Final Grade expressed as a percentage -- percent
    rather than raw points because different exams aren't guaranteed to share
    the same possible-points total.
    This is the class record for the semester, so it reads only what a course
    sheet stated. The semester's scanned exam scores are the working set and
    live behind the Exam Grading section; they are deliberately not averaged
    into the course record.
    """
    rows = conn.execute(
        """
        SELECT s.student_id, s.first_name, s.last_name,
               AVG(es.final_grade / es.possible_points * 100) AS overall_percent,
               COUNT(es.final_grade) AS exams_counted
        FROM historical_exam_scores es
        JOIN exams e ON e.id = es.exam_id
        JOIN students s ON s.student_id = es.student_id
        WHERE e.course_offering_id = ? AND es.final_grade IS NOT NULL
          AND es.possible_points > 0
        GROUP BY s.student_id
        ORDER BY overall_percent DESC
        """,
        (course_offering_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_student_semester_scores(conn: sqlite3.Connection) -> list:
    """Every (course_offering, student) overall-score pair across all
    semesters -- the pool historical percentiles/pass-rate are drawn from. A
    student who took the course twice contributes one entry per semester.
    Record data only -- scanned working data is excluded, so a half-graded
    current semester can't skew a cross-semester figure.
    """
    rows = conn.execute(
        """
        SELECT co.id AS course_offering_id, co.semester_code,
               s.student_id, s.first_name, s.last_name,
               AVG(es.final_grade / es.possible_points * 100) AS overall_percent
        FROM historical_exam_scores es
        JOIN exams e ON e.id = es.exam_id
        JOIN course_offerings co ON co.id = e.course_offering_id
        JOIN students s ON s.student_id = es.student_id
        WHERE es.final_grade IS NOT NULL AND es.possible_points > 0
        GROUP BY co.id, s.student_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def percentile_cutoffs(scores: list) -> dict:
    """The minimum score needed to fall within the top 10% / 5% / 1% of `scores`."""
    if not scores:
        return {"top_10_pct": None, "top_5_pct": None, "top_1_pct": None}
    ordered = sorted(scores, reverse=True)
    n = len(ordered)

    def cutoff(fraction: float) -> float:
        count = max(1, math.ceil(n * fraction))
        return ordered[count - 1]

    return {
        "top_10_pct": cutoff(0.10),
        "top_5_pct": cutoff(0.05),
        "top_1_pct": cutoff(0.01),
    }


def pass_rate(scores: list) -> dict:
    passed = sum(1 for s in scores if s >= PASS_THRESHOLD_PERCENT)
    return {"passed": passed, "failed": len(scores) - passed, "total": len(scores)}


# ---------------------------------------------------------------------------
# Course-grade export import (D2L course sheets)
# ---------------------------------------------------------------------------

# Kept only so an older database still opens: the `source` column it refers to
# is no longer written or read. Which table a score is in is its provenance now
# -- exam_scores is scanned working data, historical_exam_scores is the class
# record -- so a flag cannot go stale and mislead.
HISTORICAL_SOURCE = "historical_import"


def _upsert_student(conn: sqlite3.Connection, student_id, first_name, last_name) -> None:
    conn.execute(
        """
        INSERT INTO students (student_id, first_name, last_name)
        VALUES (?, ?, ?)
        ON CONFLICT(student_id) DO UPDATE SET
            first_name = COALESCE(excluded.first_name, students.first_name),
            last_name = COALESCE(excluded.last_name, students.last_name)
        """,
        (student_id, first_name, last_name),
    )


def resolve_student_ids(conn: sqlite3.Connection, rows: Iterable) -> tuple:
    """Fill in `student_id` on course-export rows that don't carry one.

    Real D2L exports lead with OrgDefinedId, so this is a fallback for sheets
    where that column is blank: it matches on last+first name against students
    already known to the database. Returns `(resolved, unresolved)`; a name that
    matches more than one student is left unresolved rather than guessed at."""
    by_name: dict = {}
    for row in conn.execute("SELECT student_id, first_name, last_name FROM students"):
        if not row["first_name"] or not row["last_name"]:
            continue
        key = (row["last_name"].strip().lower(), row["first_name"].strip().lower())
        by_name.setdefault(key, []).append(row["student_id"])

    resolved, unresolved = [], []
    for row in rows:
        if row.student_id:
            resolved.append(row)
            continue
        if not (row.last_name and row.first_name):
            unresolved.append(row)
            continue
        candidates = by_name.get((row.last_name.strip().lower(), row.first_name.strip().lower()), [])
        if len(candidates) == 1:
            row.student_id = candidates[0]
            resolved.append(row)
        else:
            unresolved.append(row)
    return resolved, unresolved


def _component_values(row) -> tuple:
    """(lab earned, lab possible, extra credit…, other…) for one student."""
    from app.core.course_export import (
        COMPONENT_EXTRA_CREDIT,
        COMPONENT_LAB,
        COMPONENT_OTHER,
    )

    values = []
    for component in (COMPONENT_LAB, COMPONENT_EXTRA_CREDIT, COMPONENT_OTHER):
        total = (row.components or {}).get(component)
        values.extend((total.earned, total.possible) if total else (None, None))
    return tuple(values)


def save_course_grades(
    conn: sqlite3.Connection,
    course_offering_id: int,
    rows: Iterable,
    source_file: Optional[str] = None,
) -> int:
    """Upsert overall course grades for a semester (roadmap item 1).

    Kept in its own table rather than folded into exam_scores: this is the
    semester-level outcome (lab + exams + quizzes + everything else), not an
    exam-level record.

    One transaction for the whole sheet: a row that fails part-way rolls back
    every row before it, so the semester is never half-imported."""
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    with conn:
        for row in rows:
            if not row.student_id:
                continue
            _upsert_student(conn, row.student_id, row.first_name, row.last_name)
            conn.execute(
                """
                INSERT INTO course_grades (
                    course_offering_id, student_id, adjusted_numerator,
                    adjusted_denominator, calculated_numerator, calculated_denominator,
                    grade_basis, final_percent,
                    lab_earned, lab_possible, extra_credit_earned,
                    extra_credit_possible, other_earned, other_possible,
                    letter_grade, source_file, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(course_offering_id, student_id) DO UPDATE SET
                    adjusted_numerator = excluded.adjusted_numerator,
                    adjusted_denominator = excluded.adjusted_denominator,
                    calculated_numerator = excluded.calculated_numerator,
                    calculated_denominator = excluded.calculated_denominator,
                    grade_basis = excluded.grade_basis,
                    final_percent = excluded.final_percent,
                    lab_earned = excluded.lab_earned,
                    lab_possible = excluded.lab_possible,
                    extra_credit_earned = excluded.extra_credit_earned,
                    extra_credit_possible = excluded.extra_credit_possible,
                    other_earned = excluded.other_earned,
                    other_possible = excluded.other_possible,
                    letter_grade = excluded.letter_grade,
                    source_file = excluded.source_file,
                    updated_at = excluded.updated_at
                """,
                (
                    course_offering_id,
                    row.student_id,
                    row.adjusted_numerator,
                    row.adjusted_denominator,
                    row.calculated_numerator,
                    row.calculated_denominator,
                    row.grade_basis,
                    row.percent,
                    *_component_values(row),
                    # No letter grade: the sheet is authoritative, and assigning one
                    # here would be the app inventing a value the file doesn't carry.
                    None,
                    source_file,
                    now,
                ),
            )
            saved += 1
    return saved


def save_historical_exam_scores(
    conn: sqlite3.Connection,
    course_offering_id: int,
    parsed,
    rows: Iterable = None,
    source_file: Optional[str] = None,
) -> dict:
    """Backfill exam-level history out of a course-grade sheet (roadmap item 6).

    `final_grade` is taken straight from the sheet rather than recomputed from
    the Individual/Group blend. The sheet is the historical record of what the
    students were actually given, and it can embed adjustments the formula in
    grading.py doesn't know about -- observed in real data as a flat +2 curve
    across one whole exam plus the occasional per-student tweak. Older sheets
    go further and record only one score per exam, with no Individual/Group
    breakdown to blend in the first place. Rows written this way are tagged
    `source = HISTORICAL_SOURCE` so they stay distinguishable from scores the
    app graded itself.

    One transaction, exam rows included: a failure part-way leaves neither
    new exams nor any scores behind.
    """
    now = datetime.now(timezone.utc).isoformat()
    rows = parsed.rows if rows is None else rows
    counts = {"students": 0, "scores": 0, "exams": {}}

    _forget_point_mismatches(conn)
    with conn:
        exam_ids = {}
        for entry in parsed.exam_columns:
            exam_ids[entry.exam_name] = get_or_create_exam(
                conn, course_offering_id, entry.exam_name, entry.possible_points, commit=False
            )

        for row in rows:
            if not row.student_id:
                continue
            _upsert_student(conn, row.student_id, row.first_name, row.last_name)
            counts["students"] += 1
            for exam_name, score in row.exams.items():
                exam_id = exam_ids.get(exam_name)
                if exam_id is None or score.final_score is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO historical_exam_scores (
                        exam_id, student_id, individual_score, raw_group_score,
                        final_grade, possible_points, source_file, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(exam_id, student_id) DO UPDATE SET
                        individual_score = excluded.individual_score,
                        raw_group_score = excluded.raw_group_score,
                        final_grade = excluded.final_grade,
                        possible_points = excluded.possible_points,
                        source_file = excluded.source_file,
                        updated_at = excluded.updated_at
                    """,
                    (
                        exam_id,
                        row.student_id,
                        score.individual_score,
                        score.raw_group_score,
                        score.final_score,
                        # The sheet's own MaxPoints: the denominator the record's
                        # percentages must use.
                        score.possible_points,
                        source_file,
                        now,
                    ),
                )
                counts["scores"] += 1
                counts["exams"][exam_name] = counts["exams"].get(exam_name, 0) + 1
    return counts


def get_course_grades(conn: sqlite3.Connection, course_offering_id: int) -> list:
    return list(
        conn.execute(
            """
            SELECT cg.student_id, s.first_name, s.last_name,
                   cg.adjusted_numerator, cg.adjusted_denominator,
                   cg.calculated_numerator, cg.calculated_denominator,
                   cg.grade_basis, cg.final_percent, cg.letter_grade, cg.updated_at
            FROM course_grades cg
            JOIN students s ON s.student_id = cg.student_id
            WHERE cg.course_offering_id = ?
            ORDER BY s.last_name, s.first_name
            """,
            (course_offering_id,),
        )
    )


def list_semester_codes(conn: sqlite3.Connection) -> list:
    """Every saved semester code, newest first in calendar order (not text order)."""
    rows = conn.execute("SELECT DISTINCT semester_code FROM course_offerings").fetchall()
    return sorted(
        (r["semester_code"] for r in rows), key=chronological_sort_key, reverse=True
    )


def get_all_course_grades(conn: sqlite3.Connection) -> list:
    rows = list(
        conn.execute(
            """
            SELECT co.semester_code, co.course_number, cg.student_id,
                   s.first_name, s.last_name, cg.adjusted_numerator,
                   cg.adjusted_denominator, cg.grade_basis, cg.final_percent, cg.letter_grade
            FROM course_grades cg
            JOIN course_offerings co ON co.id = cg.course_offering_id
            JOIN students s ON s.student_id = cg.student_id
            ORDER BY s.last_name, s.first_name
            """
        )
    )
    # Semester ordering is done here rather than in SQL: text ordering puts
    # "2201" (Spring 2020) after "202610" (Spring 2026). The sort is stable,
    # so rows stay in name order within each semester.
    return sorted(rows, key=lambda r: chronological_sort_key(r["semester_code"]))


# ---------------------------------------------------------------------------
# Class rank (roadmap item 7) -- percentage only, pooled across all semesters
# ---------------------------------------------------------------------------

# The five tiers asked for, ordered most selective first.
RANK_TIERS = (0.001, 0.01, 0.05, 0.10, 0.20)

# Below this many pooled records the top 0.1% tier resolves to a single student
# -- i.e. it is just "the highest score" wearing a more precise-looking label --
# so it is reported as provisional rather than as a real cutoff.
MIN_POOL_FOR_TOP_TENTH_PERCENT = 1000


def tier_label(fraction: float) -> str:
    return f"Top {fraction * 100:g}%"


def get_course_grade_pool(conn: sqlite3.Connection) -> list:
    """Every semester's overall course grade, as percentages, highest first.

    This is the pool class rank is measured against. Percentages only: a course
    grade's numerator/denominator pair has a per-student denominator (D2L drops
    exempted items), so raw numerators aren't comparable even within one
    semester."""
    rows = conn.execute(
        "SELECT final_percent FROM course_grades "
        "WHERE final_percent IS NOT NULL ORDER BY final_percent DESC"
    ).fetchall()
    return [r["final_percent"] for r in rows]


def class_rank_cutoffs(scores: list) -> list:
    """The percentage needed to reach each of the five class-rank tiers.

    Returns one dict per tier: fraction, label, cutoff percent, how many
    students that tier covers, and whether the pool is big enough to support it.
    Ties are not split -- the nth-highest score is taken outright, so a tie
    spanning a cutoff can leave slightly more than N% of students at or above
    it."""
    ordered = sorted(scores, reverse=True)
    n = len(ordered)
    results = []
    for fraction in RANK_TIERS:
        if not n:
            results.append(
                {
                    "fraction": fraction,
                    "label": tier_label(fraction),
                    "cutoff": None,
                    "students": 0,
                    "supported": False,
                }
            )
            continue
        count = max(1, math.ceil(n * fraction))
        supported = not (
            fraction == 0.001 and n < MIN_POOL_FOR_TOP_TENTH_PERCENT
        )
        results.append(
            {
                "fraction": fraction,
                "label": tier_label(fraction),
                "cutoff": ordered[count - 1],
                "students": count,
                "supported": supported,
            }
        )
    return results


def rank_for_percent(
    scores: list, percent: Optional[float], cutoffs: Optional[list] = None
) -> Optional[dict]:
    """Where one percentage falls in the pool: its rank, and the most selective
    tier it reaches (None if it doesn't reach even the widest tier).

    `cutoffs` is `class_rank_cutoffs(scores)` when the caller already has it:
    a view ranking many grades against one pool computes it once instead of
    re-sorting the pool and re-deriving five tiers per grade."""
    if percent is None or not scores:
        return None
    n = len(scores)
    # Count scores strictly above `percent` with one binary search on an
    # ascending copy; the sort is O(n log n) once, the count O(log n).
    ascending = sorted(scores)
    better = n - bisect.bisect_right(ascending, percent)
    rank = better + 1
    reached = None
    for tier in cutoffs if cutoffs is not None else class_rank_cutoffs(scores):
        # Unsupported tiers are skipped rather than claimed: a 0.1% label drawn
        # from a few hundred records means only "top scorer", and this figure
        # is headed for recommendation letters.
        if not tier["supported"] or tier["cutoff"] is None:
            continue
        if percent >= tier["cutoff"]:
            reached = tier
            break
    return {
        "rank": rank,
        "pool": n,
        "percentile": (n - better) / n * 100,
        "tier": reached,
        # What to show a reader: the tier reached, or that they are outside the
        # widest one. The rank number itself isn't shown -- the tier is the
        # useful fact for a letter.
        "label": reached["label"] if reached else f"below {RANK_TIERS[-1] * 100:g}%",
    }


# ---------------------------------------------------------------------------
# Student lookup (rec-letter workflow)
# ---------------------------------------------------------------------------

def search_students(conn: sqlite3.Connection, query: str, limit: int = 50) -> list:
    """Find students by name or ID. Matches anywhere in either name or the id."""
    query = (query or "").strip()
    if not query:
        return []
    # Typed text is matched literally: without the escape, "%" alone matched
    # every student and "_" any single character.
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    return list(
        conn.execute(
            r"""
            SELECT s.student_id, s.first_name, s.last_name,
                   COUNT(DISTINCT cg.course_offering_id) AS semesters
            FROM students s
            LEFT JOIN course_grades cg ON cg.student_id = s.student_id
            WHERE s.last_name LIKE ? ESCAPE '\' OR s.first_name LIKE ? ESCAPE '\'
               OR s.student_id LIKE ? ESCAPE '\'
               OR (s.first_name || ' ' || s.last_name) LIKE ? ESCAPE '\'
            GROUP BY s.student_id
            ORDER BY s.last_name, s.first_name
            LIMIT ?
            """,
            (like, like, like, like, limit),
        )
    )


def get_student_course_grades(conn: sqlite3.Connection, student_id: str) -> list:
    rows = list(
        conn.execute(
            """
            SELECT co.semester_code, cg.final_percent, cg.grade_basis,
                   cg.adjusted_numerator, cg.adjusted_denominator,
                   cg.calculated_numerator, cg.calculated_denominator,
                   cg.lab_earned, cg.lab_possible,
                   cg.extra_credit_earned, cg.extra_credit_possible,
                   cg.other_earned, cg.other_possible
            FROM course_grades cg
            JOIN course_offerings co ON co.id = cg.course_offering_id
            WHERE cg.student_id = ?
            """,
            (student_id,),
        )
    )
    return sorted(rows, key=lambda r: chronological_sort_key(r["semester_code"]), reverse=True)


def get_student_exam_scores(conn: sqlite3.Connection, student_id: str) -> list:
    """Per-exam history for one student, raw score and percentage together."""
    rows = list(
        conn.execute(
            """
            SELECT co.semester_code, e.exam_name, es.possible_points,
                   es.final_grade,
                   CASE WHEN es.possible_points > 0
                        THEN es.final_grade / es.possible_points * 100 END AS percent
            FROM historical_exam_scores es
            JOIN exams e ON e.id = es.exam_id
            JOIN course_offerings co ON co.id = e.course_offering_id
            WHERE es.student_id = ? AND es.final_grade IS NOT NULL
            ORDER BY e.exam_name
            """,
            (student_id,),
        )
    )
    # Newest semester first. The sort is stable, so exams stay in name order
    # within each semester.
    return sorted(rows, key=lambda r: chronological_sort_key(r["semester_code"]), reverse=True)


# ---------------------------------------------------------------------------
# One exam across every semester (roadmap item 5)
# ---------------------------------------------------------------------------

def list_exam_names(conn: sqlite3.Connection) -> list:
    """Distinct exam names across all semesters, in natural order.

    Names are normalised first: a Scantron export stores "202610 Exam 4" while
    a historical import stores "Exam 4", and those are the same exam."""
    rows = conn.execute("SELECT DISTINCT exam_name FROM exams").fetchall()
    names = {}
    for row in rows:
        name = normalize_exam_name(row["exam_name"])
        if name:
            names.setdefault(exam_group_key(name), name)
    return sorted(names.values(), key=exam_sort_key)


def get_exam_history(conn: sqlite3.Connection, exam_name: str) -> list:
    """Every saved score for one exam, from every semester it was given.

    Rows are ordered oldest semester first, then by name, and carry the raw
    score with its possible points and the percentage -- the percentage being
    the comparable figure, since the same exam has been out of 50, 72 and 80
    points in different semesters."""
    wanted = exam_group_key(exam_name)
    exam_ids = [
        row["id"]
        for row in conn.execute("SELECT id, exam_name FROM exams")
        if exam_group_key(row["exam_name"]) == wanted
    ]
    if not exam_ids:
        return []

    placeholders = ",".join("?" * len(exam_ids))
    rows = list(
        conn.execute(
            f"""
            SELECT co.semester_code, e.exam_name, es.possible_points,
                   es.student_id, s.first_name, s.last_name, es.final_grade,
                   CASE WHEN es.possible_points > 0
                        THEN es.final_grade / es.possible_points * 100 END AS percent
            FROM historical_exam_scores es
            JOIN exams e ON e.id = es.exam_id
            JOIN course_offerings co ON co.id = e.course_offering_id
            JOIN students s ON s.student_id = es.student_id
            WHERE es.exam_id IN ({placeholders}) AND es.final_grade IS NOT NULL
            ORDER BY s.last_name, s.first_name
            """,
            exam_ids,
        )
    )
    # Semester ordering in Python: text ordering would put "2201" (Spring 2020)
    # after "202610" (Spring 2026). Stable, so names stay ordered within a semester.
    return sorted(rows, key=lambda r: chronological_sort_key(r["semester_code"]))


# ---------------------------------------------------------------------------
# Semester state: in progress vs closed (roadmap item 15)
# ---------------------------------------------------------------------------

IN_PROGRESS_SUFFIX = "in progress"


def set_semester_closed(conn: sqlite3.Connection, course_offering_id: int, closed: bool) -> None:
    """Mark a semester's grades complete, or put it back to in progress.

    Closing is a label on the record, not a lock: a closed semester can still
    be re-imported over, so a late correction is never blocked."""
    conn.execute(
        "UPDATE course_offerings SET closed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat() if closed else None, course_offering_id),
    )
    conn.commit()


def is_semester_closed(conn: sqlite3.Connection, course_offering_id: int) -> bool:
    row = conn.execute(
        "SELECT closed_at FROM course_offerings WHERE id = ?", (course_offering_id,)
    ).fetchone()
    return bool(row and row["closed_at"])


def closed_semester_codes(conn: sqlite3.Connection) -> set:
    return {
        r["semester_code"]
        for r in conn.execute(
            "SELECT semester_code FROM course_offerings WHERE closed_at IS NOT NULL"
        )
    }


def in_progress_semester_codes(conn: sqlite3.Connection) -> list:
    """Semester codes still open, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT semester_code FROM course_offerings WHERE closed_at IS NULL"
    ).fetchall()
    return sorted(
        (r["semester_code"] for r in rows), key=chronological_sort_key, reverse=True
    )


def describe_semesters(conn: sqlite3.Connection) -> dict:
    """`semester_code -> label` for every saved semester, in one query.

    For loops over many rows: `describe_semester` runs the closed-semesters
    query on every call, which made a Reports export one query per student."""
    closed = closed_semester_codes(conn)
    labels = {}
    for r in conn.execute("SELECT semester_code FROM course_offerings"):
        code = r["semester_code"]
        label = describe(code)
        labels[code] = label if code in closed else f"{label} — {IN_PROGRESS_SUFFIX}"
    return labels


def describe_semester(conn: sqlite3.Connection, semester_code: str) -> str:
    """ "202610 (Spring 2026)", with " — in progress" when it isn't closed.

    In-progress semesters are labelled rather than hidden: a half-graded term
    should be obvious in cross-semester reporting, not silently missing."""
    label = describe(semester_code)
    if semester_code in closed_semester_codes(conn):
        return label
    return f"{label} — {IN_PROGRESS_SUFFIX}"


def semesters_missing_course_grades(conn: sqlite3.Connection) -> list:
    """Saved semesters with no course grades, newest first.

    These are the semesters absent from the class-rank pool, which is drawn
    from `course_grades` only. Reported so their absence is explained rather
    than silent."""
    rows = conn.execute(
        """
        SELECT DISTINCT co.semester_code
        FROM course_offerings co
        WHERE NOT EXISTS (
            SELECT 1 FROM course_grades cg WHERE cg.course_offering_id = co.id
        )
        """
    ).fetchall()
    return sorted(
        (r["semester_code"] for r in rows), key=chronological_sort_key, reverse=True
    )


def count_app_graded_scores(conn: sqlite3.Connection, course_offering_id: int) -> dict:
    """Scanned exam scores in this semester, per exam.

    Reported when a course sheet is imported -- not as a warning, since the
    sheet writes to the class record and leaves these untouched, but so it's
    clear they stay where they are, in Exam Grading."""
    rows = conn.execute(
        """
        SELECT e.exam_name, COUNT(*) AS n
        FROM exam_scores es
        JOIN exams e ON e.id = es.exam_id
        WHERE e.course_offering_id = ?
        GROUP BY e.exam_name
        ORDER BY e.exam_name
        """,
        (course_offering_id,),
    ).fetchall()
    return {r["exam_name"]: r["n"] for r in rows}


def count_course_grades(conn: sqlite3.Connection, course_offering_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM course_grades WHERE course_offering_id = ?",
        (course_offering_id,),
    ).fetchone()
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# Deleting a semester (roadmap item 13)
# ---------------------------------------------------------------------------

def semester_students(conn: sqlite3.Connection, course_offering_id: int) -> list:
    """Every student with any data in this semester, course grades or scores."""
    rows = conn.execute(
        """SELECT student_id FROM course_grades WHERE course_offering_id = ?
           UNION
           SELECT s.student_id FROM exam_scores s
             JOIN exams e ON e.id = s.exam_id WHERE e.course_offering_id = ?
           UNION
           SELECT h.student_id FROM historical_exam_scores h
             JOIN exams e ON e.id = h.exam_id WHERE e.course_offering_id = ?""",
        (course_offering_id,) * 3,
    ).fetchall()
    return [r["student_id"] for r in rows]


def semester_contents(
    conn: sqlite3.Connection, course_offering_id: int
) -> dict:
    """What deleting this semester would take with it.

    Students are the careful part. A student who repeats the course appears in
    more than one semester, and their record elsewhere is not this semester's
    to delete -- so they are split into those who appear nowhere else
    (`students_removed`, deleted along with the semester) and those who do
    (`students_kept`, left exactly as they are)."""
    counts = {
        "course_grades": conn.execute(
            "SELECT COUNT(*) AS n FROM course_grades WHERE course_offering_id = ?",
            (course_offering_id,),
        ).fetchone()["n"],
        "exams": conn.execute(
            "SELECT COUNT(*) AS n FROM exams WHERE course_offering_id = ?",
            (course_offering_id,),
        ).fetchone()["n"],
    }
    for table, label in (
        ("historical_exam_scores", "record_scores"),
        ("exam_scores", "scanned_scores"),
    ):
        counts[label] = conn.execute(
            f"""SELECT COUNT(*) AS n FROM {table} t
                  JOIN exams e ON e.id = t.exam_id
                WHERE e.course_offering_id = ?""",
            (course_offering_id,),
        ).fetchone()["n"]

    removed, kept = [], []
    for student_id in semester_students(conn, course_offering_id):
        elsewhere = conn.execute(
            """SELECT 1 FROM course_grades
                 WHERE student_id = ? AND course_offering_id != ?
               UNION ALL
               SELECT 1 FROM exam_scores s JOIN exams e ON e.id = s.exam_id
                 WHERE s.student_id = ? AND e.course_offering_id != ?
               UNION ALL
               SELECT 1 FROM historical_exam_scores h JOIN exams e ON e.id = h.exam_id
                 WHERE h.student_id = ? AND e.course_offering_id != ?
               LIMIT 1""",
            (student_id, course_offering_id) * 3,
        ).fetchone()
        (kept if elsewhere else removed).append(student_id)

    source_files = [
        r["source_file"]
        for r in conn.execute(
            """SELECT DISTINCT source_file FROM course_grades
                 WHERE course_offering_id = ? AND source_file IS NOT NULL
               UNION
               SELECT DISTINCT h.source_file FROM historical_exam_scores h
                 JOIN exams e ON e.id = h.exam_id
                WHERE e.course_offering_id = ? AND h.source_file IS NOT NULL""",
            (course_offering_id, course_offering_id),
        ).fetchall()
    ]

    counts["students_removed"] = sorted(removed)
    counts["students_kept"] = sorted(kept)
    counts["source_files"] = sorted(source_files)
    return counts


def delete_semester(conn: sqlite3.Connection, course_offering_id: int) -> dict:
    """Delete a semester outright: its course grades, exams, scores and the
    offering itself, so the semester can be imported again from scratch.

    Returns what `semester_contents` reported, as the record of what went.

    One transaction, and in foreign-key order (scores, then course grades,
    then exams, then the offering), so a failure part-way leaves the semester
    exactly as it was rather than half-deleted. Students who also appear in
    another semester are deliberately left alone -- see `semester_contents`."""
    contents = semester_contents(conn, course_offering_id)
    with conn:
        for table in ("exam_scores", "historical_exam_scores"):
            conn.execute(
                f"""DELETE FROM {table} WHERE exam_id IN
                      (SELECT id FROM exams WHERE course_offering_id = ?)""",
                (course_offering_id,),
            )
        conn.execute(
            "DELETE FROM course_grades WHERE course_offering_id = ?", (course_offering_id,)
        )
        conn.execute("DELETE FROM exams WHERE course_offering_id = ?", (course_offering_id,))
        conn.execute("DELETE FROM course_offerings WHERE id = ?", (course_offering_id,))
        for student_id in contents["students_removed"]:
            conn.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
    return contents


# ---------------------------------------------------------------------------
# Reopening a saved exam (roadmap item 14)
# ---------------------------------------------------------------------------

def list_offerings(conn: sqlite3.Connection) -> list:
    """`(id, semester_code)` for every saved semester, newest first."""
    rows = conn.execute("SELECT id, semester_code FROM course_offerings").fetchall()
    return sorted(
        ((r["id"], r["semester_code"]) for r in rows),
        key=lambda o: chronological_sort_key(o[1]),
        reverse=True,
    )


def exam_source_counts(conn: sqlite3.Connection, exam_id: int) -> dict:
    """How many of an exam's scores the app graded vs imported from a sheet."""
    return {
        "scanned": conn.execute(
            "SELECT COUNT(*) FROM exam_scores WHERE exam_id = ?", (exam_id,)
        ).fetchone()[0],
        "imported": conn.execute(
            "SELECT COUNT(*) FROM historical_exam_scores WHERE exam_id = ?", (exam_id,)
        ).fetchone()[0],
    }


def list_reopenable_exams(conn: sqlite3.Connection, course_offering_id: int) -> list:
    """Exams in this semester that can safely be reopened for editing.

    Only exams with scanned scores, which is now structural rather than a
    check: the class record lives in historical_exam_scores and this reads
    exam_scores, so a record-only exam simply isn't here. That matters because
    reopening rebuilds the final grade from the Individual and Group scores,
    and a record final carries whatever the sheet said -- a curve, a manual
    adjustment, or no component scores at all for the oldest sheets."""
    rows = conn.execute(
        """
        SELECT e.id, e.exam_name, e.possible_points, COUNT(es.student_id) AS students
        FROM exams e
        LEFT JOIN exam_scores es ON es.exam_id = e.id
        WHERE e.course_offering_id = ?
        GROUP BY e.id
        HAVING students > 0
        """,
        (course_offering_id,),
    ).fetchall()
    return sorted((dict(r) for r in rows), key=lambda r: exam_sort_key(r["exam_name"]))


def load_saved_exam(conn: sqlite3.Connection, exam_id: int) -> tuple:
    """Rebuild a saved exam as if its file had just been run.

    Returns `(exam_name, possible_points, matched_students)`, ready for
    `ResultsView.load_results`."""
    from app.core.matching import match_rows

    row = conn.execute(
        "SELECT exam_name, possible_points FROM exams WHERE id = ?", (exam_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no exam with id {exam_id}")
    matched = match_rows(
        get_existing_round_rows(conn, exam_id, "individual"),
        get_existing_round_rows(conn, exam_id, "group"),
    )
    return row["exam_name"], row["possible_points"], matched


# ---------------------------------------------------------------------------
# Point-value mismatches between imports
# ---------------------------------------------------------------------------

_point_mismatches: dict = {}


def _record_point_mismatch(
    conn: sqlite3.Connection, exam_id: int, recorded: float, incoming: float
) -> None:
    """Note that an import stated a different point value than the exam has.

    Held in memory for the current run rather than stored: it describes the
    import that just happened, and the UI reports it before the instructor
    moves on. The exam keeps its recorded value -- changing it would silently
    rescale every score already saved against it.

    Keyed on the connection itself rather than `id(conn)`: a connection isn't
    weak-referenceable, and an id could be reused by a later connection and
    surface someone else's mismatch. One dict entry per connection is a cheaper
    price than that -- the app has one connection for its whole run. Each
    save starts by clearing its own entry (`_forget_point_mismatches`), so a
    mismatch is only ever the one the save just made."""
    _point_mismatches.setdefault(conn, {})[exam_id] = (recorded, incoming)


def _forget_point_mismatches(conn: sqlite3.Connection) -> None:
    """Called at the start of a save: whatever an earlier import left unread
    must not be reported as part of this one."""
    _point_mismatches.pop(conn, None)


def exam_point_mismatches(conn: sqlite3.Connection) -> list:
    """Point-value mismatches seen this run: `(exam_name, recorded, incoming)`."""
    seen = _point_mismatches.get(conn, {})
    out = []
    for exam_id, (recorded, incoming) in seen.items():
        row = conn.execute("SELECT exam_name FROM exams WHERE id = ?", (exam_id,)).fetchone()
        out.append((row["exam_name"] if row else str(exam_id), recorded, incoming))
    return sorted(out)


def clear_exam_point_mismatches(conn: sqlite3.Connection) -> None:
    _point_mismatches.pop(conn, None)


def scan_save_warnings(conn: sqlite3.Connection, exam_id: int) -> list:
    """What saving scanned scores over this exam would disturb.

    Saving can no longer reach the class record -- that lives in its own table
    -- so the only thing worth noting is that the semester has been closed as
    complete, which means its exams are done and a new scan is probably
    unintended."""
    warnings = []
    row = conn.execute(
        """SELECT co.id, co.semester_code, co.closed_at FROM exams e
           JOIN course_offerings co ON co.id = e.course_offering_id WHERE e.id = ?""",
        (exam_id,),
    ).fetchone()
    if row and row["closed_at"]:
        warnings.append(
            f"{describe(row['semester_code'])} is closed, meaning its grades are "
            "complete. Saving scanned scores into it won't change the class record "
            "— that comes from the course sheet — but it is probably not intended."
        )
    return warnings


# ---------------------------------------------------------------------------
# Datasets for the Reports tab
# ---------------------------------------------------------------------------

def get_exam_average_pool(conn: sqlite3.Connection) -> list:
    """Every student-semester exam average in the class record, as percentages.

    The exam-only counterpart to `get_course_grade_pool`: that one is the whole
    course (lab, quizzes and all), this one is exams alone. They are different
    measures and give different rankings, which is why both are labelled
    wherever they're shown."""
    return [r["overall_percent"] for r in get_all_student_semester_scores(conn)]


def get_student_exam_averages(conn: sqlite3.Connection, student_id: str) -> list:
    """One student's exam average per semester, newest first.

    The same aggregate as get_all_student_semester_scores, restricted in SQL
    to one student: this runs on every selection in the Rec Letter view, and
    used to aggregate every student in the record to keep one."""
    rows = conn.execute(
        """
        SELECT co.id AS course_offering_id, co.semester_code,
               s.student_id, s.first_name, s.last_name,
               AVG(es.final_grade / es.possible_points * 100) AS overall_percent
        FROM historical_exam_scores es
        JOIN exams e ON e.id = es.exam_id
        JOIN course_offerings co ON co.id = e.course_offering_id
        JOIN students s ON s.student_id = es.student_id
        WHERE es.student_id = ? AND es.final_grade IS NOT NULL AND es.possible_points > 0
        GROUP BY co.id, s.student_id
        """,
        (student_id,),
    ).fetchall()
    return sorted(
        (dict(r) for r in rows),
        key=lambda r: chronological_sort_key(r["semester_code"]),
        reverse=True,
    )


def exam_averages_by_semester(conn: sqlite3.Connection) -> dict:
    """`semester_code -> [exam average percent]`, oldest semester first."""
    grouped: dict = {}
    for row in get_all_student_semester_scores(conn):
        grouped.setdefault(row["semester_code"], []).append(row["overall_percent"])
    return {code: grouped[code] for code in sorted(grouped, key=chronological_sort_key)}


def exam_percents_by_exam(conn: sqlite3.Connection) -> dict:
    """`exam name -> [percent]` across every semester, in natural exam order."""
    rows = conn.execute(
        """
        SELECT e.exam_name, es.final_grade / es.possible_points * 100 AS percent
        FROM historical_exam_scores es
        JOIN exams e ON e.id = es.exam_id
        WHERE es.final_grade IS NOT NULL AND es.possible_points > 0
        """
    ).fetchall()
    grouped: dict = {}
    for row in rows:
        name = normalize_exam_name(row["exam_name"]) or row["exam_name"]
        grouped.setdefault(name, []).append(row["percent"])
    return {name: grouped[name] for name in sorted(grouped, key=exam_sort_key)}


def exam_percents_for_semester(conn: sqlite3.Connection, semester_code: str) -> dict:
    """`exam name -> [percent]` within one semester."""
    rows = conn.execute(
        """
        SELECT e.exam_name, es.final_grade / es.possible_points * 100 AS percent
        FROM historical_exam_scores es
        JOIN exams e ON e.id = es.exam_id
        JOIN course_offerings co ON co.id = e.course_offering_id
        WHERE co.semester_code = ? AND es.final_grade IS NOT NULL
          AND es.possible_points > 0
        """,
        (semester_code,),
    ).fetchall()
    grouped: dict = {}
    for row in rows:
        name = normalize_exam_name(row["exam_name"]) or row["exam_name"]
        grouped.setdefault(name, []).append(row["percent"])
    return {name: grouped[name] for name in sorted(grouped, key=exam_sort_key)}


def course_averages_by_semester(conn: sqlite3.Connection) -> dict:
    """`semester_code -> [course grade percent]`, oldest semester first."""
    grouped: dict = {}
    for row in get_all_course_grades(conn):
        if row["final_percent"] is not None:
            grouped.setdefault(row["semester_code"], []).append(row["final_percent"])
    return {code: grouped[code] for code in sorted(grouped, key=chronological_sort_key)}


def exam_percents_by_semester_for_exam(conn: sqlite3.Connection, exam_name: str) -> dict:
    """`semester_code -> [percent]` for one exam, oldest semester first.

    How a single exam has fared term by term -- the view that shows whether
    Exam 2 has got harder, rather than how the exams compare with each other."""
    wanted = exam_group_key(exam_name)
    rows = conn.execute(
        """
        SELECT co.semester_code, e.exam_name,
               es.final_grade / es.possible_points * 100 AS percent
        FROM historical_exam_scores es
        JOIN exams e ON e.id = es.exam_id
        JOIN course_offerings co ON co.id = e.course_offering_id
        WHERE es.final_grade IS NOT NULL AND es.possible_points > 0
        """
    ).fetchall()
    grouped: dict = {}
    for row in rows:
        if exam_group_key(row["exam_name"]) != wanted:
            continue
        grouped.setdefault(row["semester_code"], []).append(row["percent"])
    return {code: grouped[code] for code in sorted(grouped, key=chronological_sort_key)}
