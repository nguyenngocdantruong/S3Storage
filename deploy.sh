#!/usr/bin/env bash
set -e

APP_DIR="$HOME/VideoS3Player"
SESSION="VideoS3Player"

cd "$APP_DIR"

git fetch origin main
git reset --hard origin/main

source .venv/bin/activate
pip install -r requirements.txt

# Kill the old session first to release any database locks (e.g. stuck processes)
tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"

# Migrations are automatically handled inside app.py programmatically upon starting up.


tmux new-session -d -s "$SESSION" \
  "cd $APP_DIR && source .venv/bin/activate && python3 app.py"

echo "Deploy OK"
