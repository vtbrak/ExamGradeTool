"""Efficiency follow-ups from the October 2026 review, checked for equivalence:
the faster paths must give the same answers as the ones they replace.
Requires QT_QPA_PLATFORM=offscreen for the view tests."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app.core.course_export import parse_course_export
from app.db import store
from app.gui import rec_letter_view as rlv

FIXTURES = Path(__file__).parent / "fixtures"
TRIPLE = FIXTURES / "synthetic_course_export.csv"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "grades.db")
    parsed = parse_course_export(TRIPLE)
    for code, closed in (("202430", True), ("202510", False)):
        offering = store.get_or_create_course_offering(c, code)
        store.save_course_grades(c, offering, parsed.rows, "sheet.csv")
        store.save_historical_exam_scores(c, offering, parsed, source_file="sheet.csv")
        if closed:
            store.set_semester_closed(c, offering, True)
    yield c
    c.close()


def test_describe_semesters_matches_describe_semester(conn):
    labels = store.describe_semesters(conn)
    assert set(labels) == {"202430", "202510"}
    for code, label in labels.items():
        assert label == store.describe_semester(conn, code)
    assert "in progress" in labels["202510"] and "in progress" not in labels["202430"]


def test_student_exam_averages_match_the_full_aggregate(conn):
    everyone = store.get_all_student_semester_scores(conn)
    assert everyone, "fixture should produce exam averages"
    for student_id in {r["student_id"] for r in everyone}:
        expected = sorted(
            (r for r in everyone if r["student_id"] == student_id),
            key=lambda r: r["semester_code"], reverse=True,
        )
        got = store.get_student_exam_averages(conn, student_id)
        assert [(r["semester_code"], round(r["overall_percent"], 6)) for r in got] == [
            (r["semester_code"], round(r["overall_percent"], 6)) for r in expected
        ]


@pytest.mark.parametrize("percent", [100.0, 92.5, 85.0, 70.1, 50.0, 0.0])
def test_rank_with_precomputed_cutoffs_matches_without(percent):
    pool = [95.0, 92.5, 92.5, 88.0, 85.0, 85.0, 79.0, 70.1, 64.0, 50.0] * 30
    cutoffs = store.class_rank_cutoffs(pool)
    assert store.rank_for_percent(pool, percent, cutoffs) == store.rank_for_percent(pool, percent)


def test_rank_counts_only_strictly_better_scores():
    pool = [90.0, 80.0, 80.0, 70.0]
    assert store.rank_for_percent(pool, 80.0)["rank"] == 2  # ties share the rank
    assert store.rank_for_percent(pool, 95.0)["rank"] == 1
    assert store.rank_for_percent(pool, 60.0)["rank"] == 5


# --- the view ------------------------------------------------------------------------

@pytest.fixture
def view(qapp, conn):
    v = rlv.RecLetterInfoView(conn)
    v.refresh()
    return v


def test_a_search_renders_the_detail_once(view, monkeypatch):
    renders = []
    real = view._render_detail
    monkeypatch.setattr(view, "_render_detail", lambda record: renders.append(record["student_id"]) or real(record))

    view.search_box.setText("Alpha")
    view.flush_search()
    assert len(renders) == 1

    view.search_box.setText("Bravo")
    view.flush_search()
    assert len(renders) == 2 and "Ben Bravo" in view.detail_label.text()


def test_typing_is_debounced_into_one_search(view, monkeypatch):
    searches = []
    real = view._on_search
    monkeypatch.setattr(view, "_on_search", lambda text: searches.append(text) or real(text))

    for text in ("A", "Al", "Alp", "Alph", "Alpha"):
        view.search_box.setText(text)
    assert searches == []  # nothing yet: the timer is pending
    assert view._search_timer.isActive() and view._search_timer.isSingleShot()
    assert view._search_timer.interval() == rlv.SEARCH_DELAY_MS

    # The delay expiring (driven directly rather than by waiting on the clock,
    # which is flaky under other tests' pending repaints).
    view._search_timer.stop()
    view._search_timer.timeout.emit()
    assert searches == ["Alpha"]
    assert "Ann Alpha" in view.detail_label.text()


def test_cutoffs_are_cached_on_refresh(view):
    assert view.pool_cutoffs == store.class_rank_cutoffs(view.pool)
    assert view.exam_pool_cutoffs == store.class_rank_cutoffs(view.exam_pool)
