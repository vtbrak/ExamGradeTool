"""Where the app keeps its data folder, independent of where the app itself lives.

The database deliberately does not live next to the code. The default is
anchored to the user's home directory -- the Dropbox-synced course folder --
rather than computed from this package's `__file__`, so it resolves to the same
place whether the project folder sits on the Desktop, is moved, or is copied to
another Mac (different macOS username, same Dropbox folder structure). That is
what lets grade history travel between Macs through Dropbox while the code is
run from a local copy via run_app.command. An explicit override, if the
instructor ever picks a different folder via "Choose Data Folder...", is
persisted here and takes precedence.

**Keeping development away from the live database.** Because the default is
anchored to the home directory, *any* copy of this project that launches the
app would otherwise open the one real `grades.db` -- so testing a change in a
development copy would touch live grades. Two things prevent that, checked in
this order:

1. ``EXAMGRADER_DATA_FOLDER`` in the environment wins outright.
2. A file named ``.dev-copy`` in the project root marks that copy as a
   development one, and its data folder becomes ``<project>/dev-data``.

In either case the saved "Choose Data Folder..." setting is read from and
written to that folder as well, so a choice made while developing cannot move
the production copy's database. `is_development_data()` says whether one of
these is in effect, which the window title shows so the two are never confused.
"""

import json
import os
from pathlib import Path

ENV_DATA_FOLDER = "EXAMGRADER_DATA_FOLDER"
DEV_MARKER_NAME = ".dev-copy"
DEV_DATA_DIRNAME = "dev-data"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "CellBiologyExamGrader"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_DATA_FOLDER = (
    Path.home()
    / "Library"
    / "CloudStorage"
    / "Dropbox"
    / "_0 West Chester"
    / "0_Teaching"
    / "211 - Cell Biology"
    / "01 - Exam tool"
)


def _forced_data_folder():
    """A folder that overrides everything else, or None.

    This is what keeps a development copy off the live database."""
    from_env = os.environ.get(ENV_DATA_FOLDER)
    if from_env:
        return Path(from_env).expanduser()
    if (PROJECT_ROOT / DEV_MARKER_NAME).exists():
        return PROJECT_ROOT / DEV_DATA_DIRNAME
    return None


def is_development_data() -> bool:
    return _forced_data_folder() is not None


def _config_path() -> Path:
    """Where the chosen-folder setting is kept.

    A development copy keeps its own, inside its own data folder: otherwise
    picking a folder while developing would move the production copy too, since
    the setting is per-user and shared by every copy on the Mac."""
    forced = _forced_data_folder()
    return forced / "config.json" if forced else CONFIG_PATH


def get_data_folder() -> Path:
    forced = _forced_data_folder()
    saved_at = _config_path()
    if saved_at.exists():
        try:
            saved = json.loads(saved_at.read_text()).get("data_folder")
            if saved:
                return Path(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return forced if forced else DEFAULT_DATA_FOLDER


def set_data_folder(folder) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"data_folder": str(folder)}))
