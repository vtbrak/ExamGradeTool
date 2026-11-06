"""One look for every pop-up in the app: the first line bold and two points
larger than the rest, everything after it regular weight.

Qt renders the whole of a message box's `text` bold, and `informativeText` --
the usual way to get a regular-weight paragraph under it -- can't be given a
size of its own. So the message is built as rich text here instead. That keeps
the styling in one place, which is the point: a dialog assembled by hand at the
call site is how the app ends up with several different looks.

Every dialog goes through these functions rather than QMessageBox directly, and
they still call QMessageBox's own static methods, so the native icon, button
layout and keyboard handling are unchanged.
"""

from html import escape, unescape

from PySide6.QtWidgets import QApplication, QMessageBox

# How much larger the first line is than the body.
HEADLINE_POINTS_LARGER = 2
# Used only if the application font reports no point size (a font set in
# pixels), which shouldn't happen but would otherwise give a 0pt headline.
FALLBACK_POINT_SIZE = 13.0


def _base_point_size() -> float:
    app = QApplication.instance()
    size = app.font().pointSizeF() if app is not None else 0.0
    return size if size > 0 else FALLBACK_POINT_SIZE


def format_message(message) -> str:
    """Rich text for a dialog body: first line as the headline, rest beneath.

    The split is on the first line break, so a message is written as plain text
    with its headline first and needs no markup at the call site. Quotes are
    left as they are -- `escape(quote=True)` would turn an apostrophe into an
    entity, and these strings are read by people."""
    headline, _, rest = str(message).partition("\n")
    base = _base_point_size()
    html = (
        f'<div style="font-size:{base + HEADLINE_POINTS_LARGER:g}pt; '
        f'font-weight:bold;">{escape(headline, quote=False)}</div>'
    )
    rest = rest.strip("\n")
    if rest:
        html += (
            f'<div style="font-size:{base:g}pt; font-weight:normal; '
            f'margin-top:0.6em;">{_body_html(rest)}</div>'
        )
    return html


def _body_html(text: str) -> str:
    """Escaped text with its line breaks and indentation kept.

    HTML collapses runs of spaces, which would flatten the indented lists
    several dialogs use ("  3 course grade(s)"), so a line's leading spaces
    become non-breaking ones."""
    lines = []
    for line in escape(text, quote=False).split("\n"):
        stripped = line.lstrip(" ")
        indent = "&nbsp;" * (len(line) - len(stripped))
        lines.append(indent + stripped)
    return "<br>".join(lines)


def split_rendered(html) -> tuple:
    """`(headline, body)` as plain text, back out of a formatted message.

    For tests and for reading a dialog's content back without parsing HTML at
    the assertion site. Kept beside `format_message` so the two can't drift."""
    import re as _re

    parts = _re.findall(r"<div[^>]*>(.*?)</div>", str(html), _re.S)
    plain = [
        # The non-breaking spaces `_body_html` uses to hold indentation read
        # back as ordinary ones; they are a rendering device, not content.
        unescape(_re.sub(r"<br\s*/?>", "\n", part)).replace("\xa0", " ")
        for part in parts
    ]
    headline = plain[0] if plain else unescape(str(html))
    body = plain[1] if len(plain) > 1 else ""
    return headline, body


def information(parent, title, message):
    return QMessageBox.information(parent, title, format_message(message))


def warning(parent, title, message):
    return QMessageBox.warning(parent, title, format_message(message))


def critical(parent, title, message):
    return QMessageBox.critical(parent, title, format_message(message))


def question(parent, title, message, buttons=None, default=None):
    """Yes/No by default; `buttons` and `default` are passed straight through."""
    if buttons is None:
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    if default is None:
        default = QMessageBox.StandardButton.No
    return QMessageBox.question(parent, title, format_message(message), buttons, default)
