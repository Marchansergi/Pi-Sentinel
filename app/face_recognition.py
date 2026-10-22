"""Reconocimiento facial local: YuNet (detección, MIT) + SFace (embedding de 128-d, Apache-2.0).

Todo se procesa en memoria y en este equipo. Solo se conservan embeddings de los perfiles
autorizados (nunca imágenes).

IMPORTANTE: la «similitud» es un coseno entre embeddings, NO una probabilidad de identidad.
El umbral por defecto (0.363) es el sugerido por OpenCV para SFace en LFW y debe calibrarse con
las condiciones reales de la habitación.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .domain import IdentityStatus

log = logging.getLogger(__name__)

DET_MAX_SIDE = 640
EMBED_DIM = 128


class FaceModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DetectedFace:
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 (px del fotograma original)
    landmarks: np.ndarray = field(repr=False)  # (5, 2): ojo der., ojo izq., nariz, boca der., boca izq.
    score: float = 0.0
    row: np.ndarray = field(default=None, repr=False)  # fila original de YuNet (para alignCrop)

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    def center(self) -> tuple[float, float]:
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)


@dataclass(frozen=True)
class FaceQuality:
    ok: bool
    reasons: tuple[str, ...]
    sharpness: float
    brightness: float
    yaw: float
    size_px: int

    def score(self) -> float:
        """Resumen 0-1 solo orientativo para la interfaz."""
        s = min(1.0, self.sharpness / 120.0) * 0.4 + min(1.0, self.size_px / 160.0) * 0.3
        s += (1.0 - min(1.0, abs(self.yaw))) * 0.3
        return round(max(0.0, min(1.0, s)), 3)


@dataclass(frozen=True)
class FaceObservation:
    box: tuple[int, int, int, int]
    status: IdentityStatus
    name: str | None
    person_id: int | None
    similarity: float | None
    reasons: tuple[str, ...]
    det_score: float
    yaw: float = 0.0


@dataclass(frozen=True)
class FaceAnalysis:
    ok: bool
    faces: tuple[FaceObservation, ...] = ()
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class GalleryEntry:
    person_id: int
    name: str
    embeddings: np.ndarray  # (n, 128) L2-normalizados


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    person_id: int | None
    name: str | None
    best_score: float | None  # mejor coseno contra cualquier perfil (aunque no supere el umbral)
    per_person: dict[int, float]


# --------------------------------------------------------------------------- lógica pura


def l2_normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def match_embedding(emb: np.ndarray, gallery: list[GalleryEntry], threshold: float) -> MatchResult:
    """Compara con cada perfil (máximo coseno entre sus muestras). Sin perfiles → no hay coincidencia."""
    emb = l2_normalize(emb)
    per: dict[int, float] = {}
    best_id, best_name, best = None, None, None
    for g in gallery:
        if g.embeddings.size == 0:
            continue
        sims = g.embeddings @ emb
        s = float(np.max(sims))
        per[g.person_id] = s
        if best is None or s > best:
            best, best_id, best_name = s, g.person_id, g.name
    if best is not None and best >= threshold:
        return MatchResult(True, best_id, best_name, best, per)
    return MatchResult(False, None, None, best, per)


def estimate_yaw(lm: np.ndarray) -> float:
    """Giro horizontal aproximado: 0 = frontal; |valor| creciente = más de perfil."""
    eye_r, eye_l, nose = lm[0], lm[1], lm[2]
    dx = float(eye_l[0] - eye_r[0])
    if abs(dx) < 1e-6:
        return 9.9
    return float((nose[0] - (eye_r[0] + eye_l[0]) / 2) / dx)


def assess_quality(frame: np.ndarray, face: DetectedFace, cfg) -> FaceQuality:
    """Comprueba si el rostro es apto para reconocer. `cfg` es FaceSettings."""
    import cv2

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = face.box
    reasons: list[str] = []
    size = min(x2 - x1, y2 - y1)
    if face.score < cfg.min_detection_score:
        reasons.append("detección poco fiable (¿cara parcialmente tapada o muy girada?)")
    if size < cfg.min_face_px:
        reasons.append("cara demasiado pequeña o lejana")
    margin = 2
    if x1 <= margin or y1 <= margin or x2 >= w - margin or y2 >= h - margin:
        reasons.append("cara cortada por el borde de la imagen")

    cx1, cy1, cx2, cy2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    sharp, bright = 0.0, 0.0
    if cx2 - cx1 >= 8 and cy2 - cy1 >= 8:
        crop = cv2.cvtColor(frame[cy1:cy2, cx1:cx2], cv2.COLOR_BGR2GRAY)
        crop = cv2.resize(crop, (96, 96), interpolation=cv2.INTER_AREA)
        sharp = float(cv2.Laplacian(crop, cv2.CV_64F).var())
        bright = float(crop.mean())
    if sharp < cfg.min_sharpness:
        reasons.append("imagen borrosa o con poco detalle")
    if bright < 35:
        reasons.append("iluminación insuficiente")
    elif bright > 225:
        reasons.append("iluminación excesiva")
    yaw = estimate_yaw(face.landmarks)
    if abs(yaw) > cfg.max_yaw:
        reasons.append("cara demasiado girada")
    lm = face.landmarks
    if not (lm[2][1] > (lm[0][1] + lm[1][1]) / 2 and lm[3][1] > lm[2][1]):
        reasons.append("geometría facial incoherente")
    return FaceQuality(not reasons, tuple(reasons), sharp, bright, yaw, int(size))


# --------------------------------------------------------------------------- motor


class FaceEngine:
    def __init__(self, detector_model: Path | str, recognizer_model: Path | str) -> None:
        self.detector_model = Path(detector_model)
        self.recognizer_model = Path(recognizer_model)
        self._det = None
        self._rec = None
        self._lock = threading.Lock()
        self._det_size: tuple[int, int] | None = None
        self.loaded = False
        self.last_error: str | None = None
        self.last_elapsed_ms = 0.0

    def load(self) -> None:
        import cv2

        for p in (self.detector_model, self.recognizer_model):
            if not p.is_file():
                raise FaceModelUnavailable(f"Falta el modelo {p.name}. Ejecuta scripts/download_models.py")
        if not (hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF")):
            raise FaceModelUnavailable("Esta versión de OpenCV no incluye FaceDetectorYN/FaceRecognizerSF (se necesita ≥ 4.8)")
        try:
            self._det = cv2.FaceDetectorYN.create(str(self.detector_model), "", (320, 320), 0.6, 0.3, 20)
            self._rec = cv2.FaceRecognizerSF.create(str(self.recognizer_model), "")
        except Exception as exc:
            raise FaceModelUnavailable(f"No se pudieron cargar los modelos faciales: {exc}") from exc
        self.loaded = True

    # -- primitivas (llamar con el candado tomado) ---------------------------------

    def _detect_locked(self, frame: np.ndarray) -> list[DetectedFace]:
        import cv2

        h, w = frame.shape[:2]
        scale = min(1.0, DET_MAX_SIDE / max(h, w))
        img = frame if scale == 1.0 else cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        size = (img.shape[1], img.shape[0])
        if self._det_size != size:
            self._det.setInputSize(size)
            self._det_size = size
        _, rows = self._det.detect(img)
        out: list[DetectedFace] = []
        if rows is None:
            return out
        for r in rows:
            r = np.array(r, dtype=np.float32)
            full = r.copy()
            full[:14] = r[:14] / scale
            x, y, bw, bh = full[:4]
            lm = full[4:14].reshape(5, 2)
            out.append(DetectedFace((int(x), int(y), int(x + bw), int(y + bh)), lm, float(full[14]), full))
        out.sort(key=lambda f: -(f.box[2] - f.box[0]) * (f.box[3] - f.box[1]))
        return out

    def _embed_locked(self, frame: np.ndarray, face: DetectedFace) -> np.ndarray:
        aligned = self._rec.alignCrop(frame, face.row)
        return l2_normalize(self._rec.feature(aligned))

    # -- API pública -------------------------------------------------------------

    def detect(self, frame: np.ndarray) -> list[DetectedFace]:
        with self._lock:
            if not self.loaded:
                self.load()
            return self._detect_locked(frame)

    def embed(self, frame: np.ndarray, face: DetectedFace) -> np.ndarray:
        with self._lock:
            if not self.loaded:
                self.load()
            return self._embed_locked(frame, face)

    def analyze(
        self,
        frame: np.ndarray,
        gallery: list[GalleryEntry],
        cfg,
        max_embeds: int = 3,
    ) -> FaceAnalysis:
        """Detecta rostros, evalúa su calidad y los clasifica. Nunca lanza excepciones."""
        t0 = time.perf_counter()
        try:
            with self._lock:
                if not self.loaded:
                    self.load()
                faces = self._detect_locked(frame)
                out: list[FaceObservation] = []
                embedded = 0
                for f in faces:
                    q = assess_quality(frame, f, cfg)
                    if not q.ok or embedded >= max_embeds:
                        reasons = q.reasons or ("demasiados rostros simultáneos para evaluarlos todos",)
                        out.append(
                            FaceObservation(f.box, IdentityStatus.UNVERIFIED, None, None, None, reasons, f.score, q.yaw)
                        )
                        continue
                    emb = self._embed_locked(frame, f)
                    embedded += 1
                    m = match_embedding(emb, gallery, cfg.similarity_threshold)
                    status = IdentityStatus.AUTHORIZED if m.matched else IdentityStatus.UNKNOWN
                    out.append(FaceObservation(f.box, status, m.name, m.person_id, m.best_score, (), f.score, q.yaw))
            ms = (time.perf_counter() - t0) * 1000
            self.last_elapsed_ms, self.last_error = ms, None
            return FaceAnalysis(True, tuple(out), None, ms)
        except Exception as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            log.error("Fallo del reconocimiento facial: %s", self.last_error)
            return FaceAnalysis(False, (), self.last_error, (time.perf_counter() - t0) * 1000)

    def extract_sample(self, frame: np.ndarray, cfg) -> tuple[np.ndarray | None, FaceQuality | None, str | None]:
        """Para el registro: exige exactamente un rostro apto. Devuelve (embedding, calidad, motivo_rechazo)."""
        try:
            with self._lock:
                if not self.loaded:
                    self.load()
                faces = self._detect_locked(frame)
                if not faces:
                    return None, None, "No se detecta ninguna cara. Colócate frente a la cámara con buena luz."
                if len(faces) > 1:
                    return None, None, "Se detecta más de una cara. Asegúrate de estar solo en la imagen."
                q = assess_quality(frame, faces[0], cfg)
                if not q.ok:
                    return None, q, "Muestra rechazada: " + "; ".join(q.reasons)
                return self._embed_locked(frame, faces[0]), q, None
        except Exception as exc:
            log.error("Fallo al extraer muestra facial: %s", exc)
            return None, None, f"Error del motor facial: {exc}"
