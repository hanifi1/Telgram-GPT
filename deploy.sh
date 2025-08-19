#!/usr/bin/env bash
set -euo pipefail

# ======== EDIT THESE ========
SERVER_USER="mh"
SERVER_HOST=192.168.2.5
SSH_PORT="2222"               # use "22" if default
SERVER_DIR="/home/mh/myapp"   # code location on server

# Conda env name inside the Docker image (matches Step 1 Dockerfile ARG default)
ENV_NAME="telgpt_env"

# Container/image/app config
CONTAINER_NAME="myapp"
IMAGE_NAME="myapp:latest"

# If your app is a script: APP_MODE=script, APP_ENTRY=app.py
# If FastAPI/Flask via Uvicorn: APP_MODE=web, APP_ENTRY=main:app, APP_PORT matters
APP_MODE="script"             # "script" or "web"
APP_ENTRY="telgram_agent.py"            # script: app.py | web: module:app (e.g., main:app)
# APP_PORT="8000"               # used only when APP_MODE=web
# ============================

echo "[deploy] Syncing code to server..."
rsync -avz --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude ".DS_Store" \
  -e "ssh -p ${SSH_PORT}" \
  ./ "${SERVER_USER}@${SERVER_HOST}:${SERVER_DIR}/"

echo "[deploy] Building image on server and (re)starting container..."
ssh -p "${SSH_PORT}" "${SERVER_USER}@${SERVER_HOST}" "
  set -e
  cd '${SERVER_DIR}'

  docker build --build-arg ENV_NAME='${ENV_NAME}' -t '${IMAGE_NAME}' .

  if [ '${APP_MODE}' = 'web' ]; then
    # (Re)start as a long-running web service
    if docker ps -a --format '{{.Names}}' | grep -qx '${CONTAINER_NAME}'; then
      docker rm -f '${CONTAINER_NAME}' || true
    fi
    docker run -d \
      --name '${CONTAINER_NAME}' \
      -p ${APP_PORT}:${APP_PORT} \
      --env-file .env \
      --restart unless-stopped \
      -e APP_MODE='${APP_MODE}' \
      -e APP_ENTRY='${APP_ENTRY}' \
      -e APP_PORT='${APP_PORT}' \
      '${IMAGE_NAME}'
  else
    # Run a one-off batch/script
    docker run --rm \
      --env-file .env \
      -e APP_MODE='${APP_MODE}' \
      -e APP_ENTRY='${APP_ENTRY}' \
      '${IMAGE_NAME}'
  fi
"

echo "[deploy] Done."
