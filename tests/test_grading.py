import pytest

from app.core.grading import compute_grade, letter_grade_bucket, percent_to_letter_grade

POSSIBLE_POINTS = 50


def test_normal_blend_no_override():
    # final output.csv row: Individual=32, Group=42, Final=36
    result = compute_grade(32, 42, POSSIBLE_POINTS)
    assert result.effective_group_score == 42
    assert result.final_grade == pytest.approx(36.0)
    assert result.override_original_group_score is None
    assert result.missing_flag is None
    assert result.percent == pytest.approx(72.0)
    assert result.passed is True


def test_weighted_average_with_decimal_result():
    # final output.csv row: Individual=38, Group=46, Final=41.2
    result = compute_grade(38, 46, POSSIBLE_POINTS)
    assert result.final_grade == pytest.approx(41.2)


def test_below_passing_threshold():
    # final output.csv row: Individual=26, Group=38, Final=30.8 (61.6%, fails)
    result = compute_grade(26, 38, POSSIBLE_POINTS)
    assert result.final_grade == pytest.approx(30.8)
    assert result.percent == pytest.approx(61.6)
    assert result.passed is False


def test_group_score_below_individual_is_overridden():
    # final output.csv row: raw Group=36 < Individual=38 -> Group substituted
    # with Individual, Final=38, and "Individual > Group" preserves the raw 36.
    result = compute_grade(38, 36, POSSIBLE_POINTS)
    assert result.effective_group_score == 38
    assert result.final_grade == pytest.approx(38.0)
    assert result.override_original_group_score == 36


def test_override_case_44_vs_42():
    # final output.csv row: Individual=44, raw Group=42 -> Final=44, "* 42"
    result = compute_grade(44, 42, POSSIBLE_POINTS)
    assert result.final_grade == pytest.approx(44.0)
    assert result.override_original_group_score == 42


def test_equal_scores_do_not_trigger_override():
    result = compute_grade(40, 40, POSSIBLE_POINTS)
    assert result.effective_group_score == 40
    assert result.override_original_group_score is None
    assert result.final_grade == pytest.approx(40.0)


def test_missing_group_score():
    result = compute_grade(34, None, POSSIBLE_POINTS)
    assert result.final_grade is None
    assert result.missing_flag == "Missing Group"
    assert result.passed is None


def test_missing_individual_score():
    result = compute_grade(None, 42, POSSIBLE_POINTS)
    assert result.final_grade is None
    assert result.missing_flag == "Missing Individual"


def test_missing_both_scores():
    result = compute_grade(None, None, POSSIBLE_POINTS)
    assert result.final_grade is None
    assert result.missing_flag == "Missing Individual, Missing Group"


@pytest.mark.parametrize(
    "percent,expected",
    [
        (100, "A"),
        (93, "A"),
        (92.9, "A-"),
        (90, "A-"),
        (89.9, "B+"),
        (87, "B+"),
        (83, "B"),
        (80, "B-"),
        (77, "C+"),
        (73, "C"),
        (70, "C-"),
        (69.9, "D+"),
        (67, "D+"),
        (63, "D"),
        (60, "D-"),
        (59.9, "F"),
        (0, "F"),
    ],
)
def test_percent_to_letter_grade_cutoffs(percent, expected):
    assert percent_to_letter_grade(percent) == expected


@pytest.mark.parametrize(
    "percent,expected_bucket",
    [
        (95, "A"),
        (91, "A"),
        (85, "B"),
        (81, "B"),
        (75, "C"),
        (71, "C"),
        (65, "D"),
        (61, "D"),
        (50, "F"),
    ],
)
def test_letter_grade_bucket_collapses_plus_minus(percent, expected_bucket):
    assert letter_grade_bucket(percent) == expected_bucket
