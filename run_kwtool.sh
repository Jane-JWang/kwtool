#!/usr/bin/env bash

source ~/.bashrc

echo "Activated!"

function run() {
    local script_dir app_dir python_exe
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    app_dir="$script_dir/kwtool"
    python_exe="$app_dir/.venv/Scripts/python.exe"

    if [[ ! -f "$python_exe" ]]; then
        printf 'Python environment not found: %s\n' "$python_exe" >&2
        printf 'See kwtool/README.md for setup instructions.\n' >&2
        return 1
    fi

    (cd -- "$app_dir" && "$python_exe" launcher.py)
}
