from dataclasses import dataclass
from typing import Optional

INDIVIDUAL_WEIGHT = 0.6
GROUP_WEIGHT = 0.4
PASS_THRESHOLD_PERCENT = 70.0

# (minimum percent, letter grade), highest cutoff first. No A+ on this scale.
LETTER_GRADE_CUTOFFS = [
    (93, "A"),
    (90, "A-"),
    (87, "B+"),
    (83, "B"),
    (80, "B-"),
    (77, "C+"),
    (73, "C"),
    (70, "C-"),
    (67, "D+"),
    (63, "D"),
    (60, "D-"),
    (0, "F"),
]


def percent_to_letter_grade(percent: float) -> str:
    """Full letter grade (with +/-) for a percent score, e.g. 91.5 -> "A-"."""
    for cutoff, letter in LETTER_GRADE_CUTOFFS:
        if percent >= cutoff:
            return letter
    return "F"


def letter_grade_bucket(percent: float) -> str:
    """Base letter only (A/B/C/D/F), collapsing +/- for grouped displays
    like the grade-distribution histogram."""
    return percent_to_letter_grade(percent)[0]


@dataclass(frozen=True)
class GradeResult:
    individual_score: Optional[float]
    raw_group_score: Optional[float]
    possible_points: float
    effective_group_score: Optional[float]
    final_grade: Optional[float]
    override_original_group_score: Optional[float]
    missing_flag: Optional[str]
    percent: Optional[float]
    passed: Optional[bool]


def compute_grade(
    individual_score: Optional[float],
    raw_group_score: Optional[float],
    possible_points: float,
) -> GradeResult:
    """Blend an Individual (A/B) score with a Group (C) score.

    A student's raw Group score is never allowed to pull their grade below
    what their Individual score alone would have earned: if Group < Individual,
    the Individual score is substituted in for the Group side of the blend, and
    the real Group score is preserved via override_original_group_score so it
    stays visible even though it isn't used in the calculation.
    """
    if individual_score is None or raw_group_score is None:
        missing = []
        if individual_score is None:
            missing.append("Missing Individual")
        if raw_group_score is None:
            missing.append("Missing Group")
        return GradeResult(
            individual_score=individual_score,
            raw_group_score=raw_group_score,
            possible_points=possible_points,
            effective_group_score=None,
            final_grade=None,
            override_original_group_score=None,
            missing_flag=", ".join(missing),
            percent=None,
            passed=None,
        )

    overridden = individual_score > raw_group_score
    effective_group_score = individual_score if overridden else raw_group_score
    final_grade = INDIVIDUAL_WEIGHT * individual_score + GROUP_WEIGHT * effective_group_score
    percent = (final_grade / possible_points * 100) if possible_points else None
    passed = percent is not None and percent >= PASS_THRESHOLD_PERCENT

    return GradeResult(
        individual_score=individual_score,
        raw_group_score=raw_group_score,
        possible_points=possible_points,
        effective_group_score=effective_group_score,
        final_grade=final_grade,
        override_original_group_score=raw_group_score if overridden else None,
        missing_flag=None,
        percent=percent,
        passed=passed,
    )
