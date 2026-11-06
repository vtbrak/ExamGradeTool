from pathlib import Path

from app.db import config


def test_default_data_folder_when_no_config_saved(monkeypatch, tmp_path):
    # No override of any kind: the live course folder is the answer.
    monkeypatch.delenv(config.ENV_DATA_FOLDER, raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "does_not_exist.json")
    assert config.get_data_folder() == config.DEFAULT_DATA_FOLDER


def test_set_and_get_data_folder_roundtrip(monkeypatch, tmp_path):
    # Patch the seam set_data_folder actually writes through. Patching
    # CONFIG_PATH alone isn't enough -- a development copy resolves its
    # settings file via _config_path(), so the write would escape to the real
    # project folder.
    settings = tmp_path / "AppSupport" / "config.json"
    monkeypatch.setattr(config, "_config_path", lambda: settings)

    custom = tmp_path / "custom_data_folder"
    config.set_data_folder(custom)

    assert config.get_data_folder() == custom
    assert settings.exists()


def test_default_data_folder_is_independent_of_this_files_location():
    # The whole point of app/db/config.py: the default must not be derived
    # from __file__ (which would move the database whenever the project
    # folder moves) -- it must be anchored to the user's home directory.
    assert str(config.DEFAULT_DATA_FOLDER).startswith(str(Path.home()))
    # Points at the course folder itself -- "the folder where the initial
    # CSV files were" -- not a dedicated ExamGradeTool/data subfolder.
    assert str(config.DEFAULT_DATA_FOLDER).endswith("01 - Exam tool")
