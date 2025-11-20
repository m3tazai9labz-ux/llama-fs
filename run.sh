#!/usr/bin/env bash
# run.sh - start dev server (POSIX)
# Usage: ./run.sh

set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  echo "python is required but not found in PATH" >&2
  exit 1
fi

if ! python -c "import uvicorn" >/dev/null 2>&1; then
  echo "uvicorn not installed. Installing into the active environment..."
  python -m pip install --user "uvicorn[standard]"
fi

python -m uvicorn server:app --reload --host 127.0.0.1 --port 8000
