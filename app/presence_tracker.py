"""Seguimiento temporal de presencias.

Une las observaciones de ciclos consecutivos en «presencias» (tracks), acumula evidencia facial y
emite eventos cuando el estado se confirma. No toca cámara, red ni disco: es lógica pura y se
prueba con un reloj inyectado.

Reglas principales
- Una presencia sigue viva mientras no pasen `exit_seconds` sin verla. Que la cara desaparezca un
  instante NO la termina.
- AUTHORIZED: ≥ `min_authorized_observations` reconocimientos y más aciertos que desconocidos
  en la ventana reciente.
- UNKNOWN: ≥ `min_observations` rostros válidos no coincidentes, más que los autorizados, y al
  menos `confirm_seconds` de presencia.
- UNVERIFIED: persona vista ≥ `unverified_timeout_seconds` sin evidencia facial suficiente.
- Una detección fugaz que no llega a confirmarse se descarta en silencio: nunca es «desconocido».
- Un evento por presencia y estado. Si el estado cambia (p. ej. no verificable → desconocido) se
  cierra el evento anterior y se abre uno nuevo.
"""
from __future__ import annotations

import enum
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Iterable

from .domain import IdentityStatus, Observation

WINDOW = 10  # veredictos faciales recientes que se tienen en cuenta


@dataclass
class TrackerConfig:
    confirm_seconds: float = 3.0
    min_observations: int = 3
    min_authorized_observations: int = 2
    exit_seconds: float = 10.0
    unverified_timeout_seconds: float = 12.0
    min_affinity: float = 0.05

    @classmethod
    def from_settings(cls, t) -> "TrackerConfig":
        return cls(
            confirm_seconds=t.confirm_seconds,
            min_observations=t.min_observations,
            min_authorized_observations=t.min_authorized_observations,
            exit_seconds=t.exit_seconds,
            unverified_timeout_seconds=t.unverified_timeout_seconds,
        )


class EventType(str, enum.Enum):
    CONFIRMED = "CONFIRMED"  # nueva presencia / nuevo estado confirmado
    RECLASSIFIED = "RECLASSIFIED"  # un evento previo resultó ser AUTHORIZED
    ENDED = "ENDED"  # la presencia terminó (o su estado fue sustituido)


@dataclass(frozen=True)
class TrackerEvent:
    type: EventType
    event_id: str
    track_id: str
    status: IdentityStatus
    at: float
    started_at: float
    observations: int = 0
    name: str | None = None
    similarity: float | None = None
    prev_event_id: str | None = None
    detail: str = ""


@dataclass
class _Track:
    id: str
    first_seen: float
    last_seen: float
    bbox: tuple[int, int, int, int]
    n_obs: int = 0
    verdicts: deque = field(default_factory=lambda: deque(maxlen=WINDOW))
    names: Counter = field(default_factory=Counter)
    best_sim: float | None = None
    status: IdentityStatus | None = None
    event_id: str | None = None
    event_started: float = 0.0


def _area(b) -> float:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


def affinity(a, b) -> float:
    """IoU, o una afinidad baja si el centro de la caja pequeña cae dentro de la grande."""
    v = _iou(a, b)
    if v > 0:
        return v
    small, big = (a, b) if _area(a) <= _area(b) else (b, a)
    cx, cy = (small[0] + small[2]) / 2, (small[1] + small[3]) / 2
    mx, my = 0.1 * (big[2] - big[0]), 0.1 * (big[3] - big[1])
    if big[0] - mx <= cx <= big[2] + mx and big[1] - my <= cy <= big[3] + my:
        return 0.06
    return 0.0


