#!/usr/bin/env bash
# Actualiza Pi Sentinel: git pull + dependencias + modelos + reinicio del servicio (si existe).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
echo "Copia de seguridad de los datos (perfiles/configuración)..."
[ -d data ] && tar -czf "data-backup-$(date +%Y%m%d-%H%M%S).tar.gz" data && chmod 600 data-backup-*.tar.gz || true
git pull --ff-only
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/download_models.py
if systemctl list-unit-files 2>/dev/null | grep -q '^pi-sentinel.service'; then sudo systemctl restart pi-sentinel; fi
echo "Actualizado. Las copias data-backup-*.tar.gz contienen datos biométricos: guárdalas con cuidado o bórralas."
