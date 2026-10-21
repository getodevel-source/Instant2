#!/usr/bin/env bash
# Suite de tests de Instant2 aislada: no toca el estado real de la maquina.
#
# La app escribe en dos lugares del disco del usuario: la configuracion
# ($APPDATA/instant2/config.json) y los modelos ($LOCALAPPDATA/instant2/models
# y el directorio del producto anterior). Este script los apunta a un
# directorio temporal, asi que correr los tests nunca puede romper la
# maquina de quien los corre.
#
# Uso: scripts/run-tests.sh [argumentos para unittest]
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/dictado"

# El sitio de usuario de Python esta bajo $APPDATA (o ~/.local): si lo movemos
# sin guardarlo, el interprete deja de encontrar numpy, onnxruntime, etc.
usersite="$(python -m site --user-site 2>/dev/null || true)"
if [ -n "$usersite" ]; then
  export PYTHONPATH="$usersite${PYTHONPATH:+:$PYTHONPATH}"
fi

isolation="${TMPDIR:-/tmp}/instant2-tests"
rm -rf "$isolation"
mkdir -p "$isolation/appdata" "$isolation/localdata" "$isolation/models"
export APPDATA="$isolation/appdata"
export LOCALAPPDATA="$isolation/localdata"
export INSTANT2_DATA="$isolation/models"
export INSTANT2_CONFIG_DIR="$isolation/appdata/instant2"

exec python -m unittest discover -s tests "$@"
