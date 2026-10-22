"""Captura de cámara.

Diseño:
- `FrameSource` abstrae el origen (OpenCV/V4L2, Picamera2, o uno de pruebas).
- `CameraManager` ejecuta UN hilo de captura y conserva únicamente el último fotograma
  (cola de tamaño 1): si el análisis va más lento, se descartan los antiguos.
- Los fotogramas inválidos se cuentan y se descartan; tras varios fallos seguidos se reabre el
  dispositivo con espera progresiva. Nada de esto afecta a otros módulos.
- Los fotogramas viven solo en memoria. Nunca se escriben a disco.
"""
from __future__ import annotations

import glob
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

log = logging.getLogger(__name__)


class CameraError(RuntimeError):
    pass


class FrameSource(Protocol):
    def open(self) -> None: ...
    def read(self, decode: bool = True) -> tuple[bool, np.ndarray | None]: ...
    def close(self) -> None: ...


def validate_frame(frame) -> str | None:
    """Devuelve None si el fotograma es utilizable, o el motivo del rechazo."""
    if frame is None:
        return "fotograma vacío"
    if not isinstance(frame, np.ndarray):
        return "tipo de fotograma no válido"
    if frame.dtype != np.uint8:
        return "profundidad de color no soportada"
    if frame.ndim != 3 or frame.shape[2] != 3:
        return "el fotograma no es BGR de 3 canales"
    if frame.shape[0] < 32 or frame.shape[1] < 32:
        return "fotograma demasiado pequeño"
    return None


# --------------------------------------------------------------------------- orígenes reales


class OpenCVSource:
    """Cámaras USB / V4L2 mediante OpenCV."""

    def __init__(self, device: str, width: int, height: int, fps: float) -> None:
        self.device, self.width, self.height, self.fps = device, width, height, fps
        self._cap = None

    def open(self) -> None:
        import cv2

        target: int | str = int(self.device) if self.device.isdigit() else self.device
        cap = cv2.VideoCapture(target, cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else cv2.VideoCapture(target)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(target)  # reintento con el backend por defecto
        if not cap.isOpened():
            cap.release()
            raise CameraError(f"No se pudo abrir la cámara '{self.device}'")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap

    def read(self, decode: bool = True) -> tuple[bool, np.ndarray | None]:
        if self._cap is None:
            return False, None
        if not self._cap.grab():
            return False, None
        if not decode:
            return True, None
        return self._cap.retrieve()

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            finally:
                self._cap = None


class Picamera2Source:
    """Módulos de cámara de Raspberry Pi vía libcamera/Picamera2. SIN VALIDAR EN HARDWARE REAL."""

    def __init__(self, index: int, width: int, height: int, fps: float) -> None:
        self.index, self.width, self.height, self.fps = index, width, height, fps
        self._cam = None
        self._period = 1.0 / max(fps, 1.0)
        self._last = 0.0

    def open(self) -> None:
        try:
            from picamera2 import Picamera2  # type: ignore
        except Exception as exc:
            raise CameraError(f"Picamera2 no está disponible: {exc}") from exc
        try:
            cam = Picamera2(self.index)
            # En Picamera2 el formato "RGB888" entrega los píxeles en orden BGR, el que usa OpenCV.
            cfg = cam.create_video_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"}, buffer_count=2
            )
            cam.configure(cfg)
            cam.start()
        except Exception as exc:
            raise CameraError(f"No se pudo iniciar la cámara de Raspberry Pi: {exc}") from exc
        self._cam = cam

    def read(self, decode: bool = True) -> tuple[bool, np.ndarray | None]:
        if self._cam is None:
            return False, None
        wait = self._period - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            return True, self._cam.capture_array("main")
        except Exception:
            return False, None

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            except Exception:
                log.exception("Error al cerrar Picamera2")
            finally:
                self._cam = None


def list_cameras() -> list[dict]:
    """Enumera dispositivos sin abrirlos (no interfiere con la captura en curso)."""
    out: list[dict] = []
    for path in sorted(glob.glob("/dev/video*")):
        out.append({"backend": "opencv", "device": path, "label": path})
    try:  # noqa: SIM105
        from picamera2 import Picamera2  # type: ignore

        for i, info in enumerate(Picamera2.global_camera_info()):
            out.append({"backend": "picamera2", "device": str(i), "label": f"Pi Camera {i} ({info.get('Model', '?')})"})
    except Exception:
        pass
    return out


def default_source_factory(cfg) -> FrameSource:
    backend = cfg.backend
    if backend == "auto":
        try:
            from picamera2 import Picamera2  # type: ignore  # noqa: F401

            if Picamera2.global_camera_info():
                backend = "picamera2"
        except Exception:
            backend = "opencv"
    if backend == "picamera2":
        idx = int(cfg.device) if cfg.device.isdigit() else 0
        return Picamera2Source(idx, cfg.width, cfg.height, cfg.capture_fps)
    return OpenCVSource(cfg.device, cfg.width, cfg.height, cfg.capture_fps)


# --------------------------------------------------------------------------- gestor


@dataclass(frozen=True)
class Frame:
    seq: int
    ts: float
    image: np.ndarray


