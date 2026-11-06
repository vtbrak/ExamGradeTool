"""The Rec Letter Info tab: class-rank cutoffs plus student lookup.
Requires QT_QPA_PLATFORM=offscreen.
"""

from pathlib import Path

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.course_export import parse_course_export  # noqa: E402
from app.db import store  # noqa: E402
from app.gui.rec_letter_view import RecLetterInfoView  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
TRIPLE = FIXTURES / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def search_immediately(monkeypatch):
    """The view waits SEARCH_DELAY_MS after typing before it searches; these
    tests set the text and read the result on the next line, so search at once.
    The delay itself is covered in test_review_efficiency.py."""
    monkeypatch.setattr(
        RecLetterInfoView, "_schedule_search", lambda self, _text: self.flush_search()
    )


@pytest.fixture
def view(qapp, tmp_path):
    conn = store.connect(tmp_path / "grades.db")
    offering = store.get_or_create_course_offering(conn, "202430")
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")
    store.save_historical_exam_scores(conn, offering, parsed)
    v = RecLetterInfoView(conn)
    v.refresh()
    return v


def _rank_rows(view):
    return [
        tuple(view.rank_table.item(r, c).text() for c in range(3))
        for r in range(view.rank_table.rowCount())
    ]


def test_rank_table_shows_the_five_tiers(view):
    assert [row[0] for row in _rank_rows(view)] == [
        "Top 0.1%",
        "Top 1%",
        "Top 5%",
        "Top 10%",
        "Top 20%",
    ]


def test_the_pool_note_shows_the_count_and_hides_the_caveats(view):
    """In a narrow column the caveats buried the one number worth seeing, so
    they moved to the tooltip rather than being dropped."""
    assert view.pool_label.text() == "Pool: 3 course grades"
    assert "Ties are not split" in view.pool_label.toolTip()


def test_a_semester_without_course_grades_is_explained_in_the_tooltip(qapp, tmp_path):
    """The explanation still exists -- it's a hover away, not dropped."""
    conn = store.connect(tmp_path / "partial.db")
    with_grades = store.get_or_create_course_offering(conn, "202430")
    store.get_or_create_course_offering(conn, "202630")  # no sheet imported
    store.save_course_grades(conn, with_grades, parse_course_export(TRIPLE).rows, "s.csv")

    v = RecLetterInfoView(conn)
    v.refresh()
    assert v.pool_label.text() == "Pool: 3 course grades"
    assert "Not included: 202630" in v.pool_label.toolTip()


def test_unsupported_tier_is_marked_provisional(view):
    top_tenth = _rank_rows(view)[0]
    assert "provisional" in top_tenth[1]
    assert "pool too small" in top_tenth[2]


def test_cutoffs_are_percentages_never_raw_scores(view):
    """Ranking is percentage-only -- no raw numerator should appear here."""
    for _label, cutoff, _count in _rank_rows(view)[1:]:
        assert cutoff.endswith("%")
        assert "/" not in cutoff


def test_search_finds_a_student_and_shows_their_rank(view):
    view.search_box.setText("Alpha")
    assert view.results_table.rowCount() == 1
    assert "Ann Alpha" in view.detail_label.text()
    assert "%" in view.detail_label.text()


def test_switching_searches_does_not_leave_a_stale_student(view):
    """Regression: the results table repopulates with row 0 already selected,
    so the selection signal never fires -- the detail panel used to keep showing
    the previous student's grades under the new search."""
    view.search_box.setText("Alpha")
    assert "Ann Alpha" in view.detail_label.text()

    view.search_box.setText("Bravo")
    assert "Ben Bravo" in view.detail_label.text()
    assert "Alpha" not in view.detail_label.text()


def test_no_match_clears_the_detail_panel(view):
    view.search_box.setText("Alpha")
    assert view.detail_table.rowCount() > 0

    view.search_box.setText("zzzzz")
    assert view.results_table.rowCount() == 0
    assert view.detail_table.rowCount() == 0
    assert "No student matches" in view.detail_label.text()


def _detail_rows(view):
    return [
        tuple(view.detail_table.item(r, c).text() for c in range(4))
        for r in range(view.detail_table.rowCount())
    ]


def test_detail_shows_course_grade_and_exams_with_both_figures(view):
    view.search_box.setText("Alpha")
    rows = _detail_rows(view)
    course = [r for r in rows if "course grade" in r[1]]
    exams = [r for r in rows if r[1].startswith("Exam")]
    assert len(course) == 1
    assert len(exams) == 2
    # Both carry a score and a percentage.
    assert "/" in exams[0][2] and exams[0][3].endswith("%")
    assert course[0][3].endswith("%")


def test_detail_has_no_class_rank_column(view):
    """Rank belongs in the headline as a tier, not per row."""
    headers = [
        view.detail_table.horizontalHeaderItem(c).text()
        for c in range(view.detail_table.columnCount())
    ]
    assert headers == ["Semester", "Item", "Score", "Percent"]


