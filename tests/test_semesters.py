import pytest

from app.core.semesters import (
    FALL,
    FORMAT_CURRENT,
    FORMAT_LEGACY,
    SPRING,
    describe,
    find_existing_code,
    newest_first,
    parse_semester_code,
    same_semester,
)
from app.db import store


# --- the two formats -------------------------------------------------------

@pytest.mark.parametrize(
    "code, year, term",
    [("202610", 2026, SPRING), ("202630", 2026, FALL), ("202010", 2020, SPRING)],
)
def test_current_six_digit_codes(code, year, term):
    semester = parse_semester_code(code)
    assert (semester.year, semester.term, semester.format) == (year, term, FORMAT_CURRENT)


@pytest.mark.parametrize(
    "code, year, term",
    [("2201", 2020, SPRING), ("2205", 2020, FALL), ("2165", 2016, FALL), ("2191", 2019, SPRING)],
)
def test_legacy_four_digit_codes(code, year, term):
    """Three-digit year then a one-digit term: 220 is 2020, 1 Spring, 5 Fall."""
    semester = parse_semester_code(code)
    assert (semester.year, semester.term, semester.format) == (year, term, FORMAT_LEGACY)


@pytest.mark.parametrize(
    "code",
    [
        "Fall 2019",  # the free-text form the old dialog tooltip suggested
        "",
        None,
        "20261",  # a digit short
        "2026100",  # a digit long
        "202640",  # 40 is not a term code
        "202611",
        "2204",  # 4 is not a legacy term code
        "220",
        " ",
    ],
)
def test_invalid_codes_are_rejected_not_guessed(code):
    assert parse_semester_code(code) is None


def test_surrounding_whitespace_is_tolerated():
    assert parse_semester_code(" 202610 ").label == "Spring 2026"


def test_describe():
    assert describe("202610") == "202610 (Spring 2026)"
    assert describe("2201") == "2201 (Spring 2020)"
    assert describe("junk") == "junk"


# --- the same semester under both formats ---------------------------------

def test_same_semester_across_formats():
    assert same_semester("2201", "202010")
    assert same_semester("2205", "202030")
    assert not same_semester("2201", "202030")  # same year, different term
    assert not same_semester("2201", "junk")


def test_find_existing_code_prefers_exact_then_equivalent():
    assert find_existing_code("202010", ["202010", "2201"]) == "202010"
    assert find_existing_code("202010", ["2201", "202610"]) == "2201"
    assert find_existing_code("202630", ["2201", "202610"]) is None


# --- ordering --------------------------------------------------------------

def test_newest_first_is_calendar_order_not_text_order():
    """As text, "2205" sorts above "202610", which would put Fall 2020 above
    Spring 2026."""
    codes = ["202610", "2201", "2165", "202630", "2205"]
    assert sorted(codes, reverse=True)[0] == "2205"  # the bug being avoided
    assert newest_first(codes) == ["202630", "202610", "2205", "2201", "2165"]


def test_spring_sorts_before_fall_within_a_year():
    assert newest_first(["202610", "202630"]) == ["202630", "202610"]
    assert newest_first(["2201", "2205"]) == ["2205", "2201"]


def test_unparseable_codes_sort_to_the_bottom():
    assert newest_first(["junk", "202610", "2201"]) == ["202610", "2201", "junk"]


# --- the store guard ---------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "grades.db")


def test_equivalent_code_reuses_the_existing_offering(conn):
    """Spring 2020 saved as 2201 must not become a second offering when later
    entered as 202010, or the other way round."""
    legacy = store.get_or_create_course_offering(conn, "2201")
    assert store.get_or_create_course_offering(conn, "202010") == legacy
    assert store.list_semester_codes(conn) == ["2201"]


def test_different_semesters_stay_separate(conn):
    spring = store.get_or_create_course_offering(conn, "2201")
    fall = store.get_or_create_course_offering(conn, "2205")
    assert spring != fall


def test_guard_applies_to_the_scantron_path_too(conn):
    """Both import paths go through get_or_create_course_offering."""
    store.get_or_create_course_offering(conn, "202610")
    assert store.resolve_semester_code(conn, "2261") == "202610"


def test_list_semester_codes_is_newest_first(conn):
    for code in ("2165", "202610", "2201", "202430"):
        store.get_or_create_course_offering(conn, code)
    assert store.list_semester_codes(conn) == ["202610", "202430", "2201", "2165"]


def test_student_detail_is_ordered_by_calendar(conn):
    from pathlib import Path

    from app.core.course_export import parse_course_export

    parsed = parse_course_export(Path(__file__).parent / "fixtures" / "synthetic_course_export.csv")
    row = parsed.rows[0]
    for code in ("2201", "202610", "2165"):
        store.save_course_grades(conn, store.get_or_create_course_offering(conn, code), [row])
    semesters = [r["semester_code"] for r in store.get_student_course_grades(conn, row.student_id)]
    assert semesters == ["202610", "2201", "2165"]


# --- summer ------------------------------------------------------------------

@pytest.mark.parametrize(
    "code, label",
    [
        ("202620", "Summer 2026"),
        ("2202", "Summer 1, 2020"),
        ("2203", "Summer 2, 2020"),
    ],
)
def test_summer_codes(code, label):
    assert parse_semester_code(code).label == label


def test_older_summer_sessions_are_distinct_from_each_other():
    assert not same_semester("2202", "2203")


def test_current_summer_is_never_merged_with_an_older_session():
    """The formats' summer terms have no stated one-to-one match, so they are
    not treated as the same semester in either direction."""
    assert not same_semester("202020", "2202")
    assert not same_semester("202020", "2203")
    assert find_existing_code("202020", ["2202", "2203"]) is None


def test_related_summer_codes_flags_cross_format_overlap_only():
    from app.core.semesters import related_summer_codes

    assert related_summer_codes("202020", ["2202", "2203", "2201"]) == ["2202", "2203"]
    assert related_summer_codes("2203", ["2202", "202020"]) == ["202020"]
    # Two older sessions in one year is normal, not a warning.
    assert related_summer_codes("2203", ["2202"]) == []
    # Different year, or not summer at all.
    assert related_summer_codes("202120", ["2202"]) == []
    assert related_summer_codes("202010", ["2202"]) == []


def test_summer_sorts_between_spring_and_fall():
    assert newest_first(["202630", "202610", "202620"]) == ["202630", "202620", "202610"]
    assert newest_first(["2205", "2201", "2203", "2202"]) == ["2205", "2203", "2202", "2201"]


def test_store_keeps_summer_sessions_separate(conn):
    s1 = store.get_or_create_course_offering(conn, "2202")
    s2 = store.get_or_create_course_offering(conn, "2203")
    current = store.get_or_create_course_offering(conn, "202020")
    assert len({s1, s2, current}) == 3
