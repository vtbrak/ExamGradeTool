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

import shutil
from pathlib import Path

import pytest

from app.db import config

# Where pytest puts tmp_path and friends. Inside the project (gitignored), not
# the computer's temporary folder: the main disk is nearly full, this drive is
# not, and a folder here is visible rather than buried under /var/folders.
TEMP_ROOT = Path(__file__).resolve().parent.parent / ".pytest-tmp"


def pytest_configure(config):  # noqa: F811 - pytest matches hooks by argument name
    """Point --basetemp at TEMP_ROOT unless the run asked for somewhere else.

    pytest clears an explicit basetemp at the start of every session, so a
    folder left behind by a run that was killed is removed by the next one.

    The argument has to be called `config`; that it shadows `app.db.config` for
    the length of this function is harmless, since this function doesn't use
    it."""
    if not config.getoption("basetemp"):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = TEMP_ROOT


@pytest.fixture(scope="session", autouse=True)
def _remove_temp_root():
    """And take it away again when the run ends, pass or fail.

    pytest on its own keeps the last three runs' directories; across a suite
    that writes databases and exported CSVs that adds up."""
    yield
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_app_data(monkeypatch, tmp_path):
    data = tmp_path / "isolated-data"
    data.mkdir()
    monkeypatch.setenv(config.ENV_DATA_FOLDER, str(data))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "isolated-settings")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "isolated-settings" / "config.json")
    return data
