"""Keeping development work away from the live database.

The data folder is anchored to the home directory, so *any* copy that launches
the app would otherwise open the one real grades.db. These are the guards that
stop a development copy doing that.
"""

import json

import pytest

from app.db import config


@pytest.fixture(autouse=True)
def no_inherited_override(monkeypatch, tmp_path):
    """Each test decides its own override, uninfluenced by the real project."""
    monkeypatch.delenv(config.ENV_DATA_FOLDER, raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return tmp_path


def test_without_an_override_the_live_folder_is_used(no_inherited_override):
    assert config.is_development_data() is False
    assert config.get_data_folder() == config.DEFAULT_DATA_FOLDER


def test_the_environment_variable_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_DATA_FOLDER, str(tmp_path / "elsewhere"))
    assert config.is_development_data() is True
    assert config.get_data_folder() == tmp_path / "elsewhere"


def test_a_dev_marker_redirects_to_the_project_folder(no_inherited_override):
    (no_inherited_override / config.DEV_MARKER_NAME).touch()
    assert config.is_development_data() is True
    assert config.get_data_folder() == no_inherited_override / config.DEV_DATA_DIRNAME


def test_the_environment_variable_beats_the_marker(monkeypatch, no_inherited_override):
    (no_inherited_override / config.DEV_MARKER_NAME).touch()
    monkeypatch.setenv(config.ENV_DATA_FOLDER, str(no_inherited_override / "chosen"))
    assert config.get_data_folder() == no_inherited_override / "chosen"


def test_a_development_copy_keeps_its_own_chosen_folder(no_inherited_override, monkeypatch):
    """The saved setting is per-user and shared by every copy on the Mac, so a
    development copy must keep its own — otherwise choosing a folder while
    developing would move the production copy's database too."""
    (no_inherited_override / config.DEV_MARKER_NAME).touch()
    shared = no_inherited_override / "shared_config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", shared)

    config.set_data_folder(no_inherited_override / "dev_choice")

    assert config.get_data_folder() == no_inherited_override / "dev_choice"
    assert not shared.exists()  # the production copy's setting is untouched
    dev_config = no_inherited_override / config.DEV_DATA_DIRNAME / "config.json"
    assert json.loads(dev_config.read_text())["data_folder"].endswith("dev_choice")


def test_a_production_copy_still_uses_the_shared_setting(no_inherited_override, monkeypatch):
    shared = no_inherited_override / "shared_config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", shared)

    config.set_data_folder(no_inherited_override / "course_folder")

    assert shared.exists()
    assert config.get_data_folder() == no_inherited_override / "course_folder"


def test_the_window_title_announces_development_data(monkeypatch, tmp_path):
    """So a development window can never be mistaken for the live one."""
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from app.db import store
    from app.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv(config.ENV_DATA_FOLDER, str(tmp_path / "devdata"))
    window = MainWindow(store.connect(tmp_path / "devdata" / "grades.db"))
    assert "DEVELOPMENT DATA" in window.windowTitle()

    monkeypatch.delenv(config.ENV_DATA_FOLDER)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    plain = MainWindow(store.connect(tmp_path / "other.db"))
    assert plain.windowTitle() == "Cell Biology Exam Grader"
    assert app is not None


@pytest.mark.skipif(
    not (config.PROJECT_ROOT / config.DEV_MARKER_NAME).exists(),
    reason="only meaningful in a copy marked .dev-copy (the marker is gitignored, "
    "so a fresh clone has none)",
)
def test_this_project_copy_is_marked_as_development(monkeypatch):
    """The copy this test runs from is the development one, so its own guard
    must be in place — a failure here means the live database is reachable."""
    import importlib

    monkeypatch.delenv(config.ENV_DATA_FOLDER, raising=False)
    fresh = importlib.reload(config)
    monkeypatch.delenv(fresh.ENV_DATA_FOLDER, raising=False)
    try:
        assert (fresh.PROJECT_ROOT / fresh.DEV_MARKER_NAME).exists()
        assert fresh.is_development_data() is True
        assert fresh.get_data_folder() == fresh.PROJECT_ROOT / fresh.DEV_DATA_DIRNAME
    finally:
        importlib.reload(config)
