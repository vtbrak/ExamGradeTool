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
    # Points added to the whole exam, and what the blend came to before they
    # were added. Kept so the curve stays visible rather than disappearing
    # into a final grade nobody can account for.
    curve_points: float = 0.0
    uncurved_final_grade: Optional[float] = None

    @property
    def curve_capped(self) -> bool:
        """True when the curve was trimmed to keep the score within the exam.

        Worth showing: a student already near the top gets less of the curve
        than everyone else, which is a question someone will eventually ask."""
        if self.final_grade is None or self.uncurved_final_grade is None:
            return False
        return self.curve_points > 0 and (
            self.final_grade < self.uncurved_final_grade + self.curve_points
        )


def compute_grade(
    individual_score: Optional[float],
    raw_group_score: Optional[float],
    possible_points: float,
    curve_points: float = 0.0,
) -> GradeResult:
    """Blend an Individual (A/B) score with a Group (C) score.

    A student's raw Group score is never allowed to pull their grade below
    what their Individual score alone would have earned: if Group < Individual,
    the Individual score is substituted in for the Group side of the blend, and
    the real Group score is preserved via override_original_group_score so it
    stays visible even though it isn't used in the calculation.

    `curve_points` is added to every student's final grade, **capped at the
    exam's point total**: a curve can lift a score to full marks but never past
    them, so no one finishes with more points than the exam was worth. The cap
    is applied here rather than at the call sites so that the percentage, the
    pass/fail line, the saved score and the D2L export all see the same number.
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
    uncurved = INDIVIDUAL_WEIGHT * individual_score + GROUP_WEIGHT * effective_group_score

    final_grade = uncurved + (curve_points or 0.0)
    if possible_points:
        # The cap. A curve can take a student to full marks and no further.
        final_grade = min(final_grade, possible_points)
    # Rounded because the blend produces values like 41.60000000000001, which
    # would otherwise show up in the gradebook and in exported CSVs.
    final_grade = round(final_grade, 2)

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
        curve_points=curve_points or 0.0,
        uncurved_final_grade=round(uncurved, 2),
    )
