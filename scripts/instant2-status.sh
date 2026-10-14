#!/usr/bin/env bash
# instant2-status.sh: diagnostico rapido, NO levanta el daemon, solo lo inspecciona.
# Modelos: <repo>/models (relativo al script). Solo default, respeta INSTANT2_DATA previa.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
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
CONFDIR="$(config_dir)"
PIDFILE="$CONFDIR/instant2.pid"
LOGFILE="$CONFDIR/instant2.log"
CONFFILE="$CONFDIR/config.json"

alive_pid() {
  kill -0 "$1" 2>/dev/null
}

matches_instant() {
  local p="$1" cmd=""
  if [ -r "/proc/$p/cmdline" ]; then
    cmd="$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)"
  elif command -v ps >/dev/null 2>&1; then
    cmd="$(ps -p "$p" -o args= 2>/dev/null)"
  else
    return 0
  fi
  case "$cmd" in
    *instant2*) return 0 ;;
    *) return 1 ;;
  esac
}

echo "== proceso =="
if [ -f "$PIDFILE" ]; then
  LEPID="$(tr -d '[:space:]' < "$PIDFILE")"
  if [ -z "${LEPID:-}" ]; then
    echo "PID file vacio; se limpia."
    rm -f "$PIDFILE"
  elif alive_pid "$LEPID" && matches_instant "$LEPID"; then
    echo "vivo: SI PID $LEPID"
  else
    echo "vivo: NO PID $LEPID stale; el proceso murio."
    rm -f "$PIDFILE"
    echo "PID file stale limpio."
  fi
elif command -v pgrep >/dev/null 2>&1 && pgrep -f "instant2_app run|instant2 run" >/dev/null 2>&1; then
  echo "vivo: SI (pgrep) sin PID file, lanzado con version vieja."
else
  echo "vivo: NO"
fi

echo "== modelos =="
echo "INSTANT2_DATA=$INSTANT2_DATA"
if [ -f "$INSTANT2_DATA/parakeet-v3-int8/encoder.int8.onnx" ]; then
  echo "dir INSTANT2_DATA: OK encoder.int8.onnx presente."
else
  echo "dir INSTANT2_DATA: VACIO o sin modelos, revisa la ruta."
fi

echo ""
echo "== log: ultimas 15 lineas de \$config/instant2.log ($LOGFILE) =="
if [ -f "$LOGFILE" ]; then
  tail -n 15 "$LOGFILE"
else
  echo "(sin log todavia)"
fi

echo ""
echo "== config: config.json ($CONFFILE) =="
if [ -f "$CONFFILE" ]; then
  cat "$CONFFILE"
else
  echo "(sin config todavia: corre 'instant2 setup')"
fi
