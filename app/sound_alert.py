"""Alarma sonora local (sin Internet).

- Sonidos WAV PCM de 16 bits. El volumen se aplica sobre las muestras, así que no depende del mezclador.
- Se reproduce con el primer reproductor disponible (aplay, paplay, pw-play) en un hilo aparte.
- Un fallo de audio se registra y se informa, pero NUNCA se propaga a la detección ni a ntfy.
"""
from __future__ import annotations

import array
import io
import logging
import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger(__name__)

MAX_SOUND_BYTES = 8 * 1024 * 1024
MAX_SOUND_SECONDS = 60
PLAYERS = (("aplay", ["-q"]), ("paplay", []), ("pw-play", []))


class AudioError(RuntimeError):
    pass


class AudioBackend(Protocol):
    def check(self) -> str: ...
    def play(self, path: Path, stop: threading.Event, device: str = "") -> None: ...


class SubprocessBackend:
    def __init__(self) -> None:
        self.name: str | None = None

    def _find(self) -> tuple[str, list[str]]:
        for exe, args in PLAYERS:
            p = shutil.which(exe)
            if p:
                self.name = exe
                return p, args
        raise AudioError(
            "No se encontró ningún reproductor de audio (aplay/paplay/pw-play). "
            "Instala 'alsa-utils' y comprueba la salida de audio con 'aplay -l'."
        )

    def check(self) -> str:
        exe, _ = self._find()
        return Path(exe).name

    def play(self, path: Path, stop: threading.Event, device: str = "") -> None:
        exe, args = self._find()
        cmd = [exe, *args]
        if device:
            if Path(exe).name == "aplay":
                cmd += ["-D", device]
            elif Path(exe).name == "paplay":
                cmd += [f"--device={device}"]
        cmd.append(str(path))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            raise AudioError(f"No se pudo ejecutar el reproductor: {exc}") from exc
        try:
            while proc.poll() is None:
                if stop.wait(0.1):
                    proc.terminate()
                    try:
                        proc.wait(2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return
            if proc.returncode != 0:
                err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()[:200]
                raise AudioError(f"El reproductor falló (código {proc.returncode}): {err or 'sin detalles'}. ¿Hay salida de audio conectada?")
        finally:
            if proc.stderr:
                proc.stderr.close()


# --------------------------------------------------------------------------- WAV


def generate_default_wav(path: Path, seconds: float = 1.2, rate: int = 22050) -> None:
    """Sirena de dos tonos generada por código (sin derechos de terceros)."""
    n = int(seconds * rate)
    buf = array.array("h")
    for i in range(n):
        t = i / rate
        f = 880.0 if int(t / 0.3) % 2 == 0 else 660.0
        fade = min(1.0, (i % int(0.3 * rate)) / (0.02 * rate), (int(0.3 * rate) - i % int(0.3 * rate)) / (0.02 * rate))
        buf.append(int(0.8 * 32767 * fade * math.sin(2 * math.pi * f * t)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(buf.tobytes())


def read_wav(data: bytes | Path) -> tuple[int, int, bytes]:
    """Devuelve (canales, frecuencia, PCM 16 bit). Lanza AudioError si no es válido."""
    try:
        src = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else str(data)
        with wave.open(src, "rb") as w:
            if w.getsampwidth() != 2 or w.getcomptype() != "NONE":
                raise AudioError("Solo se admiten WAV PCM de 16 bits")
            ch, rate, n = w.getnchannels(), w.getframerate(), w.getnframes()
            if ch not in (1, 2) or not (8000 <= rate <= 48000):
                raise AudioError("WAV no soportado (1-2 canales, 8-48 kHz)")
            if n / rate > MAX_SOUND_SECONDS:
                raise AudioError(f"El sonido dura más de {MAX_SOUND_SECONDS}s")
            return ch, rate, w.readframes(n)
    except (wave.Error, EOFError, struct.error) as exc:
        raise AudioError(f"Fichero WAV no válido: {exc}") from exc


def build_pcm(pcm: bytes, channels: int, rate: int, volume: float, repetitions: int, duration: float) -> bytes:
    import numpy as np

    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2")
    if volume < 0.999:
        samples = (samples.astype(np.float32) * volume).astype("<i2")
    out = np.tile(samples, max(1, repetitions))
    return out[: int(duration * rate) * channels].tobytes()


# --------------------------------------------------------------------------- alarma


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
    ) -> None:
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

    # -- sonidos ------------------------------------------------------------------

    def ensure_default(self) -> None:
        if not self.default_path.exists():
            try:
                generate_default_wav(self.default_path)
            except OSError as exc:
                log.error("No se pudo crear el sonido por defecto: %s", exc)

    def list_sounds(self) -> list[dict]:
        out = [{"name": "default", "builtin": True}]
        if self.user_dir.is_dir():
            out += [{"name": p.name, "builtin": False} for p in sorted(self.user_dir.glob("*.wav"))]
        return out

    def resolve(self, name: str) -> Path:
        if name == "default":
            self.ensure_default()
            return self.default_path
        p = (self.user_dir / name).resolve()
        if p.parent != self.user_dir.resolve() or not p.is_file():
            raise AudioError(f"El sonido '{name}' no existe")
        return p

    def add_user_sound(self, name: str, data: bytes) -> None:
        if len(data) > MAX_SOUND_BYTES:
            raise AudioError("El fichero supera el tamaño máximo (8 MB)")
        read_wav(bytes(data))  # valida
        self.user_dir.mkdir(parents=True, exist_ok=True)
        dest = (self.user_dir / name).resolve()
        if dest.parent != self.user_dir.resolve():
            raise AudioError("Nombre no válido")
        dest.write_bytes(data)

    def delete_user_sound(self, name: str) -> None:
        p = self.resolve(name)
        if p == self.default_path:
            raise AudioError("No se puede eliminar el sonido por defecto")
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

    def trigger(self, force: bool = False) -> str:
        """Dispara la alarma. Estados: TRIGGERED, DISABLED, MUTED, RATE_LIMITED, BUSY, ERROR."""
        try:
            cfg = self._get().alarm
            with self._lock:
                if not force:
                    if not cfg.enabled:
                        return "DISABLED"
                    if self.is_muted():
                        return "MUTED"
                    if self._last_start is not None and self._clock() - self._last_start < cfg.min_interval_seconds:
                        return "RATE_LIMITED"
                if self.playing():
                    return "BUSY"
                try:
                    self.player = self._backend.check()
                    path = self.resolve(cfg.sound)
                    ch, rate, pcm = read_wav(path)
                except AudioError as exc:
                    self.last_error = str(exc)
                    log.error("Alarma no disponible: %s", exc)
                    return "ERROR"
                data = build_pcm(pcm, ch, rate, cfg.volume, cfg.repetitions, cfg.duration_seconds)
                self._last_start = self._clock()
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._play, args=(data, ch, rate, cfg.audio_device), name="alarm", daemon=True
                )
                self._thread.start()
                return "TRIGGERED"
        except Exception as exc:  # la alarma jamás debe romper el flujo principal
            self.last_error = str(exc)
            log.exception("Error inesperado al disparar la alarma")
            return "ERROR"

    def _play(self, data: bytes, ch: int, rate: int, device: str) -> None:
        tmp = None
        try:
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".wav", dir=self.tmp_dir)
            with os.fdopen(fd, "wb") as fh, wave.open(fh, "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(data)
            self._backend.play(Path(tmp), self._stop, device)
            self.last_error = ""
            self.last_played_at = self._wall()
        except AudioError as exc:
            self.last_error = str(exc)
            log.error("Fallo de audio: %s", exc)
        except Exception as exc:
            self.last_error = f"Error inesperado de audio: {exc}"
            log.exception("Error inesperado reproduciendo la alarma")
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
        cfg = self._get().alarm
        try:
            available, player, problem = True, self._backend.check(), ""
        except AudioError as exc:
            available, player, problem = False, None, str(exc)
        return {
            "enabled": cfg.enabled,
            "muted": self.is_muted(),
            "muted_until": self._mute_until if self.is_muted() else None,
            "playing": self.playing(),
            "audio_available": available,
            "player": player,
            "last_error": self.last_error or problem,
            "last_played_at": self.last_played_at,
        }
