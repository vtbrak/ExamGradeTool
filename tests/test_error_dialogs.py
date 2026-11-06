"""A failed save or export is reported in a dialog, never swallowed.

Before `app.gui.errors`, only Run and the import file picker caught exceptions.
A locked database or an unwritable folder raised straight out of the slot;
PySide printed the traceback to a terminal nobody was watching and the app
carried on as if the save had worked."""

import os
import sqlite3
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.db import store
from app.gui import errors
from app.gui.main_window import MainWindow

FIXTURES = Path(__file__).parent / "fixtures"
COMBINED = FIXTURES / "synthetic_combined_round.csv"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialogs(monkeypatch):
    """Record every message box instead of blocking on it."""
    shown = {"critical": [], "information": [], "warning": []}
    for kind in shown:
        monkeypatch.setattr(QMessageBox, kind, lambda *a, _k=kind, **kw: shown[_k].append(a[1:3]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    return shown


@pytest.fixture
def window(qapp, tmp_path, dialogs):
    conn = store.connect(tmp_path / "grades.db")
    win = MainWindow(conn)
    win._handle_file_dropped(COMBINED)
    win._run()
    yield win
    conn.close()


def test_guarded_turns_an_exception_into_a_critical_dialog(qapp, dialogs):
    class Widget:
        @errors.guarded("Save failed")
        def slot(self):
            raise sqlite3.OperationalError("database is locked")

    assert Widget().slot() is None
    [(title, text)] = dialogs["critical"]
    assert title == "Save failed"
    assert "locked" in text and "Dropbox" in text  # the plain-words explanation


def test_guarded_passes_through_a_normal_return(qapp, dialogs):
    class Widget:
        @errors.guarded("x")
        def slot(self, value):
            return value * 2

    assert Widget().slot(21) == 42
    assert dialogs["critical"] == []


def test_failed_save_is_reported_and_does_not_claim_success(window, dialogs, monkeypatch):
    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "save_matched_students", boom)
    window.results_view._save_to_history()

    assert [t for t, _ in dialogs["critical"]] == ["Save failed"]
    assert not any(t == "Saved" for t, _ in dialogs["information"])
    # The status line is what claims success now, so it must stay empty too.
    assert window.results_view.save_status_label.text() == ""


def test_export_to_unwritable_path_is_reported(window, dialogs, monkeypatch, tmp_path):
    missing_dir = tmp_path / "no-such-folder" / "scores.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(missing_dir), ""))
    window.results_view._export_csv()

    [(title, text)] = dialogs["critical"]
    assert title == "Export failed"
    assert "no-such-folder" in text
    assert dialogs["information"] == []


def test_backup_failure_is_reported(window, dialogs, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise PermissionError(13, "Permission denied", str(tmp_path / "ro"))

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "ro"))
    monkeypatch.setattr(store, "backup_database", boom)
    window._backup_database()

    [(title, text)] = dialogs["critical"]
    assert title == "Backup failed"
    assert "No permission" in text
    assert dialogs["information"] == []


def test_describe_error_plain_words():
    assert "disk is full" in errors.describe_error(OSError(28, "No space left on device"))
    assert errors.describe_error(ValueError()) == "ValueError"
    assert errors.describe_error(ValueError("bad row")) == "bad row"


def test_open_database_reports_failure_instead_of_crashing(qapp, dialogs, tmp_path):
    from app import main as app_main

    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")  # a file where the data *folder* should be: mkdir fails
    conn = app_main.open_database(blocker / "grades.db")

    assert conn is None
    [(title, text)] = dialogs["critical"]
    assert title == "Could not open the grade database"
    assert str(blocker) in text


@pytest.mark.skipif(os.name != "posix", reason="read-only folder test")
def test_open_database_reports_read_only_folder(qapp, dialogs, tmp_path):
    from app import main as app_main

    folder = tmp_path / "ro"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        if os.access(folder, os.W_OK):  # running as root: the folder isn't really read-only
            pytest.skip("folder is writable despite chmod")
        assert app_main.open_database(folder / "grades.db") is None
        assert [t for t, _ in dialogs["critical"]] == ["Could not open the grade database"]
    finally:
        folder.chmod(0o700)
