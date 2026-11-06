"""The tab bar is divided into the app's two jobs -- scoring this semester's
exams, and the class record -- by an inert tab styled down to a thin rule.

Qt has no separator for a tab bar. The risk in styling one is that a stylesheet
on a QTabBar makes Qt paint the *whole* bar itself, losing the Mac's own tab
rendering, so these tests check the other tabs are left untouched.

Requires QT_QPA_PLATFORM=offscreen.
"""

import pytest

from app.db import store

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget  # noqa: E402

from app.gui import widgets  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    window = MainWindow(store.connect(tmp_path / "grades.db"))
    window.show()
    return window


def _widths(tabs):
    bar = tabs.tabBar()
    return [bar.tabRect(i).width() for i in range(tabs.count())]


def test_the_divider_is_not_selectable_and_has_no_label(window):
    """A "|" tab reads as a tab you failed to click."""
    index = window.tab_divider_index
    assert window.tabs.isTabEnabled(index) is False
    assert window.tabs.tabText(index) == ""


def test_the_divider_sits_between_the_two_jobs(window):
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    index = window.tab_divider_index

    assert labels[:index] == ["Results", "Dashboard"]
    assert labels[index + 1:] == ["Class History", "Reports", "Rec Letter Info"]


def test_the_divider_is_narrow(window):
    """A rule, not a tab-sized gap."""
    widths = _widths(window.tabs)
    divider = widths.pop(window.tab_divider_index)

    assert divider < min(widths) / 2, (divider, widths)


def test_the_real_tabs_keep_their_own_widths(qapp, tmp_path):
    """If the stylesheet had taken over painting the whole bar, every tab's
    metrics would change -- so this compares against an unstyled bar."""
    plain = QTabWidget()
    for label in ("Results", "Dashboard", "", "Class History", "Reports", "Rec Letter Info"):
        plain.addTab(QWidget(), label)
    plain.show()
    before = _widths(plain)

    widgets.divide_tab_bar(plain, 2)
    after = _widths(plain)

    for i in (0, 1, 3, 4, 5):
        assert after[i] == before[i], f"tab {i} changed width: {before} -> {after}"
    assert after[2] < before[2]


def test_the_rule_follows_the_window_palette(window):
    """A hard-coded light grey vanishes against a dark appearance."""
    colour = widgets.divider_color(window.tabs)
    assert colour.startswith("#") and len(colour) == 7
    assert colour in window.tabs.styleSheet()


def test_only_the_disabled_tab_is_styled(window):
    """So the others are left to the platform style."""
    sheet = window.tabs.styleSheet()
    assert "QTabBar::tab:disabled" in sheet
    # No rule that would catch every tab.
    assert "QTabBar::tab {" not in sheet
    assert "QTabWidget" not in sheet
