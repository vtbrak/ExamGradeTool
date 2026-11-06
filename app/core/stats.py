"""Summary statistics for a set of percentages.

Kept separate from the charts so the numbers can be tested without a GUI.
"""

import math
from typing import Optional

from app.core.grading import PASS_THRESHOLD_PERCENT


def summarize(values, pass_threshold: float = PASS_THRESHOLD_PERCENT) -> dict:
    """Count, mean, spread and range for a group of scores.

    `sd` is the sample standard deviation (n-1), the usual choice when the
    students at hand are treated as a sample of the course rather than the
    whole population. `se` is the standard error of the mean, which is what
    error bars on a mean should show. Both are None when there's only one
    value, since a single score has no spread.

    `passing` counts the scores at or above `pass_threshold` -- 70% by default,
    the C-/D+ boundary, which is the pass mark for most students. At or above:
    exactly 70 is a C- and passes."""
    values = [v for v in values if v is not None]
    n = len(values)
    if not n:
        return {"n": 0, "mean": None, "sd": None, "se": None,
                "median": None, "min": None, "max": None,
                "passing": 0, "pass_rate": None, "pass_threshold": pass_threshold}

    mean = sum(values) / n
    sd: Optional[float] = None
    se: Optional[float] = None
    if n > 1:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        sd = math.sqrt(variance)
        se = sd / math.sqrt(n)

    ordered = sorted(values)
    middle = n // 2
    median = ordered[middle] if n % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    passing = sum(1 for v in values if v >= pass_threshold)
    return {
        "n": n, "mean": mean, "sd": sd, "se": se,
        "median": median, "min": ordered[0], "max": ordered[-1],
        "passing": passing, "pass_rate": passing / n * 100,
        "pass_threshold": pass_threshold,
    }


def histogram(values, start=0.0, stop=100.0, width=5.0) -> list:
    """Counts in fixed bins, as `(low, high, count)`.

    Fixed bins rather than data-driven ones: a percentage scale is the natural
    axis here, and bins that move with the data make two semesters impossible
    to compare by eye. The top bin includes its upper edge, so 100% is counted."""
    values = [v for v in values if v is not None]
    bins = []
    low = start
    while low < stop:
        high = min(low + width, stop)
        last = high >= stop
        count = sum(
            1 for v in values if (low <= v <= high) if last or v < high
        )
        bins.append((low, high, count))
        low = high
    return bins