class PresenceTracker:
    def __init__(self, cfg: TrackerConfig | None = None) -> None:
        self.cfg = cfg or TrackerConfig()
        self._tracks: dict[str, _Track] = {}

    def set_config(self, cfg: TrackerConfig) -> None:
        self.cfg = cfg

    def reset(self) -> None:
        self._tracks.clear()

    # ------------------------------------------------------------------

    def update(self, now: float, observations: Iterable[Observation]) -> list[TrackerEvent]:
        events: list[TrackerEvent] = []
        obs_list = list(observations)

        # asociación voraz por afinidad descendente
        pairs = []
        for oi, o in enumerate(obs_list):
            for tid, t in self._tracks.items():
                a = affinity(o.bbox, t.bbox)
                if a >= self.cfg.min_affinity:
                    pairs.append((a, oi, tid))
        pairs.sort(reverse=True)
        used_obs: set[int] = set()
        used_tracks: set[str] = set()
        for a, oi, tid in pairs:
            if oi in used_obs or tid in used_tracks:
                continue
            used_obs.add(oi)
            used_tracks.add(tid)
            self._apply(self._tracks[tid], obs_list[oi], now)
        for oi, o in enumerate(obs_list):
            if oi not in used_obs:
                t = _Track(uuid.uuid4().hex[:12], now, now, o.bbox)
                self._tracks[t.id] = t
                self._apply(t, o, now)

        for t in list(self._tracks.values()):
            events.extend(self._evaluate(t, now))
        events.extend(self._expire(now))
        return events

    def snapshot(self) -> list[dict]:
        return [
            {
                "track_id": t.id,
                "status": t.status.value if t.status else "PENDING",
                "event_id": t.event_id,
                "observations": t.n_obs,
                "bbox": t.bbox,
            }
            for t in self._tracks.values()
        ]

    # ------------------------------------------------------------------

    def _apply(self, t: _Track, o: Observation, now: float) -> None:
        t.last_seen = now
        t.n_obs += 1
        if not (_area(o.bbox) < 0.2 * _area(t.bbox)):  # una caja de solo-cara no encoge el track
            t.bbox = o.bbox
        if o.face_status in (IdentityStatus.AUTHORIZED, IdentityStatus.UNKNOWN):
            t.verdicts.append(o.face_status)
            if o.face_status is IdentityStatus.AUTHORIZED and o.name:
                t.names[o.name] += 1
            if o.similarity is not None and (t.best_sim is None or o.similarity > t.best_sim):
                t.best_sim = o.similarity

    def _target(self, t: _Track, now: float) -> IdentityStatus | None:
        c = self.cfg
        auth = sum(1 for v in t.verdicts if v is IdentityStatus.AUTHORIZED)
        unk = sum(1 for v in t.verdicts if v is IdentityStatus.UNKNOWN)
        dur = now - t.first_seen
        cur = t.status
        if cur is IdentityStatus.UNKNOWN:
            # para retirar una alerta por desconocido se exige más evidencia que para confirmarla
            if auth >= max(c.min_authorized_observations, c.min_observations) and auth > unk:
                return IdentityStatus.AUTHORIZED
            return None
        if auth >= c.min_authorized_observations and auth > unk:
            return IdentityStatus.AUTHORIZED if cur is not IdentityStatus.AUTHORIZED else None
        if unk >= c.min_observations and unk > auth and dur >= c.confirm_seconds:
            return IdentityStatus.UNKNOWN
        if cur is None and dur >= c.unverified_timeout_seconds and t.n_obs >= c.min_observations:
            return IdentityStatus.UNVERIFIED
        return None

    def _evaluate(self, t: _Track, now: float) -> list[TrackerEvent]:
        target = self._target(t, now)
        if target is None or target is t.status:
            return []
        out: list[TrackerEvent] = []
        name = t.names.most_common(1)[0][0] if t.names else None
        prev_id, prev_status = t.event_id, t.status
        if prev_status is IdentityStatus.UNKNOWN and target is IdentityStatus.AUTHORIZED and prev_id:
            new_id = uuid.uuid4().hex[:12]
            out.append(
                TrackerEvent(EventType.RECLASSIFIED, new_id, t.id, target, now, t.event_started, t.n_obs, name, t.best_sim,
                             prev_event_id=prev_id, detail="Reclasificada como persona autorizada")
            )
            t.status, t.event_id, t.event_started = target, new_id, now
            return out
        if prev_id and prev_status is not None:
            out.append(TrackerEvent(EventType.ENDED, prev_id, t.id, prev_status, now, t.event_started, t.n_obs,
                                    detail="Sustituido por un nuevo estado"))
        new_id = uuid.uuid4().hex[:12]
        t.status, t.event_id, t.event_started = target, new_id, now if prev_id else t.first_seen
        out.append(
            TrackerEvent(EventType.CONFIRMED, new_id, t.id, target, now, t.event_started, t.n_obs, name, t.best_sim,
                         prev_event_id=prev_id)
        )
        return out

    def _expire(self, now: float) -> list[TrackerEvent]:
        out: list[TrackerEvent] = []
        for t in list(self._tracks.values()):
            if now - t.last_seen >= self.cfg.exit_seconds:
                if t.status is not None and t.event_id:
                    out.append(TrackerEvent(EventType.ENDED, t.event_id, t.id, t.status, t.last_seen, t.event_started,
                                            t.n_obs, detail="La presencia terminó"))
                del self._tracks[t.id]
        return out
