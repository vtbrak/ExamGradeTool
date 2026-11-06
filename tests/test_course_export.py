from pathlib import Path

import pytest

from app.core.course_export import (
    FORMAT_INDIVIDUAL_PARTNER_FINAL,
    FORMAT_SINGLE_SCORE,
    find_exam_columns,
    parse_course_export,
    parse_item_header,
)

FIXTURES = Path(__file__).parent / "fixtures"
TRIPLE = FIXTURES / "synthetic_course_export.csv"
SINGLE = FIXTURES / "synthetic_course_export_single.csv"


def test_parse_item_header_pulls_label_max_and_category():
    label, max_points, category = parse_item_header(
        "Exam 1 - Individual Points Grade <Numeric MaxPoints:50 Category:Exams>"
    )
    assert label == "Exam 1 - Individual"
    assert max_points == 50
    assert category == "Exams"


def test_parse_item_header_ignores_non_item_columns():
    for column in (
        "Adjusted Final Grade Numerator",
        "End-of-Line Indicator",
        "Last Name",
        "Exams Subtotal Denominator",
    ):
        assert parse_item_header(column) is None


def test_exam_columns_detected_by_header_not_position():
    """Column order must not matter -- the headers are what identify an exam."""
    columns = [
        "Exam 2 - Final Points Grade <Numeric MaxPoints:50 Category:Exams>",
        "Exam 1 - Individual Points Grade <Numeric MaxPoints:50 Category:Exams>",
        "Exam 2 - Individual Points Grade <Numeric MaxPoints:50 Category:Exams>",
        "Exam 1 - Final Points Grade <Numeric MaxPoints:50 Category:Exams>",
    ]
    exams, layout = find_exam_columns(columns)
    assert [e.exam_name for e in exams] == ["Exam 1", "Exam 2"]
    assert layout == FORMAT_INDIVIDUAL_PARTNER_FINAL


def test_exams_sort_numerically_not_lexically():
    columns = [
        f"Exam {n} Points Grade <Numeric MaxPoints:80 Category:>" for n in (1, 10, 2)
    ]
    exams, _ = find_exam_columns(columns)
    assert [e.exam_name for e in exams] == ["Exam 1", "Exam 2", "Exam 10"]


def test_sibling_items_are_not_mistaken_for_exams():
    """"Student Questions - Exam 1" and "Lecture Summary 1" are separate grade
    items that live right next to the exam columns in real sheets."""
    parsed = parse_course_export(TRIPLE)
    assert parsed.exam_names == ["Exam 1", "Exam 2"]


def test_triple_layout_reads_all_three_rounds():
    parsed = parse_course_export(TRIPLE)
    assert parsed.layout == FORMAT_INDIVIDUAL_PARTNER_FINAL
    assert parsed.has_id_column is True
    assert len(parsed.rows) == 3

    exam1 = parsed.rows[0].exams["Exam 1"]
    assert (exam1.individual_score, exam1.raw_group_score, exam1.final_score) == (30, 40, 34)
    assert exam1.possible_points == 50
    assert exam1.single_score_only is False


def test_single_score_layout_leaves_rounds_empty():
    parsed = parse_course_export(SINGLE)
    assert parsed.layout == FORMAT_SINGLE_SCORE
    exam1 = parsed.rows[0].exams["Exam 1"]
    assert exam1.single_score_only is True
    assert exam1.individual_score is None
    assert exam1.raw_group_score is None
    assert exam1.final_score == 55
    assert exam1.possible_points == 80


def test_adjusted_grade_pair_and_percent():
    parsed = parse_course_export(TRIPLE)
    row = parsed.rows[0]
    assert (row.adjusted_numerator, row.adjusted_denominator) == (474.5, 587)
    assert row.adjusted_percent == pytest.approx(474.5 / 587 * 100)


def test_denominator_varies_per_student():
    """D2L drops items a student was exempted from, so the denominator is
    per-student -- which is why the pair is stored rather than a percent."""
    parsed = parse_course_export(TRIPLE)
    assert {r.adjusted_denominator for r in parsed.rows} == {587, 543}


