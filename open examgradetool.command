#!/bin/bash
#
# Open ExamGradeTool. Double-click in Finder.
#
# Expects the conda environment that "install examgradetool.command" creates.
# This file deliberately does not create or change anything: if the environment
# isn't there, it says so and stops, rather than quietly building one while
# someone waits to grade an exam.

set -u

# Finder starts a .command in the user's home folder, not beside the file.
cd "$(dirname "$0")" || { echo "Could not find the app folder."; exit 1; }

# Only pause on the way out if something went wrong. On a clean exit the
# window closing is the right behaviour -- nobody wants to dismiss a prompt
# every time they close the app.
fail() {
    echo
    echo "=============================================================="
    echo " $1"
    echo "=============================================================="
    echo
    read -r -p "Press Return to close this window. "
    exit 1
}

if [ ! -f "tools/conda_env.sh" ]; then
    fail "tools/conda_env.sh is missing, so conda can't be located. It has to stay inside the ExamGradeTool folder."
fi
# shellcheck source=tools/conda_env.sh
. "tools/conda_env.sh"

if ! load_conda; then
    echo
    no_conda_message
    fail "conda not found."
fi

if ! env_exists; then
    fail "The \"$ENV_NAME\" environment does not exist yet. Double-click \"install examgradetool.command\" first; it only has to be done once."
fi

# Version first, then which database -- the two things worth knowing if the app
# ever behaves unexpectedly.
conda run --no-capture-output -n "$ENV_NAME" python -c "
from app.db import config, store
from app.version import TITLE
where = 'DEVELOPMENT' if config.is_development_data() else 'live'
print(f'==> {TITLE}')
print(f'==> Using the {where} database: {store.default_db_path()}')
" || fail "The environment exists but the app could not be read. Run \"install examgradetool.command\" again."

conda run --no-capture-output -n "$ENV_NAME" python -m app.main
status=$?

if [ "$status" -ne 0 ]; then
    fail "The app exited with an error (code $status). The message above says why."
fi
