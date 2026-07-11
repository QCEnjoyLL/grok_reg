#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export DATA_DIR="${DATA_DIR:-/data}"
export APP_HOME="${APP_HOME:-/app}"
export RESOLUTION="${RESOLUTION:-1920x1080x24}"
export VNC_PASSWORD="${VNC_PASSWORD:-grokreg}"
export WEB_PORT="${WEB_PORT:-8080}"
export VNC_PORT="${VNC_PORT:-5900}"
export NOVNC_PORT="${NOVNC_PORT:-6080}"

mkdir -p "${DATA_DIR}/cpa_auths" "${DATA_DIR}/logs" "${DATA_DIR}/cookies" "${DATA_DIR}/screenshots"

# Seed config into /data if missing
if [[ ! -f "${DATA_DIR}/config.json" ]]; then
  if [[ -f "${APP_HOME}/config.example.json" ]]; then
    echo "[init] seeding ${DATA_DIR}/config.json from config.example.json"
    cp "${APP_HOME}/config.example.json" "${DATA_DIR}/config.json"
  fi
fi

# Always prefer /data/config.json for the app process
if [[ -f "${DATA_DIR}/config.json" ]]; then
  ln -sfn "${DATA_DIR}/config.json" "${APP_HOME}/config.json"
elif [[ ! -f "${APP_HOME}/config.json" && -f "${APP_HOME}/config.example.json" ]]; then
  cp "${APP_HOME}/config.example.json" "${APP_HOME}/config.json"
fi

# Persist accounts / cpa / cookies on the volume
touch "${DATA_DIR}/accounts_cli.txt"
ln -sfn "${DATA_DIR}/accounts_cli.txt" "${APP_HOME}/accounts_cli.txt"
ln -sfn "${DATA_DIR}/cpa_auths" "${APP_HOME}/cpa_auths"
ln -sfn "${DATA_DIR}/cookies" "${APP_HOME}/cookies"
ln -sfn "${DATA_DIR}/screenshots" "${APP_HOME}/screenshots" 2>/dev/null || true

export GROK_REG_DATA_DIR="${DATA_DIR}"
export CHROME_BIN="${CHROME_BIN:-/usr/bin/chromium}"
export CHROMIUM_FLAGS="${CHROMIUM_FLAGS:---no-sandbox --disable-dev-shm-usage --disable-gpu}"

start_display() {
  bash "${APP_HOME}/docker/start-display.sh"
}

cmd="${1:-web}"
shift || true

case "${cmd}" in
  web|dashboard)
    start_display
    exec python -m web.app --host 0.0.0.0 --port "${WEB_PORT}"
    ;;
  register)
    start_display
    exec python -u register_cli.py --accounts-file "${DATA_DIR}/accounts_cli.txt" "$@"
    ;;
  backfill)
    start_display
    exec python -u scripts/backfill_cpa_xai_from_accounts.py --out-dir "${DATA_DIR}/cpa_auths" "$@"
    ;;
  shell|bash)
    start_display
    exec bash "$@"
    ;;
  display-only)
    start_display
    tail -f /dev/null
    ;;
  *)
    start_display
    exec "${cmd}" "$@"
    ;;
esac
