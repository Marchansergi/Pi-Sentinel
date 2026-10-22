#!/usr/bin/env bash
# Desinstala Pi Sentinel. Pregunta antes de borrar los datos (incluye perfiles biométricos).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if systemctl list-unit-files 2>/dev/null | grep -q '^pi-sentinel.service'; then
  echo "Deteniendo y eliminando el servicio systemd (requiere sudo)..."
  sudo systemctl disable --now pi-sentinel || true
  sudo rm -f /etc/systemd/system/pi-sentinel.service; sudo systemctl daemon-reload
fi
read -r -p "¿Borrar TODOS los datos (base de datos, perfiles faciales, secretos, registros) en $DIR/data? [escribe BORRAR] " r
if [ "$r" = "BORRAR" ]; then rm -rf "$DIR/data" "$DIR"/data-backup-*.tar.gz; echo "Datos eliminados."; else echo "Datos conservados."; fi
rm -rf "$DIR/.venv"
echo "Entorno virtual eliminado. Puedes borrar la carpeta $DIR si ya no la necesitas."
