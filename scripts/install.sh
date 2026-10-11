#!/usr/bin/env bash
# Instala Instant en un entorno virtual del repositorio y abre el asistente.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
OS="$(uname -s)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[Instant] Falta Python 3.12 o posterior." >&2
  exit 1
fi
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"; then
  echo "[Instant] Se requiere Python 3.12 o posterior." >&2
  exit 1
fi

case "$OS" in
  Linux)
    echo "[Instant] Instalando dependencias del sistema..."
    if command -v pacman >/dev/null 2>&1; then
      # Omarchy y otras distros Arch usan Hyprland/Wayland con frecuencia.
      # Instalar en un sistema Arch ya actualizado evita forzar un upgrade
      # completo del sistema desde el instalador de Instant.
      sudo pacman -S --needed --noconfirm \
        python python-pip portaudio xclip xdotool wl-clipboard wtype \
        xdg-desktop-portal xdg-desktop-portal-hyprland \
        xcb-util-cursor libxkbcommon mesa nss libxcomposite libxdamage \
        libxrandr libxtst cups atk alsa-lib qt6-wayland
    elif command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update
      sudo apt-get install -y python3-venv libportaudio2 xclip xdotool wl-clipboard
      # wtype es solo para Wayland. Algunas instalaciones mínimas de Ubuntu
      # no tienen habilitado Universe, así que su ausencia no bloquea X11.
      sudo apt-get install -y wtype || true
      # Libs que el wheel de PySide6 no trae y el panel/overlay web necesita para
      # abrir: xcb del panel y el stack de QtWebEngine del overlay (NSS, X11
      # extendido, ALSA, GBM). Si alguna no existe en esta distro, no aborta la
      # instalación: el setup lo reporta y sugiere el paquete exacto.
      sudo apt-get install -y libxcb-cursor0 libegl1 libgl1 libxkbcommon0 \
        libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libcups2 \
        libatk-bridge2.0-0 libgbm1 libasound2 || true
    else
      echo "[Instant] Linux necesita apt o pacman; instala las dependencias manualmente." >&2
      exit 1
    fi
    ;;
  Darwin)
    echo "[Instant] Instalando PortAudio..."
    brew install portaudio
    ;;
  *)
    echo "[Instant] Sistema operativo no compatible: $OS" >&2
    exit 1
    ;;
esac

if [ -z "${DICTADO_DATA:-}" ]; then
  export DICTADO_DATA="$ROOT/models"
fi

PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "[Instant] Creando entorno virtual..."
  python3 -m venv "$ROOT/.venv"
fi

echo "[Instant] Instalando Instant y sus dependencias..."
"$PYTHON" -m pip install "$ROOT/dictado"

SETUP_ARGS=()
if [ -n "${INSTANT_UNATTENDED:-}" ]; then
  SETUP_ARGS+=(--yes)
fi
if [ "${INSTANT_AUTOSTART:-0}" = "1" ]; then
  SETUP_ARGS+=(--autostart)
fi
# Sin entorno gráfico en Linux (SSH, servidor) el panel no puede abrir:
# asistente de terminal. En macOS el panel abre sin DISPLAY, así que se ofrece.
if [ "$OS" = "Linux" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
  SETUP_ARGS+=(--tui)
fi

echo "[Instant] Configurando modelos, micrófono y tecla..."
cd "$ROOT"
"$PYTHON" -m instant_app setup "${SETUP_ARGS[@]}"

echo
echo "[Instant] Instalación completa. Arranca con ./instant-run.sh."