class CameraManager:
    """Hilo de captura con recuperación. Seguro para uso desde varios hilos."""

    MAX_BAD_READS = 5

    def __init__(
        self,
        get_config: Callable[[], object],
        source_factory: Callable[[object], FrameSource] = default_source_factory,
        clock: Callable[[], float] = time.monotonic,
        backoff_max: float = 30.0,
        backoff_initial: float = 1.0,
    ) -> None:
        self._get_config = get_config
        self._factory = source_factory
        self._clock = clock
        self._backoff_max = backoff_max
        self._backoff_initial = backoff_initial
        self._lock = threading.Lock()
        self._frame: Frame | None = None
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._new_frame = threading.Condition()
        # estado observable
        self._state = "stopped"  # stopped | starting | ok | error
        self._message = "Detenida"
        self.frames_total = 0
        self.invalid_frames = 0
        self.reconnects = 0
        self._fps_window: list[float] = []
        self._last_ok_time: float | None = None

    # ------------------------------------------------------------------ ciclo de vida

    @property
    def running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._restart.clear()
            self._set_state("starting", "Iniciando cámara…")
            self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            t = self._thread
            self._stop.set()
        if t:
            t.join(timeout)
            if t.is_alive():
                log.error("El hilo de cámara no terminó en %.1fs", timeout)
        with self._lock:
            self._thread = None
            self._frame = None
            self._set_state("stopped", "Detenida")
        with self._new_frame:
            self._new_frame.notify_all()

    def restart(self) -> None:
        """Pide reabrir el dispositivo (p. ej. tras cambiar resolución)."""
        self._restart.set()

    # ------------------------------------------------------------------ lectura

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frame

    def wait_frame(self, after_seq: int = 0, timeout: float = 2.0) -> Frame | None:
        """Espera un fotograma más reciente que `after_seq`."""
        deadline = time.monotonic() + timeout
        with self._new_frame:
            while True:
                f = self.latest()
                if f is not None and f.seq > after_seq:
                    return f
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._stop.is_set():
                    return self.latest() if (self.latest() and self.latest().seq > after_seq) else None
                self._new_frame.wait(min(remaining, 0.25))

    def status(self) -> dict:
        with self._lock:
            now = self._clock()
            win = [t for t in self._fps_window if now - t <= 3.0]
            fps = (len(win) - 1) / (win[-1] - win[0]) if len(win) >= 2 and win[-1] > win[0] else 0.0
            cfg = self._get_config()
            return {
                "state": self._state,
                "message": self._message,
                "fps": round(fps, 1),
                "frames_total": self.frames_total,
                "invalid_frames": self.invalid_frames,
                "reconnects": self.reconnects,
                "resolution": f"{cfg.width}x{cfg.height}",
                "seconds_since_frame": None if self._last_ok_time is None else round(now - self._last_ok_time, 1),
            }

    # ------------------------------------------------------------------ interno

    def _set_state(self, state: str, message: str) -> None:
        self._state, self._message = state, message

    def _run(self) -> None:
        backoff = self._backoff_initial
        while not self._stop.is_set():
            source: FrameSource | None = None
            try:
                cfg = self._get_config()
                source = self._factory(cfg)
                source.open()
            except Exception as exc:
                msg = str(exc) or exc.__class__.__name__
                with self._lock:
                    self._set_state("error", f"{msg}. Reintentando en {backoff:.0f}s")
                log.warning("Cámara: %s (reintento en %.0fs)", msg, backoff)
                self._safe_close(source)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, self._backoff_max)
                continue

            backoff = self._backoff_initial
            with self._lock:
                self._set_state("ok", "Cámara activa")
            log.info("Cámara abierta")
            try:
                self._capture_loop(source, cfg)
            except Exception:
                log.exception("Error inesperado en la captura")
                with self._lock:
                    self._set_state("error", "Error inesperado en la captura; reintentando")
            finally:
                self._safe_close(source)  # libera siempre el dispositivo
                log.info("Cámara liberada")
            if self._stop.is_set():
                break
            self.reconnects += 1
            if self._restart.is_set():
                self._restart.clear()
            elif self._stop.wait(backoff):
                break

    def _capture_loop(self, source: FrameSource, cfg) -> None:
        interval = 1.0 / cfg.capture_fps
        next_decode = 0.0
        bad = 0
        while not self._stop.is_set() and not self._restart.is_set():
            now = self._clock()
            want = now >= next_decode
            ok, frame = source.read(decode=want)
            if not ok:
                bad += 1
                if bad >= self.MAX_BAD_READS:
                    with self._lock:
                        self._set_state("error", "La cámara dejó de entregar imágenes; reabriendo")
                    log.warning("Cámara sin fotogramas tras %d lecturas; se reabre", bad)
                    return
                self._stop.wait(0.05)
                continue
            if not want or frame is None:
                continue
            reason = validate_frame(frame)
            if reason:
                self.invalid_frames += 1
                bad += 1
                log.debug("Fotograma descartado: %s", reason)
                if bad >= self.MAX_BAD_READS:
                    with self._lock:
                        self._set_state("error", f"Fotogramas no válidos ({reason}); reabriendo")
                    return
                continue
            bad = 0
            next_decode = now + interval
            with self._lock:
                self._seq += 1
                self.frames_total += 1
                self._frame = Frame(self._seq, now, frame)
                self._last_ok_time = now
                self._fps_window.append(now)
                if len(self._fps_window) > 60:
                    del self._fps_window[:-60]
                self._set_state("ok", "Cámara activa")
            with self._new_frame:
                self._new_frame.notify_all()

    @staticmethod
    def _safe_close(source) -> None:
        if source is None:
            return
        try:
            source.close()
        except Exception:
            log.exception("Error al cerrar el origen de vídeo")
