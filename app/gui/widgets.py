"""Small shared widgets.

**Convention: every tooltip in this app is advertised.**

A tooltip is invisible. Anyone who doesn't happen to hover never learns the
explanation is there, which makes it a poor place to put something worth
knowing -- so wherever we put one, a greyed information mark (ⓘ) goes beside
the control to say so. This applies to *every* widget, in every context, not
just buttons: combo boxes, labels, check boxes, table headers, dialogs.

So: **anything given `setToolTip` should be added to its layout through
`with_hint`.** One line at the call site:

    row.addWidget(with_hint(self.export_button))
    form.addRow("Semester code:", with_hint(self.semester_combo))

`tests/test_hint_indicators.py` walks every window and dialog in the app and
fails if it finds a tooltip without a mark, so this doesn't rely on anyone
remembering it.

A tooltip set or changed *after* construction is handled too -- several are
rebuilt as data loads -- because the mark follows the widget's tooltip through
Qt's ToolTipChange event rather than being given a fixed copy of the text. It
hides itself while the tooltip is empty and reappears when there is one.
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

# U+24D8, CIRCLED LATIN SMALL LETTER I -- a standard character rather than an
# icon file, so it follows the system font and needs no asset.
INFO_CHARACTER = "ⓘ"
# How the sweep in tests/test_hint_indicators.py finds the marks.
HINT_OBJECT_NAME = "hint_indicator"
HINT_COLOR = "#8A8A8E"


class _FollowTooltip(QObject):
    """Keeps a mark showing exactly what its widget's tooltip says.

    Installed on the explained widget, so a tooltip rebuilt later -- the rank
    pool caveats change as semesters are imported -- never leaves the mark
    quoting the old text, or standing next to a widget that no longer has
    anything to explain."""

    def __init__(self, mark: QLabel):
        super().__init__(mark)
        self._mark = mark

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ToolTipChange:
            _apply(self._mark, watched.toolTip())
        return False


def _apply(mark: QLabel, text: str) -> None:
    mark.setToolTip(text)
    # Repeated where a screen reader can reach it: it cannot hover.
    mark.setAccessibleDescription(text)
    mark.setVisible(bool(text))


def hint_label(text: str = "") -> QLabel:
    """The mark on its own, for a layout `with_hint` doesn't suit."""
    mark = QLabel(INFO_CHARACTER)
    mark.setObjectName(HINT_OBJECT_NAME)
    mark.setStyleSheet(f"color: {HINT_COLOR};")
    # A pointer that says "there is something to read here".
    mark.setCursor(Qt.CursorShape.WhatsThisCursor)
    mark.setAccessibleName("More information")
    _apply(mark, text)
    return mark


def with_hint(widget):
    """`widget` with the information mark beside it, as one widget to lay out.

    Returns a container rather than adding two widgets to the caller's row: a
    row's own spacing would leave the mark floating midway between two controls
    instead of plainly belonging to one. The mark sits outside the widget and
    is a label, not a button -- it isn't clickable and must not read as part of
    the control."""
    mark = hint_label(widget.toolTip())
    widget.installEventFilter(_FollowTooltip(mark))

    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(widget)
    layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
    return holder


# Space either side of a separator, so the groups it divides read as groups
# rather than as one row with a line dropped into it.
GROUP_GAP = 10
SEPARATOR_OBJECT_NAME = "group_separator"


def vertical_separator() -> QFrame:
    """A thin vertical rule, for dividing one row of controls into groups.

    The same device as the tab bar's divider: buttons that do different jobs
    sit on one line without reading as one set."""
    line = QFrame()
    line.setObjectName(SEPARATOR_OBJECT_NAME)
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def add_group_separator(layout) -> QFrame:
    """Add a separator to `layout`, with the standard gap either side."""
    line = vertical_separator()
    layout.addSpacing(GROUP_GAP)
    layout.addWidget(line)
    layout.addSpacing(GROUP_GAP)
    return line


def divider_color(widget) -> str:
    """The rule colour, taken from the widget's own palette.

    Not a fixed grey: the window follows the Mac's light or dark appearance,
    and a hard-coded light grey disappears against a dark title bar."""
    return widget.palette().color(QPalette.ColorRole.Mid).name()


def divide_tab_bar(tabs, index: int) -> None:
    """Render the tab at `index` as a thin rule dividing the bar into groups.

    Qt has no separator for a tab bar, so the divider is an inert tab. Left as
    an ordinary tab showing a "|" it reads as a tab you failed to click; styled
    down to a 1px rule with a gap either side it reads as a division.

    Only the disabled tab is styled, so the others keep the Mac's own tab
    rendering -- `tests/test_tab_divider.py` checks their widths are untouched.
    """
    tabs.setTabEnabled(index, False)
    tabs.setTabText(index, "")
    tabs.setStyleSheet(
        "QTabBar::tab:disabled {"
        "  min-width: 1px; max-width: 1px; padding: 0px;"
        f" margin-left: {GROUP_GAP}px; margin-right: {GROUP_GAP}px;"
        f" border: none; background: {divider_color(tabs)}; color: transparent;"
        "}"
    )
