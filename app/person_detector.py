"""Detector de personas local: YOLOX (Apache-2.0, opencv_zoo) ejecutado con OpenCV DNN.

- Distingue «sin detecciones» (`ok=True`, lista vacía) de «fallo del modelo» (`ok=False`).
- No guarda imágenes; los errores se registran sin adjuntar contenido de imagen.
- Los umbrales se pasan en cada llamada, de modo que cambiar la configuración no exige reiniciar.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

PERSON_CLASS = 0  # COCO


class ModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonBox:
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)

    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


@dataclass(frozen=True)
class DetectionResult:
    ok: bool
    persons: tuple[PersonBox, ...] = ()
    error: str | None = None
    elapsed_ms: float = 0.0


def _grid(size: int, strides=(8, 16, 32)) -> tuple[np.ndarray, np.ndarray]:
    grids, exp = [], []
    for s in strides:
        n = size // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        g = np.stack((xv, yv), 2).reshape(-1, 2)
        grids.append(g)
        exp.append(np.full((g.shape[0], 1), s))
    return np.concatenate(grids, 0).astype(np.float32), np.concatenate(exp, 0).astype(np.float32)


def decode_yolox(raw: np.ndarray, size: int) -> np.ndarray:
    """Decodifica la salida cruda (N, 85) a [cx, cy, w, h, obj, clases…] en píxeles del lienzo."""
    out = np.array(raw, dtype=np.float32, copy=True)
    grid, strides = _grid(size)
    if out.shape[0] != grid.shape[0]:
        raise ValueError(f"salida inesperada del modelo: {out.shape[0]} anclas, se esperaban {grid.shape[0]}")
    out[:, :2] = (out[:, :2] + grid) * strides
    out[:, 2:4] = np.exp(out[:, 2:4]) * strides
    return out


class PersonDetector:
    def __init__(self, model_path: Path | str) -> None:
        self.model_path = Path(model_path)
        self._net = None
        self._lock = threading.Lock()  # las redes de cv2.dnn no son seguras entre hilos
        self.last_error: str | None = None
        self.last_elapsed_ms: float = 0.0
        self.loaded = False

    def load(self) -> None:
        import cv2

        if not self.model_path.is_file():
            raise ModelUnavailable(
                f"Falta el modelo de detección de personas ({self.model_path.name}). Ejecuta scripts/download_models.py"
            )
        try:
            self._net = cv2.dnn.readNetFromONNX(str(self.model_path))
            self.loaded = True
        except Exception as exc:
            raise ModelUnavailable(f"No se pudo cargar {self.model_path.name}: {exc}") from exc

    def detect(
        self,
        frame: np.ndarray,
        confidence: float = 0.5,
        nms: float = 0.45,
        input_size: int = 416,
        min_area_ratio: float = 0.0,
    ) -> DetectionResult:
        t0 = time.perf_counter()
        try:
            if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
                raise ValueError("fotograma no válido")
            with self._lock:
                if self._net is None:
                    self.load()
                persons = self._infer(frame, confidence, nms, input_size, min_area_ratio)
            ms = (time.perf_counter() - t0) * 1000
            self.last_elapsed_ms, self.last_error = ms, None
            return DetectionResult(True, tuple(persons), None, ms)
        except Exception as exc:  # el llamador distingue error de «no hay nadie»
            self.last_error = str(exc) or exc.__class__.__name__
            log.error("Fallo del detector de personas: %s", self.last_error)
            return DetectionResult(False, (), self.last_error, (time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------

    def _infer(self, frame, confidence, nms, size, min_area_ratio) -> list[PersonBox]:
        import cv2

        h, w = frame.shape[:2]
        r = min(size / h, size / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[:nh, :nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)  # BGR, 0-255, sin normalizar
        self._net.setInput(blob)
        raw = self._net.forward()
        raw = raw[0] if raw.ndim == 3 else raw
        pred = decode_yolox(raw, size)

        scores = pred[:, 4] * pred[:, 5 + PERSON_CLASS]
        keep = scores >= confidence
        if not np.any(keep):
            return []
        pred, scores = pred[keep], scores[keep]
        cx, cy, bw, bh = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        x1 = np.clip((cx - bw / 2) / r, 0, w - 1)
        y1 = np.clip((cy - bh / 2) / r, 0, h - 1)
        x2 = np.clip((cx + bw / 2) / r, 0, w - 1)
        y2 = np.clip((cy + bh / 2) / r, 0, h - 1)
        rects = [[float(a), float(b), float(c - a), float(d - b)] for a, b, c, d in zip(x1, y1, x2, y2)]
        idx = cv2.dnn.NMSBoxes(rects, [float(s) for s in scores], confidence, nms)
        out: list[PersonBox] = []
        min_area = min_area_ratio * w * h
        for i in np.array(idx).reshape(-1):
            box = PersonBox(int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i]), float(scores[i]))
            if box.area >= max(min_area, 1):
                out.append(box)
        out.sort(key=lambda b: -b.confidence)
        return out


@dataclass
class MotionGate:
    """Decide si merece la pena ejecutar el detector (evita inferencias con escena estática)."""

    threshold: float = 4.0  # diferencia media de gris (0-255) para considerar movimiento
    _prev: np.ndarray | None = field(default=None, repr=False)
    _last_run: float = -1e9

    def should_run(self, frame: np.ndarray, now: float, max_idle: float, force: bool = False) -> bool:
        import cv2

        small = cv2.resize(frame, (64, 48), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.int16)
        moved = False
        if self._prev is not None and self._prev.shape == gray.shape:
            moved = float(np.mean(np.abs(gray - self._prev))) >= self.threshold
        self._prev = gray
        run = force or moved or (now - self._last_run) >= max_idle
        if run:
            self._last_run = now
        return run
