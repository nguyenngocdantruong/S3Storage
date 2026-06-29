#!/usr/bin/env bash
set -e

APP_DIR="$HOME/VideoS3Player"
SESSION="VideoS3Player"
CONTAINER_NAME="video-s3-player"

cd "$APP_DIR"

git fetch origin main
git reset --hard origin/main

# Kill the old tmux session first to release database locks and port 7090
tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"

# Helper function to deploy via tmux
deploy_tmux() {
  echo "Deploying via tmux..."
  source .venv/bin/activate
  pip install -r requirements.txt
  
  # Run Flask app via tmux
  tmux new-session -d -s "$SESSION" \
    "cd $APP_DIR && source .venv/bin/activate && python3 app.py"
  echo "Deploy OK (tmux)"
}

# 1. Check if Docker is installed
if command -v docker &> /dev/null; then
  echo "Docker is installed. Deploying via Docker..."
  
  # Check for docker-compose / docker compose version
  if docker compose version &>/dev/null; then
    DOCKER_COMPOSE="docker compose"
  elif command -v docker-compose &>/dev/null; then
    DOCKER_COMPOSE="docker-compose"
  else
    DOCKER_COMPOSE=""
  fi
  
  # Ensure critical configuration/log/database files exist on the host so Docker does not mount them as directories
  touch s3player.db config.conf .secret_key system.log
  
  if [ -n "$DOCKER_COMPOSE" ]; then
    echo "Rebuilding and starting container using $DOCKER_COMPOSE..."
    $DOCKER_COMPOSE down || true
    $DOCKER_COMPOSE up -d --build
  else
    echo "docker-compose not found, using raw docker command..."
    docker stop "$CONTAINER_NAME" || true
    docker rm "$CONTAINER_NAME" || true
    docker build -t video-s3-player .
    docker run -d \
      --name "$CONTAINER_NAME" \
      --restart unless-stopped \
      -p 7090:7090 \
      -v "$APP_DIR/config.conf:/app/config.conf" \
      -v "$APP_DIR/s3player.db:/app/s3player.db" \
      -v "$APP_DIR/.secret_key:/app/.secret_key" \
      -v "$APP_DIR/system.log:/app/system.log" \
      video-s3-player
  fi
  echo "Deploy OK (Docker)"
else
  echo "Docker is not installed. Deploying via tmux..."
  deploy_tmux
fi
