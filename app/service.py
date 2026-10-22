"""Orquestador de Pi Sentinel: une cámara, visión, seguimiento, eventos, alarma y ntfy.

Modos:
- STOPPED: la cámara solo se enciende si hay una vista previa/registro en curso.
- TEST:    analiza y muestra lo que HARÍA (sin alarma, sin ntfy, sin historial). Para comprobar la imagen.
- ACTIVE:  vigilancia real.

Cada ciclo de análisis está envuelto en try/except: un fallo en un módulo se refleja en su estado,
pero no detiene el resto.
"""
from __future__ import annotations

import collections
import dataclasses
import logging
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .authorized_people import AuthorizedPeople, ProfileError
from .camera import CameraManager, default_source_factory, list_cameras
from .config import ConfigStore, Paths, Settings
from .database import Database
from .domain import IdentityStatus, Observation
from .event_manager import EventManager
from .face_recognition import FaceEngine, FaceModelUnavailable, assess_quality, match_embedding
from .notification_queue import NotificationQueue
from .ntfy_client import NtfyClient
from .person_detector import ModelUnavailable, MotionGate, PersonDetector
from .presence_tracker import PresenceTracker, TrackerConfig, TrackerEvent
from .security import SecretStore
from .sound_alert import SoundAlert

log = logging.getLogger(__name__)

PERSON_MODEL = "object_detection_yolox_2022nov.onnx"
FACE_DET_MODEL = "face_detection_yunet_2023mar.onnx"
FACE_REC_MODEL = "face_recognition_sface_2021dec.onnx"

PREVIEW_LEASE_SECONDS = 15.0


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class Mode:
    STOPPED = "stopped"
    TEST = "test"
    ACTIVE = "active"
    ALL = (STOPPED, TEST, ACTIVE)


@dataclass(frozen=True)
class AnalysisResult:
    ts: float
    frame_size: tuple[int, int]
    persons: tuple = ()
    faces: tuple = ()
    person_ms: float = 0.0
    face_ms: float = 0.0
    skipped_by_motion_gate: bool = False


def build_observations(persons, faces) -> list[Observation]:
    """Asocia cada rostro a la persona que lo contiene; los rostros sueltos cuentan como presencias."""
    used: set[int] = set()
    out: list[Observation] = []
    for p in persons:
        best_i, best_area = None, -1
        for i, f in enumerate(faces):
            if i in used:
                continue
            cx, cy = (f.box[0] + f.box[2]) / 2, (f.box[1] + f.box[3]) / 2
            if p.x1 <= cx <= p.x2 and p.y1 <= cy <= p.y2:
                area = (f.box[2] - f.box[0]) * (f.box[3] - f.box[1])
                if area > best_area:
                    best_i, best_area = i, area
        if best_i is None:
            out.append(Observation((p.x1, p.y1, p.x2, p.y2), p.confidence))
        else:
            used.add(best_i)
            f = faces[best_i]
            out.append(Observation((p.x1, p.y1, p.x2, p.y2), p.confidence, f.status, f.name, f.similarity, f.reasons))
    for i, f in enumerate(faces):
        if i not in used:
            out.append(Observation(tuple(f.box), f.det_score, f.status, f.name, f.similarity, f.reasons))
    return out


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


