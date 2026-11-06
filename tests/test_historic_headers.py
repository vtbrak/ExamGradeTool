"""Every semester's real D2L headers (Fall 2016 to Fall 2026) file where the
2026-10-21 decisions say they should.

`historic_headers.xlsx` holds one label row and one header row per semester,
copied from the actual course-grade exports. This pins the importer's filing
of every column that has ever appeared, so a rule change that would misfile an
old semester fails here first."""

from collections import Counter
from pathlib import Path

import openpyxl
import pytest

from app.core.course_export import (
    COMPONENT_EXTRA_CREDIT,
    COMPONENT_LAB,
    COMPONENT_OTHER,
    COMPONENT_UNCOUNTED,
    FORMAT_INDIVIDUAL_PARTNER_FINAL,
    FORMAT_SINGLE_SCORE,
    classify_component,
    find_component_columns,
    find_exam_columns,
    is_uncounted,
    parse_item_header,
)

FIXTURE = Path(__file__).parent / "fixtures" / "historic_headers.xlsx"


def _semesters():
    ws = openpyxl.load_workbook(FIXTURE, read_only=True).active
    rows = [[c for c in r if c is not None] for r in ws.iter_rows(values_only=True)]
    return {rows[i][0]: rows[i + 1] for i in range(0, len(rows), 2)}


SEMESTERS = _semesters()
THREE_COLUMN = {"Current", "Spring 2025", "Fall 2025", "Spring 2024", "Fall 2024",
                "Spring 2023", "Fall 2023", "Spring 2022", "Fall 2022"}


def test_every_semester_is_present():
    assert len(SEMESTERS) == 20
    assert "Fall 2016" in SEMESTERS and "Current" in SEMESTERS


@pytest.mark.parametrize("semester", sorted(SEMESTERS))
def test_four_exams_in_the_expected_layout(semester):
    exams, layout = find_exam_columns(SEMESTERS[semester])
    assert [e.exam_name for e in exams] == ["Exam 1", "Exam 2", "Exam 3", "Exam 4"]
    if semester in THREE_COLUMN:
        assert layout == FORMAT_INDIVIDUAL_PARTNER_FINAL
        assert all(e.individual and e.partner and e.final for e in exams)
        assert {e.possible_points for e in exams} == {50}
    else:
        assert layout == FORMAT_SINGLE_SCORE
        assert all(e.single for e in exams)
    assert all(e.possible_points for e in exams)


@pytest.mark.parametrize("semester", sorted(SEMESTERS))
def test_identity_lab_and_extra_credit_are_found(semester):
    headers = SEMESTERS[semester]
    assert "OrgDefinedId" in headers, "every sheet carries the student ID"
    assert "Last Name" in headers and "First Name" in headers
    components = find_component_columns(headers)
    assert len(components.get(COMPONENT_LAB, [])) == 1
    assert 1 <= len(components.get(COMPONENT_EXTRA_CREDIT, [])) <= 3
    assert components.get(COMPONENT_OTHER), "every semester has quizzes or participation"


def test_textbook_platform_items_and_placeholders_are_not_counted():
    uncounted = Counter()
    for headers in SEMESTERS.values():
        for column, _ in find_component_columns(headers).get(COMPONENT_UNCOUNTED, []):
            uncounted[parse_item_header(column)[0]] += 1
    assert set(uncounted) == {
        "Essential Cell Biology 6th Edition",
        "Essential Cell Biology 6th Edition animations",
        "Essential Cell Biology - Smartwork 5",
        "Essential Cell Biology - eBook",
        "Not Graded",
    }
    # ...and none of those ever lands in a counted bin.
    for headers in SEMESTERS.values():
        for component in (COMPONENT_LAB, COMPONENT_EXTRA_CREDIT, COMPONENT_OTHER):
            for column, _ in find_component_columns(headers).get(component, []):
                label, max_points, _ = parse_item_header(column)
                assert not is_uncounted(label, max_points), (component, label)


def test_other_is_a_sane_size_once_platform_items_are_out():
    """Spring 2020 and Fall 2020 carried 100-point textbook markers that used to
    inflate 'other' to 276 and 229 points."""
    for semester in ("Spring 2020", "Fall 2020"):
        other = find_component_columns(SEMESTERS[semester]).get(COMPONENT_OTHER, [])
        assert sum(m for _, m in other) < 150


def test_abandoned_test_items_are_never_graded_items():
    """Fall 2016 and Fall 2017 carried abandoned D2L "Test" items. They are
    Selectbox / Calculated columns, not Numeric grade items, so the importer
    never treats them as grades -- whether or not a given export still has them."""
    for header in (
        "TEst Grade Symbol <Selectbox MaxPoints:2 Category:Test>",
        "Test 2 Grade Symbol <Selectbox MaxPoints:2 Category:Test>",
        "Test Calculated Numerator <Calculated>",
        "Test Calculated Denominator <Calculated>",
        "Test Subtotal Numerator",
        "Test Subtotal Denominator",
    ):
        assert parse_item_header(header) is None, header
    for semester in ("Fall 2016", "Fall 2017"):
        leftovers = [h for h in SEMESTERS[semester] if h.lower().startswith("test")]
        assert all(parse_item_header(h) is None for h in leftovers)


def test_subtotals_scheme_symbols_and_email_are_ignored():
    for headers in SEMESTERS.values():
        for h in headers:
            if "Subtotal" in h or h.endswith("Scheme Symbol") or h in ("Email", "End-of-Line Indicator"):
                assert parse_item_header(h) is None, h


@pytest.mark.parametrize(
    "label, max_points, expected",
    [
        ("Not Graded", 0.01, True),
        ("Essential Cell Biology 6th Edition", 100, True),
        ("Essential Cell Biology - Smartwork 5", 100, True),
        ("Some  Placeholder", 0.5, True),  # anything under a point
        ("Module 2: Proteins Quiz", 6, False),
        ("Class Participation", 12, False),
        ("Lab Grade", 275, False),
    ],
)
def test_is_uncounted_rules(label, max_points, expected):
    assert is_uncounted(label, max_points) is expected


def test_classify_component_routes_uncounted_before_other():
    assert classify_component("Not Graded", None, 0.01) == COMPONENT_UNCOUNTED
    assert classify_component("Essential Cell Biology - eBook", None, 100) == COMPONENT_UNCOUNTED
    assert classify_component("Class Participation", None, 12) == COMPONENT_OTHER
    assert classify_component("Extra Credit - reflection", None, 4) == COMPONENT_EXTRA_CREDIT
