#!/usr/bin/env bash
# instant2-run.sh: sin args -> daemon oculto (nohup, sin ventana); con args -> passthrough en consola.
# Sin duplicados: si ya hay instancia viva (PID file o pgrep), avisa y sale.
# Modelos: <repo>/models (relativo al script). Solo default, respeta INSTANT2_DATA previa.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
if [ -x "$ROOT/.venv/bin/python" ]; then
  RUN=("$ROOT/.venv/bin/python" -m instant2_app)
elif command -v instant2 >/dev/null 2>&1; then
  RUN=(instant2)
else
  RUN=(python3 -m instant2_app)
fi
if [ -z "${INSTANT2_DATA:-}" ]; then
  INSTANT2_DATA="$ROOT/models"
  export INSTANT2_DATA
fi

config_dir() {
  case "$(uname -s)" in
    Darwin) printf '%s' "$HOME/Library/Application Support/instant2" ;;
    *) printf '%s' "${XDG_CONFIG_HOME:-$HOME/.config}/instant2" ;;
  esac
}
PIDFILE="$(config_dir)/instant2.pid"

alive_pid() { # alive_pid <pid> -> 0 si el proceso vive
  kill -0 "$1" 2>/dev/null
}

matches_instant() { # matches_instant <pid> -> 0 si su cmdline es instant2
  local p="$1" cmd=""
  if [ -r "/proc/$p/cmdline" ]; then
    cmd="$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)"
  elif command -v ps >/dev/null 2>&1; then
    cmd="$(ps -p "$p" -o args= 2>/dev/null)"
  else
    return 0 # sin forma de verificar: asume que si para no duplicar
  fi
  case "$cmd" in
    *instant2*) return 0 ;;
    *) return 1 ;;
  esac
}

if [ $# -gt 0 ]; then
  # Passthrough en consola: instant2-run.sh check, run --help, etc.
  cd "$ROOT"
  exec "${RUN[@]}" "$@"
fi

if [ -f "$PIDFILE" ]; then
  LEPID="$(tr -d '[:space:]' < "$PIDFILE")"
  if [ -n "${LEPID:-}" ] && alive_pid "$LEPID" && matches_instant "$LEPID"; then
    echo "Ya hay instancia viva PID $LEPID. No se lanza otra. Usa instant2-status.sh."
    exit 0
  fi
  rm -f "$PIDFILE" # stale o vacio: se limpia
fi

if command -v pgrep >/dev/null 2>&1 && pgrep -f "instant2_app run|instant2 run" >/dev/null 2>&1; then
  echo "Ya hay instancia viva (pgrep) sin PID file. No se lanza otra."
  exit 0
fi

cd "$ROOT"
nohup "${RUN[@]}" run >/dev/null 2>&1 &
disown 2>/dev/null || true
echo "Daemon lanzado oculto (PID $!). Verifica con instant2-status.sh; frena con instant2-stop.sh."
