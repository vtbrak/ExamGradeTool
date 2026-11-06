from pathlib import Path

from app.core.matching import exclude_duplicates, match_rows
from app.core.parsers import parse_exam_file

FIXTURES = Path(__file__).parent / "fixtures"


def _load_matched():
    individual = parse_exam_file(FIXTURES / "synthetic_individual_round.csv")
    group = parse_exam_file(FIXTURES / "synthetic_group_round.csv")

    clean_individual_rows = exclude_duplicates(
        individual.individual_rows, individual.duplicate_student_ids["individual"]
    )
    return match_rows(clean_individual_rows, group.group_rows)


def test_students_present_in_both_rounds_are_fully_matched():
    matched = _load_matched()
    by_id = {m.student_id: m for m in matched}

    alice = by_id["1000001"]
    assert alice.individual_score == 40
    assert alice.raw_group_score == 42
    assert alice.grade.final_grade is not None
    assert alice.first_name == "Alice"


def test_student_missing_group_round_is_flagged():
    matched = _load_matched()
    by_id = {m.student_id: m for m in matched}

    # Dan (1004) is present in both fixtures, so use a student only in one.
    # Eve (1005) only appears in the group-round fixture.
    eve = by_id["1000005"]
    assert eve.individual_score is None
    assert eve.raw_group_score == 40
    assert eve.grade.missing_flag == "Missing Individual"
    assert eve.grade.final_grade is None


def test_duplicate_student_excluded_from_matching_until_resolved():
    matched = _load_matched()
    ids = {m.student_id for m in matched}
    # Cara (1003) has an unresolved duplicate form in the individual round and
    # must not show up in the matched results at all until the instructor
    # resolves it.
    assert "1000003" not in ids


def test_sorted_by_student_id():
    matched = _load_matched()
    ids = [m.student_id for m in matched]
    assert ids == sorted(ids)
