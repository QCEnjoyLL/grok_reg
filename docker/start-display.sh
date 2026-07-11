#!/bin/bash
set -euo pipefail

DISPLAY_NUM="${DISPLAY#:}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
export DISPLAY=":${DISPLAY_NUM}"
RESOLUTION="${RESOLUTION:-1920x1080x24}"
VNC_PASSWORD="${VNC_PASSWORD:-grokreg}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
LOG_DIR="${DATA_DIR:-/data}/logs"
mkdir -p "${LOG_DIR}" /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix || true

# Already running?
if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
  echo "[display] ${DISPLAY} already up"
else
  echo "[display] starting Xvfb ${DISPLAY} ${RESOLUTION}"
  Xvfb "${DISPLAY}" -screen 0 "${RESOLUTION}" -ac +extension GLX +render -noreset \
    >"${LOG_DIR}/xvfb.log" 2>&1 &
  for i in $(seq 1 50); do
    if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
fi

# Lightweight WM so Chromium has a window manager (helps some CF/page layouts)
# Avoid fluxbox first-run wallpaper wizard (fbsetbg xmessage)
mkdir -p /root/.fluxbox
if [[ ! -f /root/.fluxbox/init ]]; then
  cat > /root/.fluxbox/init <<'EOF'
session.screen0.rootCommand: xsetroot -solid #0b1020
session.screen0.toolbar.visible: false
EOF
fi
if ! pgrep -x fluxbox >/dev/null 2>&1; then
  # solid background first
  if command -v xsetroot >/dev/null 2>&1; then
    xsetroot -solid "#0b1020" >/dev/null 2>&1 || true
  fi
  fluxbox >"${LOG_DIR}/fluxbox.log" 2>&1 &
  sleep 0.3
  xsetroot -solid "#0b1020" >/dev/null 2>&1 || true
fi

# VNC
if ! pgrep -x x11vnc >/dev/null 2>&1; then
  mkdir -p /root/.vnc
  x11vnc -storepasswd "${VNC_PASSWORD}" /root/.vnc/passwd >/dev/null 2>&1 || true
  x11vnc -display "${DISPLAY}" -rfbauth /root/.vnc/passwd -forever -shared -rfbport "${VNC_PORT}" -noxdamage -quiet \
    >"${LOG_DIR}/x11vnc.log" 2>&1 &
  echo "[display] VNC on :${VNC_PORT} (password from VNC_PASSWORD)"
fi

# noVNC web client
if ! pgrep -f "websockify.*${NOVNC_PORT}" >/dev/null 2>&1; then
  # Debian novnc path
  NOVNC_WEB=""
  for p in /usr/share/novnc /usr/share/novnc/utils/.. /usr/share/novnc; do
    if [[ -d "$p" ]]; then NOVNC_WEB="$p"; break; fi
  done
  if [[ -n "${NOVNC_WEB}" ]]; then
    websockify --web="${NOVNC_WEB}" "${NOVNC_PORT}" "localhost:${VNC_PORT}" \
      >"${LOG_DIR}/novnc.log" 2>&1 &
    echo "[display] noVNC on :${NOVNC_PORT} -> open http://HOST:${NOVNC_PORT}/vnc.html"
  else
    echo "[display] noVNC package path not found; VNC still available on ${VNC_PORT}"
  fi
fi

echo "[display] ready DISPLAY=${DISPLAY}"
