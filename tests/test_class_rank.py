from pathlib import Path

import pytest

from app.core.course_export import parse_course_export
from app.db import store

FIXTURES = Path(__file__).parent / "fixtures"
TRIPLE = FIXTURES / "synthetic_course_export.csv"


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


@pytest.fixture
def loaded(conn):
    offering = store.get_or_create_course_offering(conn, "202430")
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")
    store.save_historical_exam_scores(conn, offering, parsed)
    return conn


def test_five_tiers_in_order():
    tiers = store.class_rank_cutoffs([float(i) for i in range(100)])
    assert [t["label"] for t in tiers] == ["Top 0.1%", "Top 1%", "Top 5%", "Top 10%", "Top 20%"]


def test_cutoffs_get_less_selective_as_the_tier_widens():
    scores = [float(i) for i in range(2000)]
    cutoffs = [t["cutoff"] for t in store.class_rank_cutoffs(scores)]
    assert cutoffs == sorted(cutoffs, reverse=True)


def test_top_tenth_percent_is_unsupported_until_the_pool_is_big_enough():
    """Below ~1000 records the 0.1% tier is just "the highest score"."""
    small = store.class_rank_cutoffs([float(i) for i in range(323)])[0]
    assert small["students"] == 1
    assert small["supported"] is False

    big = store.class_rank_cutoffs([float(i) for i in range(2000)])[0]
    assert big["students"] == 2
    assert big["supported"] is True


def test_wider_tiers_are_supported_even_in_a_small_pool():
    tiers = store.class_rank_cutoffs([float(i) for i in range(323)])
    assert all(t["supported"] for t in tiers[1:])


def test_empty_pool_yields_no_cutoffs():
    tiers = store.class_rank_cutoffs([])
    assert len(tiers) == 5
    assert all(t["cutoff"] is None and t["students"] == 0 for t in tiers)


def test_ties_are_not_split():
    """A tie spanning a cutoff leaves more than N% at or above it -- documented
    behaviour, asserted so a future change to it is deliberate."""
    scores = [90.0] * 10 + [50.0] * 10
    top_20 = store.class_rank_cutoffs(scores)[-1]
    assert top_20["cutoff"] == 90.0
    assert sum(1 for s in scores if s >= top_20["cutoff"]) == 10  # not 4


def test_rank_for_percent_reports_rank_and_tier():
    scores = [float(i) for i in range(2000)]
    result = store.rank_for_percent(scores, 1999.0)
    assert result["rank"] == 1
    assert result["pool"] == 2000
    assert result["tier"]["label"] == "Top 0.1%"


def test_rank_never_claims_a_tier_the_pool_cannot_support():
    """The top scorer in a small pool is "Top 1%", not "Top 0.1%" -- this label
    ends up in recommendation letters, so it must not overstate."""
    scores = [float(i) for i in range(100)]
    result = store.rank_for_percent(scores, 99.0)
    assert result["rank"] == 1
    assert result["tier"]["label"] == "Top 1%"


def test_rank_for_percent_below_every_tier_has_no_tier():
    scores = [float(i) for i in range(100)]
    result = store.rank_for_percent(scores, 5.0)
    assert result["tier"] is None
    assert result["rank"] == 95


def test_rank_for_percent_handles_missing_input():
    assert store.rank_for_percent([1.0], None) is None
    assert store.rank_for_percent([], 90.0) is None


def test_pool_is_course_grades_only_and_percentage_based(loaded):
    pool = store.get_course_grade_pool(loaded)
    assert len(pool) == 3
    assert pool == sorted(pool, reverse=True)
    assert all(0 <= p <= 100 for p in pool)


def test_search_by_last_name_first_name_and_id(loaded):
    assert [r["student_id"] for r in store.search_students(loaded, "Alpha")] == ["0900001"]
    assert [r["student_id"] for r in store.search_students(loaded, "Ben")] == ["0900002"]
    assert [r["student_id"] for r in store.search_students(loaded, "0900003")] == ["0900003"]


def test_search_matches_partially_and_case_insensitively(loaded):
    assert len(store.search_students(loaded, "9000")) == 3
    assert [r["student_id"] for r in store.search_students(loaded, "alph")] == ["0900001"]


def test_search_on_full_name(loaded):
    assert [r["student_id"] for r in store.search_students(loaded, "Ann Alpha")] == ["0900001"]


def test_empty_search_returns_nothing(loaded):
    assert store.search_students(loaded, "") == []
    assert store.search_students(loaded, "   ") == []


def test_student_detail_covers_course_grade_and_exams(loaded):
    grades = store.get_student_course_grades(loaded, "0900001")
    assert len(grades) == 1
    assert grades[0]["semester_code"] == "202430"
    assert grades[0]["final_percent"] == pytest.approx(474.5 / 587 * 100)

    exams = store.get_student_exam_scores(loaded, "0900001")
    assert [e["exam_name"] for e in exams] == ["Exam 1", "Exam 2"]
    assert exams[0]["final_grade"] == 34
    assert exams[0]["percent"] == pytest.approx(68.0)
