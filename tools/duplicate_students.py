"""One-off cleanup for students saved under more than one ID (ROADMAP.md item 8).

Two steps, with a review in between. Nothing is ever merged without someone
saying so, and the whole thing runs against a *copy* of the database.

    # 1. propose -- writes two CSVs, changes nothing
    conda run -n examgradetool python tools/duplicate_students.py report --db grades_copy.db

    # 2. open duplicates_review.csv, set each `action` to merge or skip;
    #    open conflicts_review.csv, set `use_id` where you disagree

    # 3. apply -- merges only what was approved, and writes merge_log.csv
    conda run -n examgradetool python tools/duplicate_students.py apply --db grades_copy.db

Afterwards: back up the live database (File > Backup Database... in the app),
then put the merged copy in its place.

Edit the review files in a text editor or Numbers if you can. Excel rewrites
student IDs on save ("0123456" becomes 123456, "1234567.0" becomes 1234567);
`apply` checks every ID against the database and stops, naming the row, rather
than merge the wrong student or quietly merge nothing.
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.parsers import clean_student_id  # noqa: E402
from app.db import config, duplicates, store  # noqa: E402

REVIEW_FILE = "duplicates_review.csv"
CONFLICTS_FILE = "conflicts_review.csv"
LOG_FILE = "merge_log.csv"

REVIEW_COLUMNS = [
    "group", "action", "keep_id", "merge_id", "confidence", "reason",
    "keep_record", "merge_record",
]
CONFLICT_COLUMNS = [
    "group", "keep_id", "merge_id", "kind", "item", "use_id",
    "value_if_keep_id", "value_if_merge_id", "why_default",
]


def _live_db_paths() -> set:
    """Every path the real database can be at, whichever copy of the project
    this runs from.

    `store.default_db_path()` alone isn't enough: in a development copy it
    points at dev-data/, so the Dropbox file would pass that check."""
    candidates = [
        store.default_db_path(),
        config.DEFAULT_DATA_FOLDER / "grades.db",
        config.get_data_folder() / "grades.db",
    ]
    return {Path(p).expanduser().resolve() for p in candidates}


def _open_copy(db_path: Path, allow_synced_folder: bool = False):
    """Refuse to touch the live database: this tool works on a copy."""
    db_path = Path(db_path).expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(f"error: {db_path} does not exist")
    if db_path in _live_db_paths():
        raise SystemExit(
            f"error: {db_path} is the live database.\n"
            "Copy it first and run this against the copy, so the original is untouched."
        )
    if "Dropbox" in db_path.parts and not allow_synced_folder:
        raise SystemExit(
            f"error: {db_path} is inside Dropbox.\n"
            "A copy there can be synced mid-merge, or mistaken for the live file. Work "
            "on a copy outside Dropbox, or pass --allow-synced-folder if you're sure."
        )
    return store.connect(db_path)


def _checked_id(conn, value: str, row_label: str, column: str) -> str:
    """The student ID a review-file cell names, confirmed to exist.

    The cell is taken verbatim if a student has exactly that ID -- the
    duplicates this tool exists for *are* mangled IDs like "1234567.0" and
    "123456", stored as such. Failing that, the two corrections the app itself
    makes (a trailing ".0", a dropped leading zero on a 6-digit ID) are tried,
    which is what undoes Excel re-saving the file. Anything else stops the run
    and names the row."""
    value = (value or "").strip()

    def exists(student_id):
        return student_id and conn.execute(
            "SELECT 1 FROM students WHERE student_id = ?", (student_id,)
        ).fetchone()

    if exists(value):
        return value
    cleaned, problem = clean_student_id(value)
    if cleaned and not problem and exists(cleaned):
        return cleaned
    raise SystemExit(
        f"error: {row_label}: '{column}' is '{value or '(blank)'}', which is not a student in "
        "this database. Was the file saved from Excel? Fix the ID and run apply again; "
        "nothing has been merged."
    )


def _summary(conn, student_id: str) -> str:
    s = duplicates.summarize_student(conn, student_id)
    semesters = ", ".join(s.semesters) or "no semesters"
    return (
        f"{s.name} | {semesters} | {s.exam_scores} exam score(s), "
        f"{s.course_grades} course grade(s)"
    )


def report(args):
    conn = _open_copy(args.db, args.allow_synced_folder)
    out_dir = Path(args.out or Path(args.db).parent)
    out_dir.mkdir(parents=True, exist_ok=True)

    groups = duplicates.find_duplicates(conn)
    review_rows, conflict_rows = [], []
    for number, group in enumerate(groups, start=1):
        review_rows.append(
            {
                "group": number,
                # Pre-filled only where the two IDs provably normalise to each
                # other. Name matches say "review": anything that isn't exactly
                # "merge" is skipped.
                "action": "merge" if group.confidence == duplicates.CERTAIN else "review",
                "keep_id": group.keep_id,
                "merge_id": group.other_id,
                "confidence": group.confidence,
                "reason": group.reason,
                "keep_record": _summary(conn, group.keep_id),
                "merge_record": _summary(conn, group.other_id),
            }
        )
        for conflict in duplicates.find_conflicts(conn, group.keep_id, group.other_id):
            conflict_rows.append(
                {
                    "group": number,
                    "keep_id": group.keep_id,
                    "merge_id": group.other_id,
                    "kind": conflict.kind,
                    "item": conflict.label,
                    "use_id": group.keep_id if conflict.default == "keep" else group.other_id,
                    "value_if_keep_id": conflict.keep_value,
                    "value_if_merge_id": conflict.other_value,
                    "why_default": conflict.why_default,
                }
            )

    _write(out_dir / REVIEW_FILE, REVIEW_COLUMNS, review_rows)
    _write(out_dir / CONFLICTS_FILE, CONFLICT_COLUMNS, conflict_rows)

    certain = sum(1 for r in review_rows if r["confidence"] == duplicates.CERTAIN)
    print(f"Students in database: {conn.execute('SELECT COUNT(*) FROM students').fetchone()[0]}")
    print(f"Suspected duplicate pairs: {len(review_rows)}")
    print(f"  provably the same ID (pre-filled as merge): {certain}")
    print(f"  same name only (left for you to decide):    {len(review_rows) - certain}")
    print(f"Conflicts needing a choice: {len(conflict_rows)}")
    print()
    print(f"Review: {out_dir / REVIEW_FILE}")
    if conflict_rows:
        print(f"        {out_dir / CONFLICTS_FILE}")
    print("Nothing has been changed. Run 'apply' once the review file says what you want.")


def _write(path: Path, columns, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def apply(args):
    conn = _open_copy(args.db, args.allow_synced_folder)
    review_dir = Path(args.review or Path(args.db).parent)
    review_rows = _read(review_dir / REVIEW_FILE)
    if not review_rows:
        raise SystemExit(
            f"error: no {REVIEW_FILE} in {review_dir}. Run 'report' first."
        )
    conflict_rows = _read(review_dir / CONFLICTS_FILE)

    approved = [r for r in review_rows if (r.get("action") or "").strip().lower() == "merge"]
    skipped = len(review_rows) - len(approved)
    if not approved:
        print(f"Nothing marked 'merge' in {REVIEW_FILE}. No changes made.")
        return

    # Every ID is checked before anything is merged, so a mangled row stops the
    # run with nothing changed rather than part-way through.
    plan = []
    for row in approved:
        group = row["group"]
        label = f"{REVIEW_FILE} group {group}"
        keep_id = _checked_id(conn, row["keep_id"], label, "keep_id")
        merge_id = _checked_id(conn, row["merge_id"], label, "merge_id")
        if keep_id == merge_id:
            raise SystemExit(f"error: {label}: keep_id and merge_id are the same student.")
        exam_choices, course_choices = {}, {}
        for conflict in conflict_rows:
            if conflict["group"] != group:
                continue
            chosen = (conflict.get("use_id") or "").strip()
            if chosen:
                chosen = _checked_id(conn, chosen, f"{CONFLICTS_FILE} group {group}", "use_id")
                if chosen not in (keep_id, merge_id):
                    raise SystemExit(
                        f"error: {CONFLICTS_FILE} group {group}: use_id {chosen} is neither "
                        f"{keep_id} nor {merge_id}."
                    )
            side = "keep" if chosen == keep_id else "other"
            for candidate in duplicates.find_conflicts(conn, keep_id, merge_id):
                if candidate.label != conflict["item"]:
                    continue
                if candidate.table:
                    exam_choices[(candidate.table, candidate.exam_id)] = side
                else:
                    course_choices[candidate.course_offering_id] = side
        plan.append((row, keep_id, merge_id, exam_choices, course_choices))

    # Each pair is its own transaction (merge_students), and its log lines are
    # on disk before the next pair starts: if pair 3 of 5 fails, the log says
    # exactly which two were merged.
    log_path = review_dir / LOG_FILE
    timestamp = datetime.now().isoformat(timespec="seconds")
    merged = 0
    with open(log_path, "w", newline="") as log_file:
        log = csv.DictWriter(log_file, fieldnames=["when", "group", "kept_id", "merged_id", "reason", "change"])
        log.writeheader()
        for row, keep_id, merge_id, exam_choices, course_choices in plan:
            try:
                lines = duplicates.merge_students(conn, keep_id, merge_id, exam_choices, course_choices)
            except Exception:
                print(
                    f"\nerror while merging {merge_id} into {keep_id} (group {row['group']}); "
                    f"that pair was rolled back. {merged} pair(s) before it were merged and "
                    f"are in the log: {log_path}",
                    file=sys.stderr,
                )
                raise
            for line in lines:
                log.writerow(
                    {
                        "when": timestamp,
                        "group": row["group"],
                        "kept_id": keep_id,
                        "merged_id": merge_id,
                        "reason": row["reason"],
                        "change": line,
                    }
                )
            log_file.flush()
            merged += 1
            print(f"merged {merge_id} into {keep_id} ({row['reason']})")

    print()
    print(f"Merged {merged} pair(s); {skipped} left alone.")
    print(f"Log: {log_path}")
    print(
        "The live database is untouched. Back it up (File > Backup Database... in the app), "
        "then put this copy in its place."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    report_parser = sub.add_parser("report", help="propose duplicates; changes nothing")
    report_parser.add_argument("--db", required=True, help="path to a COPY of grades.db")
    report_parser.add_argument("--out", help="where to write the review files")
    report_parser.set_defaults(func=report)

    apply_parser = sub.add_parser("apply", help="merge what the review file approves")
    apply_parser.add_argument("--db", required=True, help="path to a COPY of grades.db")
    apply_parser.add_argument("--review", help="folder holding the review files")
    apply_parser.set_defaults(func=apply)

    for p in (report_parser, apply_parser):
        p.add_argument(
            "--allow-synced-folder", action="store_true",
            help="work on a copy that lives inside Dropbox (normally refused)",
        )

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
