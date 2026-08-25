#!/usr/bin/env bash
# InsightGPT — one-command setup for macOS / Linux.
#
#   ./setup.sh                 # set up (or repair) the full Docker stack
#   ./setup.sh --doctor        # diagnose only; changes nothing
#   ./setup.sh --repair        # clean rebuild + recreate, then re-verify
#   ./setup.sh --native        # no Docker: prepare the local dev stack
#   ./setup.sh --skip-models   # skip the Ollama model pulls
#
# This wrapper only finds a usable Python. All of the real work — and every
# platform difference — lives in scripts/setup.py, so the Windows and Unix
# entry points can never drift apart.

set -euo pipefail

cd "$(dirname "$0")"

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            # 3.9+ is required (the script is stdlib-only but uses modern syntax).
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
                >/dev/null 2>&1; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

if PY="$(find_python)"; then
    exec "$PY" scripts/setup.py "$@"
fi

# No system Python: let uv provide one rather than asking the user to install it.
if ! command -v uv >/dev/null 2>&1; then
    echo "Python 3.9+ was not found. Installing uv to provide one..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if command -v uv >/dev/null 2>&1; then
    exec uv run --python 3.12 --no-project scripts/setup.py "$@"
fi

cat >&2 <<'MSG'
Could not find or install a Python 3.9+ interpreter.

Install Python from https://www.python.org/downloads/ (or uv from
https://docs.astral.sh/uv/getting-started/installation/) and run ./setup.sh again.
MSG
exit 1
