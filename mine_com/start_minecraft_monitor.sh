#!/bin/bash

SCRIPT_PATH="$(realpath "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

if [[ "$1" != "--child" ]]; then
  cd "$SCRIPT_DIR"
  sudo nohup "$SCRIPT_PATH" --child > auto_update.log 2>&1 &
  exit 0
fi

cd "$SCRIPT_DIR"
GIT_BRANCH="main"   # или твоя ветка
APP_PID_FILE="flask.pid"
LOG_FILE="server.log"

start_app() {
  source ./venv/bin/activate
  nohup python3 app.py > "$LOG_FILE" 2>&1 &
  echo $! > "$APP_PID_FILE"
  deactivate
}

stop_app() {
  if [ -f "$APP_PID_FILE" ]; then
    kill "$(cat "$APP_PID_FILE")"
    rm "$APP_PID_FILE"
  fi
}

start_app

while true
do
  git fetch origin "$GIT_BRANCH"
  LOCAL=$(git rev-parse "$GIT_BRANCH")
  REMOTE=$(git rev-parse "origin/$GIT_BRANCH")
  if [ "$LOCAL" != "$REMOTE" ]; then
    echo "Обнаружено обновление! Перезапускаю..."
    stop_app
    git pull
    start_app
  fi
  sleep 60
done