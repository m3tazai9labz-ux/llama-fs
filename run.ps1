#!/usr/bin/env pwsh
# run.ps1 - Start dev server for LlamaFS
# Usage: From repository root, run `.
un.ps1` in PowerShell.
# Ensure dependencies are installed: `python -m pip install -r requirements.txt`

python -m uvicorn server:app --reload --host 127.0.0.1 --port 8000
