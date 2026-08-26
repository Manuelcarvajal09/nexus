#!/usr/bin/env bash
# Rollback: removes only what install.sh created. Never touches
# $XDG_DATA_HOME/nexus (your clip history) unless --purge-data is passed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/nexus"

rm -f "$APPS_DIR/nexus-linux.desktop"
rm -f "$AUTOSTART_DIR/nexus-linux.desktop"
rm -rf "$ROOT/.venv"

echo "OK: launcher, autostart y venv eliminados."
echo "El código fuente en $ROOT no se tocó."
echo "Tu historial de clips en $DATA_DIR NO se eliminó."

if [[ "${1:-}" == "--purge-data" ]]; then
    rm -rf "$DATA_DIR"
    echo "OK: $DATA_DIR eliminado (--purge-data)."
fi