def test_identity_columns_are_read():
    parsed = parse_course_export(TRIPLE)
    row = parsed.rows[0]
    assert row.student_id == "0900001"
    assert (row.first_name, row.last_name) == ("Ann", "Alpha")
    assert row.display_name == "Ann Alpha"


CALCULATED_ONLY = FIXTURES / "synthetic_course_export_calculated_only.csv"


def test_sheet_without_adjusted_columns_falls_back_to_calculated():
    """The oldest exports have no Adjusted Final Grade columns at all -- only
    the calculated pair -- so the basis is recorded rather than assumed."""
    from app.core.course_export import BASIS_ADJUSTED, BASIS_CALCULATED

    parsed = parse_course_export(CALCULATED_ONLY)
    row = parsed.rows[0]
    assert row.grade_basis == BASIS_CALCULATED
    assert row.adjusted_numerator is None
    assert (row.numerator, row.denominator) == (427, 450)
    assert row.percent == pytest.approx(94.88888888888889)

    assert parse_course_export(TRIPLE).rows[0].grade_basis == BASIS_ADJUSTED


def test_adjusted_is_preferred_when_both_pairs_present():
    row = parse_course_export(TRIPLE).rows[0]
    assert (row.calculated_numerator, row.adjusted_numerator) == (470.5, 474.5)
    assert row.numerator == 474.5


def test_exempt_cell_is_dropped_not_zeroed():
    """A student exempted from an exam has the literal text "Exempt" in that
    cell. It must not become a zero -- their denominator is reduced instead."""
    parsed = parse_course_export(CALCULATED_ONLY)
    exempted = parsed.rows[1]
    assert "Exam 2" not in exempted.exams
    assert exempted.exams["Exam 1"].final_score == 62.5
    assert exempted.denominator == 378  # vs 450 for the un-exempted student


def test_a_third_possible_points_value():
    """50, 80, and 72 have all been seen for the same "Exam 1"."""
    parsed = parse_course_export(CALCULATED_ONLY)
    assert parsed.exam_columns[0].possible_points == 72


def test_email_column_does_not_become_an_exam():
    parsed = parse_course_export(CALCULATED_ONLY)
    assert parsed.exam_names == ["Exam 1", "Exam 2"]
    assert parsed.has_id_column is True


def test_one_blank_id_does_not_turn_other_ids_into_decimals(tmp_path):
    """Same regression as the Scantron parser: a blank OrgDefinedId widens the
    column to floats, which must not leak "1234567.0" into matching."""
    import pandas as pd

    header = "Exam 1 Points Grade <Numeric MaxPoints:80 Category:>"
    path = tmp_path / "course.xlsx"
    pd.DataFrame(
        [
            {"OrgDefinedId": 1234567, "Last Name": "Alpha", "First Name": "Ann", header: 60},
            {"OrgDefinedId": None, "Last Name": "Bravo", "First Name": "Ben", header: 70},
        ]
    ).to_excel(path, index=False)

    parsed = parse_course_export(path)
    assert [r.student_id for r in parsed.rows] == ["1234567", None]


def _course_file(tmp_path, ids):
    import pandas as pd

    header = "Exam 1 Points Grade <Numeric MaxPoints:80 Category:>"
    path = tmp_path / "course.csv"
    pd.DataFrame(
        [
            {"OrgDefinedId": v, "Last Name": f"L{i}", "First Name": f"F{i}", header: 60}
            for i, v in enumerate(ids)
        ]
    ).to_csv(path, index=False)
    return path


def test_problem_ids_are_reported_and_kept_out_of_the_import(tmp_path):
    parsed = parse_course_export(_course_file(tmp_path, ["1234567", "123456", "12345", "X9"]))

    assert [r.student_id for r in parsed.rows] == ["1234567", "0123456"]
    assert [(p.row, p.student_id, p.problem) for p in parsed.id_problem_rows] == [
        (4, "12345", "5 digits, expected 7"),
        (5, "X9", "not a number"),
    ]


def test_only_six_digit_ids_are_padded(tmp_path):
    parsed = parse_course_export(_course_file(tmp_path, ["123456", "1234567"]))
    assert [r.student_id for r in parsed.rows] == ["0123456", "1234567"]
