# Finding conda, and this app's environment inside it.
#
# Sourced by "install examgradetool.command" and "open examgradetool.command",
# never run on its own. Both need the same answers, and two copies of this
# logic would drift.
#
# Why search for conda.sh rather than call `conda`: a .command file
# double-clicked in Finder gets a non-interactive shell that has not run
# ~/.zshrc, so `conda` is usually not on PATH even when it works fine in a
# Terminal window. `conda.sh` is the script conda's own shell hook sources, and
# sourcing it is what makes `conda activate` work.

ENV_NAME="examgradetool"
PYTHON_VERSION="3.12"
# What the app itself needs importable; checked after installing.
REQUIRED_IMPORTS="PySide6 pandas openpyxl matplotlib"

# Print the conda base directory that has a conda.sh in it, or return 1.
# Checked in order of authority: what conda itself reports, then the places a
# Mac install normally lands.
find_conda_base() {
    local candidates=() base

    if [ -n "${CONDA_EXE:-}" ] && [ -x "${CONDA_EXE}" ]; then
        candidates+=("$("${CONDA_EXE}" info --base 2>/dev/null)")
    fi
    if command -v conda >/dev/null 2>&1; then
        candidates+=("$(conda info --base 2>/dev/null)")
    fi
    candidates+=(
        "/opt/miniconda3"
        "/opt/anaconda3"
        "$HOME/miniconda3"
        "$HOME/anaconda3"
        "$HOME/opt/anaconda3"
        "$HOME/opt/miniconda3"
        "/usr/local/Caskroom/miniconda/base"
        "/opt/homebrew/Caskroom/miniconda/base"
    )

    for base in "${candidates[@]}"; do
        [ -n "$base" ] || continue
        if [ -f "$base/etc/profile.d/conda.sh" ]; then
            printf '%s\n' "$base"
            return 0
        fi
    done
    return 1
}

# Source conda.sh so `conda activate` works in this shell. Sets CONDA_BASE.
load_conda() {
    CONDA_BASE="$(find_conda_base)" || return 1
    # shellcheck source=/dev/null
    . "$CONDA_BASE/etc/profile.d/conda.sh" || return 1
    return 0
}

# Does the environment already exist? Matched on the name column exactly, so
# "examgradetool" is never satisfied by "examtool" or "examgradetool-old".
env_exists() {
    conda env list 2>/dev/null | awk '{print $1}' | grep -qx "$ENV_NAME"
}

# The message every failure ends with, so neither script has to repeat it.
no_conda_message() {
    cat <<'MSG'
Could not find a conda installation.

Looked for etc/profile.d/conda.sh under:
  /opt/miniconda3            /opt/anaconda3
  ~/miniconda3               ~/anaconda3
  ~/opt/anaconda3            ~/opt/miniconda3
  /usr/local/Caskroom/miniconda/base
  /opt/homebrew/Caskroom/miniconda/base

If conda is installed somewhere else, open a Terminal window and run

    conda info --base

then tell me the path and I will add it.
MSG
}
