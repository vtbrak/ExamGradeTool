import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from app.db import store
from app.db.config import is_development_data
from app.gui.errors import describe_error, install_excepthook
from app.gui.main_window import MainWindow
from app.version import APP_NAME
from app.gui import dialogs


def open_database(db_path):
    """Open the grade database, or explain why it couldn't be opened and quit.

    Without this a missing drive, a read-only folder or a corrupt file ends
    the app with a traceback in a terminal the instructor never sees.

    A *missing* live database is asked about, not created: on a new Mac the
    usual reason is that Dropbox hasn't finished syncing the course folder,
    and an empty database created now would split the record from the real
    one when it arrives. A development copy (dev-data/) is created silently."""
    db_path = Path(db_path)
    if not db_path.exists() and not is_development_data():
        answer = dialogs.question(
            None,
            "Grade database not found",
            f"There is no grade database at\n\n{db_path}\n\n"
            "If this Mac's Dropbox is still syncing, quit and try again once the "
            "course folder has finished downloading.\n\n"
            "Create a new, empty database here instead? (Only right if this is "
            "genuinely the first time the app has been used.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
    try:
        return store.connect(db_path)
    except Exception as exc:  # noqa: BLE001 - shown, then the app exits
        dialogs.critical(
            None,
            "Could not open the grade database",
            f"The database at\n\n{db_path}\n\ncould not be opened.\n\n"
            + describe_error(exc)
            + "\n\nIf this folder was chosen with “Choose Data Folder…” and no longer "
            "exists, delete the saved choice to return to the default folder.",
        )
        return None


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    install_excepthook()
    conn = open_database(store.default_db_path())
    if conn is None:
        sys.exit(1)
    window = MainWindow(conn)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
