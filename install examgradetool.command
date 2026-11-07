#!/bin/bash
#
# Install ExamGradeTool's Python environment into conda. Double-click in Finder.
#
# Creates a conda environment named examgradetool and installs what the app
# needs into it. Run it once per Mac, and again after requirements.txt changes.
# Safe to run repeatedly: an existing environment is updated, not rebuilt.
#
# Day to day you want "open examgradetool.command" instead; this one only sets
# things up.

set -u

# Finder starts a .command in the user's home folder, not beside the file.
cd "$(dirname "$0")" || { echo "Could not find the app folder."; exit 1; }
PROJECT_DIR="$(pwd)"

# Keep the Terminal window up on the way out so a message can be read. Finder
# closes it immediately otherwise, taking the error with it.
finish() {
    local code=$1
    echo
    if [ "$code" -eq 0 ]; then
        echo "=============================================================="
        echo " Done. Open the app with \"open examgradetool.command\"."
        echo "=============================================================="
    else
        echo "=============================================================="
        echo " Setup did not finish. Nothing was changed in your grades."
        echo "=============================================================="
    fi
    echo
    read -r -p "Press Return to close this window. "
    exit "$code"
}

echo "ExamGradeTool — environment setup"
echo "Folder: $PROJECT_DIR"
echo

if [ ! -f "tools/conda_env.sh" ]; then
    echo "tools/conda_env.sh is missing, so conda can't be located."
    echo "This file has to stay inside the ExamGradeTool folder."
    finish 1
fi
# shellcheck source=tools/conda_env.sh
. "tools/conda_env.sh"

if [ ! -f "requirements.txt" ]; then
    echo "requirements.txt is missing; there is nothing to install."
    finish 1
fi

# --- 1. find conda --------------------------------------------------------
echo "==> Looking for conda..."
if ! load_conda; then
    echo
    no_conda_message
    finish 1
fi
echo "    conda found: $CONDA_BASE"
conda --version 2>/dev/null | sed 's/^/    /'

# --- 2. the environment ---------------------------------------------------
echo
if env_exists; then
    echo "==> Environment \"$ENV_NAME\" already exists; leaving it in place."
else
    echo "==> Creating environment \"$ENV_NAME\" (Python $PYTHON_VERSION)..."
    echo "    This downloads a few hundred MB the first time."
    if ! conda create -y -n "$ENV_NAME" "python=$PYTHON_VERSION"; then
        echo
        echo "Creating the environment failed. If the message above mentions"
        echo "the network, try again on a working connection."
        finish 1
    fi
fi

# --- 3. the app's packages ------------------------------------------------
# pip inside the conda environment, via `conda run`, so this works without
# `conda activate` having been set up for this non-interactive shell.
echo
echo "==> Installing the app's packages into \"$ENV_NAME\"..."
if ! conda run --no-capture-output -n "$ENV_NAME" python -m pip install -r requirements.txt; then
    echo
    echo "Installing the packages failed. The environment exists, so running"
    echo "this file again picks up where it left off."
    finish 1
fi

# --- 4. prove it works ----------------------------------------------------
echo
echo "==> Checking the app can start..."
if ! conda run --no-capture-output -n "$ENV_NAME" python -c "
import sys
# importlib.util, not importlib: on 3.12 the submodule is not imported for you.
import importlib.util
missing = [m for m in '''$REQUIRED_IMPORTS'''.split() if importlib.util.find_spec(m) is None]
if missing:
    sys.exit('    missing after install: ' + ', '.join(missing))
from app.version import TITLE
print('    ' + TITLE + ' is ready.')
print('    Python ' + sys.version.split()[0])
"; then
    echo
    echo "The packages installed but the app could not be imported."
    finish 1
fi

# Which database this copy will open. A development copy must never be
# mistaken for the live one, and that is worth saying at install time too.
echo
conda run --no-capture-output -n "$ENV_NAME" python -c "
from app.db import config, store
where = 'DEVELOPMENT' if config.is_development_data() else 'live'
print(f'==> This copy will use the {where} database:')
print(f'    {store.default_db_path()}')
" 2>/dev/null

finish 0
