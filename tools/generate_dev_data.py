"""Fill a development database with plausible data, to see how the app behaves.

Writes six completed semesters into the class record and one current semester of
scanned exams, 20 students each. Deterministic, so re-running gives the same
data.

    conda run -n examgradetool python tools/generate_dev_data.py          # fills dev-data/grades.db
    conda run -n examgradetool python tools/generate_dev_data.py --reset  # wipes it first

**It refuses to run against anything but a development database.** The guard is
`config.is_development_data()`, the same one that keeps the app off the live
file, so this cannot invent grades in the real course folder.
"""

import argparse
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.grading import compute_grade  # noqa: E402
from app.db import config, store  # noqa: E402

# Newest first. The oldest semester's exams were out of 80 points; the lab was
# worth 293 in the two most recent and 260 before that.
CURRENT_SEMESTER = "202630"  # Fall 2026, still in progress
HISTORY = [
    # (semester code, exam points, lab max)
    ("202610", 50, 293),  # Spring 2026
    ("202530", 50, 293),  # Fall 2025
    ("202510", 50, 260),  # Spring 2025
    ("202430", 50, 260),  # Fall 2024
    ("202410", 50, 260),  # Spring 2024
    ("202330", 80, 260),  # Fall 2023 -- exams out of 80
]
EXAM_NAMES = ["Exam 1", "Exam 2", "Exam 3", "Exam 4"]
CURRENT_EXAMS_SCORED = 2
STUDENTS_PER_SEMESTER = 20
QUIZ_TOTAL = 60  # quizzes, muddiest point and the rest, lumped together

FIRST_NAMES = [
    "Ava", "Ben", "Cara", "Dev", "Elena", "Finn", "Grace", "Hugo", "Iris", "Jonah",
    "Kira", "Liam", "Maya", "Noor", "Owen", "Priya", "Quinn", "Rosa", "Sam", "Tessa",
    "Uma", "Victor", "Wren", "Xavier", "Yara", "Zane", "Alice", "Bruno", "Clara", "Diego",
]
LAST_NAMES = [
    "Abbott", "Barnes", "Chen", "Delgado", "Eriksen", "Flores", "Gupta", "Hayes",
    "Ibrahim", "Jensen", "Kowalski", "Lindqvist", "Moreau", "Nakamura", "Okafor",
    "Petrov", "Quintero", "Ramirez", "Silva", "Tanaka", "Ueda", "Vargas", "Weber",
    "Xu", "Yilmaz", "Zhang", "Adeyemi", "Bauer", "Costa", "Dubois",
]


def _student_pool(rng):
    """7-digit IDs with names, unique and stable for a given seed."""
    used_ids, used_names, pool = set(), set(), []
    while len(pool) < STUDENTS_PER_SEMESTER * (len(HISTORY) + 1):
        student_id = f"{rng.randint(1000000, 9999999)}"
        name = (rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))
        if student_id in used_ids or name in used_names:
            continue
        used_ids.add(student_id)
        used_names.add(name)
        pool.append((student_id, *name))
    return pool


def _ability(rng):
    """A student's rough standard, so their exams hang together."""
    return min(0.99, max(0.35, rng.gauss(0.80, 0.11)))


def _exam_score(rng, ability, points):
    return round(min(points, max(0, rng.gauss(ability * points, points * 0.07))) * 2) / 2