def test_detail_breaks_the_course_grade_into_its_parts(qapp, tmp_path):
    """Lab, extra credit and everything else, each with score and percentage
    the way the exams are shown."""
    import pandas as pd

    conn = store.connect(tmp_path / "parts.db")
    offering = store.get_or_create_course_offering(conn, "202430")
    path = tmp_path / "course.csv"
    pd.DataFrame(
        [{
            "OrgDefinedId": "1234567", "Last Name": "Alpha", "First Name": "Ann",
            "Exam 1 - Final Points Grade <Numeric MaxPoints:50 Category:Exams>": 40,
            "Lab Grade Points Grade <Numeric MaxPoints:293>": 256.7,
            "Extra Credit 1 Points Grade <Numeric MaxPoints:4 Category:Extra Credit>": 4,
            "Module 02: Proteins Quiz Points Grade <Numeric MaxPoints:6 Category:Quizzes>": 5.5,
            "Module 01 Muddiest Point Points Grade <Numeric MaxPoints:2 Category:Muddiest Point>": 2,
            "Adjusted Final Grade Numerator": 500,
            "Adjusted Final Grade Denominator": 600,
        }]
    ).to_csv(path, index=False)
    store.save_course_grades(conn, offering, parse_course_export(path).rows, "course.csv")

    v = RecLetterInfoView(conn)
    v.refresh()
    v.search_box.setText("Alpha")
    items = {r[1]: (r[2], r[3]) for r in _detail_rows(v)}

    assert items["Lab grade"] == ("256.7 / 293", "87.6%")
    # Extra credit shows the points and no percentage: it's a bonus, so a
    # proportion of it says nothing.
    assert items["Extra credit"] == ("4 / 4", "")
    assert items["Other grades (quizzes, etc.)"] == ("7.5 / 8", "93.8%")


def test_headline_gives_a_tier_not_a_rank_number(view):
    view.search_box.setText("Alpha")
    headline = view.detail_label.text()
    assert "%" in headline
    assert "rank" not in headline.lower()  # the number isn't shown


def test_a_student_outside_every_tier_is_marked_below_20(qapp, tmp_path):
    conn = store.connect(tmp_path / "below.db")
    offering = store.get_or_create_course_offering(conn, "202430")
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "sheet.csv")

    pool = store.get_course_grade_pool(conn)
    worst = min(pool)
    assert store.rank_for_percent(pool, worst)["label"] == "below 20%"
    # The best of three reaches the most selective *supported* tier, which in a
    # pool this small is Top 1% -- 0.1% needs ~1000 records.
    assert store.rank_for_percent(pool, max(pool))["label"] == "Top 1%"


def test_empty_pool_renders_without_cutoffs(qapp, tmp_path):
    conn = store.connect(tmp_path / "empty.db")
    v = RecLetterInfoView(conn)
    v.refresh()
    assert [row[1] for row in _rank_rows(v)] == ["—"] * 5
    assert v.pool_label.text() == "No course grades yet."


# --- the two rank scales -----------------------------------------------------

def _rank_rows_of(table):
    return [
        tuple(table.item(r, c).text() for c in range(3)) for r in range(table.rowCount())
    ]


def test_both_rank_scales_are_shown_and_labelled(view):
    """Course grades and exam averages are different measures, so both scales
    appear with their own pool rather than one standing for the other."""
    course = _rank_rows_of(view.rank_table)
    exams = _rank_rows_of(view.exam_rank_table)

    assert [r[0] for r in course] == [r[0] for r in exams]  # same five tiers
    assert "course grades" in view.pool_label.text()
    assert "exam averages" in view.exam_pool_label.text()
    # Which measure each is stays explained, in the tooltip.
    assert "lab and quizzes" in view.exam_pool_label.toolTip()


def test_the_exam_scale_uses_the_exam_pool(view):
    assert view.exam_pool_label.text() == "Pool: 3 exam averages"
    assert "per student per semester" in view.exam_pool_label.toolTip()


def test_an_empty_exam_record_says_so(qapp, tmp_path):
    conn = store.connect(tmp_path / "noexams.db")
    offering = store.get_or_create_course_offering(conn, "202430")
    store.save_course_grades(conn, offering, parse_course_export(TRIPLE).rows, "s.csv")

    v = RecLetterInfoView(conn)
    v.refresh()
    assert v.exam_pool_label.text() == "No exam scores yet."


def test_the_headline_gives_both_standings_for_the_student(qapp, tmp_path):
    conn = store.connect(tmp_path / "both.db")
    offering = store.get_or_create_course_offering(conn, "202430")
    parsed = parse_course_export(TRIPLE)
    store.save_course_grades(conn, offering, parsed.rows, "s.csv")
    store.save_historical_exam_scores(conn, offering, parsed)

    v = RecLetterInfoView(conn)
    v.refresh()
    v.search_box.setText("Alpha")
    headline = v.detail_label.text()

    assert "<b>Course:</b>" in headline  # the course grade, labelled and bold
    assert "<b>Exam:</b>" in headline  # and the exam average on the next line
    assert "percentile of" in headline  # with its percentile
    assert "rank " not in headline.lower()  # never a bare rank number

    # Name, ID and semester share the first line; the two standings follow.
    lines = headline.split("<br>")
    assert "ID" in lines[0] and "2024" in lines[0]
    assert lines[1].startswith("<b>Course:</b>")
    assert lines[2].startswith("<b>Exam:</b>")


def test_percentile_wording_reads_correctly_at_both_ends():
    from app.gui.rec_letter_view import _ordinal

    # "top 99%" would be true and useless for a weak student; a percentile is
    # unambiguous at both ends.
    assert _ordinal(0.5) == "1st"
    assert [_ordinal(n) for n in (1, 2, 3, 11, 12, 13, 21, 100)] == [
        "1st", "2nd", "3rd", "11th", "12th", "13th", "21st", "100th",
    ]
