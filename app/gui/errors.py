"""Surface failures to the instructor instead of letting them vanish.

A Qt slot that raises doesn't crash the app: PySide prints the traceback to a
terminal nobody is watching and carries on, so a save that failed looks exactly
like a save that worked. Everything that writes -- the database, a CSV, a chart
image, a backup -- goes through `guarded`, which turns the exception into a
dialog. The exception is re-raised nowhere, so the slot simply ends.
"""

import functools
import sqlite3
import sys
import traceback

from PySide6.QtWidgets import QMessageBox

from app.gui import dialogs


def describe_error(exc: BaseException) -> str:
    """The message shown in the dialog: plain words first, the raw error after."""
    if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
        return (
            "The grade database is locked by another program (Dropbox may be "
            "syncing it, or it's open elsewhere). Wait a moment and try again.\n\n"
            f"{exc}"
        )
    if isinstance(exc, PermissionError):
        return f"No permission to write there. Choose another location.\n\n{exc}"
    if isinstance(exc, OSError) and exc.errno == 28:  # ENOSPC
        return f"The disk is full.\n\n{exc}"
    return str(exc) or exc.__class__.__name__


def show_error(parent, title: str, exc: BaseException) -> None:
    dialogs.critical(parent, title, describe_error(exc))


def guarded(title: str):
    """Decorate a slot method so any exception becomes a dialog titled `title`.

    The method's `self` is the dialog's parent widget. The slot returns None
    when it fails; callers that need to know (none today) can check for that."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - every failure is shown, none swallowed
                traceback.print_exc()
                show_error(self, title, exc)
                return None

        return wrapper

    return decorate


def install_excepthook() -> None:
    """Catch what the slots don't: an exception on the main thread anywhere else
    gets a dialog instead of silently killing the event loop's handler."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        previous(exc_type, exc, tb)
        try:
            dialogs.critical(
                None,
                "Unexpected error",
                "Something went wrong and the last action may not have completed.\n\n"
                + describe_error(exc),
            )
        except Exception:  # noqa: BLE001 - no QApplication yet, nothing more to do
            pass

    sys.excepthook = hook