def generate(db_path: Path, reset: bool) -> None:
    if reset and db_path.exists():
        db_path.unlink()
    conn = store.connect(db_path)
    rng = random.Random(20261021)  # fixed, so the data is reproducible
    now = datetime.now(timezone.utc).isoformat()
    pool = _student_pool(rng)
    cursor = 0

    def add_student(student_id, first, last):
        conn.execute(
            "INSERT OR IGNORE INTO students (student_id, first_name, last_name) "
            "VALUES (?, ?, ?)",
            (student_id, first, last),
        )

    # --- six completed semesters, in the class record ---------------------
    previous_cohort = None
    for semester, exam_points, lab_max in HISTORY:
        offering = store.get_or_create_course_offering(conn, semester)
        exams = {
            name: store.get_or_create_exam(conn, offering, name, exam_points)
            for name in EXAM_NAMES
        }
        cohort = pool[cursor : cursor + STUDENTS_PER_SEMESTER]
        cursor += STUDENTS_PER_SEMESTER
        # Two students repeat the course from the semester before, which is not
        # a duplicate and shouldn't be reported as one. They're taken from the
        # previous cohort's *new* students, so a repeat appears in exactly two
        # semesters rather than chaining through all six.
        fresh = cohort
        if previous_cohort:
            cohort = previous_cohort[2:4] + cohort[2:]
        previous_cohort = fresh

        for student_id, first, last in cohort:
            add_student(student_id, first, last)
            ability = _ability(rng)
            exam_total = 0.0
            for name, exam_id in exams.items():
                # One student each semester misses an exam entirely.
                if rng.random() < 0.02:
                    continue
                individual = _exam_score(rng, ability, exam_points)
                partner = _exam_score(rng, min(0.98, ability + 0.10), exam_points)
                final = round(0.6 * individual + 0.4 * max(individual, partner), 2)
                exam_total += final
                conn.execute(
                    """INSERT OR REPLACE INTO historical_exam_scores
                         (exam_id, student_id, individual_score, raw_group_score,
                          final_grade, possible_points, source_file, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        exam_id, student_id, individual, partner, final,
                        exam_points, f"{semester} course sheet.csv", now,
                    ),
                )

            lab = round(min(lab_max, max(0, rng.gauss(ability * lab_max, lab_max * 0.06))), 1)
            quizzes = round(min(QUIZ_TOTAL, max(0, rng.gauss(ability * QUIZ_TOTAL, 4))), 1)
            denominator = len(EXAM_NAMES) * exam_points + lab_max + QUIZ_TOTAL
            numerator = round(exam_total + lab + quizzes, 2)
            # Most students earn some extra credit, a few none at all.
            extra = rng.choice([0, 0, 4, 4, 8])
            conn.execute(
                """INSERT OR REPLACE INTO course_grades
                     (course_offering_id, student_id, adjusted_numerator,
                      adjusted_denominator, calculated_numerator,
                      calculated_denominator, grade_basis, final_percent,
                      lab_earned, lab_possible, extra_credit_earned,
                      extra_credit_possible, other_earned, other_possible,
                      letter_grade, source_file, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'adjusted', ?, ?, ?, ?, 8, ?, ?,
                           NULL, ?, ?)""",
                (
                    offering, student_id, numerator + extra, denominator,
                    numerator, denominator,
                    (numerator + extra) / denominator * 100,
                    lab, lab_max, extra,
                    quizzes, QUIZ_TOTAL,
                    f"{semester} course sheet.csv", now,
                ),
            )
        # Its grades are complete, so the semester is closed.
        store.set_semester_closed(conn, offering, True)

    # --- the current semester: two exams scanned, still in progress -------
    offering = store.get_or_create_course_offering(conn, CURRENT_SEMESTER)
    cohort = pool[cursor : cursor + STUDENTS_PER_SEMESTER]
    for index, name in enumerate(EXAM_NAMES[:CURRENT_EXAMS_SCORED]):
        # Scanned files name the exam with the semester code in front, as the
        # real Scantron export does.
        exam_id = store.get_or_create_exam(conn, offering, f"{CURRENT_SEMESTER} {name}", 50)
        for student_id, first, last in cohort:
            add_student(student_id, first, last)
            ability = _ability(rng)
            individual = _exam_score(rng, ability, 50)
            partner = _exam_score(rng, min(0.98, ability + 0.10), 50)
            # One student missed the group round of the second exam, so the
            # missing-score flag has something to show.
            if index == 1 and student_id == cohort[-1][0]:
                partner = None
            grade = compute_grade(individual, partner, 50)
            conn.execute(
                """INSERT OR REPLACE INTO exam_scores
                     (exam_id, student_id, individual_score, raw_group_score,
                      effective_group_score, final_grade, individual_key_used,
                      missing_flag, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    exam_id, student_id, individual, partner,
                    grade.effective_group_score, grade.final_grade,
                    rng.choice(["A", "B"]), grade.missing_flag, now,
                ),
            )
    conn.commit()

    # --- what was made ----------------------------------------------------
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("students", "course_offerings", "exams", "exam_scores",
                      "historical_exam_scores", "course_grades")
    }
    print(f"Wrote {db_path}")
    for table, n in counts.items():
        print(f"  {table:24} {n}")
    print()
    print("  class record (closed):", ", ".join(store.closed_semester_codes(conn)))
    print("  in progress          :", ", ".join(store.in_progress_semester_codes(conn)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete the database first")
    parser.add_argument("--db", help="path to write (default: this copy's dev database)")
    args = parser.parse_args(argv)

    if args.db:
        db_path = Path(args.db).expanduser().resolve()
    else:
        if not config.is_development_data():
            raise SystemExit(
                "error: this copy is not marked as a development copy, so it would "
                "write to the live database.\n"
                "Create a .dev-copy file in the project root, or set "
                f"{config.ENV_DATA_FOLDER}, and try again."
            )
        db_path = Path(store.default_db_path())

    if db_path == Path(config.DEFAULT_DATA_FOLDER) / "grades.db":
        raise SystemExit(f"error: {db_path} is the live database. Refusing to write test data.")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    generate(db_path, args.reset)


if __name__ == "__main__":
    main()
