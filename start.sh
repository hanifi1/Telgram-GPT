#!/usr/bin/env bash
set -euo pipefail

# Choose behavior without editing Dockerfile:
# - APP_MODE=script (default) runs "python APP_ENTRY"
# - APP_MODE=web runs "uvicorn APP_ENTRY"
: "${APP_MODE:=script}"
: "${APP_ENTRY:=app.py}"     # script: app.py | web: module:app (e.g., main:app)
: "${APP_PORT:=8000}"        # used only for web mode

if [[ "$APP_MODE" == "web" ]]; then
  echo "[start.sh] Starting web app: uvicorn $APP_ENTRY on port $APP_PORT"
  exec micromamba run -n "$MAMBA_DEFAULT_ENV" uvicorn "$APP_ENTRY" --host 0.0.0.0 --port "$APP_PORT"
else
  echo "[start.sh] Running script: python $APP_ENTRY"
  exec micromamba run -n "$MAMBA_DEFAULT_ENV" python "$APP_ENTRY"
fi
