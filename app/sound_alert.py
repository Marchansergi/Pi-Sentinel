"""Audio local (sin Internet): alarma y sonido de entrada.

- Los sonidos se guardan normalizados como WAV PCM 16 bit. Al subir se aceptan WAV de 8/16/24/32 bit
  entero o coma flotante, cualquier frecuencia y número de canales, y —si `ffmpeg` está instalado—
  MP3/OGG/FLAC/M4A, que se convierten.
- El volumen se aplica sobre las muestras, así que no depende del mezclador.
- Reproductores, en este orden: pw-play (PipeWire) → paplay (PulseAudio) → aplay (ALSA). En modo
  automático, si uno falla se prueba el siguiente.
- Un fallo de audio se registra y se informa, pero NUNCA se propaga a la detección ni a ntfy.
"""
from __future__ import annotations

import array
import glob
import logging
import math
import os
import pwd
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

log = logging.getLogger(__name__)

MAX_SOUND_BYTES = 8 * 1024 * 1024
MAX_SOUND_SECONDS = 60
TARGET_RATE = 44100
# Orden de preferencia: los servidores de sonido (PipeWire/PulseAudio) primero, porque son los que
# enrutan al altavoz Bluetooth; `aplay` va directo a ALSA y puede acabar en HDMI/jack.
PLAYERS = (("pw-play", []), ("paplay", []), ("aplay", ["-q"]))
PLAYER_CHOICES = ("auto", "pw-play", "paplay", "aplay")
PLAY_TIMEOUT = 330.0


class AudioError(RuntimeError):
    pass


class AudioBackend(Protocol):
    def check(self, player: str = "auto") -> str: ...
    def play(self, path: Path, stop: threading.Event, device: str = "", player: str = "auto") -> str | None: ...


# --------------------------------------------------------------------------- sesión de audio


def _candidate_runtime_dirs() -> list[str]:
    """Directorios /run/user/<uid> donde puede estar el servidor de sonido del escritorio."""
    out: list[str] = []
    if os.environ.get("XDG_RUNTIME_DIR"):
        out.append(os.environ["XDG_RUNTIME_DIR"])
    uid = os.getuid()
    out.append(f"/run/user/{uid}")
    sudo_uid = os.environ.get("SUDO_UID")
    if sudo_uid and sudo_uid.isdigit():
        out.append(f"/run/user/{sudo_uid}")
    if uid == 0:  # como root no hay sesión de audio propia: se busca la del usuario del escritorio
        users = sorted((d for d in glob.glob("/run/user/*") if d.rsplit("/", 1)[-1].isdigit()),
                       key=lambda d: int(d.rsplit("/", 1)[-1]))
        out += [d for d in users if int(d.rsplit("/", 1)[-1]) >= 1000]
    seen: list[str] = []
    for d in out:
        if d not in seen:
            seen.append(d)
    return seen


def _has_sound_socket(d: str) -> bool:
    return os.path.exists(f"{d}/pipewire-0") or os.path.exists(f"{d}/pulse/native")


def session_runtime_dir() -> str | None:
    for d in _candidate_runtime_dirs():
        if os.path.isdir(d) and _has_sound_socket(d):
            return d
    return None


def audio_env() -> dict:
    """Entorno para el reproductor: apunta a la sesión de audio (PipeWire/PulseAudio) del usuario aunque
    la aplicación corra como servicio systemd o como root."""
    env = dict(os.environ)
    d = session_runtime_dir()
    if d is None:
        run_dir = f"/run/user/{os.getuid()}"
        if "XDG_RUNTIME_DIR" not in env and os.path.isdir(run_dir):
            env["XDG_RUNTIME_DIR"] = run_dir
        return env
    env["XDG_RUNTIME_DIR"] = d
    if "PULSE_SERVER" not in os.environ and os.path.exists(f"{d}/pulse/native"):
        env["PULSE_SERVER"] = f"unix:{d}/pulse/native"
    return env


def available_players() -> list[str]:
    return [e for e, _ in PLAYERS if shutil.which(e)]


