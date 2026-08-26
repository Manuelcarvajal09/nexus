#!/usr/bin/env bash
# Installs Nexus Linux for the current user only. No sudo, no system files
# touched outside $XDG_DATA_HOME/applications and $XDG_CONFIG_HOME/autostart.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

echo "== Nexus Linux install =="
echo "Directorio del proyecto: $ROOT"

if [[ ! -d "$VENV" ]]; then
    echo "-- Creando venv en $VENV"
    python3 -m venv "$VENV"
fi

echo "-- Instalando dependencias (PySide6) en el venv"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$ROOT"

echo "-- Generando launcher .desktop"
mkdir -p "$APPS_DIR"
sed "s|__INSTALL_DIR__|$ROOT|g" "$ROOT/resources/nexus-linux.desktop" > "$APPS_DIR/nexus-linux.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "OK: instalado. Lanzador disponible en el menú de aplicaciones como 'Nexus'."
echo "Para autostart: ./install.sh --autostart"

if [[ "${1:-}" == "--autostart" ]]; then
    mkdir -p "$AUTOSTART_DIR"
    cp "$APPS_DIR/nexus-linux.desktop" "$AUTOSTART_DIR/nexus-linux.desktop"
    echo "OK: autostart habilitado (XDG autostart en $AUTOSTART_DIR)."
fi
