#!/usr/bin/env bash
# Instalador de Pi Sentinel (Raspberry Pi OS 64 bits / Debian / Ubuntu).
# Uso: ./install.sh [--yes] [--service] [--no-models] [--dry-run]
#   --yes        no pregunta (acepta las acciones descritas)
#   --service    instala y activa el servicio systemd (inicio automático)
#   --no-models  omite la descarga de modelos
#   --dry-run    solo muestra lo que haría
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=0; SERVICE=0; MODELS=1; DRY=0
for a in "$@"; do case "$a" in
  --yes) YES=1;; --service) SERVICE=1;; --no-models) MODELS=0;; --dry-run) DRY=1;;
  -h|--help) sed -n '2,8p' "$0"; exit 0;; *) echo "Opción desconocida: $a"; exit 2;; esac; done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
run()  { echo "+ $*"; [ "$DRY" = 1 ] || "$@"; }
ask()  { [ "$YES" = 1 ] && return 0; read -r -p "$1 [s/N] " r; [[ "$r" =~ ^[sSyY]$ ]]; }

if [ "$(id -u)" = 0 ]; then
  echo "No ejecutes este instalador como root: el servicio se ejecutará con tu usuario. (Usará sudo solo para apt/systemd.)"; exit 1
fi

say "1-3. Compatibilidad"
ARCH="$(uname -m)"; OS="$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-desconocido}")"
IS_PI=0; MODEL="no es una Raspberry Pi"
if [ -r /proc/device-tree/model ]; then MODEL="$(tr -d '\0' </proc/device-tree/model)"; grep -qi "raspberry" /proc/device-tree/model && IS_PI=1; fi
echo "Arquitectura: $ARCH | SO: $OS | Equipo: $MODEL"
case "$ARCH" in aarch64|x86_64) ;; *) echo "ERROR: arquitectura $ARCH no soportada (se necesita un SO de 64 bits)."; exit 1;; esac
command -v python3 >/dev/null || { echo "ERROR: falta python3"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || { echo "ERROR: se necesita Python >= 3.9"; exit 1; }
RAM=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo); echo "RAM: ${RAM} MB"
[ "$RAM" -lt 1800 ] && echo "AVISO: poca RAM. Tras instalar usa Resolución del detector 320 y 1 análisis/s."

say "4. Dependencias del sistema (apt, requiere sudo)"
echo "Se instalarán: python3-venv python3-pip, audio (alsa-utils, pulseaudio-utils para pactl/paplay, pipewire-bin para pw-play),"
echo "ffmpeg (para subir sonidos MP3/OGG/FLAC/M4A) y, en Raspberry Pi, python3-picamera2 (cámara oficial)."
if command -v apt-get >/dev/null && ask "¿Instalar con apt?"; then
  run sudo apt-get update
  PKGS="python3-venv python3-pip alsa-utils pulseaudio-utils pipewire-bin ffmpeg"
  [ "$IS_PI" = 1 ] && PKGS="$PKGS python3-picamera2"
  run sudo apt-get install -y $PKGS || { echo "AVISO: no se pudieron instalar todos los paquetes; se reintenta uno a uno."; for p in $PKGS; do run sudo apt-get install -y "$p" || echo "  - no disponible: $p"; done; }
else echo "(omitido)"; fi
groups | grep -qw video || echo "AVISO: tu usuario no está en el grupo 'video' (cámara USB): sudo usermod -aG video $USER"
groups | grep -qw audio || echo "AVISO: tu usuario no está en el grupo 'audio': sudo usermod -aG audio $USER"

say "5-6. Entorno virtual y dependencias de Python"
VENVOPT=""; [ "$IS_PI" = 1 ] && VENVOPT="--system-site-packages"   # picamera2 viene de apt
[ -d "$DIR/.venv" ] || run python3 -m venv $VENVOPT "$DIR/.venv"
run "$DIR/.venv/bin/pip" install --upgrade pip
run "$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

say "7. Modelos (descarga verificada por SHA-256)"
if [ "$MODELS" = 1 ]; then run "$DIR/.venv/bin/python" "$DIR/scripts/download_models.py"; else echo "(omitido)"; fi

say "8. Configuración inicial"
[ -f "$DIR/.env" ] || run cp "$DIR/.env.example" "$DIR/.env"
run mkdir -p "$DIR/data"; run chmod 700 "$DIR/data"; [ -f "$DIR/.env" ] && run chmod 600 "$DIR/.env"

say "9-10. Comprobación de cámara y audio"
[ "$DRY" = 1 ] || "$DIR/.venv/bin/python" -m app.main check || echo "AVISO: la comprobación detectó problemas (ver arriba)."
echo "Diagnóstico de audio (Bluetooth/HDMI/jack): $DIR/.venv/bin/python -m app.main audio-test"

say "11-12. ntfy"
echo "Se configura desde la interfaz (Notificaciones): genera un tópico aleatorio, suscríbete desde la app ntfy del móvil y pulsa «Enviar notificación de prueba»."

say "14. Servicio systemd (inicio automático)"
if [ "$SERVICE" = 1 ] || { [ "$YES" = 0 ] && ask "¿Instalar el servicio systemd para iniciar Pi Sentinel al arrancar?"; }; then
  UNIT=/etc/systemd/system/pi-sentinel.service
  if [ "$DRY" = 1 ]; then echo "+ (generaría $UNIT para el usuario $USER)"; else
    sed -e "s|@USER@|$USER|g" -e "s|@UID@|$(id -u)|g" -e "s|@DIR@|$DIR|g" "$DIR/scripts/pi-sentinel.service.in" | sudo tee "$UNIT" >/dev/null
    echo "Para que el audio (p. ej. un altavoz Bluetooth) funcione sin tener la sesión abierta, hace falta mantener tu sesión de audio activa: sudo loginctl enable-linger $USER"
    if ask "¿Ejecutar 'loginctl enable-linger $USER' ahora?"; then sudo loginctl enable-linger "$USER"; fi
    sudo systemctl daemon-reload; sudo systemctl enable --now pi-sentinel.service
    echo "Servicio activo. Desactivar el inicio automático: sudo systemctl disable --now pi-sentinel"
  fi
else echo "(omitido) Puedes ejecutarlo a mano con: $DIR/.venv/bin/python -m app.main"; fi

say "13. Listo"
echo "Inicia:   $DIR/.venv/bin/python -m app.main      Interfaz: http://127.0.0.1:8765"
echo "Siguiente: registra tu rostro (Personas) y prueba en 'Modo prueba' antes de activar la vigilancia."
