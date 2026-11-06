"""Every pop-up looks the same: first line bold and two points larger, the rest
regular weight.

Qt renders all of a message box's `text` bold, so the two parts are built as
rich text (app/gui/dialogs.py). These tests render that markup the way Qt will
and read the resolved weight and size back off it, rather than asserting on the
HTML string -- the string is the means, the appearance is the requirement.

Requires QT_QPA_PLATFORM=offscreen.
"""

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

from app.gui import dialogs  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _rendered(html, base_font):
    """(weight, point size, text) per run, as Qt will lay it out."""
    doc = QTextDocument()
    doc.setDefaultFont(base_font)
    doc.setHtml(html)
    runs = []
    block = doc.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                font = fragment.charFormat().font()
                runs.append((font.weight(), font.pointSizeF(), fragment.text()))
            iterator += 1
        block = block.next()
    return runs


def test_the_first_line_is_bold_and_two_points_larger(qapp):
    base = QApplication.instance().font()
    html = dialogs.format_message("The headline.\nThe body, which is longer.")
    runs = _rendered(html, base)

    headline = next(r for r in runs if "headline" in r[2])
    body = next(r for r in runs if "body" in r[2])

    assert headline[0] == 700, "the first line is bold"
    assert body[0] == 400, "the rest is regular weight"
    assert headline[1] == pytest.approx(body[1] + dialogs.HEADLINE_POINTS_LARGER)


def test_a_bold_label_font_does_not_leak_into_the_body(qapp):
    """QMessageBox sets a bold font on its label, so the body has to override
    it rather than merely not ask for bold."""
    base = QApplication.instance().font()
    bold = QtWidgets.QApplication.font()
    bold.setBold(True)
    html = dialogs.format_message("Headline.\nBody text.")

    body = next(r for r in _rendered(html, bold) if "Body" in r[2])
    assert body[0] == 400
    # And it is still smaller than the headline.
    headline = next(r for r in _rendered(html, bold) if "Headline" in r[2])
    assert headline[1] > body[1]


def test_a_one_line_message_is_all_headline(qapp):
    headline, body = dialogs.split_rendered(dialogs.format_message("Saved."))
    assert headline == "Saved."
    assert body == ""


def test_blank_lines_in_the_body_survive(qapp):
    """Several messages are a headline plus a list, spaced with blank lines."""
    message = "Import complete.\n\nCourse grades saved for 20 student(s).\nExam scores saved: 80."
    headline, body = dialogs.split_rendered(dialogs.format_message(message))

    assert headline == "Import complete."
    assert body == "Course grades saved for 20 student(s).\nExam scores saved: 80."


def test_text_is_not_mistaken_for_markup(qapp):
    """A message is plain text. Anything that looks like a tag is shown, not
    interpreted -- and a filename with an ampersand in it still reads right."""
    headline, body = dialogs.split_rendered(
        dialogs.format_message("Could not read <sheet>.\nSaved to Grades & Labs/final.csv")
    )
    assert headline == "Could not read <sheet>."
    assert body == "Saved to Grades & Labs/final.csv"


def test_apostrophes_and_quotes_are_left_alone(qapp):
    """These strings are read by people; an entity in place of an apostrophe
    would be worse than the escaping it avoids."""
    html = dialogs.format_message("'5.5' isn't a number.")
    assert "&#x27;" not in html and "&quot;" not in html
    assert dialogs.split_rendered(html)[0] == "'5.5' isn't a number."


# --- the whole app goes through it -------------------------------------------

def test_every_dialog_in_the_app_is_formatted(qapp):
    """A QMessageBox built at a call site is how the app ends up with two
    different looks, so there should be none left."""
    from pathlib import Path
    import re

    offenders = {}
    for path in list(Path("app/gui").glob("*.py")) + [Path("app/main.py")]:
        if path.name == "dialogs.py":
            continue
        source = path.read_text()
        hits = re.findall(
            r"QMessageBox\.(information|warning|critical|question)\(|QMessageBox\(", source
        )
        if hits:
            offenders[str(path)] = hits
    assert offenders == {}


def test_the_helpers_pass_the_formatted_text_to_qt(qapp, monkeypatch):
    seen = {}
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, name, lambda parent, title, text, _n=name: seen.setdefault(_n, text)
        )
        getattr(dialogs, name)(None, "Title", f"{name} headline.\nand a body.")
        assert dialogs.split_rendered(seen[name]) == (f"{name} headline.", "and a body.")


def test_question_defaults_to_no(qapp, monkeypatch):
    """A question that is answered by pressing Return should not be a yes."""
    captured = {}

    def fake(parent, title, text, buttons, default):
        captured.update(buttons=buttons, default=default)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", fake)
    dialogs.question(None, "Title", "Really?\nIt cannot be undone.")

    assert captured["default"] == QMessageBox.StandardButton.No
    assert captured["buttons"] == (
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )


def test_question_passes_an_explicit_default_through(qapp, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda parent, title, text, buttons, default: captured.setdefault("d", default),
    )
    dialogs.question(
        None, "Title", "Close it?", default=QMessageBox.StandardButton.Yes
    )
    assert captured["d"] == QMessageBox.StandardButton.Yes


def test_indentation_in_a_body_list_is_kept(qapp):
    """Several dialogs list details indented by two spaces. HTML collapses
    runs of spaces, so without help the list would come out flat."""
    from PySide6.QtGui import QTextDocument

    message = "This will delete:\n  3 course grade(s)\n  6 exam score(s)"
    doc = QTextDocument()
    doc.setDefaultFont(QApplication.instance().font())
    doc.setHtml(dialogs.format_message(message))

    lines = doc.toPlainText().replace("\xa0", " ").split("\n")
    assert "  3 course grade(s)" in lines
    assert "  6 exam score(s)" in lines
