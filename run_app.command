#!/bin/bash
# Cell Biology Exam Grader -- double-click this file in Finder to run the app.
#
# The first time it runs on a Mac (or whenever venv/ is missing or broken, e.g.
# after copying this folder from another Mac), it builds venv/ from
# requirements.txt. That needs an internet connection and takes a few minutes;
# after that the app starts straight away.
#
#   ./run_app.command           set up if needed, then launch the app
#   ./run_app.command --check   report whether setup is needed, without
#                               installing anything or opening the app
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

REQUIRED_MODULES="PySide6 pandas openpyxl matplotlib"

pause_and_exit() {
    echo
    read -n 1 -s -r -p "Press any key to close..."
    echo
    exit "${1:-1}"
}

# venv/ is usable if its interpreter runs and every library the app needs is
# installed. find_spec checks presence without paying to import them all.
venv_works() {
    [ -x venv/bin/python3 ] || return 1
    venv/bin/python3 - "$REQUIRED_MODULES" >/dev/null 2>&1 <<'PY'
import importlib.util, sys
missing = [m for m in sys.argv[1].split() if importlib.util.find_spec(m) is None]
sys.exit(1 if missing else 0)
PY
}

# A Python the pinned libraries support (3.9 through 3.13). The conda env this
# project was developed in is tried first, then common install locations.
find_python() {
    local candidate path
    for candidate in \
        /opt/miniconda3/envs/examtool/bin/python3 \
        python3.13 python3.12 python3.11 python3.10 python3.9 python3 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        path="$(command -v "$candidate" 2>/dev/null)" || continue
        if "$path" -c 'import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
            echo "$path"
            return 0
        fi
    done
    return 1
}

if [ "${1:-}" = "--check" ]; then
    if venv_works; then
        echo "Ready: venv/ is set up ($(venv/bin/python3 --version 2>&1))."
        exit 0
    fi
    if PYTHON="$(find_python)"; then
        echo "Setup needed: venv/ is missing, broken, or incomplete."
        echo "It would be built with $PYTHON ($("$PYTHON" --version 2>&1))."
    else
        echo "Setup needed, but no suitable Python (3.9-3.13) was found on this Mac."
    fi
    exit 1
fi

if ! venv_works; then
    echo "==> Setting up the Exam Grader on this Mac (first run only)."
    echo "    This downloads the app's libraries and can take a few minutes."
    echo

    if ! PYTHON="$(find_python)"; then
        echo "error: no suitable Python (3.9-3.13) was found on this Mac." >&2
        echo "       Install Python 3.12 from https://www.python.org/downloads/ and run this again." >&2
        pause_and_exit 1
    fi
    echo "==> Using $PYTHON ($("$PYTHON" --version 2>&1))"

    # --clear rebuilds a broken venv/ in place; it holds only installed
    # libraries, never any grade data.
    if ! "$PYTHON" -m venv --clear venv; then
        echo "error: could not create venv/." >&2
        pause_and_exit 1
    fi
    if ! venv/bin/python3 -m pip install -r requirements.txt; then
        echo "error: installing the app's libraries failed (see above). Check the internet connection and run this again." >&2
        pause_and_exit 1
    fi
    if ! venv_works; then
        echo "error: setup finished but some libraries are still missing: $REQUIRED_MODULES" >&2
        pause_and_exit 1
    fi
    echo
    echo "==> Setup complete. Starting the app..."
fi

# Say which database this copy will open, so a development copy is never
# mistaken for the live one.
venv/bin/python3 - <<'PY'
from app.db import config, store
where = "DEVELOPMENT" if config.is_development_data() else "live"
print(f"==> Using the {where} database: {store.default_db_path()}")
PY

venv/bin/python3 -m app.main
status=$?
if [ "$status" -ne 0 ]; then
    echo
    echo "The app exited with an error (status $status). The message above explains what went wrong." >&2
    pause_and_exit "$status"
fi
