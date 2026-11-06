"""Test-wide safety net: no test may reach the real data folder or settings.

`app/db/config.py` resolves its data folder from the user's home directory and
keeps the chosen-folder setting in a per-user file shared by every copy of the
app on the Mac. A test that writes that setting would therefore change where the
*installed* app looks for grades — which is exactly what
`test_set_and_get_data_folder_roundtrip` did once: it patched `CONFIG_PATH`, but
`set_data_folder` resolves through `config._config_path()`, so the patch was
bypassed and a real config file was written pointing at a pytest temp directory.

This fixture forces every test onto a throwaway folder and a throwaway settings
file, so the same mistake can't reach anything real. Tests that are *about* the
resolution rules override it deliberately (see test_dev_data_isolation.py).
"""

import pytest

from app.db import config


@pytest.fixture(autouse=True)
def isolate_app_data(monkeypatch, tmp_path):
    data = tmp_path / "isolated-data"
    data.mkdir()
    monkeypatch.setenv(config.ENV_DATA_FOLDER, str(data))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "isolated-settings")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "isolated-settings" / "config.json")
    return data
