#!/usr/bin/env bash
# uninstall.sh: desinstala Instant2 en Linux/macOS.
# Por defecto conserva config (~/.config/instant2 o ~/Library/...) y modelos
# (igual que el desinstalador de Windows): borrar 670 MB y ajustes sin pedir
# sería destructivo. Con --purge se borra también todo lo del usuario.
#
# Uso: scripts/uninstall.sh [--purge] [--yes]
#   --purge  borra config, logs, pid y modelos del usuario tras frenar todo.
#   --yes    no pide confirmación (para CI).
set -euo pipefail

PURGE=0
YES=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --yes) YES=1 ;;
    -h|--help)
      echo "Uso: $0 [--purge] [--yes]"
      echo "  Sin flags: frena daemon+panel y quita el autostart; conserva config y modelos."
      echo "  --purge: además borra la config y los modelos del usuario."
      exit 0 ;;
    *) echo "ERROR: flag desconocido: $arg (ver $0 --help)"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"

config_dir() {
  case "$OS" in
    Darwin) printf '%s' "$HOME/Library/Application Support/instant2" ;;
    *) printf '%s' "${XDG_CONFIG_HOME:-$HOME/.config}/instant2" ;;
  esac
}
data_dir() {
  case "$OS" in
    Darwin) printf '%s' "$HOME/Library/Application Support/instant2/models" ;;
    *) printf '%s' "${XDG_DATA_HOME:-$HOME/.local/share}/instant2/models" ;;
  esac
}

CONFIG_DIR="$(config_dir)"
DATA_DIR="${INSTANT2_DATA:-$(data_dir)}"
DESKTOP_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/instant2.desktop"
PLIST="$HOME/Library/LaunchAgents/com.instant2.dictado.plist"

echo "[1/3] Frenando daemon y panel..."
if [ -x "$SCRIPT_DIR/instant2-stop.sh" ]; then
  "$SCRIPT_DIR/instant2-stop.sh" || true
else
  echo "  (sin instant2-stop.sh; intento por PID file)"
  PIDFILE="$CONFIG_DIR/instant2.pid"
  if [ -f "$PIDFILE" ]; then
    LEPID="$(tr -d '[:space:]' < "$PIDFILE" 2>/dev/null || true)"
    if [ -n "${LEPID:-}" ]; then kill "$LEPID" 2>/dev/null || true; fi
  fi
fi
# El panel Qt no lo cubre el stop del daemon: se cierra por cmdline propia.
if command -v pkill >/dev/null 2>&1; then
  pkill -f "instant2.*(panel|gui|setup)" 2>/dev/null || true
fi

echo "[2/3] Quitando arranque automático..."
case "$OS" in
  Darwin)
    if [ -f "$PLIST" ]; then
      if grep -q "com.instant2.dictado" "$PLIST" 2>/dev/null; then
        if command -v launchctl >/dev/null 2>&1; then
          launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
        fi
        rm -f "$PLIST" && echo "  LaunchAgent eliminado."
      else
        echo "  $PLIST no es de Instant2; no se toca."
      fi
    else
      echo "  Sin LaunchAgent."
    fi ;;
  *)
    if [ -f "$DESKTOP_FILE" ]; then
      if grep -q "instant2-autostart-managed" "$DESKTOP_FILE" 2>/dev/null; then
        rm -f "$DESKTOP_FILE" && echo "  Autostart .desktop eliminado."
      else
        echo "  $DESKTOP_FILE no es de Instant2; no se toca."
      fi
    else
      echo "  Sin autostart .desktop."
    fi ;;
esac

if [ "$PURGE" -eq 1 ]; then
  echo "[3/3] Purga de datos de usuario..."
  echo "  Se borrará: $CONFIG_DIR"
  echo "  Se borrará: $DATA_DIR"
  if [ "$YES" -ne 1 ]; then
    printf '  Confirmar purga total (config+modelos) [s/N]: '
    read -r answer
    case "$answer" in
      s|S|sí|si|y|Y|yes) ;;
      *) echo "  Purga cancelada; config y modelos conservados."; exit 0 ;;
    esac
  fi
  canon() {
    if command -v realpath >/dev/null 2>&1; then
      realpath -m -- "$1" 2>/dev/null || printf '%s' "$1"
    elif command -v readlink >/dev/null 2>&1; then
      readlink -m -- "$1" 2>/dev/null || printf '%s' "$1"
    else
      printf '%s' "$1"
    fi
  }
  HOME_C="$(canon "$HOME")"
  for target in "$CONFIG_DIR" "$DATA_DIR"; do
    canon_target="$(canon "$target")"
    case "$canon_target" in
      ""|"/"|"$HOME_C")
        echo "  Ruta de purga peligrosa, no se toca: $target"; continue ;;
    esac
    case "$canon_target" in
      *instant2*) rm -rf -- "$target" && echo "  Eliminado: $target" ;;
      *) echo "  No parece de Instant2, no se toca: $target" ;;
    esac
  done
else
  echo "[3/3] Datos conservados:"
  echo "  config: $CONFIG_DIR"
  echo "  modelos: $DATA_DIR"
  echo "  Para borrarlos también: $0 --purge"
fi
echo "Listo."