class SentinelService:
    def __init__(
        self,
        paths: Paths,
        *,
        db: Database | None = None,
        source_factory: Callable = default_source_factory,
        detector: PersonDetector | None = None,
        faces: FaceEngine | None = None,
        audio_backend=None,
        ntfy_client: NtfyClient | None = None,
    ) -> None:
        self.paths = paths
        self.db = db or Database(paths.db_path)
        self.config = ConfigStore(self.db)
        self.secrets = SecretStore(paths.secrets_path)
        self.people = AuthorizedPeople(self.db, lambda: self.config.get().face.min_samples)
        self.camera = CameraManager(lambda: self.config.get().camera, source_factory)
        self.detector = detector or PersonDetector(paths.models_dir / PERSON_MODEL)
        self.faces = faces or FaceEngine(paths.models_dir / FACE_DET_MODEL, paths.models_dir / FACE_REC_MODEL)
        self.tracker = PresenceTracker(TrackerConfig.from_settings(self.config.get().tracking))
        self.alarm = SoundAlert(self.config.get, paths.builtin_sounds_dir, paths.user_sounds_dir, paths.tmp_dir, audio_backend)
        self.notifier = NotificationQueue(self.db, self.config.get, self.secrets, ntfy_client)
        self.chime = SoundAlert(self.config.get, paths.builtin_sounds_dir, paths.user_sounds_dir, paths.tmp_dir, audio_backend, section="entry")
        self.events = EventManager(self.db, self.config.get, self.alarm, self.notifier, chime=self.chime)
        self.gate = MotionGate()

        self._mode = Mode.STOPPED
        self._mode_note = ""
        self._mode_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._preview_until = 0.0
        self._heartbeat = 0.0
        self._last: AnalysisResult | None = None
        self._test_events: collections.deque = collections.deque(maxlen=20)
        self._detector_state = ("idle", "Sin analizar todavía")
        self._face_state = ("idle", "Sin analizar todavía")
        self._analysis_error = ""
        self._cycles = 0
        self._last_maintenance = 0.0
        self.config.on_change(self._on_config_change)

    # ------------------------------------------------------------------ ciclo de vida

    def start(self) -> None:
        self.alarm.ensure_default()
        self.notifier.start()
        self._stop.clear()
        saved = self.db.kv_get("surveillance_mode") or Mode.STOPPED
        self._thread = threading.Thread(target=self._loop, name="analysis", daemon=True)
        self._thread.start()
        if saved == Mode.ACTIVE:
            try:
                self.set_mode(Mode.ACTIVE)
            except ServiceError as exc:
                log.warning("No se pudo reanudar la vigilancia: %s", exc)
                self._mode_note = str(exc)
        elif saved == Mode.TEST:
            self.set_mode(Mode.STOPPED)

    def shutdown(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        log.info("Cerrando Pi Sentinel…")
        self._stop.set()
        if self._thread:
            self._thread.join(8)
        self.camera.stop()
        self.alarm.shutdown()
        self.chime.shutdown()
        self.notifier.stop()
        self.db.close()

    # ------------------------------------------------------------------ modos

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> str:
        if mode not in Mode.ALL:
            raise ServiceError("Modo no válido")
        with self._mode_lock:
            if mode == Mode.ACTIVE:
                if self.people.active_profile_count() < 1:
                    raise ServiceError(
                        "No se puede activar la vigilancia sin al menos un perfil autorizado activo con muestras suficientes "
                        "(si no, cualquier persona —incluido tú— se consideraría desconocida). Registra tu rostro primero.",
                        409,
                    )
                self._check_models()
            elif mode == Mode.TEST:
                self._check_models()
            self.tracker.reset()
            self.gate = MotionGate()
            self._mode = mode
            self._mode_note = ""
            self._test_events.clear()
            self.db.kv_set("surveillance_mode", mode if mode != Mode.TEST else Mode.STOPPED)
        log.info("Modo de vigilancia: %s", mode)
        return mode

    def _check_models(self) -> None:
        missing = [n for n in (PERSON_MODEL, FACE_DET_MODEL, FACE_REC_MODEL) if not (self.paths.models_dir / n).is_file()]
        if missing:
            raise ServiceError("Faltan modelos (" + ", ".join(missing) + "). Ejecuta scripts/download_models.py", 503)

    def touch_preview(self) -> None:
        self._preview_until = time.monotonic() + PREVIEW_LEASE_SECONDS

    def _on_config_change(self, old: Settings, new: Settings) -> None:
        self.tracker.set_config(TrackerConfig.from_settings(new.tracking))
        if old.camera != new.camera and self.camera.running:
            self.camera.restart()

    # ------------------------------------------------------------------ bucle de análisis

    def _tick_camera(self) -> None:
        want = self._mode != Mode.STOPPED or time.monotonic() < self._preview_until
        if want and not self.camera.running:
            self.camera.start()
        elif not want and self.camera.running:
            self.camera.stop()

    def _loop(self) -> None:
        last_seq, next_run = 0, 0.0
        while not self._stop.is_set():
            try:
                self._heartbeat = time.monotonic()
                self._tick_camera()
                self._maintenance()
                if self._mode == Mode.STOPPED:
                    self._stop.wait(0.25)
                    continue
                delay = next_run - time.monotonic()
                if delay > 0:
                    self._stop.wait(min(delay, 0.25))
                    continue
                f = self.camera.wait_frame(last_seq, 1.0)
                if f is None:
                    continue  # sin imagen: no se actualiza el seguimiento (no es «ausencia»)
                last_seq = f.seq
                cfg = self.config.get()
                next_run = time.monotonic() + 1.0 / cfg.detection.analysis_fps
                self._process(f.image, cfg)
                self._analysis_error = ""
            except Exception as exc:
                self._analysis_error = f"{exc.__class__.__name__}: {exc}"
                log.exception("Error en el ciclo de análisis (se continúa)")
                self._stop.wait(1.0)

    def _process(self, frame: np.ndarray, cfg: Settings) -> None:
        mono = time.monotonic()
        force = bool(self.tracker.snapshot())
        if cfg.detection.motion_gate and not self.gate.should_run(frame, mono, cfg.detection.max_idle_seconds, force):
            return
        h, w = frame.shape[:2]
        det = self.detector.detect(frame, cfg.detection.person_confidence, cfg.detection.nms_threshold,
                                   cfg.detection.input_size, cfg.detection.min_area_ratio)
        if not det.ok:
            self._detector_state = ("error", det.error or "Error del detector")
            return  # un error del modelo NO equivale a «no hay nadie»
        self._detector_state = ("ok", f"{det.elapsed_ms:.0f} ms por análisis")

        gallery = self.people.gallery()
        fa = self.faces.analyze(frame, gallery, cfg.face)
        if fa.ok:
            self._face_state = ("ok", f"{fa.elapsed_ms:.0f} ms por análisis")
            faces = fa.faces
        else:
            self._face_state = ("error", fa.error or "Error del reconocimiento facial")
            faces = ()

        obs = build_observations(det.persons, faces)
        tracker_events = self.tracker.update(mono, obs)
        self._last = AnalysisResult(time.time(), (w, h), det.persons, tuple(faces), det.elapsed_ms, fa.elapsed_ms)
        self._cycles += 1

        if self._mode == Mode.ACTIVE and not gallery:
            log.warning("Sin perfiles activos: se pasa a modo prueba para evitar falsas alarmas")
            self._mode_note = "Vigilancia pasada a modo prueba: no quedan perfiles autorizados activos."
            self._mode = Mode.TEST
            self.db.kv_set("surveillance_mode", Mode.STOPPED)
        if tracker_events:
            wall_events = self._to_wall(tracker_events, mono)
            results = self.events.handle(wall_events, dry_run=(self._mode != Mode.ACTIVE))
            if self._mode != Mode.ACTIVE:
                self._test_events.extend(results)

    @staticmethod
    def _to_wall(events: list[TrackerEvent], mono_now: float) -> list[TrackerEvent]:
        off = time.time() - mono_now
        return [dataclasses.replace(e, at=e.at + off, started_at=e.started_at + off) for e in events]

    def _maintenance(self) -> None:
        now = time.monotonic()
        if now - self._last_maintenance < 3600:
            return
        self._last_maintenance = now
        try:
            days = self.config.get().storage.history_retention_days
            n = self.events.purge(days) + self.notifier.purge(days)
            if n:
                log.info("Retención: %d registros antiguos eliminados", n)
        except Exception:
            log.exception("Fallo en el mantenimiento de la base de datos")

    # ------------------------------------------------------------------ estado

    def component_status(self) -> dict:
        cam = self.camera.status()
        want_cam = self._mode != Mode.STOPPED or time.monotonic() < self._preview_until
        cam_state = {"ok": "ok", "starting": "warn", "error": "error", "stopped": "disabled"}[cam["state"]]
        cam_msg = cam["message"] if want_cam or cam["state"] != "stopped" else "Apagada (se enciende al vigilar o al abrir la vista previa)"
        det_state = self._detector_state
        if not (self.paths.models_dir / PERSON_MODEL).is_file():
            det_state = ("error", f"Falta el modelo {PERSON_MODEL}")
        face_state = self._face_state
        if not (self.paths.models_dir / FACE_REC_MODEL).is_file() or not (self.paths.models_dir / FACE_DET_MODEL).is_file():
            face_state = ("error", "Faltan los modelos faciales")
        ntfy = self.notifier.status()
        ntfy_map = {"ok": "ok", "idle": "ok", "disabled": "disabled", "unconfigured": "warn", "error": "error"}
        alarm = self.alarm.status()
        hb_age = time.monotonic() - self._heartbeat if self._heartbeat else None
        analysis_state = "ok"
        analysis_msg = f"{self._cycles} análisis realizados"
        if self._analysis_error:
            analysis_state, analysis_msg = "error", self._analysis_error
        elif hb_age is not None and hb_age > 10:
            analysis_state, analysis_msg = "error", f"El hilo de análisis no responde desde hace {hb_age:.0f}s"
        return {
            "camera": {"state": cam_state, "message": cam_msg, **{k: cam[k] for k in ("fps", "resolution", "frames_total", "invalid_frames", "reconnects")}},
            "detector": {"state": {"idle": "disabled"}.get(det_state[0], det_state[0]), "message": det_state[1]},
            "face": {"state": {"idle": "disabled"}.get(face_state[0], face_state[0]), "message": face_state[1]},
            "ntfy": {"state": ntfy_map[ntfy["state"]], "message": ntfy["message"], "pending": ntfy["pending"]},
            "alarm": {
                "state": "ok" if alarm["audio_available"] else "error",
                "message": ("Silenciada" if alarm["muted"] else "Lista") if alarm["audio_available"] else alarm["last_error"],
            },
            "analysis": {"state": analysis_state, "message": analysis_msg},
        }

    def status(self) -> dict:
        cfg = self.config.get()
        gallery = self.people.gallery()
        return {
            "mode": self._mode,
            "mode_note": self._mode_note,
            "components": self.component_status(),
            "ntfy": self.notifier.status(),
            "alarm": self.alarm.status(),
            "chime": self.chime.status(),
            "last_event": self.events.last(),
            "recent_events_24h": self.events.recent_count(24),
            "people": {"total": len(self.people.list_people()), "active_ready": len(gallery)},
            "test_events": list(self._test_events),
            "live": self.live_summary(),
            "device_name": cfg.device_label(),
        }

    def live_summary(self) -> dict:
        r = self._last
        if r is None:
            return {"persons": 0, "faces": 0, "age": None}
        return {"persons": len(r.persons), "faces": len(r.faces), "age": round(time.time() - r.ts, 1),
                "person_ms": round(r.person_ms), "face_ms": round(r.face_ms), "tracks": self.tracker.snapshot()}

    # ------------------------------------------------------------------ cámara / vista previa

    def fresh_frame(self, timeout: float = 4.0):
        self.touch_preview()
        if not self.camera.running:
            self.camera.start()
        prev = self.camera.latest()
        f = self.camera.wait_frame(prev.seq if prev else 0, timeout)
        if f is None:
            st = self.camera.status()
            raise ServiceError(f"La cámara no entrega imágenes: {st['message']}", 503)
        return f

    def preview_jpeg(self, overlay: bool = True, max_width: int = 640, quality: int = 70) -> bytes:
        import cv2

        self.touch_preview()
        f = self.camera.latest()
        if f is None:
            f = self.fresh_frame()
        img = f.image.copy()
        if overlay and self._last and time.time() - self._last.ts < 5:
            self._draw(img, self._last)
        if img.shape[1] > max_width:
            s = max_width / img.shape[1]
            img = cv2.resize(img, (max_width, int(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise ServiceError("No se pudo codificar la imagen", 500)
        return buf.tobytes()

    @staticmethod
    def _draw(img: np.ndarray, r: AnalysisResult) -> None:
        import cv2

        colors = {IdentityStatus.AUTHORIZED: (80, 200, 80), IdentityStatus.UNKNOWN: (60, 60, 230), IdentityStatus.UNVERIFIED: (0, 190, 240)}
        label = {IdentityStatus.AUTHORIZED: "Autorizada", IdentityStatus.UNKNOWN: "Desconocida", IdentityStatus.UNVERIFIED: "No verificable"}
        for p in r.persons:
            cv2.rectangle(img, (p.x1, p.y1), (p.x2, p.y2), (200, 200, 200), 1)
            cv2.putText(img, f"persona {p.confidence:.2f}", (p.x1 + 3, max(12, p.y1 + 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1, cv2.LINE_AA)
        for f in r.faces:
            c = colors[f.status]
            x1, y1, x2, y2 = f.box
            cv2.rectangle(img, (x1, y1), (x2, y2), c, 2)
            txt = label[f.status] + (f" {_ascii(f.name)}" if f.name else "") + (f" {f.similarity:.2f}" if f.similarity is not None else "")
            cv2.putText(img, txt, (x1, max(12, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)

    def camera_check(self) -> dict:
        """Herramienta de comprobación: una instantánea analizada, sin guardar nada."""
        import cv2

        f = self.fresh_frame()
        cfg = self.config.get()
        gray = cv2.cvtColor(f.image, cv2.COLOR_BGR2GRAY)
        res: dict = {
            "ok": True,
            "resolution": f"{f.image.shape[1]}x{f.image.shape[0]}",
            "brightness": round(float(gray.mean()), 1),
            "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        }
        hints = []
        if res["brightness"] < 40:
            hints.append("La imagen está muy oscura: añade luz o cambia la orientación.")
        if res["brightness"] > 215:
            hints.append("La imagen está sobreexpuesta (¿contraluz?).")
        if res["sharpness"] < 30:
            hints.append("La imagen parece borrosa: limpia o enfoca la lente.")
        det = self.detector.detect(f.image, cfg.detection.person_confidence, cfg.detection.nms_threshold, cfg.detection.input_size, cfg.detection.min_area_ratio)
        res["person_detector"] = {"ok": det.ok, "persons": len(det.persons), "ms": round(det.elapsed_ms), "error": det.error}
        fa = self.faces.analyze(f.image, self.people.gallery(), cfg.face)
        res["faces"] = {"ok": fa.ok, "count": len(fa.faces), "ms": round(fa.elapsed_ms), "error": fa.error}
        res["hints"] = hints
        return res

    # ------------------------------------------------------------------ registro y pruebas

    def capture_sample(self, person_id: int, pose: str = "any") -> dict:
        cfg = self.config.get()
        self.people.get(person_id)
        f = self.fresh_frame()
        emb, q, why = self.faces.extract_sample(f.image, cfg.face)
        if emb is None:
            return {"ok": False, "reason": why, "reasons": list(q.reasons) if q else []}
        if pose == "frontal" and abs(q.yaw) > 0.22:
            return {"ok": False, "reason": "Mira de frente a la cámara."}
        if pose == "lateral" and abs(q.yaw) < 0.25:
            return {"ok": False, "reason": "Gira un poco la cabeza hacia un lado (sin llegar a perfil)."}
        existing = self.people.embeddings_for(person_id)
        if len(existing) >= 2:
            best = float(np.max(existing @ emb))
            if best < 0.2:
                return {"ok": False, "reason": "Esta cara no se parece a las muestras ya guardadas en este perfil. Si es otra persona, crea un perfil nuevo."}
        try:
            s = self.people.add_sample(person_id, emb, q.score(), pose)
        except ProfileError as exc:
            return {"ok": False, "reason": str(exc)}
        n = len(self.people.samples(person_id))
        return {"ok": True, "sample_id": s.id, "quality": s.quality, "samples": n, "ready": n >= cfg.face.min_samples}

    def diagnose_recognition(self) -> dict:
        """Prueba en directo: puntuación de cada rostro frente a cada perfil activo, sin guardar nada."""
        cfg = self.config.get()
        f = self.fresh_frame()
        gallery = self.people.gallery()
        faces = self.faces.detect(f.image)
        out = []
        for face in faces[:3]:
            q = assess_quality(f.image, face, cfg.face)
            item: dict = {"quality_ok": q.ok, "reasons": list(q.reasons), "box": face.box, "det_score": round(face.score, 2)}
            if q.ok and gallery:
                emb = self.faces.embed(f.image, face)
                m = match_embedding(emb, gallery, cfg.face.similarity_threshold)
                names = {g.person_id: g.name for g in gallery}
                item["scores"] = {names[k]: round(v, 3) for k, v in m.per_person.items()}
                item["status"] = IdentityStatus.AUTHORIZED.value if m.matched else IdentityStatus.UNKNOWN.value
                item["name"] = m.name
            else:
                item["status"] = IdentityStatus.UNVERIFIED.value
            out.append(item)
        return {"ok": True, "threshold": cfg.face.similarity_threshold, "faces": out, "profiles_active": len(gallery)}

    def calibrate(self, kind: str, frames: int = 12, timeout: float = 20.0) -> dict:
        """Mide similitudes en directo. kind='genuine' (tú) o 'impostor' (alguien NO registrado)."""
        if kind not in ("genuine", "impostor"):
            raise ServiceError("Tipo de calibración no válido")
        cfg = self.config.get()
        gallery = self.people.gallery()
        if not gallery:
            raise ServiceError("No hay perfiles activos con muestras suficientes", 409)
        scores: list[float] = []
        seq, deadline = 0, time.monotonic() + timeout
        self.touch_preview()
        while len(scores) < frames and time.monotonic() < deadline:
            f = self.camera.wait_frame(seq, 1.5) if self.camera.running else None
            if f is None:
                self.fresh_frame()
                continue
            seq = f.seq
            faces = self.faces.detect(f.image)
            if len(faces) != 1:
                continue
            q = assess_quality(f.image, faces[0], cfg.face)
            if not q.ok:
                continue
            m = match_embedding(self.faces.embed(f.image, faces[0]), gallery, cfg.face.similarity_threshold)
            if m.best_score is not None:
                scores.append(m.best_score)
        if len(scores) < 3:
            raise ServiceError("No se obtuvieron suficientes imágenes válidas. Colócate frente a la cámara con buena luz.", 422)
        arr = np.array(scores)
        stats = {"n": len(scores), "min": round(float(arr.min()), 3), "p10": round(float(np.percentile(arr, 10)), 3),
                 "mean": round(float(arr.mean()), 3), "max": round(float(arr.max()), 3), "at": time.time()}
        import json as _json

        self.db.kv_set(f"calibration_{kind}", _json.dumps(stats))
        return {"kind": kind, **stats, **self.calibration_summary()}

    def calibration_summary(self) -> dict:
        import json as _json

        g = self.db.kv_get("calibration_genuine")
        i = self.db.kv_get("calibration_impostor")
        genuine = _json.loads(g) if g else None
        impostor = _json.loads(i) if i else None
        cur = self.config.get().face.similarity_threshold
        out: dict = {"genuine": genuine, "impostor": impostor, "current_threshold": cur, "suggested_threshold": None, "advice": ""}
        if genuine and impostor:
            lo, hi = impostor["max"], genuine["p10"]
            if lo < hi:
                out["suggested_threshold"] = round((lo + hi) / 2, 2)
                out["advice"] = ("Hay separación entre tus puntuaciones y las del no registrado. Se sugiere el punto medio; "
                                 "no garantiza cero errores con otras personas, luces o ángulos.")
            else:
                out["advice"] = ("Las puntuaciones se solapan: ningún umbral separa limpiamente ambos casos en estas condiciones. "
                                 "Añade más muestras de calidad, mejora la luz o coloca la cámara de frente.")
        elif genuine:
            out["advice"] = "Mide también a una persona NO registrada (si dispones de ella) para estimar un umbral."
        return out

    def camera_list(self) -> list[dict]:
        return list_cameras()