# --------------------------------------------------------------------------- reproducción


class SubprocessBackend:
    def __init__(self) -> None:
        self.name: str | None = None  # último reproductor que funcionó

    def check(self, player: str = "auto") -> str:
        if player != "auto":
            if not shutil.which(player):
                raise AudioError(f"El reproductor '{player}' no está instalado. Elige «Automático» u otro en Alarmas.")
            return player
        players = available_players()
        if not players:
            raise AudioError(
                "No hay ningún reproductor de audio (pw-play/paplay/aplay). "
                "Instala 'pipewire-bin', 'pulseaudio-utils' o 'alsa-utils'."
            )
        return self.name if self.name in players else players[0]

    def play(self, path: Path, stop: threading.Event, device: str = "", player: str = "auto") -> str:
        """Reproduce y devuelve el reproductor usado. En modo automático prueba uno tras otro."""
        candidates = [self.check(player)] if player != "auto" else available_players()
        if not candidates:
            self.check(player)  # lanza el error explicativo
        if player == "auto" and self.name in candidates:  # primero el que funcionó la última vez
            candidates.remove(self.name)
            candidates.insert(0, self.name)
        errors: list[str] = []
        for pl in candidates:
            try:
                self._play_one(pl, path, stop, device)
                self.name = pl
                return pl
            except AudioError as exc:
                errors.append(str(exc))
                if stop.is_set():
                    break
                log.warning("Audio: %s; se prueba el siguiente reproductor", exc)
        raise AudioError(" | ".join(errors))

    @staticmethod
    def _play_one(player: str, path: Path, stop: threading.Event, device: str) -> None:
        exe = shutil.which(player)
        if not exe:
            raise AudioError(f"{player} no está instalado")
        args = dict(PLAYERS)[player]
        cmd = [exe, *args]
        if device:
            cmd += {"aplay": ["-D", device], "paplay": [f"--device={device}"], "pw-play": [f"--target={device}"]}[player]
        cmd.append(str(path))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=audio_env())
        except OSError as exc:
            raise AudioError(f"No se pudo ejecutar {player}: {exc}") from exc
        deadline = time.monotonic() + PLAY_TIMEOUT
        try:
            while proc.poll() is None:
                if stop.wait(0.1):
                    proc.terminate()
                    try:
                        proc.wait(2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return
                if time.monotonic() > deadline:
                    proc.kill()
                    raise AudioError(f"{player} no terminó (¿salida de audio bloqueada?)")
            if proc.returncode != 0:
                err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()[:240]
                raise AudioError(f"{player} falló (código {proc.returncode}): {err or 'sin detalles'}")
        finally:
            if proc.stderr:
                proc.stderr.close()


# --------------------------------------------------------------------------- decodificación


def _parse_wav(data: bytes) -> tuple[np.ndarray, int]:
    """WAV → (muestras float32 [n, canales] en -1..1, frecuencia). Soporta PCM 8/16/24/32 y float 32/64."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioError("No es un fichero WAV")
    pos, fmt, raw = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body_end = len(data) if (cid == b"data" and (size == 0 or pos + 8 + size > len(data))) else pos + 8 + size
        if cid == b"fmt ":
            fmt = data[pos + 8:body_end]
        elif cid == b"data":
            raw = data[pos + 8:body_end]
            if fmt is not None:
                break
        pos = body_end + (size & 1)
    if fmt is None or raw is None or len(fmt) < 16:
        raise AudioError("WAV incompleto (faltan las secciones de formato o datos)")
    tag, ch, rate, _, align, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:  # WAVE_FORMAT_EXTENSIBLE
        tag = struct.unpack("<H", fmt[24:26])[0]
    if ch < 1 or rate < 1000 or align < 1:
        raise AudioError("Cabecera WAV no válida")
    width = align // ch
    raw = raw[: (len(raw) // align) * align]
    if tag == 1:
        if width == 1:
            x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
        elif width == 2:
            x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
        elif width == 3:
            b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
            v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            x = (np.where(v & 0x800000, v - 0x1000000, v)).astype(np.float32) / 8388608.0
        elif width == 4:
            x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
        else:
            raise AudioError(f"WAV PCM de {bits} bits no soportado")
    elif tag == 3:
        if width == 4:
            x = np.frombuffer(raw, "<f4").astype(np.float32)
        elif width == 8:
            x = np.frombuffer(raw, "<f8").astype(np.float32)
        else:
            raise AudioError("WAV en coma flotante no soportado")
        x = np.nan_to_num(np.clip(x, -1.0, 1.0))
    else:
        raise AudioError(f"Codificación WAV no soportada (formato {tag}; p. ej. ADPCM). Conviértelo a PCM o MP3.")
    return x.reshape(-1, ch), rate


def _ffmpeg_to_wav(data: bytes) -> bytes:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise AudioError("Formato no compatible. Sube un fichero WAV, o instala ffmpeg en la Pi "
                         "(sudo apt install ffmpeg) para admitir MP3, OGG, FLAC o M4A.")
    try:
        r = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-t", str(MAX_SOUND_SECONDS),
             "-ac", "2", "-ar", str(TARGET_RATE), "-f", "wav", "-acodec", "pcm_s16le", "pipe:1"],
            input=data, capture_output=True, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioError("La conversión del audio tardó demasiado") from exc
    if r.returncode != 0 or len(r.stdout) < 64:
        raise AudioError("No se pudo leer el audio: " + (r.stderr.decode("utf-8", "replace").strip()[:200] or "formato desconocido"))
    return r.stdout


def _resample(x: np.ndarray, rate: int, target: int) -> np.ndarray:
    n_out = max(1, int(round(len(x) * target / rate)))
    src = np.arange(len(x), dtype=np.float64)
    dst = np.linspace(0, len(x) - 1, n_out)
    return np.stack([np.interp(dst, src, x[:, c]) for c in range(x.shape[1])], axis=1).astype(np.float32)


def load_audio(data: bytes) -> tuple[int, int, bytes, bool]:
    """Bytes de audio → (canales, frecuencia, PCM 16 bit, recortado). Normaliza formato y duración."""
    if not data:
        raise AudioError("El fichero está vacío")
    if data[:4] != b"RIFF":
        data = _ffmpeg_to_wav(data)
    try:
        x, rate = _parse_wav(data)
    except AudioError:
        if shutil.which("ffmpeg") and data[:4] == b"RIFF":
            x, rate = _parse_wav(_ffmpeg_to_wav(data))  # WAV con códec exótico: que lo convierta ffmpeg
        else:
            raise
    if x.shape[1] > 2:
        x = x.mean(axis=1, keepdims=True)
    if not (8000 <= rate <= 48000):
        x, rate = _resample(x, rate, TARGET_RATE), TARGET_RATE
    truncated = len(x) > MAX_SOUND_SECONDS * rate
    x = x[: MAX_SOUND_SECONDS * rate]
    if len(x) == 0:
        raise AudioError("El audio no contiene muestras")
    pcm = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2").tobytes()
    return x.shape[1], rate, pcm, truncated


def read_wav(data: bytes | Path) -> tuple[int, int, bytes]:
    """(canales, frecuencia, PCM 16 bit) de un fichero o bytes. Lanza AudioError si no es válido."""
    if not isinstance(data, (bytes, bytearray)):
        try:
            data = Path(data).read_bytes()
        except OSError as exc:
            raise AudioError(f"No se pudo leer el sonido: {exc}") from exc
    ch, rate, pcm, _ = load_audio(bytes(data))
    return ch, rate, pcm


def write_wav(path: Path, ch: int, rate: int, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def sanitize_sound_name(filename: str) -> str:
    """Nombre seguro para guardar: sin rutas, solo caracteres simples y extensión .wav."""
    stem = Path(filename.replace("\\", "/")).name
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", stem)
    stem = re.sub(r"[^A-Za-z0-9_. -]", "_", stem).strip(" ._")[:50]
    if not stem or stem.lower() in ("default", "entrada"):
        raise AudioError("Nombre de fichero no válido o reservado")
    return stem + ".wav"


def generate_default_wav(path: Path, seconds: float = 1.2, rate: int = 22050) -> None:
    """Sirena de dos tonos generada por código (sin derechos de terceros)."""
    n = int(seconds * rate)
    buf = array.array("h")
    for i in range(n):
        t = i / rate
        f = 880.0 if int(t / 0.3) % 2 == 0 else 660.0
        fade = min(1.0, (i % int(0.3 * rate)) / (0.02 * rate), (int(0.3 * rate) - i % int(0.3 * rate)) / (0.02 * rate))
        buf.append(int(0.8 * 32767 * fade * math.sin(2 * math.pi * f * t)))
    write_wav(path, 1, rate, buf.tobytes())


def generate_chime_wav(path: Path, rate: int = 22050) -> None:
    """Timbre suave de respaldo (dos notas) si falta el sonido de entrada incluido."""
    t = np.arange(int(0.9 * rate)) / rate
    wave_ = np.sin(2 * np.pi * 784 * t) * np.exp(-4.0 * t)
    t2 = t[: int(0.6 * rate)]
    wave_[int(0.25 * rate):int(0.25 * rate) + len(t2)] += np.sin(2 * np.pi * 1047 * t2) * np.exp(-5.0 * t2)
    data = (wave_ / max(1e-9, np.abs(wave_).max()) * 0.8 * 32767).astype("<i2")
    write_wav(path, 1, rate, data.tobytes())


def build_pcm(pcm: bytes, channels: int, rate: int, volume: float, repetitions: int, duration: float, normalize: bool = False) -> bytes:
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2")
    if normalize and samples.size:
        peak = float(np.abs(samples.astype(np.int32)).max())
        target = 0.9 * 32767
        if 0 < peak < target:  # sube los sonidos grabados muy bajos (máx. ×8) para que el volumen sea útil
            samples = np.clip(samples.astype(np.float32) * min(target / peak, 8.0), -32768, 32767).astype("<i2")
    if volume < 0.999:
        samples = (samples.astype(np.float32) * volume).astype("<i2")
    out = np.tile(samples, max(1, repetitions))
    return out[: int(duration * rate) * channels].tobytes()


# --------------------------------------------------------------------------- diagnóstico


def _run(cmd: list[str], timeout: float = 6.0) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=audio_env())
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def list_sinks() -> tuple[list[dict], str | None]:
    """Salidas de audio del servidor de sonido (vía pactl, compatible con PipeWire y PulseAudio)."""
    if not shutil.which("pactl"):
        return [], None
    rc, out = _run(["pactl", "list", "sinks"])
    sinks: list[dict] = []
    if rc == 0:
        cur: dict = {}
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Sink #"):
                if cur:
                    sinks.append(cur)
                cur = {}
            elif s.startswith("State:"):
                cur["state"] = s.split(":", 1)[1].strip()
            elif s.startswith("Name:"):
                cur["name"] = s.split(":", 1)[1].strip()
            elif s.startswith("Description:"):
                cur["description"] = s.split(":", 1)[1].strip()
        if cur:
            sinks.append(cur)
    for s in sinks:
        s["bluetooth"] = s.get("name", "").startswith("bluez")
    default = None
    rc, out = _run(["pactl", "get-default-sink"])
    if rc == 0 and out.strip():
        default = out.strip().splitlines()[0]
    else:
        rc, out = _run(["pactl", "info"])
        m = re.search(r"Default Sink:\s*(\S+)", out)
        default = m.group(1) if m else None
    return [s for s in sinks if s.get("name")], default


def audio_diagnostics(player: str = "auto", device: str = "") -> dict:
    """Todo lo necesario para entender por qué (no) suena, en un solo informe."""
    uid = os.getuid()
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    rdir = session_runtime_dir()
    env = audio_env()
    sinks, default = list_sinks()
    players = available_players()
    bt: list[str] = []
    if shutil.which("bluetoothctl"):
        rc, out = _run(["bluetoothctl", "devices", "Connected"], timeout=5)
        bt = [ln.split(" ", 2)[2] for ln in out.splitlines() if ln.startswith("Device ") and len(ln.split(" ", 2)) == 3]
    wp = ""
    if shutil.which("wpctl"):
        wp = "\n".join(_run(["wpctl", "status"])[1].splitlines()[:40])
    alsa = _run(["aplay", "-l"])[1].strip() if shutil.which("aplay") else ""

    hints: list[str] = []
    if not players:
        hints.append("No hay reproductor de audio instalado: sudo apt install pipewire-bin pulseaudio-utils alsa-utils")
    if uid == 0:
        hints.append("Pi Sentinel se está ejecutando como root (¿con sudo?). El audio Bluetooth pertenece a tu usuario: "
                     "ejecútalo sin sudo o como servicio con tu usuario.")
    if rdir is None:
        hints.append("No se encuentra la sesión de audio del usuario (PipeWire/PulseAudio). Si se ejecuta como servicio, "
                     "activa 'sudo loginctl enable-linger <usuario>' o inicia sesión en el escritorio.")
    bt_sinks = [s for s in sinks if s["bluetooth"]]
    if rdir and shutil.which("pactl") and not sinks:
        hints.append("El servidor de sonido no muestra ninguna salida de audio.")
    if sinks and not bt_sinks:
        hints.append("No hay ninguna salida Bluetooth activa. Empareja y conecta el altavoz (bluetoothctl o el icono de Bluetooth).")
    if bt_sinks and default and not default.startswith("bluez") and not device:
        hints.append(f"Hay un altavoz Bluetooth ({bt_sinks[0].get('description', bt_sinks[0]['name'])}) pero la salida "
                     f"predeterminada es otra ({default}). Selecciónalo como salida abajo y guarda.")
    if device and sinks and device not in {s["name"] for s in sinks} and not re.match(r"^(plug)?hw:|^default$", device):
        hints.append(f"La salida configurada «{device}» no existe ahora mismo (¿altavoz apagado o desconectado?).")
    if player == "aplay" and bt_sinks:
        hints.append("Con 'aplay' (ALSA directo) el sonido puede no llegar al altavoz Bluetooth: usa Automático, pw-play o paplay.")
    if not hints:
        hints.append("No se detectan problemas. Pulsa «Probar» en cada reproductor y comprueba por dónde suena.")
    return {
        "user": user, "uid": uid, "as_root": uid == 0,
        "systemd": bool(os.environ.get("INVOCATION_ID")),
        "runtime_dir": rdir, "env_xdg": env.get("XDG_RUNTIME_DIR"), "env_pulse": env.get("PULSE_SERVER"),
        "pipewire_socket": bool(rdir and os.path.exists(f"{rdir}/pipewire-0")),
        "pulse_socket": bool(rdir and os.path.exists(f"{rdir}/pulse/native")),
        "players": players, "configured_player": player, "configured_device": device,
        "sinks": sinks, "default_sink": default, "bluetooth_connected": bt,
        "wpctl_status": wp, "alsa_cards": alsa, "ffmpeg": bool(shutil.which("ffmpeg")),
        "hints": hints,
    }


def play_test_tone(backend: AudioBackend, tmp_dir: Path, player: str, device: str, lead_in: float, timeout: float = 15.0) -> dict:
    """Reproduce un tono corto de forma SÍNCRONA y devuelve el resultado real del reproductor."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    rate = 22050
    t = np.arange(int(0.8 * rate)) / rate
    tone = (np.sin(2 * np.pi * 880 * t) * np.minimum(1.0, np.minimum(t, 0.8 - t) * 40) * 0.8 * 32767).astype("<i2")
    pcm = b"\x00\x00" * int(lead_in * rate) + tone.tobytes()
    fd, tmp = tempfile.mkstemp(suffix=".wav", dir=tmp_dir)
    os.close(fd)
    result: dict = {"player_requested": player, "device": device}
    stop = threading.Event()
    box: dict = {}

    def run():
        try:
            box["used"] = backend.play(Path(tmp), stop, device, player)
        except AudioError as exc:
            box["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            box["error"] = f"Error inesperado: {exc}"

    try:
        write_wav(Path(tmp), 1, rate, pcm)
        t0 = time.monotonic()
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            stop.set()
            th.join(3)
            box.setdefault("error", "El reproductor no terminó a tiempo")
        result.update(ok="error" not in box, player_used=box.get("used"), error=box.get("error", ""),
                      ms=round((time.monotonic() - t0) * 1000))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return result


# --------------------------------------------------------------------------- canal de sonido


class SoundAlert:
    def __init__(
        self,
        get_settings: Callable[[], object],
        builtin_dir: Path,
        user_dir: Path,
        tmp_dir: Path,
        backend: AudioBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        section: str = "alarm",
    ) -> None:
        self.section = section  # sección de ajustes que gobierna este canal: «alarm» o «entry»
        self._get = get_settings
        self.builtin_dir, self.user_dir, self.tmp_dir = Path(builtin_dir), Path(user_dir), Path(tmp_dir)
        self._backend = backend or SubprocessBackend()
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_start: float | None = None
        self._mute_until: float = 0.0
        self.last_error: str = ""
        self.last_played_at: float | None = None
        self.player: str | None = None
        self.default_path = self.builtin_dir / "default_alarm.wav"
        self.chime_path = self.builtin_dir / "entrada.wav"

    @property
    def backend(self) -> AudioBackend:
        return self._backend

    def _cfg(self):
        return getattr(self._get(), self.section)

    def _target(self) -> tuple[str, str]:
        """(reproductor, dispositivo): compartidos por la alarma y el sonido de entrada."""
        a = self._get().alarm
        return a.player, a.audio_device

    # -- sonidos ------------------------------------------------------------------

    def ensure_default(self) -> None:
        for path, gen in ((self.default_path, generate_default_wav), (self.chime_path, generate_chime_wav)):
            if not path.exists():
                try:
                    gen(path)
                except OSError as exc:
                    log.error("No se pudo crear el sonido %s: %s", path.name, exc)

    def _builtin_names(self) -> list[str]:
        if not self.builtin_dir.is_dir():
            return []
        return sorted(p.name for p in self.builtin_dir.glob("*.wav") if p != self.default_path)

    def list_sounds(self) -> list[dict]:
        out = [{"name": "default", "builtin": True}] + [{"name": n, "builtin": True} for n in self._builtin_names()]
        if self.user_dir.is_dir():
            out += [{"name": p.name, "builtin": False} for p in sorted(self.user_dir.glob("*.wav"))]
        return out

    def resolve(self, name: str) -> Path:
        if name == "default":
            self.ensure_default()
            return self.default_path
        if name in self._builtin_names():
            return self.builtin_dir / name
        p = (self.user_dir / name).resolve()
        if p.parent != self.user_dir.resolve() or not p.is_file():
            raise AudioError(f"El sonido '{name}' no existe")
        return p

    def add_user_sound(self, filename: str, data: bytes) -> tuple[str, bool]:
        """Convierte y guarda un sonido subido. Devuelve (nombre guardado, recortado a 60 s)."""
        if len(data) > MAX_SOUND_BYTES:
            raise AudioError("El fichero supera el tamaño máximo (8 MB)")
        name = sanitize_sound_name(filename)
        ch, rate, pcm, truncated = load_audio(bytes(data))
        self.user_dir.mkdir(parents=True, exist_ok=True)
        dest = (self.user_dir / name).resolve()
        if dest.parent != self.user_dir.resolve() or name in self._builtin_names():
            raise AudioError("Nombre no válido")
        write_wav(dest, ch, rate, pcm)
        return name, truncated

    def delete_user_sound(self, name: str) -> None:
        p = self.resolve(name)
        if p.parent != self.user_dir.resolve():
            raise AudioError("No se pueden eliminar los sonidos incluidos")
        p.unlink()

    # -- control ------------------------------------------------------------------

    def mute(self, minutes: float | None) -> float:
        """Silencia durante `minutes` (None = reactivar). Devuelve el instante (epoch) de fin."""
        if minutes is None or minutes <= 0:
            self._mute_until = 0.0
            return 0.0
        self._mute_until = self._wall() + min(float(minutes), 24 * 60) * 60
        return self._mute_until

    def is_muted(self) -> bool:
        return self._wall() < self._mute_until

    def stop(self) -> None:
        self._stop.set()

    def playing(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def trigger(self, force: bool = False, wait: float = 0.0) -> str:
        """Dispara el sonido. Estados: TRIGGERED, DISABLED, MUTED, RATE_LIMITED, BUSY, ERROR.

        Con `wait` > 0 (botón «Probar») espera ese tiempo para detectar fallos del reproductor."""
        try:
            cfg = self._cfg()
            with self._lock:
                if not force:
                    if not cfg.enabled:
                        return "DISABLED"
                    if self.is_muted():
                        return "MUTED"
                    if self._last_start is not None and self._clock() - self._last_start < cfg.min_interval_seconds:
                        return "RATE_LIMITED"
                if self.playing():
                    if not force:
                        return "BUSY"
                    self._stop.set()  # una prueba explícita sustituye a lo que esté sonando
                    self._thread.join(3)
                try:
                    self.player = self._backend.check(self._target()[0])
                    ch, rate, pcm = read_wav(self.resolve(cfg.sound))
                except AudioError as exc:
                    self.last_error = str(exc)
                    log.error("Sonido no disponible (%s): %s", self.section, exc)
                    return "ERROR"
                data = build_pcm(pcm, ch, rate, cfg.volume, cfg.repetitions, cfg.duration_seconds, normalize=True)
                lead = int(self._get().alarm.lead_in_seconds * rate) * ch
                data = b"\x00\x00" * lead + data
                self._last_start = self._clock()
                self.last_error = ""
                self._stop.clear()
                self._thread = threading.Thread(target=self._play, args=(data, ch, rate), name=f"sound-{self.section}", daemon=True)
                self._thread.start()
                thread = self._thread
            if wait > 0:
                thread.join(wait)
                if not thread.is_alive() and self.last_error:
                    return "ERROR"
            return "TRIGGERED"
        except Exception as exc:  # el sonido jamás debe romper el flujo principal
            self.last_error = str(exc)
            log.exception("Error inesperado al disparar el sonido")
            return "ERROR"

    def _play(self, data: bytes, ch: int, rate: int) -> None:
        tmp = None
        try:
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".wav", dir=self.tmp_dir)
            os.close(fd)
            write_wav(Path(tmp), ch, rate, data)
            player, device = self._target()
            used = self._backend.play(Path(tmp), self._stop, device, player)
            if used:
                self.player = used
            self.last_error = ""
            self.last_played_at = self._wall()
        except AudioError as exc:
            self.last_error = str(exc)
            log.error("Fallo de audio (%s): %s", self.section, exc)
        except Exception as exc:
            self.last_error = f"Error inesperado de audio: {exc}"
            log.exception("Error inesperado reproduciendo el sonido")
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def join(self, timeout: float = 5.0) -> None:
        t = self._thread
        if t:
            t.join(timeout)

    def shutdown(self) -> None:
        self._stop.set()
        self.join(3.0)

    def status(self) -> dict:
        cfg = self._cfg()
        try:
            available, player, problem = True, self._backend.check(self._target()[0]), ""
        except AudioError as exc:
            available, player, problem = False, None, str(exc)
        return {
            "enabled": cfg.enabled,
            "muted": self.is_muted(),
            "muted_until": self._mute_until if self.is_muted() else None,
            "playing": self.playing(),
            "audio_available": available,
            "player": self.player or player,
            "last_error": self.last_error or problem,
            "last_played_at": self.last_played_at,
        }
