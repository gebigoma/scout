#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/tinapark2/scout"
CLAUDE_BIN="/Users/tinapark2/.local/bin/claude"

cd "$PROJECT_DIR"

"$CLAUDE_BIN" -p "$(cat scripts/weekly_prompt.md)" \
  --allowed-tools "Bash(python3 scripts/fetch_listings.py) Read Write Bash(git add *) Bash(git commit *) Bash(git push *)" \
  --permission-mode acceptEdits
